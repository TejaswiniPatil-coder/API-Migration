"""
Type Validator — lightweight post-migration type compatibility checks.

Parses the migrated code, extracts call arguments, and verifies
type compatibility against target API specifications.
"""

from __future__ import annotations

import ast
from typing import Optional

from app.models.validation_models import (
    ValidationResult,
    ValidatorType,
    ValidationLevel,
)
from app.analyzer.semantic_analyzer import (
    TargetAPISpec,
    _infer_arg_type_from_repr,
    _types_compatible,
)
from app.analyzer.symbol_analyzer import SymbolTable, build_symbol_table
from app.parser.ast_parser import parse_source


def validate_types(
    migrated_source: str,
    target_specs: dict[str, TargetAPISpec],
) -> list[ValidationResult]:
    """Check argument types in migrated code against target API specs.

    Parameters
    ----------
    migrated_source : str
        The migrated Python source code.
    target_specs : dict[str, TargetAPISpec]
        Target API specifications keyed by api_name.

    Returns
    -------
    list[ValidationResult]
    """
    results: list[ValidationResult] = []

    pr = parse_source(migrated_source)
    if not pr.success:
        return results  # Can't check types if we can't parse

    st = build_symbol_table(pr)

    for call in pr.calls:
        # Match this call to a target spec
        spec = _find_matching_spec(call.func_name, target_specs)
        if spec is None:
            continue

        # Check each positional argument
        required_params = [p for p in spec.parameters if p.required]

        for i, arg in enumerate(call.positional_args):
            if i >= len(spec.parameters):
                break

            param = spec.parameters[i]
            actual_type = _infer_arg_type_from_repr(arg.value_repr, st)

            if actual_type is None:
                continue  # Can't check — skip

            if not _types_compatible(param.type_hint, actual_type):
                results.append(ValidationResult(
                    validator=ValidatorType.TYPE,
                    passed=False,
                    message=(
                        f"Type mismatch: parameter '{param.name}' of "
                        f"'{spec.api_name}' expects {param.type_hint}, "
                        f"got {actual_type}"
                    ),
                    level=ValidationLevel.ERROR,
                    line=call.line,
                ))
            else:
                results.append(ValidationResult(
                    validator=ValidatorType.TYPE,
                    passed=True,
                    message=(
                        f"Type OK: parameter '{param.name}' of "
                        f"'{spec.api_name}' — {actual_type} matches {param.type_hint}"
                    ),
                    level=ValidationLevel.INFO,
                    line=call.line,
                ))

    if not results:
        results.append(ValidationResult(
            validator=ValidatorType.TYPE,
            passed=True,
            message="No type issues detected (limited check).",
            level=ValidationLevel.INFO,
        ))

    return results


def _find_matching_spec(
    method_name: str,
    specs: dict[str, TargetAPISpec],
) -> Optional[TargetAPISpec]:
    """Find a target spec by method name."""
    for spec in specs.values():
        api_method = spec.api_name.rsplit(".", 1)[-1]
        if api_method == method_name:
            return spec
    return None
