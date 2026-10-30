"""
Migration Agent — orchestrates AI-assisted migration.

The agent:
  1. Receives migration context (source, detections, plans, semantics).
  2. Consults the LLM for explanations / complex-case assistance.
  3. Returns structured AI insights alongside the deterministic results.

IMPORTANT:
  - The agent does NOT replace the deterministic AST pipeline.
  - The agent does NOT directly modify source code.
  - All AI suggestions are advisory — validation (Phase 9) determines
    correctness.
  - If the LLM is unavailable, the system falls back gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any

from app.ai.llm_client import BaseLLMProvider, LLMResponse, create_llm_client
from app.ai.prompts import build_migration_prompt, SYSTEM_PROMPT
from app.models.api_models import DetectedAPI
from app.models.migration_models import (
    MigrationMapping,
    MigrationPlan,
    MigrationResult,
)
from app.analyzer.semantic_analyzer import SemanticReport


# ────────────────────────────────────────────────────────────
# Result model
# ────────────────────────────────────────────────────────────

@dataclass
class AIInsight:
    """AI analysis for a single migration."""

    migration_id: str
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    complex_notes: str = ""
    raw_response: Optional[LLMResponse] = None
    available: bool = True  # False if AI was unavailable


@dataclass
class AIAssistResult:
    """Result of AI-assisted analysis for all migrations."""

    insights: list[AIInsight] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    ai_available: bool = True

    def get_insight(self, migration_id: str) -> Optional[AIInsight]:
        return next((i for i in self.insights if i.migration_id == migration_id), None)


# ────────────────────────────────────────────────────────────
# Agent
# ────────────────────────────────────────────────────────────

class MigrationAgent:
    """Orchestrate AI-assisted migration analysis.

    Parameters
    ----------
    llm : BaseLLMProvider, optional
        LLM provider instance. If None, uses the factory default.
    """

    def __init__(self, llm: Optional[BaseLLMProvider] = None) -> None:
        self.llm = llm or create_llm_client()

    def assist(
        self,
        source_code: str,
        detections: list[DetectedAPI],
        plans: list[MigrationPlan],
        mappings: list[MigrationMapping],
        semantic_report: Optional[SemanticReport] = None,
    ) -> AIAssistResult:
        """Request AI assistance for all planned migrations.

        Parameters
        ----------
        source_code : str
            Original source code.
        detections : list[DetectedAPI]
            Detected deprecated API calls.
        plans : list[MigrationPlan]
            Migration plans from the planner.
        mappings : list[MigrationMapping]
            Full mapping set.
        semantic_report : SemanticReport, optional
            Pre-migration semantic analysis.

        Returns
        -------
        AIAssistResult
        """
        mapping_lookup = {m.old_api: m for m in mappings}
        det_lookup = {d.api_name: d for d in detections}

        result = AIAssistResult(
            provider=self.llm.provider_name,
            model=self.llm.model_name,
        )

        for plan in plans:
            mapping = mapping_lookup.get(plan.old_api)
            detection = det_lookup.get(plan.old_api)

            if mapping is None or detection is None:
                continue

            insight = self._get_insight(
                source_code, detection, mapping, plan, semantic_report,
            )
            result.insights.append(insight)

        return result

    def _get_insight(
        self,
        source_code: str,
        detection: DetectedAPI,
        mapping: MigrationMapping,
        plan: MigrationPlan,
        semantic_report: Optional[SemanticReport],
    ) -> AIInsight:
        """Query the LLM for insight on a single migration."""
        semantic_notes = plan.validation_notes if plan.validation_notes else None

        prompt = build_migration_prompt(
            source_code=source_code,
            detection=detection,
            mapping=mapping,
            plan=plan,
            semantic_notes=semantic_notes,
        )

        try:
            response = self.llm.complete(prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as exc:
            return AIInsight(
                migration_id=plan.migration_id,
                explanation=f"AI unavailable: {exc}",
                available=False,
            )

        if not response.success:
            return AIInsight(
                migration_id=plan.migration_id,
                explanation=f"AI error: {response.error}",
                available=False,
                raw_response=response,
            )

        # Parse structured response
        return self._parse_response(plan.migration_id, response)

    def _parse_response(self, migration_id: str, response: LLMResponse) -> AIInsight:
        """Extract structured insight from LLM response."""
        if response.parsed_json:
            data = response.parsed_json
            return AIInsight(
                migration_id=migration_id,
                explanation=data.get("explanation", ""),
                assumptions=data.get("assumptions", []),
                suggestions=data.get("suggestions", []),
                confidence=float(data.get("confidence", 0.0)),
                complex_notes=data.get("complex_notes", ""),
                raw_response=response,
                available=True,
            )

        # JSON parse failed — use raw content
        return AIInsight(
            migration_id=migration_id,
            explanation=response.content[:500],
            raw_response=response,
            available=True,
        )
