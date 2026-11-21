import time

import pytest
from fastapi.testclient import TestClient

from forge.api import create_app
from forge.chaos import Chaos
from forge.orchestrator import Orchestrator
from forge.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "forge.db")
    orch = Orchestrator(store, home=tmp_path, chaos=Chaos(), mock_latency_ms=0)
    with TestClient(create_app(store, orch, home=tmp_path, resume=False)) as c:
        c.orch = orch
        yield c
    store.close()


def wait_state(client, rid, states, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/runs/{rid}").json()
        if run["state"] in states and not run["running"]:
            return run
        time.sleep(0.2)
    raise AssertionError(f"timed out; state={run['state']}")


def test_meta_and_health(client):
    assert client.get("/api/health").json() == {"ok": True}
    meta = client.get("/api/meta").json()
    assert {s["id"] for s in meta["scenarios"]} >= {"REQ-PAY-001", "REQ-INV-001"}
    assert meta["modes"]["mock"] is True
    assert {t["name"] for t in meta["targets"]} == {"payments-service", "inventory-service"}


def test_full_run_through_api(client):
    r = client.post("/api/runs", json={"scenario_id": "REQ-INV-001", "requested_by": "sam"})
    assert r.status_code == 201
    rid = r.json()["id"]
    run = wait_state(client, rid, {"GATE_MERGE"})
    assert run["data"]["wait"]["artifact_hash"]
    events = client.get(f"/api/runs/{rid}/events").json()
    assert events[0]["type"] == "run_created"
    assert client.get(f"/api/runs/{rid}/events?after={events[-1]['seq']}").json() == []
    assert client.get(f"/api/runs/{rid}/artifacts/diff").json()["content"]["files"]
    assert "# Evidence pack" in client.get(f"/api/runs/{rid}/evidence.md").text
    assert client.get(f"/api/runs/{rid}/verify").json()["ok"]
    calls = client.get(f"/api/runs/{rid}/llm_calls").json()
    assert calls and "request_json" not in calls[0]
    one = client.get(f"/api/runs/{rid}/llm_calls/{calls[0]['id']}").json()
    assert one["request"][0]["role"] == "system"

    bad = client.post(f"/api/runs/{rid}/decision", json={"action": "approve", "actor": "sam"})
    assert bad.status_code == 409 and "separation of duties" in bad.json()["detail"]
    ok = client.post(f"/api/runs/{rid}/decision", json={"action": "approve", "actor": "alice"})
    assert ok.status_code == 200
    wait_state(client, rid, {"GATE_DEPLOY"})
    done = client.post(f"/api/runs/{rid}/decision", json={"action": "approve", "actor": "bob"}).json()
    assert done["state"] == "DEPLOYED"


def test_stream_returns_events(client):
    rid = client.post("/api/runs", json={"scenario_id": "REQ-INV-001"}).json()["id"]
    wait_state(client, rid, {"GATE_MERGE"})
    seen = []
    with client.stream("GET", f"/api/runs/{rid}/stream") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                seen.append(line)
            if len(seen) >= 3:
                break
    assert len(seen) == 3


def test_bad_requests(client):
    assert client.post("/api/runs", json={"requirement": "x", "target": "payments-service"}).status_code == 400
    assert client.get("/api/runs/nope").status_code == 404
    assert client.post("/api/runs", json={"scenario_id": "REQ-NOPE"}).status_code == 400


def test_chaos_endpoints(client):
    s = client.post("/api/chaos", json={"provider": "mock-primary", "down": True}).json()
    assert s["providers_down"] == ["mock-primary"]
    s = client.post("/api/chaos", json={"switch": "corrupt_next", "count": 2}).json()
    assert s["corrupt_next"] == 2
    s = client.post("/api/chaos", json={"reset": True}).json()
    assert s["providers_down"] == [] and s["corrupt_next"] == 0
    assert client.post("/api/chaos", json={"switch": "rm_rf"}).status_code == 422


def test_localhost_and_csrf_protection(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    form = client.post("/api/chaos", content="reset=1", headers={"content-type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415
    xo = client.post("/api/chaos", json={"reset": True}, headers={"origin": "https://evil.example"})
    assert xo.status_code == 403
    same = client.post("/api/chaos", json={"reset": True}, headers={"origin": "http://localhost:5173"})
    assert same.status_code == 200
