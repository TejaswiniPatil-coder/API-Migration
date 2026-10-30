"""
Symbol Analyzer — lightweight symbol table construction from AST.

Walks a parsed AST to build a symbol table that tracks:
  - variable → class/type associations  (e.g. ``client → HTTPClient``)
  - import bindings                      (e.g. ``HTTPClient → benchmark_http``)
  - function/class definitions           (scope awareness)
  - function parameter names             (within function scope)

This is a *lightweight* analysis — it does NOT attempt complete Python
type inference.  Documented limitations:

  - Cannot track types through function return values.
  - Cannot resolve types across module boundaries.
  - Cannot handle reassignment to a different type.
  - Cannot propagate types through container operations (list/dict).
  - Only analyses a single module at a time.

Compiler-design concepts exercised:
  - Symbol table construction
  - Scope management (module / function / class)
  - Name resolution
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.parser.ast_parser import ParseResult


# ────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────

class SymbolKind(Enum):
    VARIABLE = "variable"
    CLASS = "class"
    FUNCTION = "function"
    PARAMETER = "parameter"
    IMPORT = "import"


@dataclass
class SymbolInfo:
    """One entry in the symbol table."""

    name: str
    kind: SymbolKind
    inferred_type: Optional[str] = None   # e.g. "HTTPClient"
    source_module: Optional[str] = None   # e.g. "benchmark_http"
    scope: str = "module"                 # "module" | "function:<name>" | "class:<name>"
    line: int = 0
    col: int = 0

    @property
    def qualified_name(self) -> str:
        """Return scope-qualified name for disambiguation."""
        if self.scope == "module":
            return self.name
        return f"{self.scope}.{self.name}"


@dataclass
class SymbolTable:
    """Collected symbol information for one source module."""

    symbols: dict[str, list[SymbolInfo]] = field(default_factory=dict)

    # ── Mutators ─────────────────────────────────────────

    def add(self, info: SymbolInfo) -> None:
        self.symbols.setdefault(info.name, []).append(info)

    # ── Queries ──────────────────────────────────────────

    def lookup(self, name: str, scope: Optional[str] = None) -> Optional[SymbolInfo]:
        """Return the *most recent* SymbolInfo for *name*.

        If *scope* is given, prefer entries matching that scope,
        otherwise fall back to module scope.
        """
        entries = self.symbols.get(name, [])
        if not entries:
            return None

        if scope:
            scoped = [e for e in entries if e.scope == scope]
            if scoped:
                return scoped[-1]

        # Fallback: module scope or most recent
        module = [e for e in entries if e.scope == "module"]
        return module[-1] if module else entries[-1]

    def resolve_type(self, name: str, scope: Optional[str] = None) -> Optional[str]:
        """Return the inferred type string for *name*, or None."""
        info = self.lookup(name, scope)
        return info.inferred_type if info else None

    def resolve_method_class(self, receiver: str, method: str) -> Optional[str]:
        """Given ``receiver.method()``, return the class name of *receiver*.

        Example: ``client.old_connect()`` with ``client → HTTPClient``
        returns ``"HTTPClient"``.
        """
        return self.resolve_type(receiver)

    def all_symbols(self) -> list[SymbolInfo]:
        """Flat list of all symbol entries."""
        return [s for entries in self.symbols.values() for s in entries]

    def variables_of_type(self, type_name: str) -> list[SymbolInfo]:
        """Return all variables whose inferred_type matches *type_name*."""
        return [
            s for s in self.all_symbols()
            if s.kind == SymbolKind.VARIABLE and s.inferred_type == type_name
        ]


# ────────────────────────────────────────────────────────────
# AST visitor that builds the symbol table
# ────────────────────────────────────────────────────────────

class _SymbolVisitor(ast.NodeVisitor):
    """Walk the AST and populate a SymbolTable."""

    def __init__(self) -> None:
        self.table = SymbolTable()
        self._scope_stack: list[str] = ["module"]
        # Import map: imported_name → source_module
        self._import_map: dict[str, str] = {}

    @property
    def _scope(self) -> str:
        return self._scope_stack[-1]

    def _push_scope(self, name: str) -> None:
        self._scope_stack.append(name)

    def _pop_scope(self) -> None:
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()

    # ── Imports ──────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name
            self._import_map[bound_name] = alias.name
            self.table.add(SymbolInfo(
                name=bound_name,
                kind=SymbolKind.IMPORT,
                inferred_type=alias.name,
                source_module=alias.name,
                scope=self._scope,
                line=node.lineno,
                col=node.col_offset,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            bound_name = alias.asname or alias.name
            self._import_map[bound_name] = module
            self.table.add(SymbolInfo(
                name=bound_name,
                kind=SymbolKind.IMPORT,
                inferred_type=alias.name,
                source_module=module,
                scope=self._scope,
                line=node.lineno,
                col=node.col_offset,
            ))
        self.generic_visit(node)

    # ── Assignments ──────────────────────────────────────

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                inferred = self._infer_type(node.value)
                self.table.add(SymbolInfo(
                    name=target.id,
                    kind=SymbolKind.VARIABLE,
                    inferred_type=inferred,
                    source_module=self._import_map.get(inferred),
                    scope=self._scope,
                    line=node.lineno,
                    col=node.col_offset,
                ))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            # Use the annotation as the type if we can unparse it
            ann_type = None
            try:
                ann_type = ast.unparse(node.annotation)
            except Exception:
                pass

            inferred = ann_type
            if node.value is not None:
                val_type = self._infer_type(node.value)
                if val_type:
                    inferred = val_type

            self.table.add(SymbolInfo(
                name=node.target.id,
                kind=SymbolKind.VARIABLE,
                inferred_type=inferred,
                scope=self._scope,
                line=node.lineno,
                col=node.col_offset,
            ))
        self.generic_visit(node)

    # ── Function / Class definitions ─────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.table.add(SymbolInfo(
            name=node.name,
            kind=SymbolKind.FUNCTION,
            inferred_type="function",
            scope=self._scope,
            line=node.lineno,
            col=node.col_offset,
        ))

        self._push_scope(f"function:{node.name}")

        # Register parameters
        for arg in node.args.args:
            ann_type = None
            if arg.annotation:
                try:
                    ann_type = ast.unparse(arg.annotation)
                except Exception:
                    pass
            self.table.add(SymbolInfo(
                name=arg.arg,
                kind=SymbolKind.PARAMETER,
                inferred_type=ann_type,
                scope=self._scope,
                line=arg.lineno if hasattr(arg, "lineno") else node.lineno,
                col=arg.col_offset if hasattr(arg, "col_offset") else 0,
            ))

        # Visit the body
        for child in node.body:
            self.visit(child)

        self._pop_scope()

    visit_AsyncFunctionDef = visit_FunctionDef  # same logic

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.table.add(SymbolInfo(
            name=node.name,
            kind=SymbolKind.CLASS,
            inferred_type=node.name,
            scope=self._scope,
            line=node.lineno,
            col=node.col_offset,
        ))

        self._push_scope(f"class:{node.name}")
        for child in node.body:
            self.visit(child)
        self._pop_scope()

    # ── Type inference helpers ───────────────────────────

    def _infer_type(self, node: ast.expr) -> Optional[str]:
        """Best-effort type inference for a value expression.

        Handles:
          - Constructor calls:  HTTPClient()  → "HTTPClient"
          - Literal constants:  42 → "int",  "s" → "str"
          - Name references:    (delegate to symbol table)
        """
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id  # e.g. HTTPClient
            if isinstance(node.func, ast.Attribute):
                try:
                    return ast.unparse(node.func)
                except Exception:
                    return None

        if isinstance(node, ast.Constant):
            return type(node.value).__name__  # "int", "str", "float", etc.

        if isinstance(node, ast.Name):
            return self.table.resolve_type(node.id, self._scope)

        if isinstance(node, ast.List):
            return "list"
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, ast.Set):
            return "set"
        if isinstance(node, ast.Tuple):
            return "tuple"

        # Unresolvable
        return None


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────

def build_symbol_table(parse_result: ParseResult) -> SymbolTable:
    """Analyse a parsed source module and return a SymbolTable.

    Parameters
    ----------
    parse_result : ParseResult
        Must have ``success=True`` and a valid ``tree``.

    Returns
    -------
    SymbolTable
    """
    if not parse_result.success or parse_result.tree is None:
        return SymbolTable()

    visitor = _SymbolVisitor()
    visitor.visit(parse_result.tree)
    return visitor.table
