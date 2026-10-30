"""
Streamlit UI — Phase 10 complete pipeline.
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st
from app.config import load_settings, DATA_DIR
from app.parser.ast_parser import parse_source
from app.analyzer.api_detector import APIDetector, load_mappings
from app.analyzer.symbol_analyzer import build_symbol_table
from app.analyzer.semantic_analyzer import SemanticAnalyzer, load_target_specs, IssueSeverity
from app.migration.migration_planner import MigrationPlanner
from app.migration.ast_transformer import apply_migrations
from app.migration.code_generator import generate_code
from app.validation.validation_pipeline import run_validation_pipeline
from app.ai.llm_client import create_llm_client
from app.ai.migration_agent import MigrationAgent
from app.ui.components import status_badge, code_diff, EXAMPLES


@st.cache_resource
def _load_pipeline():
    mappings = load_mappings(DATA_DIR / "api_mappings.json")
    specs = load_target_specs(DATA_DIR / "target_api_specs.json")
    return mappings, specs


def main() -> None:
    settings = load_settings()

    st.set_page_config(page_title="API Migration Tool", page_icon="🔄", layout="wide")
    st.title("🔄 AI-Assisted Compiler-Guided API Migration")
    st.caption("Compiler Design Course Project — Explainable Validation Pipeline")

    mappings, target_specs = _load_pipeline()
    detector = APIDetector(mappings)
    sem_analyzer = SemanticAnalyzer(mappings, target_specs)
    planner = MigrationPlanner(mappings)

    # ── Sidebar ──────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ Settings")
        llm_status = "✅ Configured" if settings.llm.is_available else "⚠️ Mock (no key)"
        st.write(f"**LLM:** {llm_status}")
        st.write(f"**Mappings:** {len(mappings)} patterns")
        st.write(f"**Target specs:** {len(target_specs)} APIs")
        st.divider()
        st.subheader("📂 Load Example")
        example_names = list(EXAMPLES.keys())
        example_choice = st.selectbox("Choose an example:", example_names, index=0)

    # ── When dropdown changes, load that example ─────────
    if "last_example" not in st.session_state:
        st.session_state["last_example"] = None
        st.session_state["source_code"] = EXAMPLES[example_names[0]]

    if example_choice != st.session_state["last_example"]:
        st.session_state["last_example"] = example_choice
        st.session_state["source_code"] = EXAMPLES[example_choice]

    # ── Input ────────────────────────────────────────────
    st.header("1️⃣ Source Code Input")
    source_code = st.text_area(
        "Paste or edit Python source code:",
        value=st.session_state["source_code"],
        height=200,
    )
    st.session_state["source_code"] = source_code

    uploaded = st.file_uploader("Or upload a .py file", type=["py"])
    if uploaded:
        source_code = uploaded.read().decode("utf-8")
        st.session_state["source_code"] = source_code

    if not source_code or not source_code.strip():
        st.info("Enter or select source code to begin.")
        return

    # ── Buttons ──────────────────────────────────────────
    col_a, col_m = st.columns(2)
    with col_a:
        analyze_btn = st.button("🔍 Analyze", use_container_width=True)
    with col_m:
        migrate_btn = st.button("🚀 Migrate & Validate", use_container_width=True)

    # Auto-run analysis when example changes
    if not analyze_btn and not migrate_btn:
        analyze_btn = True  # always show analysis

    # -- Parse --
    pr = parse_source(source_code)
    if not pr.success:
        st.error("❌ **Parse Error**")
        for e in pr.errors:
            st.code(str(e))
        return

    # -- Detect --
    dets = detector.detect(pr)
    st_table = build_symbol_table(pr)
    sem_report = sem_analyzer.analyze(dets, st_table, pr)
    plans = planner.plan(dets, sem_report)

    # ── Analysis Results ─────────────────────────────────
    st.header("2️⃣ Analysis Results")

    with st.expander(f"📋 Detected Deprecated APIs ({len(dets)})", expanded=True):
        if dets:
            for d in dets:
                st.markdown(f"- **`{d.api_name}`** at line {d.line} — args: `{d.arguments}`")
        else:
            st.success("No deprecated APIs detected — code is up to date!")

    with st.expander(f"🔗 Symbol Table ({len(st_table.all_symbols())} entries)"):
        for s in st_table.all_symbols():
            st.markdown(f"- `{s.name}` → type=`{s.inferred_type}` | kind=`{s.kind.value}` | scope=`{s.scope}`")

    with st.expander(f"⚠️ Semantic Analysis ({len(sem_report.issues)} issues)"):
        if sem_report.issues:
            for iss in sem_report.issues:
                icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(iss.severity.value, "⚪")
                st.markdown(f"- {icon} **{iss.kind.value}** (line {iss.line}): {iss.message}")
        else:
            st.success("No semantic issues found.")

    with st.expander(f"📝 Migration Plans ({len(plans)})", expanded=True):
        if plans:
            for p in plans:
                st.markdown(
                    f"- **{p.old_api}** → **{p.new_api}** | "
                    f"type=`{p.transformation_type}` | confidence=`{p.confidence:.0%}`"
                )
                if p.validation_notes:
                    for n in p.validation_notes:
                        st.caption(f"   ↳ {n}")
        else:
            st.info("No migrations needed.")

    if not migrate_btn:
        return

    if not plans:
        st.info("Nothing to migrate — no deprecated APIs detected.")
        return

    # ── Migrate ──────────────────────────────────────────
    st.header("3️⃣ Migration Result")
    tx_result = apply_migrations(pr.tree, plans)
    gen = generate_code(source_code, tx_result.transformed_tree)
    code_diff(source_code, gen.migrated_source, gen.diff)

    st.markdown(f"**Plans applied:** {len(tx_result.plans_applied)} | **Skipped:** {len(tx_result.plans_skipped)}")
    if tx_result.errors:
        for err in tx_result.errors:
            st.warning(f"Transform error: {err}")

    # ── Validate ─────────────────────────────────────────
    st.header("4️⃣ Validation")
    val_report = run_validation_pipeline(gen.migrated_source, plans, target_specs)

    if val_report.passed:
        st.success(f"✅ **PASS** — {val_report.summary()}")
    else:
        st.error(f"❌ **FAIL** — {val_report.summary()}")

    with st.expander("📊 Detailed Validation Results", expanded=True):
        for r in val_report.results:
            icon = "✅" if r.passed else {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(r.level.value, "⚪")
            st.markdown(f"- {icon} **[{r.validator.value}]** {r.message}")

    # ── AI Insight ───────────────────────────────────────
    with st.expander("🤖 AI Insight (advisory)"):
        llm = create_llm_client()
        agent = MigrationAgent(llm=llm)
        ai_result = agent.assist(source_code, dets, plans, mappings, sem_report)
        st.caption(f"Provider: `{ai_result.provider}` | Model: `{ai_result.model}`")
        for ins in ai_result.insights:
            st.markdown(f"**{ins.migration_id}**: {ins.explanation}")
            if ins.suggestions:
                for s in ins.suggestions:
                    st.caption(f"  💡 {s}")


if __name__ == "__main__":
    main()
