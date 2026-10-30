"""
Validation-related data models.

Each validator produces a ValidationResult.  The pipeline
aggregates them into a ValidationReport.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ValidationLevel(Enum):
    """Severity of a validation finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidatorType(Enum):
    """Identifies which validator produced a result."""

    SYNTAX = "syntax"
    AST = "ast"
    TYPE = "type"
    API = "api"
    COMPILER = "compiler"


@dataclass
class ValidationResult:
    """Output of a single validation check."""

    validator: ValidatorType
    passed: bool
    message: str
    level: ValidationLevel = ValidationLevel.INFO
    line: Optional[int] = None
    details: Optional[str] = None


@dataclass
class ValidationReport:
    """Aggregated results from all validators for one migration."""

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Overall pass: no ERROR-level failures."""
        return all(
            r.passed or r.level != ValidationLevel.ERROR
            for r in self.results
        )

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if not r.passed and r.level == ValidationLevel.ERROR]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if not r.passed and r.level == ValidationLevel.WARNING]

    def summary(self) -> str:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        return (
            f"Validation: {passed}/{total} checks passed | "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s) | "
            f"Overall: {'PASS' if self.passed else 'FAIL'}"
        )
