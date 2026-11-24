from concurrent.futures import ThreadPoolExecutor
import pytest

from agentguard.workspace.store import Conflict, QueueFull, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "workspace.db", capacity=1)
    s.initialize()
    return s


def test_concurrent_retries_capacity_and_backup(store, tmp_path):
    spec = {"kind": "research", "query": "map"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: store.submit(spec, "repeat"), range(12)))
    assert len(set(ids)) == 1
    with pytest.raises(Conflict):
        store.submit({**spec, "query": "changed"}, "repeat")
    with pytest.raises(QueueFull):
        store.submit(spec)
    store.backup(tmp_path / "snapshot.db")
    restored = Store(tmp_path / "snapshot.db")
    assert restored.get(ids[0]) == store.get(ids[0])
    with pytest.raises(FileExistsError):
        store.backup(tmp_path / "snapshot.db")


def test_recovery_fences_stale_completion_and_cancel(store):
    key = store.submit({"kind": "evaluation"})
    _, stale, _ = store.claim(lease=-1)
    _, active, _ = store.claim()
    assert not store.advance(key, stale, 1)
    assert not store.finish(key, stale, {"wrong": True})
    assert store.renew(key, active)
    assert store.cancel(key) and store.cancel(key)
    assert not store.finish(key, active, {})
    assert [e["state"] for e in store.get(key)["events"]] == ["queued", "running", "reclaimed", "cancelled"]


def test_lost_worker_exhausts_retry_budget(store):
    key = store.submit({"kind": "research"})
    store.claim(lease=-1)
    assert store.claim(max_attempts=1) is None
    assert store.get(key)["error"] == "worker_lost"


def test_pruning_only_removes_terminal_records_and_related_events(store):
    import time
    old = store.submit({"kind": "research"})
    store.cancel(old)
    active = store.submit({"kind": "research"})
    cutoff = time.time() + 1
    assert store.prune(cutoff) == 1 and store.get(old)
    assert store.prune(cutoff, apply=True) == 1 and store.get(old) is None
    assert store.get(active)["status"] == "queued"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (old,)).fetchone()[0] == 0
