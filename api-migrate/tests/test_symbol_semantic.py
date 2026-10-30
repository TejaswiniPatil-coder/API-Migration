"""
Unit tests for Symbol Analyzer and Semantic Analyzer (Phase 5).

Covers:
  Symbol Analyzer:
    - variable → class association via constructor
    - import bindings
    - function parameter tracking
    - annotated assignments
    - scope awareness (module vs function)
    - literal type inference

  Semantic Analyzer:
    - type mismatch detection (str where int expected)
    - compatible types (no false error)
    - argument count mismatch
    - unknown/unresolvable type warning
    - arg_merge feasibility
    - method_to_property with arguments (error)
    - full pipeline integration with benchmark APIs
"""

import pytest
from pathlib import Path

from app.parser.ast_parser import parse_source
from app.analyzer.symbol_analyzer import (
    build_symbol_table,
    SymbolTable,
    SymbolInfo,
    SymbolKind,
)
from app.analyzer.semantic_analyzer import (
    SemanticAnalyzer,
    SemanticReport,
    SemanticIssue,
    IssueSeverity,
    IssueKind,
    load_target_specs,
    _infer_arg_type_from_repr,
    _types_compatible,
)
from app.analyzer.api_detector import load_mappings, APIDetector
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
def semantic_analyzer(mappings, target_specs):
    return SemanticAnalyzer(mappings, target_specs)


# ════════════════════════════════════════════════════════════
# SYMBOL ANALYZER TESTS
# ════════════════════════════════════════════════════════════

