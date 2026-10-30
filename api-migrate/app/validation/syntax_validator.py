"""
Syntax Validator — checks that migrated code is syntactically valid Python.

Uses ``compile()`` for fast in-process syntax checking.
"""

from __future__ import annotations

from app.models.validation_models import (
    ValidationResult,
    ValidatorType,
    ValidationLevel,
)


def validate_syntax(source: str, filename: str = "<migrated>") -> ValidationResult:
    """Check that *source* is syntactically valid Python.

    Uses ``compile()`` which performs lexing + parsing without execution.
    """
    try:
        compile(source, filename, "exec")
        return ValidationResult(
            validator=ValidatorType.SYNTAX,
            passed=True,
            message="Syntax is valid.",
            level=ValidationLevel.INFO,
        )
    except SyntaxError as exc:
        return ValidationResult(
            validator=ValidatorType.SYNTAX,
            passed=False,
            message=f"SyntaxError: {exc.msg}",
            level=ValidationLevel.ERROR,
            line=exc.lineno,
            details=str(exc),
        )
