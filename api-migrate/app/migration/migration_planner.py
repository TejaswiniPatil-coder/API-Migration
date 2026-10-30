"""
Migration Planner — generates structured migration plans.

For every detected deprecated API call, the planner creates a
``MigrationPlan`` dataclass that specifies exactly:

  - which old API to replace
  - what new API to use
  - what argument transformations to apply
  - what validations are required
  - confidence level (1.0 = deterministic, <1.0 = AI-assisted)

The planner does NOT modify any source code.  It only produces
plans that Phase 7 (AST Transformer) will consume.

Compiler-design concepts exercised:
  - Intermediate representation (the plan is an IR between
    analysis and code generation)
  - Transformation specification
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.api_models import DetectedAPI
from app.models.migration_models import (
    MigrationMapping,
    MigrationPlan,
    ArgumentTransformation,
)
from app.migration.api_mapper import MappedAPI, match_detections_to_mappings
from app.analyzer.semantic_analyzer import SemanticReport, IssueSeverity


# ────────────────────────────────────────────────────────────
# Planner
# ────────────────────────────────────────────────────────────

class MigrationPlanner:
    """Generate migration plans from matched API detections.

    Parameters
    ----------
    mappings : list[MigrationMapping]
        The full set of migration rules.
    """

    def __init__(self, mappings: list[MigrationMapping]) -> None:
        self.mappings = mappings

    def plan(
        self,
        detections: list[DetectedAPI],
        semantic_report: Optional[SemanticReport] = None,
    ) -> list[MigrationPlan]:
        """Create a migration plan for every detected deprecated call.

        Parameters
        ----------
        detections : list[DetectedAPI]
            Deprecated API calls found by the detector.
        semantic_report : SemanticReport, optional
            Pre-migration semantic analysis (used to annotate plans
            with validation notes and adjust confidence).

        Returns
        -------
        list[MigrationPlan]
            One plan per detection, sorted by source line number.
        """
        matched = match_detections_to_mappings(detections, self.mappings)
        plans: list[MigrationPlan] = []

        for m in matched:
            plan = self._build_plan(m, semantic_report)
            plans.append(plan)

        # Sort by line number so transformations are applied top-down
        plans.sort(key=lambda p: p.line)
        return plans

    # ── Internal ─────────────────────────────────────────

    def _build_plan(
        self,
        mapped: MappedAPI,
        semantic_report: Optional[SemanticReport],
    ) -> MigrationPlan:
        det = mapped.detection
        mapping = mapped.mapping

        # Build argument transformations
        arg_changes = self._build_arg_transformations(mapping)

        # Collect validation notes from semantic report
        validation_notes: list[str] = []
        confidence = 1.0

        if semantic_report:
            for issue in semantic_report.issues:
                if issue.api_name == det.api_name:
                    validation_notes.append(
                        f"[{issue.severity.value}] {issue.message}"
                    )
                    if issue.severity == IssueSeverity.ERROR:
                        confidence = min(confidence, 0.5)
                    elif issue.severity == IssueSeverity.WARNING:
                        confidence = min(confidence, 0.8)

        # Complex transformations get lower confidence
        if mapping.transformation_type in ("arg_merge", "method_to_property"):
            confidence = min(confidence, 0.9)
        if mapping.transformation_type == "arg_reorder_and_rename":
            confidence = min(confidence, 0.95)

        return MigrationPlan(
            migration_id=mapping.migration_id,
            line=det.line,
            old_api=mapping.old_api,
            new_api=mapping.new_api,
            transformation_type=mapping.transformation_type,
            argument_changes=arg_changes,
            validation_notes=validation_notes,
            confidence=confidence,
        )

    def _build_arg_transformations(
        self,
        mapping: MigrationMapping,
    ) -> list[ArgumentTransformation]:
        """Convert raw JSON dicts into ArgumentTransformation dataclasses."""
        result: list[ArgumentTransformation] = []
        for txn in mapping.argument_transformations:
            result.append(ArgumentTransformation(
                old_name=txn.get("old_name", ""),
                new_name=txn.get("new_name"),
                old_position=txn.get("old_position"),
                new_position=txn.get("new_position"),
                type_conversion=txn.get("type_conversion"),
                default_value=txn.get("default_value"),
            ))
        return result


# ────────────────────────────────────────────────────────────
# Convenience function
# ────────────────────────────────────────────────────────────

def create_migration_plans(
    detections: list[DetectedAPI],
    mappings: list[MigrationMapping],
    semantic_report: Optional[SemanticReport] = None,
) -> list[MigrationPlan]:
    """One-shot helper to generate plans without instantiating the planner."""
    planner = MigrationPlanner(mappings)
    return planner.plan(detections, semantic_report)
