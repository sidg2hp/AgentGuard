"""Single research workflow used by the UI, compatibility API and benchmark."""
import os
from copy import deepcopy
from time import perf_counter
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agentguard.evidence import BLOCKED, NO_EVIDENCE, STOPPED, collect, release
from agentguard.policy import CallBlocked, Search, ToolFailure, ToolGuard, event, protected
from agentguard.providers import extractive_draft, local_search, ollama_draft, tavily_search


class Request(TypedDict):
    query: str


class Result(TypedDict):
    response: str
    status: str
    sources: dict
    trace: list
    run_id: str


class State(Request, Result, total=False):
    rows: list
    evidence: dict
    candidate: Any


def build_pipeline(*, offline=False, search=None, draft=None, secrets=(), retrieve=None):
    """Custom providers are trusted application dependencies, supplied at construction."""
    search = search or (local_search if offline else tavily_search)
    draft = draft or (extractive_draft if offline else ollama_draft)
    secrets = tuple(secrets) + ((os.getenv("TAVILY_API_KEY") or ""),)
    guard = ToolGuard(search, secrets=secrets, retrieve=retrieve)

    def validate(state):
        started = perf_counter()
        try:
            query = Search(query=state.get("query")).query
            status = "blocked" if protected(query, secrets) else "continue"
        except ValidationError:
            query, status = "", "invalid"
        return {"query": query, "run_id": uuid4().hex, "status": status,
                "response": STOPPED, "sources": {}, "trace": [event("input", status, started)]}

    def research(state):
        trace = list(state["trace"])
        try:
            rows = guard.call("research", "search", {"query": state["query"]}, trace)
            if retrieve is not None:
                context = guard.call("research", "retrieve_context", {"query": state["query"]}, trace)
                rows = [*rows[:3], *context[:2]]
        except CallBlocked:
            return {"status": "blocked", "trace": trace}
        except ToolFailure:
            return {"status": "error", "trace": trace}
        return {"rows": rows, "trace": trace}

    def evidence(state):
        started = perf_counter()
        docs = collect(state["rows"], secrets)
        return {"evidence": docs, "status": "continue" if docs else "insufficient",
                "response": NO_EVIDENCE,
                "trace": [*state["trace"], event("evidence", "validated", started, documents=len(docs))]}

    def compose(state):
        started = perf_counter()
        try:
            candidate = draft(state["query"], deepcopy(state["evidence"]))
        except Exception:
            return {"status": "error", "response": STOPPED,
                    "trace": [*state["trace"], event("draft", "provider_error", started)]}
        return {"candidate": candidate, "trace": [*state["trace"], event("draft", "completed", started)]}

    def output(state):
        started = perf_counter()
        try:
            response, status, sources = release(state["candidate"], state["evidence"], secrets)
        except ValidationError:
            response, status, sources = BLOCKED, "blocked", {}
        return {"response": response, "status": status, "sources": sources,
                "trace": [*state["trace"], event("output", status, started)]}

    graph = StateGraph(State, input_schema=Request, output_schema=Result)
    stages = [("validate", validate), ("research", research), ("evidence", evidence),
              ("compose", compose), ("output", output)]
    graph.add_edge(START, "validate")
    for index, (name, node) in enumerate(stages):
        graph.add_node(name, node)
        if index + 1 < len(stages):
            graph.add_conditional_edges(name, lambda s: s["status"] == "continue",
                                        {True: stages[index + 1][0], False: END})
    graph.add_edge("output", END)
    return graph.compile()
