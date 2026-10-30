"""
Unit tests for Migration Planner (Phase 6).

Covers:
  API Mapper:
    - matching detections to mappings
    - unmatched detections skipped

  Migration Planner:
    - plan generation for each transformation type
    - argument transformations built correctly
    - plans sorted by line number
    - semantic issues reflected in validation notes
    - confidence scoring
    - convenience helper function
    - full pipeline integration: parse → detect → symbols → semantics → plan
"""

import pytest

from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import APIDetector, load_mappings
from app.analyzer.symbol_analyzer import build_symbol_table
from app.analyzer.semantic_analyzer import (
    SemanticAnalyzer,
    load_target_specs,
    SemanticReport,
    SemanticIssue,
    IssueSeverity,
    IssueKind,
)
from app.migration.api_mapper import match_detections_to_mappings, MappedAPI
from app.migration.migration_planner import (
    MigrationPlanner,
    create_migration_plans,
)
from app.models.api_models import DetectedAPI
from app.models.migration_models import MigrationMapping, MigrationPlan
from app.config import DATA_DIR


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def mappings():
    return load_mappings(DATA_DIR / "api_mappings.json")


@pytest.fixture(scope="module")
def target_specs():
    return load_target_specs(DATA_DIR / "target_api_specs.json")


@pytest.fixture(scope="module")
def detector(mappings):
    return APIDetector(mappings)


@pytest.fixture(scope="module")
def sem_analyzer(mappings, target_specs):
    return SemanticAnalyzer(mappings, target_specs)


@pytest.fixture(scope="module")
def planner(mappings):
    return MigrationPlanner(mappings)


# ── Helper ───────────────────────────────────────────────

def _full_pipeline(code, detector, sem_analyzer, planner):
    """Parse → detect → symbols → semantics → plan."""
    pr = parse_source(code)
    assert pr.success
    st = build_symbol_table(pr)
    dets = detector.detect(pr)
    report = sem_analyzer.analyze(dets, st, pr)
    plans = planner.plan(dets, report)
    return pr, dets, report, plans


# ════════════════════════════════════════════════════════════
# API MAPPER
# ════════════════════════════════════════════════════════════

class TestAPIMapper:

    def test_match_single(self, mappings):
        det = DetectedAPI(api_name="HTTPClient.old_connect", line=4)
        matched = match_detections_to_mappings([det], mappings)
        assert len(matched) == 1
        assert matched[0].mapping.migration_id == "BM-HTTP-001"

    def test_match_multiple(self, mappings):
        dets = [
            DetectedAPI(api_name="Database.open_connection", line=4),
            DetectedAPI(api_name="Database.execute_query", line=5),
            DetectedAPI(api_name="Database.fetchall", line=6),
        ]
        matched = match_detections_to_mappings(dets, mappings)
        assert len(matched) == 3
        ids = {m.mapping.migration_id for m in matched}
        assert ids == {"BM-DB-001", "BM-DB-002", "BM-DB-003"}

    def test_unmatched_detection_skipped(self, mappings):
        det = DetectedAPI(api_name="FakeClass.fake_method", line=1)
        matched = match_detections_to_mappings([det], mappings)
        assert len(matched) == 0

    def test_empty_detections(self, mappings):
        matched = match_detections_to_mappings([], mappings)
        assert matched == []


# ════════════════════════════════════════════════════════════
# MIGRATION PLANNER
# ════════════════════════════════════════════════════════════

