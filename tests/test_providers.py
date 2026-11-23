import json

import httpx

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
