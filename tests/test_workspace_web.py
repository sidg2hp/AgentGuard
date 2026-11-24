import sys

from fastapi.testclient import TestClient
import pytest

from agentguard.__main__ import main
from agentguard.workspace.api import create_app
from agentguard.workspace.config import Settings
from agentguard.workspace.store import Store
from agentguard.workspace.worker import Worker


@pytest.mark.parametrize(("flag", "key", "reason"), [
    (None, "", "missing_key"),
    ("1", "   ", "missing_key"),
    ("0", "test-key", "disabled"),
    (None, "test-key", None),
])
def test_web_capability_matches_job_admission(monkeypatch, tmp_path, flag, key, reason):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "a" * 40)
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "db"))
    monkeypatch.setenv("TAVILY_API_KEY", key)
    monkeypatch.delenv("AGENTGUARD_ALLOW_WEB", raising=False)
    if flag is not None:
        monkeypatch.setenv("AGENTGUARD_ALLOW_WEB", flag)
    settings = Settings.load()
    calls = []

    def search(query):
        calls.append(query)
        return [{"content": "The archive opens at noon.", "url": "https://example.org/archive"}]

    monkeypatch.setattr("agentguard.workspace.worker.tavily_search", search)
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        info = client.get("/v1/workspace").json()
        assert info["web_unavailable_reason"] == reason
        assert info["web_enabled"] == (reason is None)
        body = {"kind": "research", "source": "web", "query": "When does the archive open?"}
        receipt = client.post("/v1/runs", json=body)
        if reason:
            assert receipt.status_code == 403 and not calls
        else:
            assert receipt.status_code == 202
            assert Worker(Store(settings.database), settings).once()
            result = client.get(receipt.headers["location"]).json()["result"]
            assert result["sources"] == {"S1": "https://example.org/archive"}
            assert calls == [body["query"]]


def test_cli_loads_local_key_and_preserves_operator_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "db"))
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "a" * 40)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("AGENTGUARD_ALLOW_WEB", "0")
    (tmp_path / ".env").write_text("TAVILY_API_KEY=local-test-key\nAGENTGUARD_ALLOW_WEB=1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["agentguard", "init"])
    main()
    settings = Settings.load()
    assert "local-test-key" in settings.protected_values()
    assert settings.web_unavailable_reason == "disabled"