class TestPlanGeneration:

    def test_simple_rename_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        _, dets, report, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.old_api == "HTTPClient.close_connection"
        assert p.new_api == "HTTPClient.close"
        assert p.transformation_type == "rename"
        assert p.argument_changes == []
        assert p.confidence == 1.0

    def test_rename_with_type_conversion_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        _, dets, report, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.old_api == "HTTPClient.old_connect"
        assert p.new_api == "HTTPClient.connect"
        assert p.transformation_type == "rename_with_arg_modify"

        # Should have arg transformations
        assert len(p.argument_changes) == 2
        port_txn = [a for a in p.argument_changes if a.old_name == "port"]
        assert len(port_txn) == 1
        assert port_txn[0].type_conversion == "str_to_int"

    def test_arg_reorder_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.send_request('http://api.test', 'GET', 'data')\n"
        )
        _, _, _, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.transformation_type == "arg_reorder_and_rename"
        assert p.confidence <= 0.95  # complex transform

    def test_arg_merge_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "auth.login('admin', 'secret')\n"
        )
        _, _, _, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.transformation_type == "arg_merge"
        assert p.confidence <= 0.9

    def test_method_to_property_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "token = auth.get_token()\n"
        )
        _, _, _, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.transformation_type == "method_to_property"
        assert p.new_api == "Auth.token"

    def test_rename_with_new_param_plan(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.execute_query('SELECT 1')\n"
        )
        _, _, _, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 1
        p = plans[0]
        assert p.transformation_type == "rename_with_new_param"
        # New param 'params' has default=None, so it's an added optional
        new_param = [a for a in p.argument_changes if a.new_name == "params"]
        assert len(new_param) == 1
        assert new_param[0].default_value == "None"


class TestPlanOrdering:

    def test_plans_sorted_by_line(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "\n"
            "db = Database()\n"
            "db.open_connection('h', 5432, 'db')\n"
            "db.execute_query('SELECT 1')\n"
            "results = db.fetchall()\n"
        )
        _, _, _, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        assert len(plans) == 3
        lines = [p.line for p in plans]
        assert lines == sorted(lines)


class TestPlanConfidence:

    def test_semantic_error_reduces_confidence(self, detector, planner, mappings, target_specs):
        """Semantic error should reduce plan confidence."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        sem = SemanticAnalyzer(mappings, target_specs)
        report = sem.analyze(dets, st, pr)

        plans = planner.plan(dets, report)
        p = plans[0]
        # Semantic report has type mismatch warnings → confidence < 1.0
        if report.warnings:
            assert p.confidence < 1.0

    def test_no_semantic_report_full_confidence(self, detector, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        pr = parse_source(code)
        dets = detector.detect(pr)
        plans = planner.plan(dets, semantic_report=None)
        assert plans[0].confidence == 1.0


class TestPlanValidationNotes:

    def test_semantic_issues_in_notes(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        _, _, report, plans = _full_pipeline(code, detector, sem_analyzer, planner)
        p = plans[0]
        # Should have validation notes from semantic analysis
        if report.issues:
            assert len(p.validation_notes) > 0


class TestConvenienceHelper:

    def test_create_migration_plans(self, mappings):
        dets = [DetectedAPI(api_name="HTTPClient.close_connection", line=3)]
        plans = create_migration_plans(dets, mappings)
        assert len(plans) == 1
        assert plans[0].new_api == "HTTPClient.close"


# ════════════════════════════════════════════════════════════
# FULL PIPELINE INTEGRATION
# ════════════════════════════════════════════════════════════

class TestFullPipelineIntegration:

    def test_multi_api_full_pipeline(self, detector, sem_analyzer, planner):
        """Full pipeline with multiple APIs from different libraries."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "from benchmark_cache import Cache\n"
            "\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
            "cache = Cache()\n"
            "cache.set_value('session', 'abc', 60)\n"
            "val = cache.get_value('session')\n"
            "client.close_connection()\n"
        )
        pr, dets, report, plans = _full_pipeline(code, detector, sem_analyzer, planner)

        assert len(dets) == 4
        assert len(plans) == 4

        api_names = {p.old_api for p in plans}
        assert api_names == {
            "HTTPClient.old_connect",
            "Cache.set_value",
            "Cache.get_value",
            "HTTPClient.close_connection",
        }

        # Every plan has required fields
        for p in plans:
            assert p.migration_id
            assert p.old_api
            assert p.new_api
            assert p.transformation_type
            assert isinstance(p.confidence, float)
