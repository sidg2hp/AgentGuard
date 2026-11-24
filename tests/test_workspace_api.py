import pytest
from fastapi.testclient import TestClient

from agentguard.workspace.api import create_app
from agentguard.workspace.config import Settings
from agentguard.workspace.store import Store
from agentguard.workspace.worker import Worker


@pytest.fixture
def workspace(tmp_path):
    config = Settings(tmp_path / "workspace.db", "a" * 40)
    with TestClient(create_app(config)) as client:
        client.headers["Authorization"] = "Bearer " + config.token
        yield client, Worker(Store(config.database), config)


def test_protected_api_pack_research_and_restart(workspace):
    client, worker = workspace
    assert client.get("/").status_code == 200
    assert client.get("/readyz").status_code == 503
    assert client.get("/v1/runs", headers={"Authorization":"invalid"}).status_code == 401
    assert client.post("/v1/packs", content=b"x" * 131073).status_code == 413
    protected = client.post("/v1/runs", json={"kind":"research", "query":"AG_PRIVATE_TEST"})
    assert protected.status_code == 422 and "AG_PRIVATE_TEST" not in protected.text
    assert not client.get("/v1/runs").json()
    pack = {"name":"Archive", "documents":[{"title":"Reading room", "content":"The archive opens at noon."}]}
    key = client.post("/v1/packs", json=pack).json()["id"]
    assert client.post("/v1/packs", json=pack).json()["id"] == key
    spec = {"kind":"research","source":"pack","pack_id":key,"query":"When does the archive open?"}
    receipt = client.post("/v1/runs", json=spec, headers={"Idempotency-Key":"unique"})
    assert receipt.status_code == 202
    assert client.post("/v1/runs", json=spec, headers={"Idempotency-Key":"unique"}).json() == receipt.json()
    assert client.post("/v1/runs", json={**spec, "query":"Changed"}, headers={"Idempotency-Key":"unique"}).status_code == 409
    worker.store.heartbeat(worker.identity)
    assert client.get("/readyz").status_code == 200
    assert worker.once()
    url = receipt.headers["location"]
    row = client.get(url).json()
    assert row["status"] == "completed" and row["result"]["status"] == "answered"
    assert row["result"]["citations"] == [{"source":"S1","pack_id":key,"document":1,"title":"Reading room"}]
    assert client.post(url + "/cancel").status_code == 409
    with TestClient(create_app(worker.settings)) as reopened:
        assert reopened.get(url, headers=client.headers).json() == row


def test_evaluations_and_release_gates(workspace):
    client, worker = workspace
    def evaluate(category=None):
        receipt = client.post("/v1/runs", json={"kind":"evaluation","category":category,"profiles":["guarded-replay"]})
        assert receipt.status_code == 202
        assert worker.once()
        return receipt.json()["id"]
    a, b, partial = evaluate(), evaluate(), evaluate("normal")
    def gate(key, **fields):
        return client.post("/v1/gates", json={"run_id":key,**fields})
    decision = gate(b, baseline_id=a).json()
    assert decision["verdict"] == "pass" and decision["changes"] == []
    assert gate(b, minimum_pass_rate=1).json()["verdict"] == "fail"
    assert gate(partial).json()["verdict"] == "inconclusive"
    assert gate(b, baseline_id=partial).status_code == 409
    row = client.get("/v1/runs/" + b).json()
    assert row["progress"] == row["total"] == 30
    assert len(row["result"]["runs"]) == 30


def test_workspace_admission_and_health_contract(workspace):
    client, _ = workspace
    posts = [
        ({"kind":"research","query":"books","source":"web"},403),
        ({"kind":"research","query":"books","drafter":"ollama"},403),
        ({"kind":"research","query":"books","source":"pack","pack_id":"1"*64},404),
        ({"kind":"evaluation","category":"unlisted"},422),
        ({"kind":"evaluation","profiles":["guarded-replay","guarded-replay"]},422),
    ]
    for spec, status in posts:
        assert client.post("/v1/runs", json=spec).status_code == status
    assert "/v1/gates" in client.get("/openapi.json").json()["paths"]
    assert client.get("/healthz").json() == {"status":"alive"}
    assert 'agentguard_runs{state="queued"} 0' in client.get("/v1/metrics").text


def test_worker_refuses_different_fixtures_than_were_queued(workspace):
    client, worker = workspace
    key = worker.store.submit({"kind":"evaluation"}, total=60, suite_sha256="changed")
    worker.once()
    assert client.get("/v1/runs/" + key).json()["error"] == "suite_changed"


def test_pack_assertions_are_versioned_and_required_for_release(workspace):
    client, worker = workspace
    pack = {"name":"Field notes", "documents":[{"title":"Museum","content":"The museum opens at noon."}],
            "checks":[{"query":"When does the museum open?", "contains":"midnight", "document":1}]}
    pack_id = client.post("/v1/packs", json=pack).json()["id"]
    assert client.get("/v1/packs").json()[0]["checks"] == 1
    spec = {"kind":"evaluation", "profiles":["guarded-extractive"], "pack_id":pack_id}
    receipt = client.post("/v1/runs", json=spec)
    assert receipt.status_code == 202
    worker.once()
    run = client.get(receipt.headers["location"]).json()
    assert run["total"] == run["progress"] == 31
    assert run["suite_sha256"] == run["result"]["suite_sha256"]
    check = run["result"]["runs"][-1]
    assert check["id"] == "PACK-01" and not check["passed"] and check["checks"]["expected citation"]
    decision = client.post("/v1/gates", json={"run_id":run["id"], "profile":"guarded-extractive",
                                              "minimum_pass_rate":0}).json()
    assert decision["verdict"] == "fail"
    pack["checks"][0]["contains"] = "noon"
    assert client.post("/v1/packs", json=pack).json()["id"] != pack_id
    assert client.post("/v1/runs", json={**spec, "profiles":["baseline-replay"]}).status_code == 422
