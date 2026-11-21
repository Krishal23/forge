import sqlite3

import pytest

from forge.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    s.create_run("run_1", "payments-service", "req", "mock", "INTAKE")
    yield s
    s.close()


def test_run_update_merges_data(store):
    store.update_run("run_1", state="ANALYSIS", branch="forge/run_1")
    store.update_run("run_1", fix_iteration=2)
    run = store.get_run("run_1")
    assert run["state"] == "ANALYSIS"
    assert run["data"] == {"branch": "forge/run_1", "fix_iteration": 2}


def test_event_chain_verifies(store):
    for i in range(5):
        store.append_event("run_1", "step", {"i": i}, stage="ANALYSIS")
    assert store.verify_chain("run_1") == (True, None)


def test_event_chain_detects_tampering(store):
    for i in range(4):
        store.append_event("run_1", "step", {"i": i})
    # Simulate someone editing history directly in the database.
    store._x("UPDATE events SET data = ? WHERE run_id = ? AND seq = 2", ('{"i":99}', "run_1"))
    ok, bad = store.verify_chain("run_1")
    assert not ok and bad == 2


def test_event_chain_detects_deletion(store):
    for i in range(4):
        store.append_event("run_1", "step", {"i": i})
    store._x("DELETE FROM events WHERE run_id = ? AND seq = 2", ("run_1",))
    assert store.verify_chain("run_1") == (False, 3)


def test_listeners_receive_events_and_failures_are_isolated(store):
    seen = []

    def broken(_):
        raise RuntimeError("listener bug")

    store.subscribe(broken)
    unsubscribe = store.subscribe(seen.append)
    store.append_event("run_1", "hello")
    unsubscribe()
    store.append_event("run_1", "ignored")
    assert [e["type"] for e in seen] == ["hello"]


def test_artifact_versions_and_dedup(store):
    a1 = store.put_artifact("run_1", "diff", {"text": "a"})
    a1_again = store.put_artifact("run_1", "diff", {"text": "a"})
    a2 = store.put_artifact("run_1", "diff", {"text": "b"})
    assert (a1["version"], a1_again["version"], a2["version"]) == (1, 1, 2)
    assert store.get_artifact("run_1", "diff")["content"] == {"text": "b"}
    assert store.get_artifact("run_1", "diff", version=1)["content"] == {"text": "a"}


def test_approval_bound_to_artifact_hash(store):
    art = store.put_artifact("run_1", "diff", {"text": "reviewed"})
    approval = store.record_approval(
        "run_1", "GATE_MERGE", "approve", "alice", artifact_name="diff", artifact_hash=art["hash"]
    )
    assert store.approval_valid(approval)
    store.put_artifact("run_1", "diff", {"text": "changed after review"})
    assert not store.approval_valid(approval)


def test_once_runs_side_effect_once_and_logs_duplicate(store):
    calls = []

    def side_effect():
        calls.append(1)
        return {"applied": True}

    r1 = store.once("run_1", "apply_patch", {"diff": "x"}, side_effect)
    r2 = store.once("run_1", "apply_patch", {"diff": "x"}, side_effect)
    r3 = store.once("run_1", "apply_patch", {"diff": "y"}, side_effect)
    assert r1 == r2 == r3 == {"applied": True}
    assert len(calls) == 2
    assert [e["type"] for e in store.events("run_1")] == ["duplicate_suppressed"]


def test_once_handles_none_result(store):
    calls = []
    store.once("run_1", "noop", {}, lambda: calls.append(1))
    store.once("run_1", "noop", {}, lambda: calls.append(1))
    assert len(calls) == 1


def test_usage_summary(store):
    base = {"run_id": "run_1", "stage": "ANALYSIS", "provider": "gemini", "model": "m", "attempt": 1, "cache_hit": 0}
    store.record_llm_call(agent="analyst", status="ok", prompt_tokens=100, completion_tokens=50,
                          latency_ms=200, cost_usd=0.01, **base)
    store.record_llm_call(agent="analyst", status="error", error="503", latency_ms=50, **base)
    store.record_llm_call(agent="developer", status="ok", prompt_tokens=300, completion_tokens=100,
                          latency_ms=800, cost_usd=0.02, **{**base, "cache_hit": 1})
    s = store.usage_summary("run_1")
    assert s["calls"] == 3 and s["errors"] == 1 and s["cache_hits"] == 1
    assert s["prompt_tokens"] == 400 and s["cost_usd"] == pytest.approx(0.03)
    assert s["by_agent"]["analyst"]["errors"] == 1


def test_file_store_survives_reopen(tmp_path):
    path = tmp_path / "forge.db"
    s = Store(path)
    s.create_run("run_x", "t", "r", "mock", "DESIGN")
    s.append_event("run_x", "checkpoint", {"stage": "DESIGN"})
    s.close()
    reopened = Store(path)
    assert reopened.get_run("run_x")["state"] == "DESIGN"
    assert reopened.verify_chain("run_x") == (True, None)
    reopened.close()


def test_duplicate_run_id_rejected(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.create_run("run_1", "t", "r", "mock", "INTAKE")