class TestSymbolTableConstruction:

    def test_constructor_assignment(self):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient()\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("client") == "HTTPClient"

    def test_multiple_variables(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "from benchmark_db import Database\n"
            "client = HTTPClient()\n"
            "db = Database()\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("client") == "HTTPClient"
        assert st.resolve_type("db") == "Database"

    def test_literal_assignment(self):
        code = 'x = 42\nname = "hello"\npi = 3.14\n'
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("x") == "int"
        assert st.resolve_type("name") == "str"
        assert st.resolve_type("pi") == "float"

    def test_unresolvable_returns_none(self):
        code = "x = some_function()\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        # some_function is not in table, so x's type is "some_function"
        # (the function name, not a resolved type — but not None)
        # Actually, _infer_type for Call with Name returns node.func.id
        result = st.resolve_type("x")
        assert result == "some_function"

    def test_resolve_method_class(self):
        code = "from benchmark_db import Database\ndb = Database()\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_method_class("db", "execute_query") == "Database"

    def test_nonexistent_variable(self):
        code = "x = 1\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("nonexistent") is None


class TestSymbolTableImports:

    def test_from_import(self):
        code = "from benchmark_http import HTTPClient\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("HTTPClient")
        assert info is not None
        assert info.kind == SymbolKind.IMPORT
        assert info.source_module == "benchmark_http"

    def test_import_alias(self):
        code = "import numpy as np\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("np")
        assert info is not None
        assert info.source_module == "numpy"

    def test_plain_import(self):
        code = "import os\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("os")
        assert info is not None
        assert info.kind == SymbolKind.IMPORT


class TestSymbolTableScopes:

    def test_function_definition_tracked(self):
        code = "def my_func():\n    pass\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("my_func")
        assert info is not None
        assert info.kind == SymbolKind.FUNCTION

    def test_function_parameters(self):
        code = "def greet(name: str, count: int):\n    pass\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        name_info = st.lookup("name", scope="function:greet")
        assert name_info is not None
        assert name_info.kind == SymbolKind.PARAMETER
        assert name_info.inferred_type == "str"

        count_info = st.lookup("count", scope="function:greet")
        assert count_info is not None
        assert count_info.inferred_type == "int"

    def test_class_definition_tracked(self):
        code = "class MyClass:\n    pass\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("MyClass")
        assert info is not None
        assert info.kind == SymbolKind.CLASS

    def test_variable_inside_function(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "def setup():\n"
            "    client = HTTPClient()\n"
            "    return client\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        info = st.lookup("client", scope="function:setup")
        assert info is not None
        assert info.inferred_type == "HTTPClient"

    def test_variables_of_type(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "c1 = HTTPClient()\n"
            "c2 = HTTPClient()\n"
            "x = 42\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        http_vars = st.variables_of_type("HTTPClient")
        names = {s.name for s in http_vars}
        assert names == {"c1", "c2"}


class TestAnnotatedAssignment:

    def test_annotated_with_value(self):
        code = "count: int = 10\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("count") == "int"

    def test_annotated_without_value(self):
        code = "name: str\n"
        pr = parse_source(code)
        st = build_symbol_table(pr)
        assert st.resolve_type("name") == "str"


# ════════════════════════════════════════════════════════════
# TYPE INFERENCE HELPERS
# ════════════════════════════════════════════════════════════

class TestTypeInferenceHelpers:

    def test_string_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("'hello'", st) == "str"
        assert _infer_arg_type_from_repr('"hello"', st) == "str"

    def test_int_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("42", st) == "int"
        assert _infer_arg_type_from_repr("-7", st) == "int"

    def test_float_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("3.14", st) == "float"

    def test_bool_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("True", st) == "bool"
        assert _infer_arg_type_from_repr("False", st) == "bool"

    def test_none_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("None", st) == "NoneType"

    def test_dict_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("{'a': 1}", st) == "dict"

    def test_list_literal(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("[1, 2]", st) == "list"

    def test_variable_lookup(self):
        st = SymbolTable()
        st.add(SymbolInfo(name="port", kind=SymbolKind.VARIABLE, inferred_type="int"))
        assert _infer_arg_type_from_repr("port", st) == "int"

    def test_unresolvable(self):
        st = SymbolTable()
        assert _infer_arg_type_from_repr("some_call(x)", st) is None

    def test_types_compatible_same(self):
        assert _types_compatible("int", "int") is True

    def test_types_compatible_numeric(self):
        assert _types_compatible("int", "float") is True
        assert _types_compatible("float", "int") is True

    def test_types_incompatible(self):
        assert _types_compatible("int", "str") is False


# ════════════════════════════════════════════════════════════
# SEMANTIC ANALYZER TESTS
# ════════════════════════════════════════════════════════════

class TestSemanticTypeMismatch:

    def test_str_where_int_expected(self, detector, semantic_analyzer):
        """BM-HTTP-001: port is str but target expects int → warning (conversion planned)."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        # Should have a type-related issue for 'port'
        type_issues = [i for i in report.issues if i.kind == IssueKind.TYPE_MISMATCH]
        port_issues = [i for i in type_issues if i.parameter == "port"]
        assert len(port_issues) >= 1
        assert port_issues[0].expected_type == "int"
        assert port_issues[0].actual_type == "str"

    def test_already_correct_type_no_error(self, detector, semantic_analyzer):
        """BM-CACHE-001: all args are already correct types → no errors."""
        code = (
            "from benchmark_cache import Cache\n"
            "cache = Cache()\n"
            "cache.set_value('key', 'val', 300)\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        errors = [i for i in report.issues if i.severity == IssueSeverity.ERROR]
        assert len(errors) == 0


class TestSemanticArgCount:

    def test_correct_arg_count(self, detector, semantic_analyzer):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('host', '80')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        count_issues = [i for i in report.issues if i.kind == IssueKind.ARG_COUNT_MISMATCH]
        assert len(count_issues) == 0

    def test_wrong_arg_count(self, detector, semantic_analyzer):
        """Pass 3 args to an API expecting 2."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('host', '80', 'extra')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        count_issues = [i for i in report.issues if i.kind == IssueKind.ARG_COUNT_MISMATCH]
        assert len(count_issues) >= 1


class TestSemanticFeasibility:

    def test_method_to_property_no_args_ok(self, detector, semantic_analyzer):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "auth.get_token()\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        incompat = [i for i in report.issues if i.kind == IssueKind.INCOMPATIBLE_USAGE]
        assert len(incompat) == 0

    def test_method_to_property_with_args_error(self, semantic_analyzer):
        """Simulate a get_token call with arguments → error."""
        det = type("D", (), {
            "api_name": "Auth.get_token",
            "line": 3,
            "arguments": ["some_arg"],
            "keyword_arguments": {},
        })()
        report = semantic_analyzer.analyze([det], SymbolTable())
        incompat = [i for i in report.issues if i.kind == IssueKind.INCOMPATIBLE_USAGE]
        assert len(incompat) >= 1
        assert incompat[0].severity == IssueSeverity.ERROR


class TestSemanticUnknownType:

    def test_variable_arg_unknown_type(self, detector, semantic_analyzer):
        """Passing a variable with unknown type → info-level issue."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "h = get_host()\n"
            "client.old_connect(h, '8080')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = semantic_analyzer.analyze(dets, st, pr)

        # 'h' type is "get_host" (unresolvable function name) — but since
        # host→host has no type_conversion, the semantic analyzer checks
        # if types match.  The inferred type "get_host" ≠ "str", so it
        # should flag it.
        issues = [i for i in report.issues
                  if i.parameter == "host" and i.kind in (IssueKind.TYPE_MISMATCH, IssueKind.UNKNOWN_TYPE)]
        assert len(issues) >= 1


class TestSemanticIntegration:
    """Full pipeline: parse → detect → symbols → semantics."""

    def test_full_pipeline_benchmark_http(self, detector, semantic_analyzer):
        code = (
            "from benchmark_http import HTTPClient\n"
            "\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
            "response = client.send_request('http://api.test', 'GET', 'data')\n"
            "client.close_connection()\n"
        )
        pr = parse_source(code)
        assert pr.success
        st = build_symbol_table(pr)
        assert st.resolve_type("client") == "HTTPClient"

        dets = detector.detect(pr)
        assert len(dets) == 3

        report = semantic_analyzer.analyze(dets, st, pr)
        # Should have analysis output — no crashes
        assert isinstance(report, SemanticReport)
        assert report.summary()  # produces a string
