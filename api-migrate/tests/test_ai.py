"""
Unit tests for AI integration (Phase 8).

All tests use the mock provider — no real API key needed.

Covers:
  - Mock LLM provider returns structured JSON
  - LLM client factory (default → mock)
  - Prompt generation with full context
  - Migration agent produces insights
  - AI unavailable → graceful fallback
  - Deterministic pipeline works without AI
  - Invalid JSON handling
"""

import json
import pytest

from app.ai.llm_client import (
    MockLLMProvider,
    OpenAIProvider,
    GeminiProvider,
    LLMResponse,
    create_llm_client,
    _safe_parse_json,
)
from app.ai.prompts import build_migration_prompt, SYSTEM_PROMPT
from app.ai.migration_agent import MigrationAgent, AIInsight, AIAssistResult

from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import APIDetector, load_mappings
from app.analyzer.symbol_analyzer import build_symbol_table
from app.analyzer.semantic_analyzer import SemanticAnalyzer, load_target_specs
from app.migration.migration_planner import MigrationPlanner
from app.migration.ast_transformer import apply_migrations
from app.migration.code_generator import generate_code
from app.config import DATA_DIR


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def mappings():
    return load_mappings(DATA_DIR / "api_mappings.json")

@pytest.fixture(scope="module")
def target_specs():
    return load_target_specs(DATA_DIR / "target_api_specs.json")

@pytest.fixture(scope="module")
def detector(mappings):
    return APIDetector(mappings)

@pytest.fixture(scope="module")
def sem_analyzer(mappings, target_specs):
    return SemanticAnalyzer(mappings, target_specs)

@pytest.fixture(scope="module")
def planner(mappings):
    return MigrationPlanner(mappings)


# ════════════════════════════════════════════════════════════
# LLM CLIENT
# ════════════════════════════════════════════════════════════

class TestMockProvider:

    def test_mock_returns_success(self):
        mock = MockLLMProvider()
        resp = mock.complete("Test prompt")
        assert resp.success is True
        assert resp.is_valid_json
        assert resp.parsed_json["success"] is True

    def test_mock_provider_name(self):
        mock = MockLLMProvider()
        assert mock.provider_name == "mock"
        assert mock.model_name == "mock-1.0"

    def test_mock_has_explanation(self):
        mock = MockLLMProvider()
        resp = mock.complete("Migrate old_connect")
        assert "explanation" in resp.parsed_json
        assert len(resp.parsed_json["explanation"]) > 0


class TestClientFactory:

    def test_default_is_mock(self):
        client = create_llm_client()
        assert isinstance(client, MockLLMProvider)

    def test_explicit_mock(self):
        client = create_llm_client(provider="mock")
        assert isinstance(client, MockLLMProvider)

    def test_unknown_provider_falls_back_to_mock(self):
        client = create_llm_client(provider="unknown", api_key="key")
        assert isinstance(client, MockLLMProvider)

    def test_no_key_falls_back_to_mock(self):
        client = create_llm_client(provider="openai", api_key="")
        assert isinstance(client, MockLLMProvider)

    def test_openai_with_key(self):
        client = create_llm_client(provider="openai", api_key="test-key")
        assert isinstance(client, OpenAIProvider)

    def test_gemini_with_key(self):
        client = create_llm_client(provider="gemini", api_key="test-key")
        assert isinstance(client, GeminiProvider)


class TestJSONParsing:

    def test_valid_json(self):
        result = _safe_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_code_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = _safe_parse_json(text)
        assert result == {"key": "value"}

    def test_invalid_json(self):
        result = _safe_parse_json("not json at all")
        assert result is None


# ════════════════════════════════════════════════════════════
# PROMPTS
# ════════════════════════════════════════════════════════════

class TestPrompts:

    def test_system_prompt_exists(self):
        assert len(SYSTEM_PROMPT) > 50
        assert "JSON" in SYSTEM_PROMPT

    def test_build_migration_prompt(self, mappings, detector, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        pr = parse_source(code)
        dets = detector.detect(pr)
        plans = planner.plan(dets)

        mapping = next(m for m in mappings if m.old_api == "HTTPClient.old_connect")
        prompt = build_migration_prompt(code, dets[0], mapping, plans[0])

        assert "old_connect" in prompt
        assert "connect" in prompt
        assert "HTTPClient" in prompt
        assert "localhost" in prompt
        assert "JSON" in prompt


# ════════════════════════════════════════════════════════════
# MIGRATION AGENT
# ════════════════════════════════════════════════════════════

class TestMigrationAgent:

    def test_agent_with_mock(self, mappings, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = sem_analyzer.analyze(dets, st, pr)
        plans = planner.plan(dets, report)

        agent = MigrationAgent(llm=MockLLMProvider())
        result = agent.assist(code, dets, plans, mappings, report)

        assert isinstance(result, AIAssistResult)
        assert result.provider == "mock"
        assert len(result.insights) == 1
        insight = result.insights[0]
        assert insight.available is True
        assert len(insight.explanation) > 0

    def test_agent_multiple_migrations(self, mappings, detector, sem_analyzer, planner):
        code = (
            "from benchmark_db import Database\n"
            "db = Database()\n"
            "db.open_connection('h', 5432, 'db')\n"
            "db.execute_query('SELECT 1')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = sem_analyzer.analyze(dets, st, pr)
        plans = planner.plan(dets, report)

        agent = MigrationAgent(llm=MockLLMProvider())
        result = agent.assist(code, dets, plans, mappings, report)
        assert len(result.insights) == 2

    def test_get_insight_by_id(self, mappings, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.close_connection()\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = sem_analyzer.analyze(dets, st, pr)
        plans = planner.plan(dets, report)

        agent = MigrationAgent(llm=MockLLMProvider())
        result = agent.assist(code, dets, plans, mappings, report)

        insight = result.get_insight("BM-HTTP-003")
        assert insight is not None
        assert insight.migration_id == "BM-HTTP-003"


class TestDeterministicWithoutAI:
    """Verify that the full pipeline works without any AI."""

    def test_full_pipeline_no_ai(self, mappings, detector, sem_analyzer, planner):
        code = (
            "from benchmark_http import HTTPClient\n"
            "client = HTTPClient()\n"
            "client.old_connect('localhost', '8080')\n"
        )
        pr = parse_source(code)
        st = build_symbol_table(pr)
        dets = detector.detect(pr)
        report = sem_analyzer.analyze(dets, st, pr)
        plans = planner.plan(dets, report)
        result = apply_migrations(pr.tree, plans)
        gen = generate_code(code, result.transformed_tree)

        # Migration succeeded without any AI call
        assert result.success
        assert "old_connect" not in gen.migrated_source
        assert ".connect(" in gen.migrated_source
        assert gen.has_changes
