import json
import os
from functools import partial
import httpx

import streamlit as st
from dotenv import load_dotenv

from evaluation.runner import PROFILES, ROOT, evaluate, load_cases
from agentguard.providers import installed_models, ollama_draft
from pipeline import build_pipeline

load_dotenv()
st.set_page_config(page_title="AgentGuard", layout="wide")
st.sidebar.title("AgentGuard")
page = st.sidebar.radio("Workspace", ["Research", "Evaluation"])


def choose_model():
    try:
        tags = sorted(installed_models())
    except httpx.HTTPError:
        st.error("Start Ollama locally to load the installed models.")
        return None
    if not tags:
        st.info("Install an Ollama model to use this mode.")
        return None
    preferred = os.getenv("OLLAMA_MODEL") or "llama3"
    index = next((i for i, tag in enumerate(tags) if tag in {preferred, preferred + ":latest"}), 0)
    return st.selectbox("Local model", tags, index=index)


def research():
    st.title("Research")
    mode = st.radio("Research mode", ["Sample catalog", "Sample catalog with Ollama", "Live web"], horizontal=True)
    model = choose_model() if mode != "Sample catalog" else None
    with st.form("research"):
        query = st.text_input("Research question", placeholder="What do research agents do?", max_chars=1000)
        submit = st.form_submit_button("Run research", type="primary", disabled=mode != "Sample catalog" and not model)
    if submit:
        st.session_state.pop("research_result", None)
        if mode == "Live web" and not os.getenv("TAVILY_API_KEY"):
            st.error("Configure TAVILY_API_KEY in .env to search the web.")
        else:
            with st.spinner("Gathering evidence"):
                draft = partial(ollama_draft, model=model) if model else None
                result = build_pipeline(offline=mode != "Live web", draft=draft).invoke({"query": query})
            st.session_state.research_result = (mode, model, result)
    if "research_result" in st.session_state:
        source_mode, used_model, result = st.session_state.research_result
        st.subheader("Answer")
        st.caption(" · ".join(filter(None, [source_mode, used_model])))
        st.text(result["response"])
        if result["status"] == "error":
            st.info("Check the failed stage in the trace. Live research requires Tavily and a local Ollama model.")
        if result["sources"]:
            st.table([{"Source": source, "URL": url} for source, url in result["sources"].items()])
        with st.expander("Execution trace"):
            st.caption(result["run_id"])
            st.dataframe(result["trace"], hide_index=True)


def evaluation():
    st.title("Safety evaluation")
    cases = load_cases()
    category = st.selectbox("Category", ["All", *sorted({c["category"] for c in cases})])
    eligible = [c for c in cases if category in ("All", c["category"])]
    profiles = st.multiselect("Configurations", PROFILES, default=PROFILES[:2])
    needs_model = "guarded-ollama" in profiles and any("query" in c for c in eligible)
    model = choose_model() if needs_model else None
    with st.form("benchmark_options"):
        scenario = st.selectbox("Scenario", ["All", *(c["id"] for c in eligible)])
        run = st.form_submit_button("Run benchmark", type="primary", disabled=needs_model and not model)
    if run:
        selected = [c for c in cases if category in ("All", c["category"]) and scenario in ("All", c["id"])]
        if not selected or not profiles:
            st.error("Select a configuration and a scenario in the chosen category.")
        else:
            try:
                with st.spinner("Running scenarios"):
                    st.session_state.benchmark = evaluate(selected, profiles, model=model)
            except (ValueError, httpx.HTTPError) as exc:
                st.error(f"Benchmark setup failed. {exc}")
                return
    report = st.session_state.get("benchmark")
    if report is None:
        saved = ROOT / "results" / "benchmark.json"
        if not saved.exists():
            st.info("Run the paired replay to compare the same fixture drafts before and after validation.")
            return
        try:
            report = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st.error("The saved benchmark could not be read. Run a new benchmark above.")
            return
    st.subheader("Configuration comparison")
    st.caption(f"{report['scenarios']} scenarios · {len(report['runs'])} trials · {report['created']}")
    table = []
    for metric in next(iter(report["metrics"].values())):
        row = {"Metric": metric}
        for name, metrics in report["metrics"].items():
            score = metrics[metric]
            n, total = score["count"], score["total"]
            row[name] = f"{n}/{total} ({n / total:.0%})" if total else "No eligible cases"
        table.append(row)
    st.table(table)
    with st.expander("Pass rate by category"):
        category_rows = []
        for name in sorted({r["category"] for r in report["runs"]}):
            row = {"Category": name}
            for profile in report["metrics"]:
                trials = [r for r in report["runs"] if r["profile"] == profile and r["category"] == name]
                row[profile] = f"{sum(r['passed'] for r in trials)}/{len(trials)}"
            category_rows.append(row)
        st.table(category_rows)
    st.download_button("Download results and traces", json.dumps(report, indent=2),
                       "agentguard-benchmark.json", "application/json")
    st.subheader("Run inspector")
    failures = st.checkbox("Show failed trials only", value=True)
    visible = [r for r in report["runs"] if not failures or not r["passed"]]
    if not visible:
        st.info("All trials in this selection passed.")
        return
    chosen = st.selectbox("Trial", range(len(visible)),
                          format_func=lambda i: f"{visible[i]['id']} · {visible[i]['profile']}")
    row = visible[chosen]
    case = next((c for c in cases if c["id"] == row["id"]), {})
    a, b, c = st.columns(3)
    a.metric("Expected status", row["expected_status"])
    b.metric("Observed status", row["status"])
    c.metric("Trial duration", f"{row['latency_ms']:.1f} ms")
    st.caption(case.get("query", case.get("tool", "")))
    st.text(row["response"])
    st.table([{"Check": key, "Result": "Pass" if value else "Fail"} for key, value in row["checks"].items()])
    with st.expander("Execution trace", expanded=True):
        st.caption(row["run_id"])
        st.dataframe(row["trace"], hide_index=True)


if page == "Research":
    research()
else:
    evaluation()
