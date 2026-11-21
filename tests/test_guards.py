import pytest

from forge.tools import test_lock
from forge.tools.repo_index import RepoIndex, read_numbered
from forge.tools.scope_guard import (
    check_changes,
    check_guarded_calls,
    high_risk_touched,
    matches_any,
    removed_guarded_calls,
)


@pytest.mark.parametrize(
    "path,pattern,expected",
    [
        (".github/workflows/ci.yml", ".github/**", True),
        ("poetry.lock", "**/*.lock", True),
        ("deep/dir/uv.lock", "**/*.lock", True),
        (".env.local", ".env*", True),
        ("forge.toml", "forge.toml", True),
        ("sub/forge.toml", "forge.toml", False),
        ("refunds/service.py", "refunds/*.py", True),
        ("refunds/x/service.py", "refunds/*.py", False),
    ],
)
def test_glob(path, pattern, expected):
    assert matches_any(path, [pattern]) is expected


def test_developer_rules(cfg):
    v = check_changes(
        [("refunds/service.py", "modify"), ("payments/auth.py", "modify"), ("tests/test_x.py", "create"),
         ("forge.toml", "modify"), ("tests/test_refunds.py", "modify")],
        "developer", cfg, scope={"refunds/service.py", "forge.toml"}, locked={"tests/test_refunds.py"},
    )
    assert {(x.path, x.rule) for x in v} == {
        ("payments/auth.py", "out_of_scope"),
        ("tests/test_x.py", "tests_owned_by_qa"),
        ("forge.toml", "protected_path"),  # protected even when in scope
        ("tests/test_refunds.py", "locked_test"),
    }


def test_qa_rules(cfg):
    v = check_changes(
        [("tests/test_new.py", "create"), ("tests/conftest.py", "modify"), ("refunds/service.py", "modify")],
        "qa", cfg,
    )
    assert {(x.path, x.rule) for x in v} == {("tests/conftest.py", "qa_no_modify"),
                                             ("refunds/service.py", "qa_tests_only")}


def test_guarded_call_removal_detected(ws, cfg):
    idx = RepoIndex.build(ws.repo, cfg.source_dirs, cfg.test_dirs)
    guarded = idx.guarded_names(cfg.high_risk_paths)
    before = ws.read("refunds/service.py")
    after = before.replace("    auth.assert_merchant_owns_payment(payment, merchant_id)\n", "")
    assert removed_guarded_calls(before, after, guarded) == ["assert_merchant_owns_payment"]
    v = check_guarded_calls({"refunds/service.py": before}, {"refunds/service.py": after}, guarded)
    assert v[0].rule == "guarded_call_removed"
    assert removed_guarded_calls(before, before + "\n# ok\n", guarded) == []


def test_high_risk(cfg):
    assert high_risk_touched(["payments/auth.py", "refunds/service.py"], cfg) == ["payments/auth.py"]


def test_test_lock(ws, cfg):
    locked = test_lock.snapshot_tests(ws.repo, cfg.test_dirs)
    assert "tests/test_refunds.py" in locked and "tests/conftest.py" in locked
    assert test_lock.verify(ws.repo, locked) == []
    ws.write("tests/test_refunds.py", ws.read("tests/test_refunds.py").replace("== 10_000", "== 10_000 or True"))
    (ws.repo / "tests" / "conftest.py").unlink()
    details = {v.path: v.detail for v in test_lock.verify(ws.repo, locked)}
    assert details == {"tests/test_refunds.py": "locked test file was modified",
                       "tests/conftest.py": "locked test file was deleted"}


def test_repo_index(ws, cfg):
    idx = RepoIndex.build(ws.repo, cfg.source_dirs, cfg.test_dirs)
    svc = idx.modules["refunds/service.py"]
    assert "payments.auth" in svc.imports and "refunds.repository" in svc.imports
    assert any(s.signature.startswith("def issue_refund(conn, merchant_id: str") for s in svc.symbols)
    assert "refunds/service.py" in idx.dependents("refunds/repository.py")
    assert idx.guarded_names(cfg.high_risk_paths) >= {"assert_merchant_owns_payment"}
    ranked = [r["path"] for r in idx.rank("Extend refunds.service.issue_refund; the refund API handler too")]
    assert ranked[0] == "refunds/service.py" and "api/handlers.py" in ranked
    summary = idx.summary()
    assert "refunds/service.py" in summary and "tests: test_full_refund" in summary
    assert "<<<FILE payments/auth.py>>>" in read_numbered(ws.repo, "payments/auth.py")


def test_repo_index_survives_syntax_error(ws, cfg):
    ws.write("refunds/broken.py", "def x(:\n")
    idx = RepoIndex.build(ws.repo, cfg.source_dirs, cfg.test_dirs)
    assert idx.modules["refunds/broken.py"].parse_error
