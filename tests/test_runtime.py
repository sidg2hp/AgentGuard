import pytest

from agentguard.evidence import release
from agentguard.policy import CallBlocked, ToolGuard
from pipeline import build_pipeline


def test_refuses_before_dispatch():
    trace = []
    guard = ToolGuard(lambda query: pytest.fail("must not dispatch"))
    for actor, name, args in [("draft", "search", {"query": "q"}),
                              ("research", "search", {"query": "q", "admin": True}),
                              ("research", "unknown", {}),
                              ("research", "search", {"query": "AG_PRIVATE_DEMO"})]:
        with pytest.raises(CallBlocked):
            guard.call(actor, name, args, trace)
    assert len(trace) == 4
    assert all(not item["executed"] for item in trace)
    assert "AG_PRIVATE_DEMO" not in str(trace)


def test_run_isolation_and_input_projection():
    app = build_pipeline(offline=True)
    first = app.invoke({"query": "What do research agents do?", "trace": ["untrusted"], "status": "answered"})
    assert first["status"] == "answered"
    assert first["sources"] and first["trace"][0]["stage"] == "input"
    second = app.invoke({"query": " "})
    assert second["status"] == "invalid" and len(second["trace"]) == 1
    assert first["run_id"] != second["run_id"]


def test_provider_failure_hides_exception():
    def broken(query):
        raise RuntimeError("private provider details")
    result = build_pipeline(search=broken).invoke({"query": "question"})
    assert result["status"] == "error"
    assert "private provider details" not in str(result)


def test_cannot_cite_absent_or_partial_evidence():
    docs = {"S1": {"url": "", "excerpts": ["This item is unavailable."]}}
    for source, quote in [("S2", "This item is unavailable."), ("S1", "available.")]:
        assert release({"claims": [{"source": source, "quote": quote}]}, docs)[1] == "blocked"


def test_redacts_before_model_and_checks_after_generation():
    def draft(query, docs):
        assert "AG_PRIVATE_TEST" not in str(docs)
        return {"claims": [{"source": "S1", "quote": "AG_PRIVATE_TEST"}]}
    rows = [{"content": "Public fact. AG_PRIVATE_TEST", "url": "https://example.org/?s=AG_PRIVATE_TEST"}]
    result = build_pipeline(search=lambda query: rows, draft=draft).invoke({"query": "fact"})
    assert result["status"] == "blocked" and "AG_PRIVATE_TEST" not in str(result)
