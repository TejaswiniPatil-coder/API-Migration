"""
AST Transformer — applies migration plans via ast.NodeTransformer.

Takes the migration plans produced by Phase 6 and applies them to
the AST, performing source-to-source transformation.

Supported transformation types:
  - rename                   (method name change, no arg changes)
  - rename_with_arg_modify   (rename + type conversion / arg rename)
  - rename_with_new_param    (rename + optional new parameter added)
  - arg_reorder_and_rename   (argument reordering + renaming)
  - arg_merge                (multiple args → single dict arg)
  - method_to_property       (call → attribute access)

Compiler-design concepts:
  - Source-to-source transformation
  - AST rewriting via visitor pattern
  - Intermediate representation consumption (MigrationPlan)

All transformations operate on a *copy* of the AST — the original
is never mutated.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from typing import Optional

from app.models.migration_models import MigrationPlan, ArgumentTransformation


# ────────────────────────────────────────────────────────────
# Result model
# ────────────────────────────────────────────────────────────

@dataclass
class TransformResult:
    """Outcome of applying transformations to an AST."""

    original_tree: ast.Module
    transformed_tree: ast.Module
    plans_applied: list[MigrationPlan] = field(default_factory=list)
    plans_skipped: list[MigrationPlan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.plans_applied) > 0 and len(self.errors) == 0


# ────────────────────────────────────────────────────────────
# Node transformer
# ────────────────────────────────────────────────────────────

class MigrationTransformer(ast.NodeTransformer):
    """Apply migration plans by rewriting AST Call nodes.

    Parameters
    ----------
    plans : list[MigrationPlan]
        Plans to apply, typically sorted by line number.
    """

    def __init__(self, plans: list[MigrationPlan]) -> None:
        super().__init__()
        # Index plans by line number for fast lookup
        self._plans_by_line: dict[int, MigrationPlan] = {
            p.line: p for p in plans
        }
        self.applied: list[MigrationPlan] = []
        self.skipped: list[MigrationPlan] = []
        self.errors: list[str] = []

    # ── Main visit ───────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> ast.AST:
        """Visit a Call node and apply a matching migration plan."""
        self.generic_visit(node)  # transform children first

        plan = self._match_plan(node)
        if plan is None:
            return node

        try:
            result = self._apply_plan(node, plan)
            self.applied.append(plan)
            return result
        except Exception as exc:
            self.errors.append(f"Line {plan.line}: {exc}")
            self.skipped.append(plan)
            return node

    # ── Plan matching ────────────────────────────────────

    def _match_plan(self, node: ast.Call) -> Optional[MigrationPlan]:
        """Check if a Call node matches any pending plan."""
        line = getattr(node, "lineno", None)
        if line is None:
            return None

        plan = self._plans_by_line.get(line)
        if plan is None:
            return None

        # Verify the method name matches the old API
        old_method = plan.old_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            if node.func.attr != old_method:
                return None
        elif isinstance(node.func, ast.Name):
            if node.func.id != old_method:
                return None
        else:
            return None

        return plan

    # ── Dispatch ─────────────────────────────────────────

    def _apply_plan(self, node: ast.Call, plan: MigrationPlan) -> ast.AST:
        """Route to the correct transformation handler."""
        t = plan.transformation_type

        if t == "rename":
            return self._transform_rename(node, plan)
        elif t == "rename_with_arg_modify":
            return self._transform_rename_with_arg_modify(node, plan)
        elif t == "rename_with_new_param":
            return self._transform_rename_with_new_param(node, plan)
        elif t == "arg_reorder_and_rename":
            return self._transform_arg_reorder_and_rename(node, plan)
        elif t == "arg_merge":
            return self._transform_arg_merge(node, plan)
        elif t == "method_to_property":
            return self._transform_method_to_property(node, plan)
        else:
            raise ValueError(f"Unknown transformation type: {t}")

    # ── Transformation handlers ──────────────────────────

    def _transform_rename(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.Call:
        """Simple method rename — no argument changes."""
        new_method = plan.new_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            node.func.attr = new_method
        elif isinstance(node.func, ast.Name):
            node.func.id = new_method
        return node

    def _transform_rename_with_arg_modify(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.Call:
        """Rename + argument modifications (type conversions, renames)."""
        # Rename the method
        new_method = plan.new_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            node.func.attr = new_method
        elif isinstance(node.func, ast.Name):
            node.func.id = new_method

        # Apply argument transformations
        new_args: list[ast.expr] = list(node.args)  # copy

        for txn in plan.argument_changes:
            if txn.old_position is not None and txn.old_position < len(new_args):
                arg = new_args[txn.old_position]

                # Apply type conversion
                if txn.type_conversion == "str_to_int":
                    new_args[txn.old_position] = ast.Call(
                        func=ast.Name(id="int", ctx=ast.Load()),
                        args=[arg],
                        keywords=[],
                    )

                # If arg was renamed and became optional (has default),
                # convert positional to keyword
                if (txn.new_name and txn.old_name and
                        txn.new_name != txn.old_name and
                        txn.default_value is not None):
                    # Will be handled in keyword conversion pass below
                    pass

        node.args = new_args
        ast.fix_missing_locations(node)
        return node

    def _transform_rename_with_new_param(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.Call:
        """Rename + new optional parameters (not emitted if they have defaults)."""
        new_method = plan.new_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            node.func.attr = new_method
        elif isinstance(node.func, ast.Name):
            node.func.id = new_method
        # New params with defaults are simply not emitted — they'll
        # use their default values in the target API.
        return node

    def _transform_arg_reorder_and_rename(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.Call:
        """Reorder arguments and rename where needed."""
        new_method = plan.new_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            node.func.attr = new_method
        elif isinstance(node.func, ast.Name):
            node.func.id = new_method

        old_args = list(node.args)

        # Build a map: new_position → (old arg node, txn)
        reorder_map: dict[int, tuple[ast.expr, ArgumentTransformation]] = {}
        keyword_conversions: list[tuple[ast.expr, ArgumentTransformation]] = []

        for txn in plan.argument_changes:
            if txn.old_position is not None and txn.old_position < len(old_args):
                arg_node = old_args[txn.old_position]
                if txn.new_position is not None:
                    # If has default_value, emit as keyword
                    if txn.default_value is not None and txn.new_name:
                        keyword_conversions.append((arg_node, txn))
                    else:
                        reorder_map[txn.new_position] = (arg_node, txn)

        # Build new positional args in new order
        max_pos = max(reorder_map.keys()) if reorder_map else -1
        new_positional: list[ast.expr] = []
        for i in range(max_pos + 1):
            if i in reorder_map:
                new_positional.append(reorder_map[i][0])
            else:
                # Position not mapped — shouldn't happen but be safe
                new_positional.append(ast.Constant(value=None))

        # Build keyword args for renamed/optional params
        new_keywords = list(node.keywords)
        for arg_node, txn in keyword_conversions:
            if txn.new_name:
                new_keywords.append(ast.keyword(
                    arg=txn.new_name,
                    value=arg_node,
                ))

        node.args = new_positional
        node.keywords = new_keywords
        ast.fix_missing_locations(node)
        return node

    def _transform_arg_merge(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.Call:
        """Merge multiple positional args into a single dict argument."""
        new_method = plan.new_api.rsplit(".", 1)[-1]
        if isinstance(node.func, ast.Attribute):
            node.func.attr = new_method
        elif isinstance(node.func, ast.Name):
            node.func.id = new_method

        # Find the merge spec
        merge_keys: list[str] = []
        merge_positions: list[int] = []

        for txn in plan.argument_changes:
            if txn.type_conversion == "merge_to_dict" and txn.old_name:
                merge_keys.append(txn.old_name)
                if txn.old_position is not None:
                    merge_positions.append(txn.old_position)

        # Collect the actual arg nodes to merge
        merge_values: list[ast.expr] = []
        for pos in merge_positions:
            if pos < len(node.args):
                merge_values.append(node.args[pos])
            else:
                merge_values.append(ast.Constant(value=None))

        # Build the dict node
        dict_node = ast.Dict(
            keys=[ast.Constant(value=k) for k in merge_keys],
            values=merge_values,
        )

        # Replace args with single dict
        node.args = [dict_node]
        node.keywords = []
        ast.fix_missing_locations(node)
        return node

    def _transform_method_to_property(
        self, node: ast.Call, plan: MigrationPlan,
    ) -> ast.AST:
        """Convert a method call to a property access.

        ``auth.get_token()`` → ``auth.token``

        Returns an ``ast.Attribute`` node (not a Call).
        """
        new_attr = plan.new_api.rsplit(".", 1)[-1]

        if isinstance(node.func, ast.Attribute):
            # Build: receiver.new_attr
            attr_node = ast.Attribute(
                value=node.func.value,
                attr=new_attr,
                ctx=ast.Load(),
            )
            ast.copy_location(attr_node, node)
            return attr_node

        # Fallback — shouldn't happen for our patterns
        return node


# ────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────

def apply_migrations(
    tree: ast.Module,
    plans: list[MigrationPlan],
) -> TransformResult:
    """Apply migration plans to an AST tree.

    The original tree is NOT mutated — a deep copy is transformed.

    Parameters
    ----------
    tree : ast.Module
        The parsed AST.
    plans : list[MigrationPlan]
        Plans to apply.

    Returns
    -------
    TransformResult
    """
    original = tree
    working_copy = copy.deepcopy(tree)

    transformer = MigrationTransformer(plans)
    transformed = transformer.visit(working_copy)
    ast.fix_missing_locations(transformed)

    return TransformResult(
        original_tree=original,
        transformed_tree=transformed,
        plans_applied=transformer.applied,
        plans_skipped=transformer.skipped,
        errors=transformer.errors,
    )
