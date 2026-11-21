"""HTTP API and event stream for the dashboard.

Forge has no login, so the server only listens on 127.0.0.1 and the middleware rejects
requests with a foreign Host header (DNS rebinding) and cross-site or non-JSON writes.
Approver names come from the request body; a real deployment would take them from SSO.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from forge.chaos import CHAOS
from forge.config import FORGE_HOME, PROJECT_ROOT, Budgets
from forge.llm import LLMError
from forge.llm.providers import PROVIDER_SPECS
from forge.orchestrator import DecisionError, Orchestrator
from forge.repos import MAX_ZIP_BYTES, RepoError, add_local, import_zip, list_repos, remove_repo
from forge.scenarios import list_targets, load_scenarios
from forge.state import GATES, HUMAN_WAIT, PIPELINE, TERMINAL
from forge.store import Store

log = logging.getLogger(__name__)
WEB_DIST = PROJECT_ROOT / "web" / "dist"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


def _hostname(netloc: str) -> str:
    """Host part of 'host:port', including bracketed IPv6 like '[::1]:8765'."""
    if netloc.startswith("["):
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


class RunCreate(BaseModel):
    scenario_id: str | None = None
    requirement: str | None = Field(default=None, max_length=8000)
    target: str | None = None
    mode: Literal["mock", "live", "replay"] = "mock"
    title: str | None = Field(default=None, max_length=200)
    requested_by: str = Field(default="requester", max_length=80)
    require_criteria_approval: bool = False


class LocalRepo(BaseModel):
    path: str = Field(max_length=1000)


class Decision(BaseModel):
    action: str = Field(max_length=40)
    actor: str = Field(max_length=80)
    comment: str = Field(default="", max_length=4000)
    answers: str = Field(default="", max_length=4000)
    justification: str = Field(default="", max_length=4000)


class AbortRequest(BaseModel):
    actor: str = Field(max_length=80)
    reason: str = Field(default="", max_length=1000)


class ChaosRequest(BaseModel):
    provider: str | None = None
    down: bool | None = None
    switch: Literal["corrupt_next", "fail_tests_next", "plant_vuln_next", "repeat_next"] | None = None
    count: int = Field(default=1, ge=0, le=20)
    reset: bool = False


def create_app(store: Store | None = None, orchestrator: Orchestrator | None = None, home: Path = FORGE_HOME,
               resume: bool = True) -> FastAPI:
    store = store or Store(Path(home) / "forge.db")
    orch = orchestrator or Orchestrator(store, home=home)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if resume:
            resumed = orch.resume_all()
            if resumed:
                log.info("resumed runs: %s", resumed)
        yield

    app = FastAPI(title="Forge", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.orch = orch

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = _hostname(request.headers.get("host") or "")
        if host not in LOCAL_HOSTS and os.environ.get("FORGE_ALLOW_REMOTE") != "1":
            return JSONResponse({"detail": "Forge only serves localhost"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            ctype = request.headers.get("content-type", "")
            origin = request.headers.get("origin")
            # The zip upload sends raw bytes. application/zip is not a "simple" content type, so a
            # cross-site page cannot send it without a CORS preflight, which this server never allows.
            # DELETE with no body has the same protection, since DELETE also needs a preflight.
            is_upload = request.url.path == "/api/repos/upload" and ctype.startswith("application/zip")
            bodiless_delete = request.method == "DELETE" and not request.headers.get("content-length", "0").strip("0")
            if not ctype.startswith("application/json") and not is_upload and not bodiless_delete:
                return JSONResponse({"detail": "JSON body required"}, status_code=415)
            if origin and _hostname(origin.split("://", 1)[-1]) not in LOCAL_HOSTS:
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    def get_run(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    def run_view(run: dict, full: bool = False) -> dict:
        usage = store.usage_summary(run["id"])
        data = run["data"]
        view = {
            "id": run["id"], "title": run["title"], "scenario_id": run["scenario_id"], "target": run["target"],
            "mode": run["mode"], "state": run["state"], "created_at": run["created_at"],
            "updated_at": run["updated_at"], "running": orch.is_running(run["id"]),
            "waiting": run["state"] in {s.value for s in HUMAN_WAIT},
            "terminal": run["state"] in {s.value for s in TERMINAL},
            "requested_by": data.get("requested_by"), "fix_iteration": data.get("fix_iteration", 0),
            "cost_usd": usage["cost_usd"], "llm_calls": usage["calls"],
        }
        if full:
            view.update({
                "requirement": run["requirement"], "data": data, "usage": usage,
                "artifacts": store.list_artifacts(run["id"]), "approvals": store.approvals(run["id"]),
            })
        return view

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/meta")
    def meta():
        live_providers = [n for n, s in PROVIDER_SPECS.items() if os.environ.get(s.key_env, "").strip()]
        return {
            "scenarios": list(load_scenarios()), "targets": list_targets(),
            "modes": {"mock": True, "live": bool(live_providers), "replay": True},
            "live_providers": live_providers, "budgets": Budgets().__dict__,
            "pipeline": [s.value for s in PIPELINE], "gates": sorted(s.value for s in GATES),
            "human_wait": sorted(s.value for s in HUMAN_WAIT),
        }

    @app.get("/api/providers")
    def providers():
        out = {}
        for mode in ("mock", "live"):
            try:
                out[mode] = orch.gateway(mode).status()
            except LLMError as exc:
                out[mode] = {"error": str(exc)}
        return out

    @app.get("/api/repos")
    def repos():
        return list_repos(home)

    @app.post("/api/repos/upload", status_code=201)
    async def upload_repo(request: Request, filename: str = "repo.zip"):
        declared = int(request.headers.get("content-length") or 0)
        if declared > MAX_ZIP_BYTES:
            raise HTTPException(413, f"zip is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_ZIP_BYTES:
                raise HTTPException(413, f"zip is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
        try:
            return await asyncio.to_thread(import_zip, bytes(data), filename[:120], home)
        except RepoError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/repos/local", status_code=201)
    async def add_local_repo(body: LocalRepo):
        try:
            return await asyncio.to_thread(add_local, body.path, home)
        except RepoError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/repos/{name}")
    def delete_repo(name: str):
        if not remove_repo(name, home):
            raise HTTPException(404, "repo not found")
        return {"ok": True}

    @app.get("/api/runs")
    def list_runs():
        return [run_view(r) for r in store.list_runs()]

    @app.post("/api/runs", status_code=201)
    def create_run(body: RunCreate):
        try:
            run = orch.create_run(
                requirement=body.requirement, target=body.target, mode=body.mode, scenario_id=body.scenario_id,
                title=body.title, requested_by=body.requested_by,
                options={"require_criteria_approval": body.require_criteria_approval},
            )
        except (ValueError, LLMError) as exc:
            raise HTTPException(400, str(exc)) from exc
        orch.start(run["id"])
        return run_view(store.get_run(run["id"]), full=True)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        return run_view(get_run(run_id), full=True)

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, after: int = 0):
        get_run(run_id)
        return store.events(run_id, after_seq=after)

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request, after: int = 0):
        get_run(run_id)

        async def gen():
            last = after
            idle = 0
            while not await request.is_disconnected():
                events = await asyncio.to_thread(store.events, run_id, last)
                for ev in events:
                    last = ev["seq"]
                    yield f"id: {ev['seq']}\nevent: forge\ndata: {json.dumps(ev, default=str)}\n\n"
                idle = 0 if events else idle + 1
                if idle and idle % 30 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    def artifact(run_id: str, name: str, version: int | None = None):
        get_run(run_id)
        art = store.get_artifact(run_id, name, version)
        if art is None:
            raise HTTPException(404, "artifact not found")
        return art

    @app.get("/api/runs/{run_id}/llm_calls")
    def llm_calls(run_id: str, include_payload: bool = False):
        get_run(run_id)
        calls = store.llm_calls(run_id)
        if not include_payload:
            for c in calls:
                c.pop("request_json", None)
                c.pop("response_text", None)
        return calls

    @app.get("/api/runs/{run_id}/llm_calls/{call_id}")
    def llm_call(run_id: str, call_id: int):
        get_run(run_id)
        call = next((c for c in store.llm_calls(run_id) if c["id"] == call_id), None)
        if call is None:
            raise HTTPException(404, "call not found")
        call["request"] = json.loads(call.pop("request_json") or "[]")
        return call

    @app.get("/api/runs/{run_id}/evidence.md", response_class=PlainTextResponse)
    def evidence_md(run_id: str):
        get_run(run_id)
        art = store.get_artifact(run_id, "evidence")
        if art is None:
            raise HTTPException(404, "evidence not ready")
        return PlainTextResponse(art["content"]["markdown"], media_type="text/markdown",
                                 headers={"Content-Disposition": f'inline; filename="{run_id}-evidence.md"'})

    @app.get("/api/runs/{run_id}/verify")
    def verify(run_id: str):
        get_run(run_id)
        ok, bad = store.verify_chain(run_id)
        events = store.events(run_id)
        return {"ok": ok, "first_bad_seq": bad, "events": len(events),
                "head_hash": events[-1]["hash"] if events else None}

    @app.post("/api/runs/{run_id}/decision")
    def decision(run_id: str, body: Decision):
        get_run(run_id)
        try:
            run = orch.decide(run_id, body.action, body.actor, comment=body.comment, answers=body.answers,
                              justification=body.justification)
        except DecisionError as exc:
            raise HTTPException(409, str(exc)) from exc
        return run_view(run, full=True)

    @app.post("/api/runs/{run_id}/abort")
    def abort(run_id: str, body: AbortRequest):
        get_run(run_id)
        try:
            return run_view(orch.abort(run_id, body.actor, body.reason), full=True)
        except DecisionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/chaos")
    def chaos_state():
        return orch.chaos.snapshot()

    @app.post("/api/chaos")
    def chaos_set(body: ChaosRequest):
        c = orch.chaos
        if body.reset:
            c.reset()
            for mode in list(orch._gateways):
                for br in orch._gateways[mode].breakers.values():
                    br.record_success()
        if body.provider is not None and body.down is not None:
            c.set_provider_down(body.provider, body.down)
            if not body.down:
                for gw in orch._gateways.values():
                    if body.provider in gw.breakers:
                        gw.breakers[body.provider].record_success()
        if body.switch:
            c.arm(body.switch, body.count)
        return c.snapshot()

    if WEB_DIST.is_dir():
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (WEB_DIST / path).resolve()
            if path and file.is_file() and file.is_relative_to(WEB_DIST.resolve()):
                return FileResponse(file)
            return FileResponse(WEB_DIST / "index.html")

    return app


__all__ = ["CHAOS", "create_app"]
