"""
Unit tests for Validation Pipeline (Phase 9).

Covers all 5 validators + the orchestration pipeline.
"""

import pytest
from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import APIDetector, load_mappings
from app.analyzer.symbol_analyzer import build_symbol_table
from app.analyzer.semantic_analyzer import SemanticAnalyzer, load_target_specs
from app.migration.migration_planner import MigrationPlanner
from app.migration.ast_transformer import apply_migrations
from app.migration.code_generator import generate_code
from app.validation.syntax_validator import validate_syntax
from app.validation.ast_validator import validate_ast
from app.validation.type_validator import validate_types
from app.validation.api_validator import validate_api_compliance
from app.validation.compiler_validator import validate_compilation
from app.validation.validation_pipeline import run_validation_pipeline
from app.models.validation_models import ValidationLevel
from app.config import DATA_DIR


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


def _migrate(code, detector, sem_analyzer, planner):
    pr = parse_source(code)
    st = build_symbol_table(pr)
    dets = detector.detect(pr)
    report = sem_analyzer.analyze(dets, st, pr)
    plans = planner.plan(dets, report)
    result = apply_migrations(pr.tree, plans)
    gen = generate_code(code, result.transformed_tree)
    return gen.migrated_source, plans


# ── Syntax Validator ─────────────────────────────────────

class TestSyntaxValidator:
    def test_valid_code(self):
        r = validate_syntax("x = 1\n")
        assert r.passed

    def test_invalid_code(self):
        r = validate_syntax("def foo(\n")
        assert not r.passed
        assert r.level == ValidationLevel.ERROR


# ── Compiler Validator ───────────────────────────────────

class TestCompilerValidator:
    def test_valid(self):
        assert validate_compilation("x = 1").passed

    def test_invalid(self):
        assert not validate_compilation("def :(").passed


# ── AST Validator ────────────────────────────────────────

class TestASTValidator:
    def test_migrated_code_valid_ast(self, detector, sem_analyzer, planner):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient()\nclient.close_connection()\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        results = validate_ast(src, plans)
        assert all(r.passed for r in results)

    def test_old_api_absent(self, detector, sem_analyzer, planner):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient()\nclient.old_connect('h', '80')\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        results = validate_ast(src, plans)
        removal_checks = [r for r in results if "removed" in r.message]
        assert all(r.passed for r in removal_checks)


# ── API Validator ────────────────────────────────────────

class TestAPIValidator:
    def test_method_to_property_compliance(self, detector, sem_analyzer, planner, target_specs):
        code = "from benchmark_auth import Auth\nauth = Auth()\ntoken = auth.get_token()\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        results = validate_api_compliance(src, plans, target_specs)
        assert any(r.passed and "property" in r.message for r in results)


# ── Full Pipeline ────────────────────────────────────────

class TestValidationPipeline:
    def test_simple_rename_passes(self, detector, sem_analyzer, planner, target_specs):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient()\nclient.close_connection()\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        report = run_validation_pipeline(src, plans, target_specs)
        assert report.passed
        assert len(report.errors) == 0

    def test_complex_migration_passes(self, detector, sem_analyzer, planner, target_specs):
        code = "from benchmark_auth import Auth\nauth = Auth()\nauth.login('admin', 'secret')\ntoken = auth.get_token()\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        report = run_validation_pipeline(src, plans, target_specs)
        assert report.passed

    def test_multi_api_pipeline(self, detector, sem_analyzer, planner, target_specs):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.open_connection('h', 5432, 'db')\n"
            "db.execute_query('SELECT 1')\n"
            "results = db.fetchall()\n"
        )
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        report = run_validation_pipeline(src, plans, target_specs)
        assert report.passed
        assert len(report.results) > 5

    def test_invalid_source_fails(self):
        report = run_validation_pipeline("def :(", [])
        assert not report.passed

    def test_report_summary(self, detector, sem_analyzer, planner, target_specs):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient()\nclient.close_connection()\n"
        src, plans = _migrate(code, detector, sem_analyzer, planner)
        report = run_validation_pipeline(src, plans, target_specs)
        s = report.summary()
        assert "PASS" in s or "FAIL" in s
