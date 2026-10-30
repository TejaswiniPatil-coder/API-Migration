"""
AST Parser — core parsing and extraction layer.

Uses Python's built-in ``ast`` module to parse source code and
extract structured information about function calls, method calls,
imports, attributes, and arguments.

This module is intentionally independent of the migration logic
and LLM layer so that it can serve as a reusable compiler front-end.

Compiler-design concepts exercised here:
  - Lexical / syntax analysis  (ast.parse)
  - Abstract Syntax Tree construction
  - Tree traversal (ast.NodeVisitor)
  - Error reporting with source locations
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional, Any


# ────────────────────────────────────────────────────────────
# Data models returned by the parser
# ────────────────────────────────────────────────────────────

@dataclass
class ParseError:
    """A syntax or parse error detected in the source."""

    message: str
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None

    def __str__(self) -> str:
        loc = f"line {self.line}" if self.line else "unknown location"
        if self.col is not None:
            loc += f", col {self.col}"
        return f"ParseError({loc}): {self.message}"


@dataclass
class ImportInfo:
    """An import statement found in the source.

    For ``import foo``          → module='foo',  names=['foo'],  alias=None
    For ``from foo import bar`` → module='foo',  names=['bar'],  alias=None
    For ``import foo as f``     → module='foo',  names=['foo'],  alias='f'
    """

    module: Optional[str]
    names: list[str] = field(default_factory=list)
    aliases: dict[str, Optional[str]] = field(default_factory=dict)
    line: int = 0
    col: int = 0
    node_type: str = "Import"  # "Import" | "ImportFrom"


@dataclass
class ArgumentInfo:
    """A single argument in a function/method call."""

    value_repr: str          # ast.dump or unparse of the value node
    name: Optional[str] = None  # keyword name (None for positional args)
    line: Optional[int] = None
    col: Optional[int] = None

    @property
    def is_keyword(self) -> bool:
        return self.name is not None


@dataclass
class CallInfo:
    """A function or method call found in the source.

    Examples::

        func(a, b)            → func_name='func',   receiver=None
        obj.method(a)         → func_name='method',  receiver='obj'
        mod.Cls.method(a)     → func_name='method',  receiver='mod.Cls'
        Cls()                 → func_name='Cls',     receiver=None
    """

    func_name: str
    receiver: Optional[str] = None      # dotted receiver, e.g. 'obj' or 'mod.Cls'
    positional_args: list[ArgumentInfo] = field(default_factory=list)
    keyword_args: list[ArgumentInfo] = field(default_factory=list)
    line: int = 0
    col: int = 0
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    node_type: str = "Call"

    @property
    def full_name(self) -> str:
        """Return 'receiver.func_name' or just 'func_name'."""
        if self.receiver:
            return f"{self.receiver}.{self.func_name}"
        return self.func_name

    @property
    def all_args(self) -> list[ArgumentInfo]:
        return self.positional_args + self.keyword_args


@dataclass
class AttributeInfo:
    """An attribute access (not a call) found in the source.

    Example::

        obj.attr   → name='attr', receiver='obj'
    """

    name: str
    receiver: Optional[str] = None
    line: int = 0
    col: int = 0

    @property
    def full_name(self) -> str:
        if self.receiver:
            return f"{self.receiver}.{self.name}"
        return self.name


@dataclass
class ParseResult:
    """Complete result of parsing a piece of source code."""

    source: str
    tree: Optional[ast.Module] = None
    success: bool = False
    errors: list[ParseError] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)
    attributes: list[AttributeInfo] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ────────────────────────────────────────────────────────────
# AST node helpers
# ────────────────────────────────────────────────────────────

def _node_repr(node: ast.AST) -> str:
    """Best-effort human-readable repr of an AST value node."""
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node)


def _resolve_receiver(node: ast.AST) -> tuple[Optional[str], str]:
    """Walk a chain of ``ast.Attribute`` nodes to extract
    (dotted_receiver, final_attr).

    For ``a.b.c``  → ('a.b', 'c')
    For ``a.b``    → ('a',   'b')
    For ``Name:a`` → (None,  'a')
    """
    if isinstance(node, ast.Attribute):
        parent_receiver, parent_name = _resolve_receiver(node.value)
        if parent_receiver:
            return f"{parent_receiver}.{parent_name}", node.attr
        return parent_name, node.attr
    if isinstance(node, ast.Name):
        return None, node.id
    # Fallback for complex expressions (e.g. function calls as receiver)
    return None, _node_repr(node)


# ────────────────────────────────────────────────────────────
# Visitors
# ────────────────────────────────────────────────────────────

class _ImportVisitor(ast.NodeVisitor):
    """Collect all import statements."""

    def __init__(self) -> None:
        self.imports: list[ImportInfo] = []

    def visit_Import(self, node: ast.Import) -> None:
        names = [alias.name for alias in node.names]
        aliases = {alias.name: alias.asname for alias in node.names}
        self.imports.append(ImportInfo(
            module=None,
            names=names,
            aliases=aliases,
            line=node.lineno,
            col=node.col_offset,
            node_type="Import",
        ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [alias.name for alias in node.names]
        aliases = {alias.name: alias.asname for alias in node.names}
        self.imports.append(ImportInfo(
            module=node.module,
            names=names,
            aliases=aliases,
            line=node.lineno,
            col=node.col_offset,
            node_type="ImportFrom",
        ))
        self.generic_visit(node)


class _CallVisitor(ast.NodeVisitor):
    """Collect all function / method calls."""

    def __init__(self) -> None:
        self.calls: list[CallInfo] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name: str
        receiver: Optional[str] = None

        if isinstance(node.func, ast.Attribute):
            receiver, func_name = _resolve_receiver(node.func.value)
            if receiver:
                receiver_str = f"{receiver}.{func_name}"
                func_name = node.func.attr
                # Actually _resolve_receiver on node.func already gives us
                # the right split — let me redo cleanly:
                pass
            # Cleaner approach: resolve the whole node.func
            receiver, func_name = _resolve_receiver(node.func)
            # receiver is the object, func_name is the method
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id
        else:
            func_name = _node_repr(node.func)

        # Extract positional arguments
        pos_args: list[ArgumentInfo] = []
        for arg_node in node.args:
            pos_args.append(ArgumentInfo(
                value_repr=_node_repr(arg_node),
                name=None,
                line=getattr(arg_node, "lineno", None),
                col=getattr(arg_node, "col_offset", None),
            ))

        # Extract keyword arguments
        kw_args: list[ArgumentInfo] = []
        for kw_node in node.keywords:
            kw_args.append(ArgumentInfo(
                value_repr=_node_repr(kw_node.value),
                name=kw_node.arg,  # None for **kwargs
                line=getattr(kw_node.value, "lineno", None),
                col=getattr(kw_node.value, "col_offset", None),
            ))

        self.calls.append(CallInfo(
            func_name=func_name,
            receiver=receiver,
            positional_args=pos_args,
            keyword_args=kw_args,
            line=node.lineno,
            col=node.col_offset,
            end_line=getattr(node, "end_lineno", None),
            end_col=getattr(node, "end_col_offset", None),
        ))

        # Continue visiting child nodes (nested calls, etc.)
        self.generic_visit(node)


class _AttributeVisitor(ast.NodeVisitor):
    """Collect attribute accesses that are NOT part of a Call.

    We skip attributes that are the ``.func`` of a Call node
    because those are already captured by _CallVisitor.
    """

    def __init__(self) -> None:
        self.attributes: list[AttributeInfo] = []
        self._call_func_nodes: set[int] = set()

    def visit_Call(self, node: ast.Call) -> None:
        # Mark the func node so we skip it in visit_Attribute
        self._call_func_nodes.add(id(node.func))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if id(node) not in self._call_func_nodes:
            receiver, _ = _resolve_receiver(node.value)
            if receiver:
                recv_str = f"{receiver}.{_}"
            else:
                recv_str = _
            # Actually let me use _resolve_receiver on the node itself
            parent_recv, attr_name = _resolve_receiver(node)
            self.attributes.append(AttributeInfo(
                name=attr_name,
                receiver=parent_recv,
                line=node.lineno,
                col=node.col_offset,
            ))
        self.generic_visit(node)


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────

def parse_source(source: str, filename: str = "<string>") -> ParseResult:
    """Parse Python source code and extract structured information.

    Parameters
    ----------
    source : str
        Python source code to parse.
    filename : str
        Filename used in error messages (default ``"<string>"``).

    Returns
    -------
    ParseResult
        Contains the AST (if parsing succeeded), any errors,
        and extracted imports / calls / attributes.
    """
    result = ParseResult(source=source)

    # ── Step 1: Parse ────────────────────────────────────
    try:
        tree = ast.parse(source, filename=filename, type_comments=True)
    except SyntaxError as exc:
        result.errors.append(ParseError(
            message=str(exc.msg) if exc.msg else str(exc),
            line=exc.lineno,
            col=exc.offset,
            end_line=getattr(exc, "end_lineno", None),
            end_col=getattr(exc, "end_offset", None),
        ))
        return result

    result.tree = tree
    result.success = True

    # ── Step 2: Extract imports ──────────────────────────
    iv = _ImportVisitor()
    iv.visit(tree)
    result.imports = iv.imports

    # ── Step 3: Extract calls ────────────────────────────
    cv = _CallVisitor()
    cv.visit(tree)
    result.calls = cv.calls

    # ── Step 4: Extract standalone attributes ────────────
    av = _AttributeVisitor()
    av.visit(tree)
    result.attributes = av.attributes

    return result
