"""End-to-end orchestrator tests in mock mode: one per failure scenario in the brief."""

import pytest

from forge.chaos import Chaos
from forge.config import Budgets
from forge.orchestrator import DecisionError, Orchestrator
from forge.store import Store


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "forge.db")
    chaos = Chaos()
    orch = Orchestrator(store, home=tmp_path, chaos=chaos, mock_latency_ms=0)
    yield orch, store, chaos
    store.close()


def types(store, rid):
    return [e["type"] for e in store.events(rid)]


def to_merge_gate(orch, rid):
    run = orch.run_sync(rid)
    if run["state"] == "GATE_CRITERIA":
        orch.decide(rid, "approve", "priya", answers="Return the original refund.")
        run = orch.wait_idle(rid)
    return run


def test_payments_full_flow(env):
    orch, store, _ = env
    rid = orch.create_run(scenario_id="REQ-PAY-001", requested_by="priya")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "GATE_CRITERIA" and run["data"]["wait"]["questions"]
    run = to_merge_gate(orch, rid)
    assert run["state"] == "GATE_MERGE"
    t = types(store, rid)
    # Architect/developer disagreement resolved by arbitration
    assert "scope_request" in t and "scope_expanded" in t
    assert "refunds/repository.py" in run["data"]["scope"]
    # Prompt injection blocked: nothing touched auth.py
    rules = {e["data"]["rule"] for e in store.events(rid) if e["type"] == "policy_violation"}
    assert rules == {"out_of_scope", "guarded_call_removed"}
    assert "payments/auth.py" not in {f["path"] for f in store.get_artifact(rid, "diff")["content"]["files"]}
    # Failed tests -> fix loop -> pass; security block -> autofix -> clean
    assert t.index("tests_failed") < t.index("fix_iteration_started") < t.index("security_blocked")
    assert run["data"]["fix_iteration"] == 1 and run["data"]["security_autofix"] == 1
    assert store.get_artifact(rid, "security")["content"]["blocking"] == []
    # Hidden acceptance tests pass
    assert store.get_artifact(rid, "acceptance_eval")["content"]["pass_rate"] == 1.0
    # Human gates
    with pytest.raises(DecisionError, match="separation of duties"):
        orch.decide(rid, "approve", "priya")
    orch.decide(rid, "approve", "alice", comment="evidence reviewed")
    run = orch.wait_idle(rid)
    assert run["state"] == "GATE_DEPLOY"
    run = orch.decide(rid, "approve", "bob")
    assert run["state"] == "DEPLOYED"
    assert store.get_artifact(rid, "deployment")["content"]["smoke_ok"]
    assert store.verify_chain(rid) == (True, None)
    md = store.get_artifact(rid, "evidence")["content"]["markdown"]
    assert "Hidden acceptance tests" in md and "alice" in md


def test_inventory_flow_no_criteria_gate(env):
    orch, store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001", requested_by="sam")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "GATE_MERGE"
    assert "GATE_CRITERIA" not in [e["data"].get("to") for e in store.events(rid) if e["type"] == "state_changed"]
    assert [h["ok"] for h in run["data"]["test_history"]] == [False, True]
    assert store.get_artifact(rid, "acceptance_eval")["content"]["passed"] == 9


def test_malformed_output_is_repaired(env):
    orch, store, chaos = env
    chaos.arm("corrupt_next", 1)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    t = types(store, rid)
    assert "agent_output_invalid" in t and "schema_repaired" in t
    assert run["state"] == "GATE_MERGE"


def test_persistent_malformed_output_escalates(env):
    orch, _store, chaos = env
    chaos.arm("corrupt_next", 10)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "ESCALATED"
    assert run["data"]["escalation"]["reason"] == "agent_output_invalid"
    chaos.reset()
    orch.decide(rid, "retry", "lead")
    assert orch.wait_idle(rid)["state"] == "GATE_MERGE"


def test_primary_provider_down_fails_over(env):
    orch, store, chaos = env
    chaos.set_provider_down("mock-primary")
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    assert orch.run_sync(rid)["state"] == "GATE_MERGE"
    assert "provider_failover" in types(store, rid) or "provider_skipped" in types(store, rid)
    assert store.usage_summary(rid)["providers"] == ["mock-secondary"]


def test_all_providers_down_pauses_then_resumes(env):
    orch, _store, chaos = env
    chaos.set_provider_down("mock-primary")
    chaos.set_provider_down("mock-secondary")
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "ESCALATED" and run["data"]["escalation"]["reason"] == "providers_unavailable"
    assert run["data"]["escalation"]["resume_state"] == "ANALYSIS"
    chaos.reset()
    orch.gateway("mock").breakers["mock-primary"].record_success()
    orch.gateway("mock").breakers["mock-secondary"].record_success()
    orch.decide(rid, "retry", "lead")
    assert orch.wait_idle(rid)["state"] == "GATE_MERGE"


def test_fix_budget_exhausted_escalates(env, tmp_path):
    store = Store(tmp_path / "b.db")
    chaos = Chaos()
    orch = Orchestrator(store, home=tmp_path / "h", chaos=chaos, budgets=Budgets(max_fix_iterations=1),
                        mock_latency_ms=0)
    chaos.arm("fail_tests_next", 5)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "ESCALATED" and run["data"]["escalation"]["reason"] == "fix_budget_exhausted"
    assert run["data"]["test_history"][0]["injected"]


