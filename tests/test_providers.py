import json

import httpx
from agentguard.catalog import catalog_text
from agentguard.evidence import collect
from agentguard.providers import extractive_draft, local_search

from pipeline import build_pipeline


def test_live_adapter_contracts(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-key")
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url == "https://api.tavily.com/search":
            assert kwargs["headers"]["Authorization"] == "Bearer test-only-key"
            body = {"results": [{"content": "Maps use scales.", "url": "https://example.org/maps"}]}
        else:
            assert url == "http://127.0.0.1:11434/api/chat"
            payload = kwargs["json"]
            assert payload["format"]["properties"]["selected"]["items"]["enum"] == ["S1.1"]
            prompt = json.loads(payload["messages"][1]["content"])
            assert prompt == {"question": "What do maps use?", "evidence": {"S1.1": "Maps use scales."}}
            body = {"message": {"content": json.dumps({"selected": ["S1.1", "S1.1"]})}}
        return httpx.Response(200, request=httpx.Request("POST", url), json=body)

    monkeypatch.setattr(httpx, "post", post)
    result = build_pipeline().invoke({"query": "What do maps use?"})
    assert len(calls) == 2
    assert result["response"] == "Maps use scales. [S1]"


def test_model_selection_must_exist_in_current_request(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"message": {"content": '{"selected":["absent"]}'}})
    monkeypatch.setattr(httpx, "post", post)
    result = build_pipeline(search=lambda query: [{"content": "A map."}]).invoke({"query": "maps"})
    assert result["status"] == "blocked"
    assert result["trace"][-1]["decision"] == "invalid_selection"


def test_sample_citation_resolves_to_the_bundled_document():
    row, = local_search("rag")
    assert catalog_text(row["url"]) == row["content"]
    assert collect([row])["S1"]["url"] == row["url"]
    assert collect([{**row, "content": "Different content."}])["S1"]["url"] == ""


def test_selection_prefers_specific_matches_and_keeps_complementary_ones():
    docs = collect([{"content": "Blue trails are open. The blue trail has a bridge."},
                    {"content": "The blue trail has a bridge. The ferry has a lift."}])
    claims = extractive_draft("Which blue trails have a bridge or a ferry?", docs)["claims"]
    assert [c["quote"] for c in claims] == ["The blue trail has a bridge.", "The ferry has a lift."]
