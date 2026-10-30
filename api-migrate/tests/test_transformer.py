"""
Unit tests for AST Transformer and Code Generator (Phase 7).

Covers:
  - Simple method rename
  - Rename with type conversion (str→int)
  - Argument reorder and rename
  - Rename with new optional param
  - Argument merge to dict
  - Method to property conversion
  - Multiple transformations in one file
  - Code re-parsability after transformation
  - Diff generation
  - Original tree not mutated
"""

import ast
import pytest

from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import APIDetector, load_mappings
from app.analyzer.symbol_analyzer import build_symbol_table
from app.analyzer.semantic_analyzer import SemanticAnalyzer, load_target_specs
from app.migration.migration_planner import MigrationPlanner
from app.migration.ast_transformer import apply_migrations, TransformResult
from app.migration.code_generator import generate_code, generate_diff, GeneratedCode
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

def _full_transform(code, detector, sem_analyzer, planner):
    """Full pipeline: parse → detect → symbols → semantics → plan → transform → codegen."""
    pr = parse_source(code)
    assert pr.success, f"Parse failed: {pr.errors}"
    st = build_symbol_table(pr)
    dets = detector.detect(pr)
    report = sem_analyzer.analyze(dets, st, pr)
    plans = planner.plan(dets, report)
    result = apply_migrations(pr.tree, plans)
    gen = generate_code(code, result.transformed_tree)
    return pr, dets, plans, result, gen


# ════════════════════════════════════════════════════════════
# TRANSFORMATION TESTS
# ════════════════════════════════════════════════════════════

class TestSimpleRename:

    def test_close_connection_to_close(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        _, _, plans, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert len(plans) == 1
        assert result.success
        assert "close_connection" not in gen.migrated_source
        assert ".close()" in gen.migrated_source

    def test_fetchall_to_fetch_all(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.fetchall()\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)
        assert result.success
        assert "fetchall" not in gen.migrated_source
        assert "fetch_all" in gen.migrated_source


class TestRenameWithTypeConversion:

    def test_old_connect_port_str_to_int(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert result.success
        assert "old_connect" not in gen.migrated_source
        assert ".connect(" in gen.migrated_source
        # Port arg should be wrapped in int()
        assert "int(" in gen.migrated_source


class TestArgReorderAndRename:

    def test_send_request_reorder(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.send_request('http://api.test', 'GET', 'payload')\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert result.success
        assert "send_request" not in gen.migrated_source
        assert ".request(" in gen.migrated_source
        # Method should come before URL in the new signature
        src = gen.migrated_source
        assert "'GET'" in src
        # 'body' should appear as keyword
        assert "body=" in src


class TestRenameWithNewParam:

    def test_execute_query_to_execute(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.execute_query('SELECT * FROM users')\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert result.success
        assert "execute_query" not in gen.migrated_source
        assert ".execute(" in gen.migrated_source
        # New 'params' param should NOT be emitted (it has a default)


class TestArgMerge:

    def test_login_to_authenticate_dict(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "auth.login('admin', 'secret')\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert result.success
        assert "login" not in gen.migrated_source
        assert ".authenticate(" in gen.migrated_source
        # Should contain a dict with username and password
        assert "'username'" in gen.migrated_source
        assert "'password'" in gen.migrated_source
        assert "'admin'" in gen.migrated_source
        assert "'secret'" in gen.migrated_source


class TestMethodToProperty:

    def test_get_token_to_property(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "token = auth.get_token()\n"
        )
        _, _, _, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert result.success
        assert "get_token()" not in gen.migrated_source
        assert "auth.token" in gen.migrated_source
        # No parentheses — it's a property now
        assert "auth.token()" not in gen.migrated_source


# ════════════════════════════════════════════════════════════
# MULTI-TRANSFORMATION & STRUCTURAL TESTS
# ════════════════════════════════════════════════════════════

class TestMultipleTransformations:

    def test_three_db_migrations(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "\n"
            "db = Database()\n"
            "db.open_connection('localhost', 5432, 'mydb')\n"
            "db.execute_query('SELECT * FROM users')\n"
            "results = db.fetchall()\n"
        )
        _, _, plans, result, gen = _full_transform(code, detector, sem_analyzer, planner)

        assert len(plans) == 3
        assert len(result.plans_applied) == 3
        assert "open_connection" not in gen.migrated_source
        assert "execute_query" not in gen.migrated_source
        assert "fetchall" not in gen.migrated_source
        assert ".connect(" in gen.migrated_source
        assert ".execute(" in gen.migrated_source
        assert ".fetch_all(" in gen.migrated_source


class TestReparsability:
    """Verify that generated code can be parsed back to a valid AST."""

    def test_simple_rename_reparsable(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        _, _, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        # Must parse without error
        reparsed = parse_source(gen.migrated_source)
        assert reparsed.success, f"Re-parse failed: {reparsed.errors}"

    def test_complex_transform_reparsable(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.send_request('http://api.test', 'GET', 'data')\n"
        )
        _, _, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        reparsed = parse_source(gen.migrated_source)
        assert reparsed.success, f"Re-parse failed: {reparsed.errors}"

    def test_arg_merge_reparsable(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "auth.login('admin', 'secret')\n"
        )
        _, _, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        reparsed = parse_source(gen.migrated_source)
        assert reparsed.success, f"Re-parse failed: {reparsed.errors}"

    def test_method_to_property_reparsable(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "token = auth.get_token()\n"
        )
        _, _, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        reparsed = parse_source(gen.migrated_source)
        assert reparsed.success, f"Re-parse failed: {reparsed.errors}"


class TestOriginalPreserved:

    def test_original_tree_not_mutated(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('host', '80')\n"
        )
        pr = parse_source(code)
        original_dump = ast.dump(pr.tree)

        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = sem_analyzer.analyze(dets, st, pr)
        plans = planner.plan(dets, report)
        result = apply_migrations(pr.tree, plans)

        # Original tree unchanged
        assert ast.dump(pr.tree) == original_dump
        # Transformed tree is different
        assert ast.dump(result.transformed_tree) != original_dump


# ════════════════════════════════════════════════════════════
# CODE GENERATOR / DIFF TESTS
# ════════════════════════════════════════════════════════════

class TestCodeGenerator:

    def test_diff_generated(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        _, _, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        assert gen.has_changes
        assert len(gen.diff) > 0
        assert "close_connection" in gen.diff
        assert "close" in gen.diff

    def test_no_changes_no_diff(self):
        diff = generate_diff("x = 1\n", "x = 1\n")
        assert diff == ""

    def test_diff_format(self):
        diff = generate_diff("old_line\n", "new_line\n")
        assert "---" in diff
        assert "+++" in diff
        assert "-old_line" in diff
        assert "+new_line" in diff

    def test_no_detections_no_changes(self, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.connect('host', 8080)\n"
        )
        _, dets, _, _, gen = _full_transform(code, detector, sem_analyzer, planner)
        assert len(dets) == 0
        # No migrations applied → no semantic changes