def test_injected_test_failure_handled_without_code_change(env):
    orch, store, chaos = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    chaos.arm("fail_tests_next", 1)
    orch.decide(rid, "request_changes", "alice", comment="please re-verify")
    run = orch.wait_idle(rid)
    assert run["state"] == "GATE_MERGE"
    assert "developer_no_change" in types(store, rid)


def test_planted_vulnerability_blocked_then_fixed(env):
    orch, store, chaos = env
    chaos.arm("plant_vuln_next", 1)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    assert "security_blocked" in types(store, rid)
    assert run["state"] == "GATE_MERGE"
    assert "_forge_planted_query" not in store.get_artifact(rid, "diff")["content"]["diff"].split("\n+")[-1]


def test_security_block_escalates_and_risk_acceptance_needs_justification(env, tmp_path):
    store = Store(tmp_path / "s.db")
    chaos = Chaos()
    orch = Orchestrator(store, home=tmp_path / "h", chaos=chaos, budgets=Budgets(max_security_autofix=0),
                        mock_latency_ms=0)
    chaos.arm("plant_vuln_next", 1)
    rid = orch.create_run(scenario_id="REQ-INV-001", requested_by="sam")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "ESCALATED" and run["data"]["escalation"]["reason"] == "security_block"
    with pytest.raises(DecisionError, match="justification"):
        orch.decide(rid, "accept_risk", "lead", justification="ok")
    with pytest.raises(DecisionError, match="separation of duties"):
        orch.decide(rid, "accept_risk", "sam", justification="the requester should not do this")
    orch.decide(rid, "accept_risk", "lead", justification="Demo only; tracked in ticket SEC-42 for fix")
    run = orch.wait_idle(rid)
    assert run["state"] == "GATE_MERGE" and run["data"]["accepted_risks"]


def test_repeated_action_is_suppressed(env):
    orch, store, chaos = env
    chaos.arm("repeat_next", 1)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    orch.run_sync(rid)
    assert "duplicate_suppressed" in types(store, rid)


def test_approval_bound_to_diff_and_request_changes(env):
    orch, store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    orch.run_sync(rid)
    gate_hash = store.get_run(rid)["data"]["wait"]["artifact_hash"]
    store.put_artifact(rid, "diff", {"diff": "tampered after the gate opened"})
    with pytest.raises(DecisionError, match="diff changed"):
        orch.decide(rid, "approve", "alice")
    assert "approval_rejected" in types(store, rid)
    assert gate_hash


def test_two_approvers_for_high_risk(env):
    orch, store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    orch.run_sync(rid)
    run = store.get_run(rid)
    store.update_run(rid, wait={**run["data"]["wait"], "required_approvals": 2})
    run = orch.decide(rid, "approve", "alice")
    assert run["state"] == "GATE_MERGE"
    with pytest.raises(DecisionError, match="already approved"):
        orch.decide(rid, "approve", "Alice")
    orch.decide(rid, "approve", "bob")
    assert orch.wait_idle(rid)["state"] == "GATE_DEPLOY"


def test_invalid_decisions(env):
    orch, _store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    with pytest.raises(DecisionError, match="not waiting"):
        orch.decide(rid, "approve", "alice")
    orch.run_sync(rid)
    with pytest.raises(DecisionError, match="not an option"):
        orch.decide(rid, "accept_risk", "alice")
    with pytest.raises(DecisionError, match="actor"):
        orch.decide(rid, "approve", " ")


def test_reject_and_abort(env):
    orch, _store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    orch.run_sync(rid)
    assert orch.decide(rid, "reject", "alice", comment="not now")["state"] == "REJECTED"
    rid2 = orch.create_run(scenario_id="REQ-INV-001")["id"]
    assert orch.abort(rid2, "alice")["state"] == "ABORTED"


def test_resume_after_crash(env, tmp_path):
    orch, store, _ = env
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = store.get_run(rid)
    # Simulate a process that died during DESIGN: state persisted, no thread running.
    orch2 = Orchestrator(store, home=tmp_path, chaos=Chaos(), mock_latency_ms=0)
    orch2._intake(orch2._ctx(run))  # INTAKE finished, then the process died during ANALYSIS
    assert store.get_run(rid)["state"] == "ANALYSIS"
    resumed = orch2.resume_all()
    assert rid in resumed
    assert orch2.wait_idle(rid)["state"] == "GATE_MERGE"
    assert "run_resumed" in types(store, rid)


def test_budget_exceeded_escalates_and_extends(env, tmp_path):
    store = Store(tmp_path / "x.db")
    orch = Orchestrator(store, home=tmp_path / "h", chaos=Chaos(), budgets=Budgets(max_llm_calls=3),
                        mock_latency_ms=0)
    rid = orch.create_run(scenario_id="REQ-INV-001")["id"]
    run = orch.run_sync(rid)
    assert run["state"] == "ESCALATED" and run["data"]["escalation"]["reason"] == "budget_exceeded"
    orch.decide(rid, "extend_budget", "lead")
    assert orch.wait_idle(rid)["state"] in {"GATE_MERGE", "ESCALATED"}


def test_mock_mode_rejects_custom_requirements(env):
    orch, _, _ = env
    with pytest.raises(ValueError, match="scripted"):
        orch.create_run(requirement="do something", target="payments-service", mode="mock")
    with pytest.raises(ValueError, match="as written"):
        orch.create_run(scenario_id="REQ-PAY-001", requirement="changed text", mode="mock")
