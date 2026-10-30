"""
LLM Client — provider-agnostic interface for AI assistance.

Supports:
  - ``mock``   : Deterministic responses for testing (no API key needed)
  - ``openai`` : OpenAI-compatible API
  - ``gemini`` : Google Gemini API

Configuration via environment variables:
  - LLM_PROVIDER  (mock | openai | gemini)
  - LLM_API_KEY
  - MODEL_NAME

The client always returns structured JSON.  If the provider is
unavailable or returns invalid output, errors are captured — the
deterministic pipeline continues unaffected.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any


# ────────────────────────────────────────────────────────────
# Response model
# ────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Structured response from the LLM."""

    success: bool = False
    content: str = ""
    parsed_json: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    provider: str = ""
    model: str = ""

    @property
    def is_valid_json(self) -> bool:
        return self.parsed_json is not None


# ────────────────────────────────────────────────────────────
# Abstract base
# ────────────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Send a prompt and return a structured response."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...


# ────────────────────────────────────────────────────────────
# Mock provider (always available)
# ────────────────────────────────────────────────────────────

class MockLLMProvider(BaseLLMProvider):
    """Deterministic mock provider for testing.

    Returns reasonable structured responses without any API call.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-1.0"

    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        # Parse the prompt to extract migration context
        response_json = {
            "success": True,
            "replacement": None,
            "explanation": (
                "Mock AI analysis: The migration can be performed "
                "deterministically using AST transformation. "
                "No AI intervention required for this pattern."
            ),
            "assumptions": [],
            "confidence": 0.95,
            "suggestions": [
                "Verify the migrated code compiles correctly.",
                "Run unit tests to ensure semantic equivalence.",
            ],
        }

        return LLMResponse(
            success=True,
            content=json.dumps(response_json, indent=2),
            parsed_json=response_json,
            provider="mock",
            model="mock-1.0",
        )


# ────────────────────────────────────────────────────────────
# OpenAI provider
# ────────────────────────────────────────────────────────────

class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible API provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._model = model

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        try:
            import urllib.request
            import urllib.error

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = json.dumps({
                "model": self._model,
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            content = data["choices"][0]["message"]["content"]
            parsed = _safe_parse_json(content)

            return LLMResponse(
                success=True,
                content=content,
                parsed_json=parsed,
                provider="openai",
                model=self._model,
            )

        except Exception as exc:
            return LLMResponse(
                success=False,
                error=f"OpenAI API error: {exc}",
                provider="openai",
                model=self._model,
            )


# ────────────────────────────────────────────────────────────
# Gemini provider
# ────────────────────────────────────────────────────────────

class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        self._api_key = api_key
        self._model = model

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        try:
            import urllib.request
            import urllib.error

            full_prompt = prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{prompt}"

            payload = json.dumps({
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }).encode("utf-8")

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/"
                f"models/{self._model}:generateContent?key={self._api_key}"
            )

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            content = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _safe_parse_json(content)

            return LLMResponse(
                success=True,
                content=content,
                parsed_json=parsed,
                provider="gemini",
                model=self._model,
            )

        except Exception as exc:
            return LLMResponse(
                success=False,
                error=f"Gemini API error: {exc}",
                provider="gemini",
                model=self._model,
            )


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _safe_parse_json(text: str) -> Optional[dict]:
    """Attempt to parse JSON from LLM output, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def create_llm_client(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> BaseLLMProvider:
    """Factory: create the appropriate LLM provider.

    Falls back to mock if no provider is configured.
    """
    provider = provider or os.environ.get("LLM_PROVIDER", "")
    api_key = api_key or os.environ.get("LLM_API_KEY", "")
    model = model or os.environ.get("MODEL_NAME", "")

    if not provider or not api_key:
        return MockLLMProvider()

    provider = provider.lower()

    if provider == "mock":
        return MockLLMProvider()
    elif provider == "openai":
        return OpenAIProvider(api_key, model or "gpt-4o-mini")
    elif provider == "gemini":
        return GeminiProvider(api_key, model or "gemini-2.0-flash")
    else:
        # Unknown provider — fall back to mock
        return MockLLMProvider()
