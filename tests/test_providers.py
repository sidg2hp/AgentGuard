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
            assert payload["format"]["properties"]["claims"]
            assert json.loads(payload["messages"][1]["content"])["question"] == "What do maps use?"
            body = {"message": {"content": json.dumps({"claims": [{"source": "S1", "quote": "Maps use scales."}]})}}
        return httpx.Response(200, request=httpx.Request("POST", url), json=body)

    monkeypatch.setattr(httpx, "post", post)
    result = build_pipeline().invoke({"query": "What do maps use?"})
    assert len(calls) == 2
    assert result["response"] == "Maps use scales. [S1]"
