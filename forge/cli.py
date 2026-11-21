"""Command line: `forge serve`, `forge run <scenario>`, `forge eval`."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from forge.config import FORGE_HOME, PROJECT_ROOT


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(args) -> int:
    import uvicorn

    from forge.api import create_app

    if not _is_loopback(args.host) and os.environ.get("FORGE_ALLOW_REMOTE") != "1":
        print(f"Refusing to bind {args.host}: Forge has no login and must stay on loopback. Use an SSH tunnel "
              "to reach it from another machine.", file=sys.stderr)
        return 2
    print(f"Forge dashboard: http://{args.host}:{args.port}  (data: {FORGE_HOME})")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_run(args) -> int:
    from forge.orchestrator import Orchestrator
    from forge.store import Store

    store = Store(FORGE_HOME / "forge.db")
    orch = Orchestrator(store, mock_latency_ms=0)
    if not args.scenario and not (args.repo and args.requirement):
        print("give a scenario id, or --repo and --requirement", file=sys.stderr)
        return 2
    requirement = args.requirement
    if requirement and requirement.startswith("@"):
        requirement = Path(requirement[1:]).expanduser().read_text()
    try:
        run = orch.create_run(scenario_id=args.scenario, target=args.repo, requirement=requirement,
                              mode=args.mode, requested_by=args.requested_by)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run {run['id']}")
    run = orch.run_sync(run["id"])
    for ev in store.events(run["id"]):
        if ev["type"] in {"state_changed", "policy_violation", "tests_failed", "tests_passed", "security_blocked",
                          "scope_ruling", "escalated", "acceptance_eval", "provider_failover", "schema_repaired"}:
            detail = ev["data"].get("reason") or ev["data"].get("detail") or ev["data"].get("to") or ""
            print(f"  {ev['seq']:>4} {ev['stage'] or '':<14} {ev['type']:<20} {str(detail)[:80]}")
    print(f"state: {run['state']}  (decide in the dashboard: forge serve)")
    return 0


def cmd_repo(args) -> int:
    from forge.repos import RepoError, add_local, import_zip, list_repos

    if args.action == "list":
        for r in list_repos(FORGE_HOME):
            flag = "ok " if r["ok"] and r["available"] else "!! "
            print(f"{flag}{r['name']:<28} {r['kind']:<7} {r['summary']}  ({r['path']})")
        return 0
    if not args.source:
        print("give a folder path or a .zip file", file=sys.stderr)
        return 2
    src = Path(args.source).expanduser()
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            res = import_zip(src.read_bytes(), src.name, FORGE_HOME)
        else:
            res = add_local(str(src), FORGE_HOME)
    except RepoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    check = res["check"]
    print(f"saved as {res['name']} ({res['kind']}): {res['summary']}")
    for msg in check["problems"]:
        print(f"  problem: {msg}")
    for msg in check["warnings"]:
        print(f"  note: {msg}")
    if check["ok"]:
        print(f"run it with: forge run --repo {res['name']} --requirement \"...\" --mode live")
    return 0 if check["ok"] else 1


def cmd_eval(args) -> int:
    from eval.baseline import main as eval_main

    return eval_main(runs=args.runs, out=args.out, mode=args.mode)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="forge")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("FORGE_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("FORGE_PORT", "8765")))
    s.set_defaults(fn=cmd_serve)
    r = sub.add_parser("run")
    r.add_argument("scenario", nargs="?", help="a scenario id from samples/scenarios.json")
    r.add_argument("--repo", help="saved repo name, sample name or folder path")
    r.add_argument("--requirement", help="the requirement text, or @file to read it from a file")
    r.add_argument("--mode", default="mock", choices=["mock", "live", "replay"])
    r.add_argument("--requested-by", default="requester")
    r.set_defaults(fn=cmd_run)
    rp = sub.add_parser("repo", help="add a repo (folder or .zip) or list saved repos")
    rp.add_argument("action", choices=["add", "list"])
    rp.add_argument("source", nargs="?")
    rp.set_defaults(fn=cmd_repo)
    e = sub.add_parser("eval")
    e.add_argument("--runs", type=int, default=1)
    e.add_argument("--out", default=str(PROJECT_ROOT / "eval" / "results.json"))
    e.add_argument("--mode", default="mock", choices=["mock", "live"])
    e.set_defaults(fn=cmd_eval)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

__all__ = ["json", "main"]
