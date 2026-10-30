"""
Unit tests for the AST parser (Phase 3).

Covers:
  - valid Python parsing
  - invalid Python / syntax error reporting
  - import extraction (import, from-import, aliases)
  - function call extraction
  - method call extraction (single and chained receivers)
  - argument extraction (positional, keyword, mixed)
  - attribute extraction (non-call attributes)
  - line/column preservation
  - empty / whitespace-only source
"""

import ast
import pytest

from app.parser.ast_parser import (
    parse_source,
    ParseResult,
    ParseError,
    ImportInfo,
    CallInfo,
    AttributeInfo,
    ArgumentInfo,
)


# ────────────────────────────────────────────────────────────
# Basic parsing — success / failure
# ────────────────────────────────────────────────────────────

class TestParseBasic:
    """Valid and invalid source parsing."""

    def test_valid_python_returns_success(self):
        result = parse_source("x = 1 + 2\n")
        assert result.success is True
        assert result.tree is not None
        assert isinstance(result.tree, ast.Module)
        assert result.has_errors is False

    def test_invalid_python_returns_error(self):
        result = parse_source("def foo(\n")
        assert result.success is False
        assert result.tree is None
        assert result.has_errors is True
        assert len(result.errors) >= 1

    def test_syntax_error_has_line_info(self):
        code = "x = 1\ny = (\nz = 3\n"
        result = parse_source(code)
        assert result.success is False
        err = result.errors[0]
        assert err.line is not None
        assert err.line >= 2  # error is on or after line 2

    def test_syntax_error_message_is_meaningful(self):
        result = parse_source("class 123bad:\n  pass\n")
        assert result.success is False
        # The message should contain something useful
        assert len(result.errors[0].message) > 0

    def test_empty_source(self):
        result = parse_source("")
        assert result.success is True
        assert result.tree is not None
        assert result.calls == []
        assert result.imports == []

    def test_whitespace_only_source(self):
        result = parse_source("   \n\n  \n")
        assert result.success is True

    def test_multiline_valid_code(self):
        code = (
            "import os\n"
            "x = 1\n"
            "def foo():\n"
            "    return x\n"
        )
        result = parse_source(code)
        assert result.success is True


# ────────────────────────────────────────────────────────────
# Import extraction
# ────────────────────────────────────────────────────────────

