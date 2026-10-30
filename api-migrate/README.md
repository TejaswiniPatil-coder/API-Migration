# AI-Assisted Compiler-Guided API Migration with Explainable Validation

An undergraduate Compiler Design course project that migrates Python source
code from deprecated/old APIs to their newer equivalents using a compiler-aware
validation pipeline.

## Research Question

> Can a compiler-aware validation and feedback layer improve the reliability
> and explainability of AI-assisted API migration?

## Pipeline

```
SOURCE CODE → AST PARSING → API DETECTION → MIGRATION PLANNING
→ AST TRANSFORMATION → VALIDATION → FINAL RESULT
```

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / macOS

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Configure LLM — deterministic pipeline works without it
copy .env.example .env
# edit .env with your provider/key

# 4. Launch Streamlit UI
python run.py

# 5. Run tests
pytest -q
```

## Project Structure

See `docs/architecture.md` (created in Phase 10).

## Controlled Benchmark APIs

This project uses synthetic "benchmark" APIs for reliable, reproducible
testing.  Each pattern is explicitly labelled as a controlled benchmark.
Real external library dependencies are **not** required to run the tests.

## License

Academic project — Compiler Design, Semester 5.
