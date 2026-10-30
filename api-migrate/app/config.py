"""
Configuration module for the API Migration system.

Loads settings from environment variables and .env file.
Provides project-wide paths and defaults.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# Project root is the 'api-migrate' directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
EXAMPLES_DIR = PROJECT_ROOT / "examples"


@dataclass
class LLMSettings:
    """LLM provider configuration. All fields are optional —
    the deterministic AST pipeline works without any LLM."""

    provider: Optional[str] = None      # e.g. "openai", "gemini", "mock"
    api_key: Optional[str] = None
    model_name: Optional[str] = None

    @property
    def is_available(self) -> bool:
        """Return True only if a provider and key are configured."""
        return bool(self.provider and self.api_key)


@dataclass
class Settings:
    """Top-level application settings."""

    llm: LLMSettings = field(default_factory=LLMSettings)
    data_dir: Path = DATA_DIR
    project_root: Path = PROJECT_ROOT
    debug: bool = False

    # --- data file paths ---
    @property
    def api_mappings_path(self) -> Path:
        return self.data_dir / "api_mappings.json"

    @property
    def target_api_specs_path(self) -> Path:
        return self.data_dir / "target_api_specs.json"

    @property
    def benchmark_cases_path(self) -> Path:
        return self.data_dir / "benchmark_cases.json"


def _load_dotenv() -> None:
    """Minimal .env loader — no external dependency required."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            os.environ.setdefault(key, value)


def load_settings() -> Settings:
    """Build a Settings instance from environment variables."""
    _load_dotenv()

    llm = LLMSettings(
        provider=os.environ.get("LLM_PROVIDER"),
        api_key=os.environ.get("LLM_API_KEY"),
        model_name=os.environ.get("MODEL_NAME"),
    )

    return Settings(
        llm=llm,
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
    )


def load_json(path: Path) -> dict | list:
    """Load and return parsed JSON from *path*."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
