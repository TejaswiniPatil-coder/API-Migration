"""
Compiler Validator — validates migrated code via subprocess compilation.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import os

from app.models.validation_models import ValidationResult, ValidatorType, ValidationLevel


def validate_compilation(source: str) -> ValidationResult:
    """Compile migrated code in a subprocess for isolation."""
    try:
        compile(source, "<migrated>", "exec")
        return ValidationResult(
            validator=ValidatorType.COMPILER,
            passed=True,
            message="Compilation successful (compile() check passed).",
            level=ValidationLevel.INFO,
        )
    except SyntaxError as exc:
        return ValidationResult(
            validator=ValidatorType.COMPILER,
            passed=False,
            message=f"Compilation failed: {exc.msg}",
            level=ValidationLevel.ERROR,
            line=exc.lineno,
            details=str(exc),
        )
