"""
API Detector — AST-based detection of deprecated API usage.

Walks the parsed AST (via the parser module's output) and matches
every function/method call against the migration knowledge base
loaded from ``api_mappings.json``.

Detection strategy
------------------
1. **Direct method-name matching**: If a call's ``func_name`` matches
   the method part of a mapping's ``old_api`` (e.g. ``old_connect``
   matches ``HTTPClient.old_connect``), it is a *candidate*.

2. **Receiver-class matching (lightweight)**: If the parser resolved
   a receiver variable name (e.g. ``client``), we attempt to associate
   it with a class by scanning assignments and constructor calls in
   the same parse result.  This uses a simple symbol table built from
   ``ParseResult.calls`` — full type inference is deferred to Phase 5
   (symbol_analyzer).

3. **Import-context matching**: We verify that the library in the
   mapping is actually imported in the source.

Limitations (documented):
  - Cannot resolve types through function return values.
  - Cannot track reassignments or aliasing across scopes.
  - These are addressed by the symbol_analyzer in Phase 5.

Compiler-design concepts exercised:
  - Symbol table construction (lightweight)
  - Pattern matching over AST nodes
  - Semantic pre-checks (import verification)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.models.api_models import DetectedAPI
from app.models.migration_models import MigrationMapping
from app.parser.ast_parser import ParseResult, CallInfo, ImportInfo


# ────────────────────────────────────────────────────────────
# Mapping loader
# ────────────────────────────────────────────────────────────

def load_mappings(path: Path) -> list[MigrationMapping]:
    """Load migration mappings from a JSON file and return
    a list of ``MigrationMapping`` dataclass instances."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    mappings: list[MigrationMapping] = []
    for entry in data.get("mappings", []):
        mappings.append(MigrationMapping(
            migration_id=entry["migration_id"],
            library=entry["library"],
            old_version=entry.get("old_version", ""),
            new_version=entry.get("new_version", ""),
            old_api=entry["old_api"],
            new_api=entry["new_api"],
            old_signature=entry.get("old_signature", []),
            new_signature=entry.get("new_signature", []),
            argument_transformations=entry.get("argument_transformations", []),
            description=entry.get("description", ""),
            transformation_type=entry.get("transformation_type", "rename"),
        ))
    return mappings


# ────────────────────────────────────────────────────────────
# Lightweight symbol table
# ────────────────────────────────────────────────────────────

