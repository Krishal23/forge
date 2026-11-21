import sys

from forge.config import TargetConfig
from forge.tools.quality import lint_delta
from forge.tools.sandbox import run_sandboxed
from forge.tools.security import blocking, scan_change
from forge.tools.testing import run_tests

# ------------------------------------------------------------ sandbox


def test_sandbox_scrubs_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    r = run_sandboxed([sys.executable, "-c", "import os; print(sorted(os.environ))"], tmp_path)
    assert r.exit_code == 0 and "GEMINI_API_KEY" not in r.stdout and "FORGE_SANDBOX" in r.stdout


def test_sandbox_blocks_outbound_network(tmp_path):
    code = (
        "import socket\n"
        "s = socket.socket()\n"
        "try:\n    s.connect(('93.184.216.34', 80))\nexcept PermissionError as e:\n    print('BLOCKED', e)\n"
    )
    r = run_sandboxed([sys.executable, "-c", code], tmp_path, timeout_s=20)
    assert "BLOCKED" in r.stdout


def test_sandbox_timeout_kills(tmp_path):
    r = run_sandboxed([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout_s=1)
    assert r.timed_out and r.exit_code == -9 and r.duration_ms < 10_000


def test_sandbox_file_size_limit(tmp_path):
    code = "open('big', 'wb').write(b'x' * 3 * 1024 * 1024)"
    r = run_sandboxed([sys.executable, "-c", code], tmp_path, max_file_mb=1)
    assert r.exit_code != 0


# ------------------------------------------------------------ test runner


def test_run_tests_green_with_coverage(ws, cfg):
    rep = run_tests(ws.repo, cfg, ws.reports)
    assert rep.ok and rep.passed == 11 and rep.failed == 0
    assert rep.coverage_pct and rep.coverage_pct > 80
    assert "refunds/service.py" in rep.file_coverage
    assert not (ws.repo / ".coverage").exists()  # artefacts stay outside the repo
    assert ws.commit("noop") is None


def test_run_tests_reports_failures(ws, cfg):
    ws.write("tests/test_broken.py", "def test_bad():\n    assert 1 + 1 == 3, 'math is broken'\n")
    rep = run_tests(ws.repo, cfg, ws.reports)
    assert not rep.ok and rep.failed == 1 and rep.passed == 11
    assert "math is broken" in rep.failures[0].message
    assert "test_bad" in rep.failure_digest()


def test_run_tests_collection_error(ws, cfg):
    ws.write("tests/test_import.py", "import does_not_exist\n")
    rep = run_tests(ws.repo, cfg, ws.reports)
    assert not rep.ok and rep.errors >= 1


def test_injected_failure_is_labelled(ws, cfg):
    rep = run_tests(ws.repo, cfg, ws.reports, inject_failure=True)
    assert not rep.ok and rep.injected and rep.failures[0].test == "forge_chaos::injected_failure"


def test_second_target_runs(inv_ws):
    rep = run_tests(inv_ws.repo, TargetConfig.load(inv_ws.repo), inv_ws.reports)
    assert rep.ok and rep.passed == 6


# ------------------------------------------------------------ quality and security


def test_lint_delta_counts_only_new(ws):
    ws.write("refunds/service.py", ws.read("refunds/service.py").replace("import uuid\n", "import os\nimport uuid\n"))
    ws.commit("add unused import")
    d = lint_delta(ws, ["refunds/service.py"])
    assert [i["code"] for i in d["new_issues"]] == ["F401"]


def test_untouched_preexisting_finding_not_scanned(ws, cfg):
    ws.write("refunds/service.py", ws.read("refunds/service.py") + "\n# comment\n")
    ws.commit("touch service only")
    assert scan_change(ws, cfg)["findings"] == []


def test_preexisting_vs_new_findings(ws, cfg):
    src = ws.read("refunds/repository.py")
    src = "# shifted\n# lines\n" + src + (
        "\n\ndef by_key(conn, key):\n"
        "    return conn.execute(f\"SELECT * FROM refunds WHERE idempotency_key = '{key}'\").fetchall()\n"
    )
    ws.write("refunds/repository.py", src)
    ws.commit("new query")
    findings = scan_change(ws, cfg)["findings"]
    b608 = [f for f in findings if "B608" in f["title"]]
    assert sorted(f["pre_existing"] for f in b608) == [False, True]  # survives line shift
    block = blocking(findings)
    assert len(block) == 1 and block[0]["severity"] == "critical" and block[0]["cwe"] == "CWE-89"


def test_secret_and_sensitive_logging(ws, cfg):
    ws.write("notifications/email.py", ws.read("notifications/email.py") + (
        '\nAPI_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        "def log_card(card_number):\n    logger.info('card %s', card_number)\n"
    ))
    ws.commit("bad")
    findings = scan_change(ws, cfg)["findings"]
    assert {f["source"] for f in findings} >= {"secrets", "policy"}
    assert all(not f["pre_existing"] for f in findings)
