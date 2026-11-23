import json
import os
import httpx

import streamlit as st
from dotenv import load_dotenv

from evaluation.runner import PROFILES, ROOT, evaluate, load_cases
from pipeline import build_pipeline

load_dotenv()
st.set_page_config(page_title="AgentGuard", layout="wide")
st.sidebar.title("AgentGuard")
page = st.sidebar.radio("Workspace", ["Research", "Evaluation"])


def research():
    st.title("Research")
    with st.form("research"):
        mode = st.radio("Sources", ["Sample catalog", "Live web"], horizontal=True)
        query = st.text_input("Research question", placeholder="What do research agents do?", max_chars=1000)
        submit = st.form_submit_button("Run research", type="primary")
    if submit:
        st.session_state.pop("research_result", None)
        if mode == "Live web" and not os.getenv("TAVILY_API_KEY"):
            st.error("Configure TAVILY_API_KEY in .env to search the web.")
        else:
            with st.spinner("Gathering evidence"):
                result = build_pipeline(offline=mode == "Sample catalog").invoke({"query": query})
            st.session_state.research_result = (query, result)
    if "research_result" in st.session_state:
        question, result = st.session_state.research_result
        st.subheader("Answer")
        st.text(question)
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
    with st.form("benchmark_options"):
        profiles = st.multiselect("Configurations", PROFILES, default=PROFILES[:2])
        scenario = st.selectbox("Scenario", ["All", *(c["id"] for c in eligible)])
        run = st.form_submit_button("Run benchmark", type="primary")
    if run:
        selected = [c for c in cases if category in ("All", c["category"]) and scenario in ("All", c["id"])]
        if not selected or not profiles:
            st.error("Select a configuration and a scenario in the chosen category.")
        else:
            try:
                with st.spinner("Running scenarios"):
                    st.session_state.benchmark = evaluate(selected, profiles)
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
