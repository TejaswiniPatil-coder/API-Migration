"""
Validation Pipeline — orchestrates all validators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.validation_models import ValidationResult, ValidationLevel
from app.models.migration_models import MigrationPlan
from app.analyzer.semantic_analyzer import TargetAPISpec
from app.validation.syntax_validator import validate_syntax
from app.validation.ast_validator import validate_ast
from app.validation.type_validator import validate_types
from app.validation.api_validator import validate_api_compliance
from app.validation.compiler_validator import validate_compilation


@dataclass
class ValidationReport:
    """Aggregated validation results from all validators."""
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.level == ValidationLevel.ERROR and not r.passed for r in self.results)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == ValidationLevel.ERROR and not r.passed]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == ValidationLevel.WARNING and not r.passed]

    def summary(self) -> str:
        e, w = len(self.errors), len(self.warnings)
        status = "PASS" if self.passed else "FAIL"
        return f"Validation {status}: {len(self.results)} checks, {e} error(s), {w} warning(s)"


def run_validation_pipeline(
    migrated_source: str,
    plans: list[MigrationPlan],
    target_specs: Optional[dict[str, TargetAPISpec]] = None,
) -> ValidationReport:
    """Run all validators and return an aggregated report."""
    report = ValidationReport()

    # 1. Syntax
    report.results.append(validate_syntax(migrated_source))

    # 2. Compilation
    report.results.append(validate_compilation(migrated_source))

    # 3. AST structure
    report.results.extend(validate_ast(migrated_source, plans))

    # 4. Type checks
    if target_specs:
        report.results.extend(validate_types(migrated_source, target_specs))

    # 5. API compliance
    if target_specs:
        report.results.extend(validate_api_compliance(migrated_source, plans, target_specs))

    return report
