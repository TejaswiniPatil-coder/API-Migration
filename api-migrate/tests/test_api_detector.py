"""
Unit tests for the API Detector (Phase 4).

Covers:
  - Detection of each deprecated API pattern
  - Correct line numbers and arguments in detections
  - Non-deprecated APIs are NOT falsely detected
  - Already-migrated APIs are ignored
  - Multiple deprecated calls in one source
  - Missing imports → no detection
  - Unrelated libraries → no detection
  - The one-shot detect_deprecated_apis() helper
"""

import pytest
from pathlib import Path

from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import (
    APIDetector,
    detect_deprecated_apis,
    load_mappings,
    _build_variable_class_map,
    _get_imported_libraries,
)
from app.config import DATA_DIR


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def mappings():
    """Load the controlled benchmark mappings once."""
    return load_mappings(DATA_DIR / "api_mappings.json")


@pytest.fixture(scope="module")
def detector(mappings):
    return APIDetector(mappings)


# ────────────────────────────────────────────────────────────
# Helper: parse then detect
# ────────────────────────────────────────────────────────────

def _detect(detector: APIDetector, code: str):
    pr = parse_source(code)
    assert pr.success, f"Parse failed: {pr.errors}"
    return detector.detect(pr)


# ────────────────────────────────────────────────────────────
# Detection of each deprecated pattern
# ────────────────────────────────────────────────────────────

