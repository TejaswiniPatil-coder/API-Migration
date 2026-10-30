"""
Semantic Analyzer — lightweight pre-migration semantic checks.

Combines AST information, the symbol table, migration mappings, and
target API specifications to detect semantic issues *before* migration
is attempted.

Checks performed
----------------
1. **Argument count**: Does the call-site provide the right number of
   arguments for the *old* API signature?
2. **Argument type compatibility**: Where argument types can be inferred
   (literals, symbol table), are they compatible with the *target* API's
   expected types?
3. **Migration feasibility**: Can the migration mapping's argument
   transformations be applied given the actual call-site arguments?
4. **Incompatible usage warnings**: Patterns that may cause issues
   after migration (e.g. passing a variable whose type is unknown).

This module is deterministic and completely independent of the LLM.

Limitations (documented):
  - Type inference is limited to literals and constructor-assigned variables.
  - Cannot evaluate expressions (e.g. ``str(port)`` is typed as "Call",
    not "str").
  - Does not track types across function boundaries.
  - Does not handle *args / **kwargs.

Compiler-design concepts exercised:
  - Semantic analysis / type checking (lightweight)
  - Cross-referencing symbol table with specification
  - Pre-condition validation
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from app.models.api_models import DetectedAPI
from app.models.migration_models import MigrationMapping
from app.parser.ast_parser import ParseResult
from app.analyzer.symbol_analyzer import SymbolTable


# ────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────

class IssueSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class IssueKind(Enum):
    TYPE_MISMATCH = "type_mismatch"
    ARG_COUNT_MISMATCH = "arg_count_mismatch"
    UNKNOWN_TYPE = "unknown_type"
    INCOMPATIBLE_USAGE = "incompatible_usage"
    MIGRATION_FEASIBILITY = "migration_feasibility"


@dataclass
class SemanticIssue:
    """A single semantic issue detected during analysis."""

    kind: IssueKind
    severity: IssueSeverity
    message: str
    line: int = 0
    col: int = 0
    api_name: Optional[str] = None
    parameter: Optional[str] = None
    expected_type: Optional[str] = None
    actual_type: Optional[str] = None

    def __str__(self) -> str:
        loc = f"line {self.line}" if self.line else ""
        return f"[{self.severity.value.upper()}] {loc}: {self.message}"


@dataclass
class SemanticReport:
    """Aggregated semantic analysis results."""

    issues: list[SemanticIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)

    @property
    def errors(self) -> list[SemanticIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[SemanticIssue]:
        return [i for i in self.issues if i.severity == IssueSeverity.WARNING]

    def summary(self) -> str:
        e = len(self.errors)
        w = len(self.warnings)
        return f"Semantic analysis: {len(self.issues)} issue(s) — {e} error(s), {w} warning(s)"


# ────────────────────────────────────────────────────────────
# Target spec loader
# ────────────────────────────────────────────────────────────

@dataclass
class TargetParamSpec:
    name: str
    type_hint: str
    required: bool
    default: Optional[str]


@dataclass
class TargetAPISpec:
    api_name: str
    library: str
    parameters: list[TargetParamSpec] = field(default_factory=list)
    return_type: Optional[str] = None
    access_type: str = "method"   # "method" | "property"


def load_target_specs(path: Path) -> dict[str, TargetAPISpec]:
    """Load target API specifications and return a dict keyed by api_name."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    specs: dict[str, TargetAPISpec] = {}
    for entry in data.get("apis", []):
        params = [
            TargetParamSpec(
                name=p["name"],
                type_hint=p.get("type", "Any"),
                required=p.get("required", True),
                default=p.get("default"),
            )
            for p in entry.get("parameters", [])
        ]
        spec = TargetAPISpec(
            api_name=entry["api_name"],
            library=entry.get("library", ""),
            parameters=params,
            return_type=entry.get("return_type"),
            access_type=entry.get("access_type", "method"),
        )
        specs[spec.api_name] = spec
    return specs


# ────────────────────────────────────────────────────────────
# Type inference helpers
# ────────────────────────────────────────────────────────────

