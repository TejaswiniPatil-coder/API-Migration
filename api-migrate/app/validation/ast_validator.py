"""
AST Validator — verifies structural integrity of migrated code.

Checks:
  1. The migrated source can be parsed to a valid AST.
  2. Expected new API calls are present in the AST.
  3. Deprecated old API calls are absent from the AST.
"""

from __future__ import annotations

import ast
from typing import Optional

from app.models.validation_models import (
    ValidationResult,
    ValidatorType,
    ValidationLevel,
)
from app.models.migration_models import MigrationPlan
from app.parser.ast_parser import parse_source


def validate_ast(
    migrated_source: str,
    plans: list[MigrationPlan],
) -> list[ValidationResult]:
    """Validate the AST structure of migrated code.

    Parameters
    ----------
    migrated_source : str
        The migrated Python source code.
    plans : list[MigrationPlan]
        The migration plans that were applied.

    Returns
    -------
    list[ValidationResult]
    """
    results: list[ValidationResult] = []

    # ── Check 1: AST can be regenerated ──
    pr = parse_source(migrated_source)
    if not pr.success:
        results.append(ValidationResult(
            validator=ValidatorType.AST,
            passed=False,
            message="Migrated code cannot be parsed to a valid AST.",
            level=ValidationLevel.ERROR,
            details=str(pr.errors),
        ))
        return results

    results.append(ValidationResult(
        validator=ValidatorType.AST,
        passed=True,
        message="AST successfully regenerated from migrated code.",
        level=ValidationLevel.INFO,
    ))

    # Collect all call names in the migrated AST
    call_names: set[str] = set()
    for node in ast.walk(pr.tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                call_names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                call_names.add(node.func.id)
        elif isinstance(node, ast.Attribute):
            call_names.add(node.attr)

    for plan in plans:
        old_method = plan.old_api.rsplit(".", 1)[-1]
        new_method = plan.new_api.rsplit(".", 1)[-1]

        # ── Check 2: new API present ──
        if plan.transformation_type == "method_to_property":
            # For property access, check attribute names
            if new_method in call_names:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=True,
                    message=f"New API '{plan.new_api}' (property) found in migrated AST.",
                    level=ValidationLevel.INFO,
                ))
            else:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=False,
                    message=f"New API '{plan.new_api}' (property) NOT found in migrated AST.",
                    level=ValidationLevel.WARNING,
                ))
        else:
            if new_method in call_names:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=True,
                    message=f"New API '{plan.new_api}' found in migrated AST.",
                    level=ValidationLevel.INFO,
                ))
            else:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=False,
                    message=f"New API '{plan.new_api}' NOT found in migrated AST.",
                    level=ValidationLevel.WARNING,
                ))

        # ── Check 3: old API absent ──
        # Only check call-based methods (not property → it's already an attribute)
        if plan.transformation_type != "method_to_property":
            # Check if old method name appears as a Call's func
            old_still_present = False
            for node in ast.walk(pr.tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute) and node.func.attr == old_method:
                        old_still_present = True
                        break
            if old_still_present:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=False,
                    message=f"Deprecated API '{plan.old_api}' still present in migrated AST.",
                    level=ValidationLevel.ERROR,
                ))
            else:
                results.append(ValidationResult(
                    validator=ValidatorType.AST,
                    passed=True,
                    message=f"Deprecated API '{plan.old_api}' correctly removed from AST.",
                    level=ValidationLevel.INFO,
                ))

    return results
