"""Exercise a running workspace using a synthetic pack, research run and release gate."""
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    base = os.environ.get("AGENTGUARD_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.getenv("AGENTGUARD_API_TOKEN") or Path(os.getenv("AGENTGUARD_TOKEN_FILE", ".agentguard/token")).read_text().strip()

    def api(path, data=None, key=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        request = Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def run(spec):
        key = uuid4().hex
        receipt = api("/v1/runs", spec, key)
        assert receipt == api("/v1/runs", spec, key)
        until = time.monotonic() + 120
        while time.monotonic() < until:
            row = api("/v1/runs/" + receipt["id"])
            if row["status"] == "completed":
                return row
            if row["status"] in {"failed", "cancelled"}:
                raise RuntimeError(f"Job {row['id']} ended as {row['status']}")
            time.sleep(0.3)
        raise TimeoutError("Workspace job timed out")

    assert api("/readyz")["status"] == "ready"
    pack = api("/v1/packs", {"name":"Archive handbook", "documents":[
        {"title":"Archive hours", "content":"The archive opens at noon."}]})
    research = run({"kind":"research", "query":"When does the archive open?", "source":"pack", "pack_id":pack["id"]})
    assert research["result"]["response"] == "The archive opens at noon. [S1]"
    assert research["result"]["citations"][0]["pack_id"] == pack["id"]
    experiment = run({"kind":"evaluation", "profiles":["guarded-replay"]})
    gate = api("/v1/gates", {"run_id":experiment["id"]})
    assert gate["verdict"] == "pass" and experiment["progress"] == 30
    print(json.dumps({"research":research["id"], "evaluation":experiment["id"], "release_gate":gate["verdict"]}))


if __name__ == "__main__":
    main()