def _infer_arg_type_from_repr(value_repr: str, symbol_table: SymbolTable) -> Optional[str]:
    """Best-effort type inference from the string repr of an argument value.

    Handles:
      - String literals: ``'hello'`` or ``\"hello\"`` → "str"
      - Integer literals: ``42`` → "int"
      - Float literals: ``3.14`` → "float"
      - Boolean / None: ``True``, ``False``, ``None``
      - Dict literals: ``{...}`` → "dict"
      - List literals: ``[...]`` → "list"
      - Variable names: look up in symbol table
    """
    s = value_repr.strip()

    # String literal
    if (s.startswith("'") and s.endswith("'")) or \
       (s.startswith('"') and s.endswith('"')):
        return "str"

    # Boolean (must check before int since bool is a subclass)
    if s in ("True", "False"):
        return "bool"

    # None
    if s == "None":
        return "NoneType"

    # Integer
    try:
        int(s)
        return "int"
    except ValueError:
        pass

    # Float
    try:
        float(s)
        return "float"
    except ValueError:
        pass

    # Dict literal
    if s.startswith("{"):
        return "dict"

    # List literal
    if s.startswith("["):
        return "list"

    # Tuple literal
    if s.startswith("("):
        return "tuple"

    # Variable name → symbol table lookup
    if s.isidentifier():
        resolved = symbol_table.resolve_type(s)
        if resolved:
            return resolved

    # Function call → we can't determine type reliably
    # e.g. int("8080") — we know the *call* but not the return type
    # without full type inference
    return None


def _types_compatible(expected: str, actual: str) -> bool:
    """Check whether *actual* is compatible with *expected*.

    Intentionally simple — handles the common cases in our
    benchmark APIs.
    """
    if expected == actual:
        return True

    # int/float are numerically compatible
    if expected in ("int", "float") and actual in ("int", "float"):
        return True

    # "Any" is always compatible
    if expected == "Any" or actual == "Any":
        return True

    return False


# ────────────────────────────────────────────────────────────
# Semantic Analyzer
# ────────────────────────────────────────────────────────────

