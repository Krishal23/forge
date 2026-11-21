import pytest

from forge.config import SAMPLES_DIR
from forge.schemas import Edit
from forge.tools.edits import apply_plan, plan_edits
from forge.tools.workspace import Workspace, WorkspaceError

# ------------------------------------------------------------ workspace


def test_workspace_isolated_on_branch(ws):
    assert ws.current_branch() == "forge/run_t"
    assert (ws.baseline / "refunds" / "service.py").exists()
    assert not (ws.repo / "__pycache__").exists()


def test_original_target_untouched(ws):
    original = (SAMPLES_DIR / "payments-service" / "refunds" / "service.py").read_text()
    ws.write("refunds/service.py", "# changed\n")
    ws.commit("change")
    assert (SAMPLES_DIR / "payments-service" / "refunds" / "service.py").read_text() == original


def test_commit_diff_and_changed_files(ws):
    assert ws.commit("nothing") is None
    ws.write("refunds/new_module.py", "X = 1\n")
    ws.write("refunds/service.py", ws.read("refunds/service.py") + "\n# note\n")
    assert ws.commit("work")
    changed = {c["path"]: c["status"] for c in ws.changed_files()}
    assert changed == {"refunds/new_module.py": "added", "refunds/service.py": "modified"}
    assert "+X = 1" in ws.diff()
    assert ws.read_base("refunds/new_module.py") is None
    assert (ws.baseline / "refunds" / "new_module.py").exists() is False


def test_merge_lands_in_baseline_only_after_call(ws):
    ws.write("refunds/new_module.py", "X = 1\n")
    ws.commit("work")
    ws.merge("merge run")
    assert (ws.baseline / "refunds" / "new_module.py").exists()


@pytest.mark.parametrize("path", ["../escape.py", ".git/config"])
def test_workspace_rejects_escaping_paths(ws, path):
    with pytest.raises(WorkspaceError):
        ws.write(path, "x")


def test_reopen_and_duplicate(ws, tmp_path):
    assert Workspace.open("run_t", home=tmp_path).base_sha == ws.base_sha
    with pytest.raises(WorkspaceError):
        Workspace.create("run_t", SAMPLES_DIR / "payments-service", home=tmp_path)


# ------------------------------------------------------------ edits


def test_modify_and_create(ws):
    plan = plan_edits(ws, [
        Edit(path="refunds/service.py", search="class RefundError(Exception):\n    pass",
             replace="class RefundError(Exception):\n    \"\"\"Refund rejected.\"\"\""),
        Edit(path="refunds/policy.py", search="", replace="MAX = 1"),
    ])
    assert plan.ok, plan.errors
    assert dict(plan.actions()) == {"refunds/service.py": "modify", "refunds/policy.py": "create"}
    apply_plan(ws, plan)
    assert ws.read("refunds/policy.py") == "MAX = 1\n"
    assert '"""Refund rejected."""' in ws.read("refunds/service.py")


def test_sequential_edits_same_file(ws):
    plan = plan_edits(ws, [
        Edit(path="payments/auth.py", search="class AuthorizationError", replace="class AuthzError"),
        Edit(path="payments/auth.py", search="class AuthzError", replace="class AuthorizationError"),
    ])
    assert plan.errors == ["edits produced no changes"]


def test_whitespace_tolerant_match(ws):
    plan = plan_edits(ws, [Edit(path="payments/auth.py", search="class AuthorizationError(Exception):   \n    pass   ",
                                replace="class AuthorizationError(Exception):\n    code = 403")])
    assert plan.ok, plan.errors


def test_missing_anchor_gives_hint(ws):
    plan = plan_edits(ws, [Edit(path="payments/auth.py", search="def assert_merchant_owns_paymnt(payment)",
                                replace="x")])
    assert not plan.ok and "Closest existing line" in plan.errors[0]


def test_ambiguous_anchor_rejected(ws):
    plan = plan_edits(ws, [Edit(path="refunds/repository.py", search="conn.commit()", replace="pass")])
    assert "matches 2 places" in plan.errors[0]


def test_invalid_python_rejected_and_nothing_written(ws):
    before = ws.read("payments/auth.py")
    plan = plan_edits(ws, [Edit(path="payments/auth.py", search="class AuthorizationError(Exception):",
                                replace="class AuthorizationError(Exception")])
    assert any("invalid Python" in e for e in plan.errors)
    with pytest.raises(ValueError):
        apply_plan(ws, plan)
    assert ws.read("payments/auth.py") == before


def test_create_existing_and_modify_missing_rejected(ws):
    plan = plan_edits(ws, [Edit(path="payments/auth.py", search="", replace="x"),
                           Edit(path="nope.py", search="a", replace="b")])
    assert len(plan.errors) == 2
