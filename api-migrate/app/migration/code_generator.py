"""
Code Generator — produces source code from transformed ASTs.

Uses ``ast.unparse()`` to regenerate Python source from the
transformed AST, and ``difflib`` to produce before/after diffs.

Compiler-design concepts:
  - Code generation (AST → source)
  - Source preservation and comparison
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass


@dataclass
class GeneratedCode:
    """Result of code generation."""

    original_source: str
    migrated_source: str
    diff: str

    @property
    def has_changes(self) -> bool:
        return self.original_source != self.migrated_source


def unparse_tree(tree: ast.Module) -> str:
    """Convert an AST back to Python source code.

    Uses ``ast.unparse()`` (Python 3.9+).
    """
    return ast.unparse(tree)


def generate_diff(
    original: str,
    migrated: str,
    filename: str = "source.py",
) -> str:
    """Produce a unified diff between original and migrated source.

    Parameters
    ----------
    original : str
        Original source code.
    migrated : str
        Migrated source code.
    filename : str
        Filename shown in diff headers.

    Returns
    -------
    str
        Unified diff string, or empty string if no changes.
    """
    original_lines = original.splitlines(keepends=True)
    migrated_lines = migrated.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        original_lines,
        migrated_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )

    return "".join(diff_lines)


def generate_code(
    original_source: str,
    transformed_tree: ast.Module,
    filename: str = "source.py",
) -> GeneratedCode:
    """Generate migrated source and diff from a transformed AST.

    Parameters
    ----------
    original_source : str
        The original Python source code (for diff).
    transformed_tree : ast.Module
        The AST after migration transformations.
    filename : str
        Filename for diff headers.

    Returns
    -------
    GeneratedCode
    """
    migrated = unparse_tree(transformed_tree)
    diff = generate_diff(original_source, migrated, filename)

    return GeneratedCode(
        original_source=original_source,
        migrated_source=migrated,
        diff=diff,
    )
