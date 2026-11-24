"""Authenticated workspace HTTP interface and same-origin operator console."""
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from agentguard.policy import protected
from agentguard.workspace.config import Settings
from agentguard.workspace.gates import assess
from agentguard.workspace.models import (
    Contract, Evaluation, EvidencePack, PackSummary, Profile, Receipt, RunDetail, RunSummary, SavedPack, Submission,
)
from agentguard.workspace.store import Conflict, QueueFull, Store
from agentguard.workspace.worker import check_capabilities, selected_cases
from evaluation.runner import PROFILES, load_cases, suite_fingerprint

MAX_BODY = 128 * 1024


class Boundary:
    """Authenticate before parsing JSON, including requests without a Content-Length."""
    def __init__(self, app, token):
        self.app, self.token = app, token.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        async def headers(message):
            if message["type"] == "http.response.start":
                message["headers"] += [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                                       (b"referrer-policy", b"no-referrer"),
                                       (b"content-security-policy", b"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")]
            await send(message)
        if scope["path"].startswith("/v1/"):
            auth = dict(scope["headers"]).get(b"authorization", b"")
            scheme, _, key = auth.partition(b" ")
            if scheme.lower() != b"bearer" or not compare_digest(key, self.token):
                return await JSONResponse({"detail": "Workspace token required."}, 401,
                                          headers={"WWW-Authenticate": "Bearer"})(scope, receive, headers)
            if scope["method"] == "POST":
                buffer = bytearray()
                while True:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        return
                    chunk = msg.get("body", b"")
                    if len(buffer) + len(chunk) > MAX_BODY:
                        return await JSONResponse({"detail": "Request exceeds 128 KiB."}, 413)(scope, receive, headers)
                    buffer.extend(chunk)
                    if not msg.get("more_body"):
                        break
                original = receive
                async def replay():
                    nonlocal buffer
                    if buffer is None:
                        return await original()
                    body, buffer = bytes(buffer), None
                    return {"type": "http.request", "body": body, "more_body": False}
                receive = replay
        await self.app(scope, receive, headers)


class GateRequest(Contract):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    baseline_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    profile: Profile = "guarded-replay"
    minimum_pass_rate: float = Field(default=0.9, ge=0, le=1, allow_inf_nan=False)


def create_app(settings=None):
    settings = settings or Settings.load()
    store = Store(settings.database, capacity=settings.capacity)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(title="AgentGuard Workspace", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(Boundary, token=settings.token)
    api = APIRouter(prefix="/v1", dependencies=[Depends(HTTPBearer(auto_error=False))])

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Validation errors can contain original inputs. Never reflect private document text.
        return JSONResponse({"detail": "Invalid request. Check the fields against /openapi.json."}, 422)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(QueueFull)
    async def busy(request, exc):
        return JSONResponse({"detail": "Queue capacity reached."}, 429, headers={"Retry-After": "5"})

    def check_content(data):
        if isinstance(data, dict):
            for value in data.values():
                check_content(value)
        elif isinstance(data, list):
            for value in data:
                check_content(value)
        elif isinstance(data, str) and protected(data, settings.protected_values()):
            raise HTTPException(422, "Protected value in submission.")

    @app.get("/healthz", tags=["operations"])
    def health():
        return {"status": "alive"}

    @app.get("/readyz", tags=["operations"])
    def ready():
        if not store.stats()["workers"]:
            raise HTTPException(503, "Waiting for a worker heartbeat.")
        return {"status": "ready"}

    @api.get("/workspace")
    def overview():
        reason = settings.web_unavailable_reason
        return {**store.stats(), "web_enabled": reason is None, "web_unavailable_reason": reason,
                "model_enabled": settings.allow_model,
                "profiles": PROFILES}

    @api.get("/scenarios")
    def scenarios():
        return [{"id": c["id"], "category": c["category"]} for c in load_cases()]

    @api.get("/metrics", response_class=PlainTextResponse, tags=["operations"])
    def metrics():
        stats = store.stats()
        lines = ["# TYPE agentguard_workers gauge", f"agentguard_workers {stats['workers']}",
                 "# TYPE agentguard_queue_capacity gauge", f"agentguard_queue_capacity {stats['capacity']}",
                 "# TYPE agentguard_runs gauge"]
        for state in ("queued", "running", "completed", "failed", "cancelled"):
            lines.append(f'agentguard_runs{{state="{state}"}} {stats["runs"].get(state, 0)}')
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @api.post("/packs", status_code=201)
    def put_pack(pack: EvidencePack):
        body = pack.model_dump()
        if not body["checks"]:
            body.pop("checks")  # Keep the existing content address for document-only packs.
        check_content(body)
        return {"id": store.put_pack(body), "documents": len(pack.documents)}

    @api.get("/packs", response_model=list[PackSummary])
    def list_packs():
        return store.packs()

    @api.get("/packs/{pack_id}", response_model=SavedPack)
    def get_pack(pack_id: str):
        pack = store.pack(pack_id)
        if pack is None:
            raise HTTPException(404, "Unknown evidence pack.")
        return {"id": pack_id, **pack}

    @api.post("/runs", status_code=202, response_model=Receipt)
    def submit(spec: Submission, idempotency_key: Annotated[str | None, Header(max_length=128, min_length=1)] = None):
        body = spec.model_dump()
        check_content(body)
        total = 1
        fingerprint = None
        pack = store.pack(spec.pack_id) if spec.pack_id else None
        if spec.pack_id and pack is None:
            raise HTTPException(404, "Unknown evidence pack.")
        cases = None
        if isinstance(spec, Evaluation):
            if pack and not pack.get("checks"):
                raise HTTPException(422, "This evidence pack has no acceptance checks.")
            cases = selected_cases(spec, pack)
            total = len(cases) * len(spec.profiles)
            fingerprint = suite_fingerprint(cases)
            if not total:
                raise HTTPException(422, "No matching evaluation scenarios.")
        try:
            check_capabilities(spec, settings, cases)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        key = store.submit(body, idempotency_key, total, fingerprint)
        return JSONResponse({"id": key}, status_code=202, headers={"Location": f"/v1/runs/{key}"})

    @api.get("/runs", response_model=list[RunSummary])
    def list_runs(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                  status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None):
        return store.list_runs(limit=limit, offset=offset, status=status)

    @api.get("/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str):
        row = store.get(run_id)
        if row is None:
            raise HTTPException(404, "Unknown run.")
        return row

    @api.post("/runs/{run_id}/cancel")
    def cancel(run_id: str):
        if not store.cancel(run_id):
            raise HTTPException(404, "Unknown run.")
        return {"id": run_id, "status": "cancelled"}

    @api.post("/gates")
    def gate(spec: GateRequest):
        def report(key):
            row = get_run(key)
            if row["kind"] != "evaluation" or row["status"] != "completed":
                raise HTTPException(409, "A gate requires a completed evaluation.")
            if spec.profile not in row["result"]["metrics"]:
                raise HTTPException(422, "Profile missing from the evaluation.")
            return row["result"]
        try:
            decision = assess(report(spec.run_id), spec.profile, spec.minimum_pass_rate,
                              report(spec.baseline_id) if spec.baseline_id else None)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"run_id": spec.run_id, "baseline_id": spec.baseline_id, **decision}

    app.include_router(api)
    app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="console")
    return app