class TestDetectIndividualAPIs:

    def test_detect_old_connect(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        d = dets[0]
        assert d.api_name == "HTTPClient.old_connect"
        assert d.line == 4
        assert len(d.arguments) == 2

    def test_detect_send_request(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.send_request('http://example.com', 'GET', 'data')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "HTTPClient.send_request"
        assert len(dets[0].arguments) == 3

    def test_detect_close_connection(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "HTTPClient.close_connection"
        assert dets[0].arguments == []

    def test_detect_open_connection(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.open_connection('localhost', 5432, 'mydb')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Database.open_connection"

    def test_detect_execute_query(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.execute_query('SELECT 1')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Database.execute_query"

    def test_detect_fetchall(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.fetchall()\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Database.fetchall"

    def test_detect_set_value(self, detector):
        code = (
            "from benchmark_cache import Cache\n"
            "cache = Cache()\n"
            "cache.set_value('key', 'val', 300)\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Cache.set_value"

    def test_detect_get_value(self, detector):
        code = (
            "from benchmark_cache import Cache\n"
            "cache = Cache()\n"
            "cache.get_value('key')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Cache.get_value"

    def test_detect_login(self, detector):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "auth.login('admin', 'secret')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Auth.login"

    def test_detect_get_token(self, detector):
        code = (
            "from benchmark_auth import Auth\n"
            "auth = Auth()\n"
            "token = auth.get_token()\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Auth.get_token"


# ────────────────────────────────────────────────────────────
# False positives — must NOT be detected
# ────────────────────────────────────────────────────────────

class TestNoFalsePositives:

    def test_new_api_not_detected(self, detector):
        """Already-migrated code using the new API → no detections."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.connect('localhost', 8080)\n"
            "client.close()\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 0

    def test_unrelated_method_same_name_no_import(self, detector):
        """A method with the same name but wrong library → no detection."""
        code = (
            "import some_other_lib\n"
            "obj = some_other_lib.Thing()\n"
            "obj.old_connect('host', 'port')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 0

    def test_no_import_no_detection(self, detector):
        """If the benchmark library is not imported, don't detect."""
        code = (
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 0

    def test_bare_function_call_ignored(self, detector):
        """A bare function call (no receiver) matching a method name
        should NOT be detected because our mappings expect Class.method."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "old_connect('host', 'port')\n"  # no receiver
        )
        dets = _detect(detector, code)
        assert len(dets) == 0

    def test_different_class_same_method_ignored(self, detector):
        """A different class with the same method name → no detection."""
        code = (
            "from benchmark_http import HTTPClient\n"
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.close_connection()\n"  # Database doesn't have this migration
        )
        dets = _detect(detector, code)
        # Database.close_connection is not a mapping — should not match
        assert len(dets) == 0


# ────────────────────────────────────────────────────────────
# Multiple detections
# ────────────────────────────────────────────────────────────

class TestMultipleDetections:

    def test_multiple_deprecated_calls(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "\n"
            "db = Database()\n"
            "db.open_connection('localhost', 5432, 'mydb')\n"
            "db.execute_query('SELECT * FROM users')\n"
            "results = db.fetchall()\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 3
        names = {d.api_name for d in dets}
        assert names == {
            "Database.open_connection",
            "Database.execute_query",
            "Database.fetchall",
        }

    def test_mixed_old_and_new(self, detector):
        """Only the deprecated calls should be detected."""
        code = (
            "from benchmark_cache import Cache\n"
            "cache = Cache()\n"
            "cache.set_value('k', 'v', 60)\n"
            "result = cache.get('k')\n"  # new API
        )
        dets = _detect(detector, code)
        assert len(dets) == 1
        assert dets[0].api_name == "Cache.set_value"

    def test_cross_library_detections(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "from benchmark_db import Database\n"
            "\n"
            "client = HTTPClient()\n"
            "client.old_connect('host', '80')\n"
            "db = Database()\n"
            "db.execute_query('SELECT 1')\n"
        )
        dets = _detect(detector, code)
        assert len(dets) == 2
        names = {d.api_name for d in dets}
        assert "HTTPClient.old_connect" in names
        assert "Database.execute_query" in names


# ────────────────────────────────────────────────────────────
# Detection metadata
# ────────────────────────────────────────────────────────────

class TestDetectionMetadata:

    def test_line_and_col(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('h', '80')\n"
        )
        dets = _detect(detector, code)
        assert dets[0].line == 3
        assert dets[0].col >= 0

    def test_arguments_captured(self, detector):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('myhost', '9090')\n"
        )
        dets = _detect(detector, code)
        assert dets[0].arguments == ["'myhost'", "'9090'"]

    def test_receiver_variable_captured(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "my_db = Database()\n"
            "my_db.execute_query('SELECT 1')\n"
        )
        dets = _detect(detector, code)
        assert dets[0].receiver_variable == "my_db"

    def test_inferred_class_captured(self, detector):
        code = (
            "from benchmark_db import Database\n"
            "my_db = Database()\n"
            "my_db.execute_query('SELECT 1')\n"
        )
        dets = _detect(detector, code)
        assert dets[0].inferred_class == "Database"


# ────────────────────────────────────────────────────────────
# Lightweight symbol table
# ────────────────────────────────────────────────────────────

class TestSymbolTable:

    def test_variable_class_map(self):
        code = (
            "client = HTTPClient()\n"
            "db = Database()\n"
            "x = 42\n"
        )
        pr = parse_source(code)
        var_map = _build_variable_class_map(pr)
        assert var_map["client"] == "HTTPClient"
        assert var_map["db"] == "Database"
        assert "x" not in var_map  # not a constructor call

    def test_imported_libraries_map(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "from benchmark_db import Database\n"
            "import os\n"
        )
        pr = parse_source(code)
        lib_map = _get_imported_libraries(pr)
        assert lib_map["HTTPClient"] == "benchmark_http"
        assert lib_map["Database"] == "benchmark_db"
        assert lib_map["os"] == "os"


# ────────────────────────────────────────────────────────────
# Convenience helper
# ────────────────────────────────────────────────────────────

class TestConvenienceHelper:

    def test_detect_deprecated_apis_helper(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('host', '80')\n"
        )
        pr, dets = detect_deprecated_apis(code)
        assert pr.success
        assert len(dets) == 1
        assert dets[0].api_name == "HTTPClient.old_connect"

    def test_detect_deprecated_apis_with_syntax_error(self):
        code = "from benchmark_http import HTTPClient\nclient = HTTPClient(\n"
        pr, dets = detect_deprecated_apis(code)
        assert not pr.success
        assert dets == []

    def test_detect_deprecated_apis_clean_code(self):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.connect('host', 8080)\n"
        )
        pr, dets = detect_deprecated_apis(code)
        assert pr.success
        assert dets == []