class TestImportExtraction:

    def test_simple_import(self):
        result = parse_source("import os\n")
        assert len(result.imports) == 1
        imp = result.imports[0]
        assert imp.node_type == "Import"
        assert "os" in imp.names

    def test_from_import(self):
        result = parse_source("from pathlib import Path\n")
        assert len(result.imports) == 1
        imp = result.imports[0]
        assert imp.node_type == "ImportFrom"
        assert imp.module == "pathlib"
        assert "Path" in imp.names

    def test_import_with_alias(self):
        result = parse_source("import numpy as np\n")
        imp = result.imports[0]
        assert imp.aliases.get("numpy") == "np"

    def test_from_import_multiple_names(self):
        result = parse_source("from os.path import join, exists\n")
        imp = result.imports[0]
        assert imp.module == "os.path"
        assert "join" in imp.names
        assert "exists" in imp.names

    def test_import_line_number(self):
        code = "# comment\nimport json\n"
        result = parse_source(code)
        assert result.imports[0].line == 2

    def test_multiple_import_statements(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        result = parse_source(code)
        assert len(result.imports) == 3


# ────────────────────────────────────────────────────────────
# Call extraction
# ────────────────────────────────────────────────────────────

class TestCallExtraction:

    def test_simple_function_call(self):
        result = parse_source("print('hello')\n")
        assert len(result.calls) >= 1
        call = result.calls[0]
        assert call.func_name == "print"
        assert call.receiver is None
        assert call.full_name == "print"

    def test_method_call(self):
        result = parse_source("client.connect('host')\n")
        calls = [c for c in result.calls if c.func_name == "connect"]
        assert len(calls) == 1
        call = calls[0]
        assert call.func_name == "connect"
        assert call.receiver == "client"
        assert call.full_name == "client.connect"

    def test_chained_method_call(self):
        # e.g. app.db.execute("sql")
        result = parse_source("app.db.execute('sql')\n")
        calls = [c for c in result.calls if c.func_name == "execute"]
        assert len(calls) == 1
        call = calls[0]
        assert call.func_name == "execute"
        assert call.receiver == "app.db"

    def test_constructor_call(self):
        result = parse_source("obj = MyClass()\n")
        calls = [c for c in result.calls if c.func_name == "MyClass"]
        assert len(calls) == 1
        assert calls[0].receiver is None

    def test_call_line_number(self):
        code = "x = 1\ny = foo()\n"
        result = parse_source(code)
        calls = [c for c in result.calls if c.func_name == "foo"]
        assert calls[0].line == 2

    def test_nested_calls(self):
        # int(str(42)) should produce two calls
        result = parse_source("int(str(42))\n")
        names = {c.func_name for c in result.calls}
        assert "int" in names
        assert "str" in names

    def test_no_calls_in_plain_assignment(self):
        result = parse_source("x = 42\n")
        assert result.calls == []

    def test_multiple_calls_same_line(self):
        result = parse_source("a(); b(); c()\n")
        names = [c.func_name for c in result.calls]
        assert "a" in names
        assert "b" in names
        assert "c" in names


# ────────────────────────────────────────────────────────────
# Argument extraction
# ────────────────────────────────────────────────────────────

class TestArgumentExtraction:

    def test_positional_args(self):
        result = parse_source("foo(1, 'hello', x)\n")
        call = result.calls[0]
        assert len(call.positional_args) == 3
        assert call.positional_args[0].value_repr == "1"
        assert call.positional_args[1].value_repr == "'hello'"
        assert call.positional_args[2].value_repr == "x"

    def test_keyword_args(self):
        result = parse_source("foo(a=1, b='two')\n")
        call = result.calls[0]
        assert len(call.keyword_args) == 2
        assert call.keyword_args[0].name == "a"
        assert call.keyword_args[0].value_repr == "1"
        assert call.keyword_args[1].name == "b"

    def test_mixed_args(self):
        result = parse_source("foo(1, 'two', key=3)\n")
        call = result.calls[0]
        assert len(call.positional_args) == 2
        assert len(call.keyword_args) == 1

    def test_no_args(self):
        result = parse_source("foo()\n")
        call = result.calls[0]
        assert call.positional_args == []
        assert call.keyword_args == []

    def test_all_args_property(self):
        result = parse_source("foo(1, b=2)\n")
        call = result.calls[0]
        assert len(call.all_args) == 2

    def test_string_arg_values(self):
        result = parse_source('client.old_connect("localhost", "8080")\n')
        call = [c for c in result.calls if c.func_name == "old_connect"][0]
        assert len(call.positional_args) == 2
        assert call.positional_args[0].value_repr == "'localhost'"
        assert call.positional_args[1].value_repr == "'8080'"

    def test_is_keyword_property(self):
        result = parse_source("foo(1, key=2)\n")
        call = result.calls[0]
        assert call.positional_args[0].is_keyword is False
        assert call.keyword_args[0].is_keyword is True


# ────────────────────────────────────────────────────────────
# Attribute extraction (non-call)
# ────────────────────────────────────────────────────────────

class TestAttributeExtraction:

    def test_simple_attribute_access(self):
        result = parse_source("x = obj.value\n")
        attrs = [a for a in result.attributes if a.name == "value"]
        assert len(attrs) >= 1
        assert attrs[0].receiver == "obj"

    def test_attribute_not_duplicated_from_call(self):
        # obj.method() → the 'method' attribute should NOT appear
        # in attributes (it's already captured as a call)
        result = parse_source("obj.method()\n")
        attr_names = [a.name for a in result.attributes]
        # 'method' should be excluded since it's the func of a Call
        assert "method" not in attr_names


# ────────────────────────────────────────────────────────────
# Benchmark API pattern (integration-style)
# ────────────────────────────────────────────────────────────

class TestBenchmarkPatterns:
    """Verify the parser handles the controlled benchmark API patterns."""

    def test_benchmark_http_pattern(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        result = parse_source(code)
        assert result.success

        # Import detected
        assert any(
            imp.module == "benchmark_http" and "HTTPClient" in imp.names
            for imp in result.imports
        )

        # Constructor call
        assert any(c.func_name == "HTTPClient" for c in result.calls)

        # Method call with args
        connect_calls = [c for c in result.calls if c.func_name == "old_connect"]
        assert len(connect_calls) == 1
        call = connect_calls[0]
        assert call.receiver == "client"
        assert call.line == 4
        assert len(call.positional_args) == 2

    def test_benchmark_multi_migration(self):
        code = (
            "from benchmark_db import Database\n"
            "\n"
            "db = Database()\n"
            "db.open_connection('localhost', 5432, 'mydb')\n"
            "db.execute_query('SELECT * FROM users')\n"
            "results = db.fetchall()\n"
        )
        result = parse_source(code)
        assert result.success

        call_names = [c.func_name for c in result.calls]
        assert "Database" in call_names
        assert "open_connection" in call_names
        assert "execute_query" in call_names
        assert "fetchall" in call_names

    def test_benchmark_method_to_property(self):
        code = (
            "from benchmark_auth import Auth\n"
            "\n"
            "auth = Auth()\n"
            "token = auth.get_token()\n"
        )
        result = parse_source(code)
        assert result.success
        get_token = [c for c in result.calls if c.func_name == "get_token"]
        assert len(get_token) == 1
        assert get_token[0].receiver == "auth"
        assert get_token[0].positional_args == []