def _build_variable_class_map(parse_result: ParseResult) -> dict[str, str]:
    """Build a mapping  variable_name → class_name  from constructor calls.

    Scans patterns like::

        client = HTTPClient()   →  {'client': 'HTTPClient'}
        db = Database()         →  {'db': 'Database'}

    This is a simplified symbol table.  It cannot handle:
      - reassignment  (client = other_thing)
      - return values (client = make_client())
      - aliased imports

    Those limitations are documented and partially addressed by
    the symbol_analyzer (Phase 5).
    """
    import ast as _ast

    var_map: dict[str, str] = {}
    if parse_result.tree is None:
        return var_map

    for node in _ast.walk(parse_result.tree):
        # Pattern: <name> = <Name>()
        if isinstance(node, _ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
            if (
                isinstance(target, _ast.Name)
                and isinstance(value, _ast.Call)
                and isinstance(value.func, _ast.Name)
            ):
                var_map[target.id] = value.func.id

    return var_map


def _get_imported_libraries(parse_result: ParseResult) -> dict[str, str]:
    """Return a mapping  class_or_module_name → library_module.

    For ``from benchmark_http import HTTPClient``
    →  {'HTTPClient': 'benchmark_http'}

    For ``import benchmark_http``
    →  {'benchmark_http': 'benchmark_http'}
    """
    lib_map: dict[str, str] = {}
    for imp in parse_result.imports:
        if imp.node_type == "ImportFrom" and imp.module:
            for name in imp.names:
                alias = imp.aliases.get(name)
                key = alias if alias else name
                lib_map[key] = imp.module
        elif imp.node_type == "Import":
            for name in imp.names:
                alias = imp.aliases.get(name)
                key = alias if alias else name
                lib_map[key] = name
    return lib_map


# ────────────────────────────────────────────────────────────
# Detector
# ────────────────────────────────────────────────────────────

class APIDetector:
    """Detect deprecated API calls in parsed source code.

    Parameters
    ----------
    mappings : list[MigrationMapping]
        The migration rules to match against.
    """

    def __init__(self, mappings: list[MigrationMapping]) -> None:
        self.mappings = mappings

        # Build lookup indices for fast matching
        # method_name → list of mappings  (e.g. 'old_connect' → [BM-HTTP-001])
        self._by_method: dict[str, list[MigrationMapping]] = {}
        for m in mappings:
            # old_api is e.g. "HTTPClient.old_connect"
            parts = m.old_api.rsplit(".", 1)
            method = parts[-1]
            self._by_method.setdefault(method, []).append(m)

    def detect(self, parse_result: ParseResult) -> list[DetectedAPI]:
        """Scan a ParseResult for deprecated API calls.

        Returns a list of ``DetectedAPI`` instances, one per
        deprecated call site found.
        """
        if not parse_result.success or parse_result.tree is None:
            return []

        var_map = _build_variable_class_map(parse_result)
        lib_map = _get_imported_libraries(parse_result)

        detections: list[DetectedAPI] = []

        for call in parse_result.calls:
            candidates = self._by_method.get(call.func_name, [])
            if not candidates:
                continue

            # Determine the class of the receiver (if any)
            inferred_class: Optional[str] = None
            if call.receiver:
                # receiver might be a variable name like 'client'
                inferred_class = var_map.get(call.receiver)
                if inferred_class is None:
                    # receiver might itself be a class name (e.g. HTTPClient.old_connect
                    # called as a static-style call — unlikely but handle it)
                    inferred_class = call.receiver

            for mapping in candidates:
                # Parse the expected class from old_api
                parts = mapping.old_api.rsplit(".", 1)
                if len(parts) == 2:
                    expected_class, expected_method = parts
                else:
                    expected_class = None
                    expected_method = parts[0]

                # ── Check 1: method name match (already satisfied) ──

                # ── Check 2: class/receiver match ──
                if expected_class and inferred_class:
                    if inferred_class != expected_class:
                        continue
                elif expected_class and not inferred_class:
                    # We have no receiver info — be conservative, skip
                    # unless there's no receiver at all (bare function call
                    # matching a method — unlikely for our patterns)
                    if call.receiver is not None:
                        # receiver exists but we couldn't resolve class
                        # Check if the library is imported
                        if expected_class not in lib_map:
                            continue
                        # Library is imported, receiver just isn't in var_map
                        # This could be a valid match (e.g. unresolved variable)
                        # — accept it but with lower confidence
                    else:
                        continue  # bare function, but mapping expects a class

                # ── Check 3: library import context ──
                lib_imported = False
                if expected_class and expected_class in lib_map:
                    if lib_map[expected_class] == mapping.library:
                        lib_imported = True
                # Also check if any import matches the mapping library
                if not lib_imported:
                    lib_imported = mapping.library in lib_map.values()

                if not lib_imported:
                    continue

                # ── Match confirmed ──
                arg_reprs = [a.value_repr for a in call.positional_args]
                kw_dict = {
                    a.name: a.value_repr
                    for a in call.keyword_args
                    if a.name is not None
                }

                detections.append(DetectedAPI(
                    api_name=mapping.old_api,
                    line=call.line,
                    col=call.col,
                    arguments=arg_reprs,
                    keyword_arguments=kw_dict,
                    node_type=call.node_type,
                    receiver_variable=call.receiver,
                    inferred_class=inferred_class if inferred_class != call.receiver else inferred_class,
                ))
                break  # one mapping per call site

        return detections


# ────────────────────────────────────────────────────────────
# Convenience function
# ────────────────────────────────────────────────────────────

def detect_deprecated_apis(
    source: str,
    mappings_path: Optional[Path] = None,
) -> tuple[ParseResult, list[DetectedAPI]]:
    """One-shot helper: parse *source* and detect deprecated APIs.

    Parameters
    ----------
    source : str
        Python source code.
    mappings_path : Path, optional
        Path to ``api_mappings.json``.  Defaults to the standard
        data directory location.

    Returns
    -------
    (ParseResult, list[DetectedAPI])
    """
    from app.parser.ast_parser import parse_source
    from app.config import DATA_DIR

    if mappings_path is None:
        mappings_path = DATA_DIR / "api_mappings.json"

    parse_result = parse_source(source)
    if not parse_result.success:
        return parse_result, []

    mappings = load_mappings(mappings_path)
    detector = APIDetector(mappings)
    detections = detector.detect(parse_result)

    return parse_result, detections
