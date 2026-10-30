"""
AI package — provider-agnostic LLM integration.

The AI layer is entirely optional.  The deterministic AST pipeline
works without any API key.
"""

from app.ai.llm_client import (
    BaseLLMProvider,
    MockLLMProvider,
    LLMResponse,
    create_llm_client,
)
from app.ai.migration_agent import MigrationAgent, AIInsight, AIAssistResult
from app.ai.prompts import build_migration_prompt, SYSTEM_PROMPT

__all__ = [
    "BaseLLMProvider",
    "MockLLMProvider",
    "LLMResponse",
    "create_llm_client",
    "MigrationAgent",
    "AIInsight",
    "AIAssistResult",
    "build_migration_prompt",
    "SYSTEM_PROMPT",
]
