"""
run.py — Launch the Streamlit UI.

Usage:
    python run.py
"""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app_path = Path(__file__).parent / "app" / "ui" / "streamlit_app.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path)],
        cwd=str(Path(__file__).parent),
    )


if __name__ == "__main__":
    main()
