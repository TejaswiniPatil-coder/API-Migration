"""
API Validator — checks migrated code against target API specifications.

Verifies argument counts, required parameters, and access types
(method vs property) match the target spec.
"""

from __future__ import annotations

from app.models.validation_models import ValidationResult, ValidatorType, ValidationLevel
from app.models.migration_models import MigrationPlan
from app.analyzer.semantic_analyzer import TargetAPISpec, load_target_specs
from app.parser.ast_parser import parse_source, CallInfo
import ast


def validate_api_compliance(
    migrated_source: str,
    plans: list[MigrationPlan],
    target_specs: dict[str, TargetAPISpec],
) -> list[ValidationResult]:
    """Check migrated code against target API specifications."""
    results: list[ValidationResult] = []
    pr = parse_source(migrated_source)
    if not pr.success:
        return results

    for plan in plans:
        spec = target_specs.get(plan.new_api)
        if spec is None:
            continue

        new_method = plan.new_api.rsplit(".", 1)[-1]

        if spec.access_type == "property":
            # Should NOT appear as a call
            for c in pr.calls:
                if c.func_name == new_method:
                    results.append(ValidationResult(
                        validator=ValidatorType.API,
                        passed=False,
                        message=f"'{plan.new_api}' should be a property, not a method call.",
                        level=ValidationLevel.ERROR,
                        line=c.line,
                    ))
                    break
            else:
                results.append(ValidationResult(
                    validator=ValidatorType.API,
                    passed=True,
                    message=f"'{plan.new_api}' correctly used as property.",
                    level=ValidationLevel.INFO,
                ))
        else:
            matching = [c for c in pr.calls if c.func_name == new_method]
            if matching:
                c = matching[0]
                required = sum(1 for p in spec.parameters if p.required)
                total_args = len(c.positional_args) + len(c.keyword_args)
                if total_args >= required:
                    results.append(ValidationResult(
                        validator=ValidatorType.API,
                        passed=True,
                        message=f"'{plan.new_api}' argument count OK ({total_args} >= {required} required).",
                        level=ValidationLevel.INFO,
                        line=c.line,
                    ))
                else:
                    results.append(ValidationResult(
                        validator=ValidatorType.API,
                        passed=False,
                        message=f"'{plan.new_api}' missing required arguments ({total_args} < {required}).",
                        level=ValidationLevel.ERROR,
                        line=c.line,
                    ))

    return results