class SemanticAnalyzer:
    """Run lightweight semantic checks on detected deprecated API calls.

    Parameters
    ----------
    mappings : list[MigrationMapping]
        Migration rules (from api_mappings.json).
    target_specs : dict[str, TargetAPISpec]
        Target API specifications (from target_api_specs.json).
    """

    def __init__(
        self,
        mappings: list[MigrationMapping],
        target_specs: dict[str, TargetAPISpec],
    ) -> None:
        self.mappings = {m.old_api: m for m in mappings}
        self.target_specs = target_specs

    def analyze(
        self,
        detections: list[DetectedAPI],
        symbol_table: SymbolTable,
        parse_result: Optional[ParseResult] = None,
    ) -> SemanticReport:
        """Analyse all detected deprecated calls and return a report.

        Parameters
        ----------
        detections : list[DetectedAPI]
            Output from the API detector.
        symbol_table : SymbolTable
            Output from the symbol analyzer.
        parse_result : ParseResult, optional
            The original parse result (used for additional context).
        """
        report = SemanticReport()

        for det in detections:
            mapping = self.mappings.get(det.api_name)
            if mapping is None:
                continue

            target_spec = self.target_specs.get(mapping.new_api)

            # ── Check 1: argument count vs old signature ──
            self._check_arg_count_old(det, mapping, report)

            # ── Check 2: argument types vs target signature ──
            if target_spec:
                self._check_arg_types_target(det, mapping, target_spec, symbol_table, report)

            # ── Check 3: migration feasibility ──
            self._check_migration_feasibility(det, mapping, symbol_table, report)

        return report

    # ── Individual checks ────────────────────────────────

    def _check_arg_count_old(
        self,
        det: DetectedAPI,
        mapping: MigrationMapping,
        report: SemanticReport,
    ) -> None:
        """Check that the call-site arg count matches the old signature."""
        expected = len(mapping.old_signature)
        actual = len(det.arguments) + len(det.keyword_arguments)

        if expected > 0 and actual != expected:
            report.issues.append(SemanticIssue(
                kind=IssueKind.ARG_COUNT_MISMATCH,
                severity=IssueSeverity.WARNING,
                message=(
                    f"{det.api_name}: expected {expected} argument(s) "
                    f"per old signature, got {actual}"
                ),
                line=det.line,
                api_name=det.api_name,
            ))

    def _check_arg_types_target(
        self,
        det: DetectedAPI,
        mapping: MigrationMapping,
        target_spec: TargetAPISpec,
        symbol_table: SymbolTable,
        report: SemanticReport,
    ) -> None:
        """Check that call-site arg types are compatible with the
        *target* API after applying known transformations."""

        transformations = mapping.argument_transformations

        for txn in transformations:
            old_pos = txn.get("old_position")
            new_name = txn.get("new_name")
            type_conv = txn.get("type_conversion")

            if old_pos is None or old_pos >= len(det.arguments):
                continue

            arg_repr = det.arguments[old_pos]
            actual_type = _infer_arg_type_from_repr(arg_repr, symbol_table)

            # Find the target parameter spec
            target_param = None
            if new_name:
                target_param = next(
                    (p for p in target_spec.parameters if p.name == new_name),
                    None,
                )

            if target_param is None:
                continue

            expected_type = target_param.type_hint

            # If there's a type conversion in the mapping, the pipeline
            # will handle it — but we note if the SOURCE type already
            # matches (no conversion needed) or differs (conversion required).
            if type_conv and actual_type:
                # e.g. type_conv = "str_to_int", actual = "str", expected = "int"
                if actual_type == expected_type:
                    # Already correct — conversion unnecessary
                    report.issues.append(SemanticIssue(
                        kind=IssueKind.TYPE_MISMATCH,
                        severity=IssueSeverity.INFO,
                        message=(
                            f"{det.api_name}: parameter '{new_name}' already "
                            f"has type {expected_type} — conversion may be unnecessary"
                        ),
                        line=det.line,
                        api_name=det.api_name,
                        parameter=new_name,
                        expected_type=expected_type,
                        actual_type=actual_type,
                    ))
                elif not _types_compatible(expected_type, actual_type):
                    # Type mismatch — conversion will be needed
                    report.issues.append(SemanticIssue(
                        kind=IssueKind.TYPE_MISMATCH,
                        severity=IssueSeverity.WARNING,
                        message=(
                            f"{det.api_name}: parameter '{new_name}' — "
                            f"source type is {actual_type}, target expects "
                            f"{expected_type} (conversion '{type_conv}' will be applied)"
                        ),
                        line=det.line,
                        api_name=det.api_name,
                        parameter=new_name,
                        expected_type=expected_type,
                        actual_type=actual_type,
                    ))

            elif actual_type and not type_conv:
                # No conversion specified — types must already match
                if not _types_compatible(expected_type, actual_type):
                    report.issues.append(SemanticIssue(
                        kind=IssueKind.TYPE_MISMATCH,
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"{det.api_name}: parameter '{new_name}' expects "
                            f"type {expected_type}, but got {actual_type}"
                        ),
                        line=det.line,
                        api_name=det.api_name,
                        parameter=new_name,
                        expected_type=expected_type,
                        actual_type=actual_type,
                    ))

            elif actual_type is None and not type_conv:
                # Cannot determine type — warn
                report.issues.append(SemanticIssue(
                    kind=IssueKind.UNKNOWN_TYPE,
                    severity=IssueSeverity.INFO,
                    message=(
                        f"{det.api_name}: cannot infer type for parameter "
                        f"'{new_name}' (value: {arg_repr}) — manual review recommended"
                    ),
                    line=det.line,
                    api_name=det.api_name,
                    parameter=new_name,
                    expected_type=expected_type,
                ))

    def _check_migration_feasibility(
        self,
        det: DetectedAPI,
        mapping: MigrationMapping,
        symbol_table: SymbolTable,
        report: SemanticReport,
    ) -> None:
        """Check if the migration transformation can be applied."""

        if mapping.transformation_type == "arg_merge":
            # Arg merge requires all source args to be present
            merge_txns = [
                t for t in mapping.argument_transformations
                if t.get("type_conversion") == "merge_to_dict"
            ]
            if len(det.arguments) < len(merge_txns):
                report.issues.append(SemanticIssue(
                    kind=IssueKind.MIGRATION_FEASIBILITY,
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"{det.api_name}: arg_merge requires "
                        f"{len(merge_txns)} arguments, but only "
                        f"{len(det.arguments)} provided"
                    ),
                    line=det.line,
                    api_name=det.api_name,
                ))

        if mapping.transformation_type == "method_to_property":
            if len(det.arguments) > 0 or len(det.keyword_arguments) > 0:
                report.issues.append(SemanticIssue(
                    kind=IssueKind.INCOMPATIBLE_USAGE,
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"{det.api_name}: method_to_property migration "
                        f"but call has arguments — properties take no arguments"
                    ),
                    line=det.line,
                    api_name=det.api_name,
                ))
