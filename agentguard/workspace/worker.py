"""Run one fenced job at a time. Heartbeats continue during blocking provider calls."""
import logging
import os
import threading
import time
from uuid import uuid4

from agentguard.pipeline import build_pipeline
from agentguard.providers import extractive_draft, local_search, ollama_draft, tavily_search
from agentguard.workspace.models import Evaluation, Research
from evaluation.runner import evaluate, load_cases, suite_fingerprint

log = logging.getLogger(__name__)


def selected_cases(spec, pack=None):
    cases = load_cases()
    if pack:
        for i, check in enumerate(pack.get("checks", []), start=1):
            cases.append({"id": f"PACK-{i:02}", "category": "acceptance", "pack_id": spec.pack_id,
                          "query": check["query"], "documents": [d["content"] for d in pack["documents"]],
                          "urls": [d["url"] for d in pack["documents"]],
                          "expect": {"status": check["status"], "required": [check["contains"]] if check["contains"] else [],
                                     "required_source": f"S{check['document']}" if check["document"] else None,
                                     "forbidden": [], "useful": check["status"] == "answered"}})
    return [case for case in cases if spec.category in (None, case["category"])
            and spec.scenario in (None, case["id"])]


def check_capabilities(spec, settings, cases=None):
    if isinstance(spec, Research):
        if spec.source == "web" and (not settings.allow_web or not os.getenv("TAVILY_API_KEY")):
            raise PermissionError("Web search is disabled or unconfigured.")
        uses_model = spec.drafter == "ollama"
    else:
        uses_model = "guarded-ollama" in spec.profiles and any("query" in c for c in cases or [])
    if uses_model and not settings.allow_model:
        raise PermissionError("Model execution is disabled.")


class ClaimLost(Exception):
    pass


class SuiteChanged(Exception):
    pass


def execute(store, settings, body, progress):
    if body["kind"] == "evaluation":
        expected = body.pop("_suite_sha256", None)
        spec = Evaluation.model_validate(body)
        cases = selected_cases(spec, store.pack(spec.pack_id) if spec.pack_id else None)
        check_capabilities(spec, settings, cases)
        if expected and expected != suite_fingerprint(cases):
            raise SuiteChanged()
        return evaluate(cases, spec.profiles, on_trial=progress)
    spec = Research.model_validate(body)
    check_capabilities(spec, settings)
    search = tavily_search if spec.source == "web" else local_search
    pack = None
    if spec.pack_id:
        pack = store.pack(spec.pack_id)
        if pack is None:
            raise ValueError("unknown_pack")
        def search(query):
            return pack["documents"]
    draft = ollama_draft if spec.drafter == "ollama" else extractive_draft
    result = build_pipeline(search=search, draft=draft, secrets=settings.protected_values()).invoke({"query": spec.query})
    if pack:
        result["citations"] = [{"source": source, "pack_id": spec.pack_id, "document": int(source[1:]),
                                "title": pack["documents"][int(source[1:]) - 1]["title"]}
                               for source in result["sources"]]
    return result


class Worker:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.identity = uuid4().hex

    def once(self):
        job = self.store.claim()
        if not job:
            return False
        run_id, fence, body = job
        lost, done = threading.Event(), threading.Event()

        def renew():
            while not done.wait(8):
                try:
                    self.store.heartbeat(self.identity)
                    if self.store.renew(run_id, fence):
                        continue
                except Exception:
                    log.error("Worker heartbeat failed for run %s", run_id)
                lost.set()
                return

        def progress(count):
            if lost.is_set() or not self.store.advance(run_id, fence, count):
                raise ClaimLost()

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            result = execute(self.store, self.settings, body, progress)
            error = "provider_error" if body["kind"] == "research" and result["status"] == "error" else None
            self.store.finish(run_id, fence, result=result, error=error)
        except ClaimLost:
            pass
        except SuiteChanged:
            self.store.finish(run_id, fence, error="suite_changed")
        except Exception as exc:
            log.error("Run %s failed (%s)", run_id, type(exc).__name__)
            self.store.finish(run_id, fence, error="execution_failed")
        finally:
            done.set()
            thread.join()
        return True

    def run(self, stop):
        last_seen = 0
        try:
            while not stop.is_set():
                if time.monotonic() - last_seen > 5 or last_seen == 0:
                    self.store.heartbeat(self.identity)
                    last_seen = time.monotonic()
                if not self.once():
                    stop.wait(0.5)
        finally:
            with self.store.connection(True) as db:
                db.execute("DELETE FROM workers WHERE id=?", (self.identity,))
