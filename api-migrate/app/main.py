"""
main.py — Entry point for the API Migration system.

Can be used as a CLI for quick checks or invoked by Streamlit.
"""

from app.config import load_settings


def print_status() -> None:
    """Print current configuration status to stdout."""
    settings = load_settings()

    print("=" * 60)
    print("  AI-Assisted Compiler-Guided API Migration")
    print("=" * 60)
    print(f"  Project root : {settings.project_root}")
    print(f"  Data dir     : {settings.data_dir}")
    print(f"  LLM provider : {settings.llm.provider or '(not configured)'}")
    print(f"  LLM available: {settings.llm.is_available}")
    print(f"  Debug mode   : {settings.debug}")
    print("=" * 60)


if __name__ == "__main__":
    print_status()
