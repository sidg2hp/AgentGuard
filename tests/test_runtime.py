import pytest

from agentguard.evidence import release
from agentguard.policy import CallBlocked, ToolGuard
from pipeline import build_pipeline


@pytest.mark.parametrize("bad_search", [False, True])
def test_malformed_retrieval_stops_at_its_tool_boundary(bad_search):
    search = lambda query: None if bad_search else []
    app = build_pipeline(search=search, retrieve=lambda query: {"unexpected": "shape"})
    result = app.invoke({"query": "trail"})
    failure = result["trace"][-1]
    assert result["status"] == "error"
    assert failure["tool"] == ("search" if bad_search else "retrieve_context")
    assert failure["reason"] == "invalid_tool_result"


def test_budget_never_turns_partial_sentences_into_evidence():
    from agentguard.evidence import excerpts

    paragraph = "A trail is open. A trail is open only on Tuesdays."
    assert excerpts(paragraph, limit=31) == ["A trail is open."]
    assert excerpts(paragraph, limit=5) == []
    assert excerpts(paragraph, limit=len("A trail is open.")) == ["A trail is open."]


def test_drafter_cannot_change_the_evidence_used_to_validate_its_claim():
    def draft(question, evidence):
        evidence["S1"]["excerpts"].append("Revised claim.")
        return {"claims": [{"source": "S1", "quote": "Revised claim."}]}

    app = build_pipeline(search=lambda query: [{"content": "Original claim."}], draft=draft)
    assert app.invoke({"query": "claim"})["status"] == "blocked"


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
