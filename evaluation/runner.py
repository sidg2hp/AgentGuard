"""Run paired offline trials or evaluate a local Ollama model against the same fixtures."""
import argparse
from datetime import datetime, timezone
import hashlib
from functools import partial
from importlib.metadata import version
import json
import os
import platform
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from uuid import uuid4

from agentguard.evidence import NO_EVIDENCE
from agentguard.mcp_server import dispatch
from agentguard.policy import CallBlocked, ToolGuard, event, protected
from agentguard.providers import extractive_draft, installed_models, local_read, local_search, ollama_draft
from evaluation.graders import grade, summarize
from pipeline import build_pipeline

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("baseline-replay", "guarded-replay", "guarded-extractive", "guarded-ollama")


def load_cases():
    cases = []
    for path in sorted((ROOT / "scenarios").glob("*.json")):
        for record in json.loads(path.read_text(encoding="utf-8")):
            cases.append({**record, "category": path.stem})
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Scenario IDs must exist and be unique.")
    return cases


def fixture_rows(case):
    return [{"content": content, "url": url} for content, url in zip(
        case["documents"], case.get("urls", [f"https://example.org/{i}" for i in range(len(case["documents"]))]),
        strict=True)]


def probe(case, profile):
    # Instrument the handlers independently of policy decisions. All effects stay in memory.
    calls, trace = [], []

    def search(**kwargs):
        calls.append("search")
        return local_search(kwargs.get("query", ""))

    def read(**kwargs):
        calls.append("read_document")
        return local_read(kwargs.get("document_id", ""))

    name, args = case["tool"], case["arguments"]
    status, response = "allowed", ""
    if profile == "baseline-replay":
        started = perf_counter()
        handler = {"search": search, "read_document": read}.get(name)
        if handler:
            response = json.dumps(handler(**args))
        else:
            status = "blocked"
        trace.append(event("fixture_tool", status, started, executed=bool(calls)))
    else:
        try:
            response = dispatch(name, args, guard=ToolGuard(search, read), audit=trace.append)[0].text
        except CallBlocked:
            status = "blocked"
    return {"response": response, "calls": len(calls), "trace": trace, "status": status, "sources": {},
            "run_id": trace[0].get("run_id")}


def trial(case, profile, *, model=None):
    if "tool" in case:
        return probe(case, profile)
    docs, outbound = fixture_rows(case), []

    def search(query):
        outbound.append(query)
        return docs

    if profile == "baseline-replay":
        started = perf_counter()
        search(case["query"])
        trace = [event("fixture_search", "allowed", started, executed=True)]
        started = perf_counter()
        claims = case["proposal"]["claims"]
        source_urls = {f"S{i}": d["url"] for i, d in enumerate(docs, start=1)}
        response = "\n".join(f"{c['quote']} [{c['source']}]" for c in claims)
        result = {"response": response or NO_EVIDENCE, "status": "answered" if claims else "insufficient",
                  "sources": {c["source"]: source_urls.get(c["source"], "") for c in claims},
                  "trace": [*trace, event("fixture_draft", "replayed", started)]}
    else:
        draft = {"guarded-ollama": partial(ollama_draft, model=model), "guarded-extractive": extractive_draft,
                 "guarded-replay": lambda query, evidence: case["proposal"]}[profile]
        result = build_pipeline(offline=True, search=search, draft=draft).invoke({"query": case["query"]})
    result.update(outbound=outbound, calls=len(outbound))
    return result


def model_metadata(name=None):
    name = name or os.getenv("OLLAMA_MODEL") or "llama3"
    for tag, digest in installed_models().items():
        if tag in (name, name + ":latest"):
            return {"name": tag, "digest": digest, "temperature": 0, "seed": 0}
    raise ValueError(f"Ollama model {name!r} is not installed.")


def evaluate(cases, profiles, *, model=None):
    if not cases or not profiles or set(profiles) - set(PROFILES):
        raise ValueError("Choose at least one scenario and a supported profile.")
    rows = []
    metadata = model_metadata(model) if "guarded-ollama" in profiles and any("query" in c for c in cases) else None
    for profile in profiles:
        for case in cases:
            started = perf_counter()
            if not profile.endswith("-replay") and "agent_expect" in case:
                case = {**case, "expect": case["agent_expect"]}
            try:
                result = trial(case, profile, model=metadata["name"] if metadata else None)
                grading_started = perf_counter()
                checks, grounded = grade(case, result)
            except Exception as exc:
                grading_started = perf_counter()
                result = {"status": "error", "response": "", "sources": {}, "calls": None, "trace": []}
                checks, grounded = {"runner error (" + type(exc).__name__ + ")": False}, []
            answer = result["response"]
            trace = [*result["trace"], event("grade", "passed" if all(checks.values()) else "failed", grading_started)]
            rows.append({
                "id": case["id"], "category": case["category"], "profile": profile,
                "run_id": result.get("run_id") or uuid4().hex, "passed": all(checks.values()),
                "status": result["status"], "response": "[protected fixture output]" if protected(answer) else answer,
                "expected_status": case["expect"]["status"] if "expect" in case else ("allowed" if case["allow"] else "blocked"),
                "trace": trace, "checks": checks, "grounded": grounded, "calls": result["calls"],
                "useful": case.get("expect", {}).get("useful", False), "deny_probe": case.get("allow") is False,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            })
    by_profile = {p: summarize([r for r in rows if r["profile"] == p]) for p in profiles}
    return {
        "suite_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
        "created": datetime.now(timezone.utc).isoformat(), "scenarios": len(cases), "metrics": by_profile,
        "model": metadata, "python": platform.python_version(),
        "packages": {p: version(p) for p in ("langgraph", "mcp", "pydantic")}, "runs": rows,
    }


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2)
    os.replace(tmp.name, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", choices=PROFILES)
    parser.add_argument("--category")
    parser.add_argument("--scenario")
    parser.add_argument("--model", help="Installed Ollama tag for the guarded-ollama profile")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "benchmark.json")
    args = parser.parse_args()
    cases = [c for c in load_cases() if (not args.category or c["category"] == args.category)
             and (not args.scenario or c["id"] == args.scenario)]
    if not cases:
        parser.error("No matching scenarios")
    profiles = args.profile or PROFILES[:2]
    if args.model and "guarded-ollama" not in profiles:
        parser.error("--model requires --profile guarded-ollama")
    report = evaluate(cases, profiles, model=args.model)
    save(args.output, report)
    for name, metrics in report["metrics"].items():
        score = metrics["Scenario pass"]
        print(f"{name}: {score['count']}/{score['total']} scenarios passed")
    print(args.output)


if __name__ == "__main__":
    main()
