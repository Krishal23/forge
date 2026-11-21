import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ChaosState, ForgeEvent, Json, Meta, RepoCheck, RunDetail, RunSummary, SavedRepo, subscribe } from "./api";

// ---------------------------------------------------------------- helpers

const STATE_LABEL: Record<string, string> = {
  INTAKE: "Intake", ANALYSIS: "Analysis", GATE_CRITERIA: "Criteria gate", DESIGN: "Design",
  IMPLEMENT: "Implement", TEST: "Test", QUALITY: "Quality", SECURITY: "Security", REVIEW: "Review",
  RELEASE: "Release", GATE_MERGE: "Merge gate", MERGED: "Merged", GATE_DEPLOY: "Deploy gate",
  DEPLOYED: "Deployed", ESCALATED: "Needs a human", REJECTED: "Rejected", ABORTED: "Aborted", FAILED: "Failed",
};

const AGENT_OF_STAGE: Record<string, string> = {
  ANALYSIS: "analyst", DESIGN: "architect", IMPLEMENT: "developer", TEST: "qa", SECURITY: "security",
  REVIEW: "reviewer", RELEASE: "release",
};

type Tone = "ok" | "bad" | "warn" | "info" | "human" | "muted";

const EVENT_TONE: Record<string, Tone> = {
  tests_passed: "ok", tests_failed: "bad", policy_violation: "bad", prompt_injection_suspected: "bad",
  security_blocked: "bad", agent_output_invalid: "warn", schema_repaired: "ok", provider_failover: "warn",
  llm_retry: "warn", circuit_open: "warn", all_providers_failed: "bad", duplicate_suppressed: "warn",
  chaos_injected: "warn", escalated: "human", gate_waiting: "human", human_decision: "human",
  approval_recorded: "human", approval_rejected: "bad", scope_request: "info", scope_ruling: "info",
  scope_expanded: "info", change_applied: "info", merged: "ok", deployed: "ok", run_completed: "muted",
  fix_iteration_started: "warn", security_autofix_started: "warn", acceptance_eval: "ok", redaction: "info",
  developer_retry: "warn", edit_rejected: "warn", run_resumed: "warn", system_error: "bad",
  qa_tests_written: "info", review_completed: "info", evidence_ready: "info", developer_no_change: "warn",
};

const QUIET = new Set(["llm_call", "stage_started", "agent_started", "agent_completed"]);

function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour12: false });
}

function stateTone(state: string): Tone {
  if (["DEPLOYED", "MERGED"].includes(state)) return "ok";
  if (["FAILED", "REJECTED", "ABORTED"].includes(state)) return "bad";
  if (state.startsWith("GATE") || state === "ESCALATED") return "human";
  return "info";
}

function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

function short(h?: string | null, n = 10) {
  return h ? h.slice(0, n) : "";
}

function describe(e: ForgeEvent): string {
  const d = e.data;
  switch (e.type) {
    case "run_created": return `Run created on ${d.target} (${d.mode})`;
    case "preflight": return `Workspace on branch ${d.branch}; ${d.locked_tests} test files hash-locked; baseline ${d.baseline_passed} tests pass, coverage ${d.coverage_pct}%`;
    case "state_changed": return `${STATE_LABEL[d.from] ?? d.from} → ${STATE_LABEL[d.to] ?? d.to}${d.reason ? ` · ${d.reason}` : ""}`;
    case "analysis_ready": return `${d.criteria} acceptance criteria, ${d.tasks} tasks${d.open_questions?.length ? `, ${d.open_questions.length} open question(s)` : ""}`;
    case "scope_set": return `Change scope: ${d.scope.join(", ")}`;
    case "scope_request": return `Developer asks to edit ${d.path}: ${d.justification}`;
    case "scope_ruling": return `${d.path}: ${d.decision} by ${d.by} · ${d.reason}`;
    case "scope_expanded": return `Scope expanded with ${d.added.join(", ")}`;
    case "policy_violation": return `Blocked [${d.rule}] ${d.path}: ${d.detail}`;
    case "prompt_injection_suspected": return d.detail;
    case "change_applied": return `Applied edits to ${d.files.join(", ")} (${short(d.sha, 8)})`;
    case "edit_rejected": return d.detail;
    case "developer_retry": return `Developer retry ${d.attempt}/${d.limit} (${d.situation})`;
    case "developer_no_change": return `Developer made no change: ${d.reason}`;
    case "qa_tests_written": return `QA wrote ${d.files.join(", ")} covering ${d.criteria.join(", ")} (now hash-locked)`;
    case "tests_passed": return `${d.passed} passed, coverage ${d.coverage_pct}%`;
    case "tests_failed": return `${d.passed} passed, ${d.failed + d.errors} failed${d.injected ? " (injected failure)" : ""}: ${(d.failing || []).slice(0, 2).join(", ")}`;
    case "fix_iteration_started": return `Fix iteration ${d.iteration}/${d.limit} (${d.situation})`;
    case "quality_report": return `Lint: ${d.new_lint_issues} new issues; coverage ${d.coverage_pct}% ${d.coverage_ok ? "meets" : "below"} threshold`;
    case "security_report": return `New findings ${JSON.stringify(d.new_by_severity)}; pre-existing ${d.pre_existing}; blocking ${d.blocking}`;
    case "security_blocked": return `Blocked: ${d.findings.map((f: Json) => `[${f.severity}] ${f.path}:${f.line} ${f.title}`).join("; ")}`;
    case "security_autofix_started": return `Automatic security fix attempt ${d.attempt}/${d.limit}`;
    case "acceptance_eval": return `Hidden acceptance tests: ${d.passed}/${d.total} passed`;
    case "review_completed": return `Reviewer: ${d.recommendation}, average ${d.average_score}/5`;
    case "evidence_ready": return `Evidence pack v${d.evidence_version} ready (${short(d.evidence_hash, 12)})`;
    case "gate_waiting": return `Waiting at ${STATE_LABEL[d.gate]}${d.artifact_hash ? `, bound to ${d.artifact} hash ${short(d.artifact_hash, 12)}` : ""}`;
    case "human_decision": return `${d.actor} chose ${d.action}${d.comment ? `: “${d.comment}”` : ""}`;
    case "approval_recorded": return `Approval by ${d.approver} (${d.have}/${d.need}) on hash ${short(d.artifact_hash, 12)}`;
    case "approval_rejected": return `Approval refused: ${d.reason}`;
    case "escalated": return `${d.reason}: ${d.detail}`;
    case "agent_output_invalid": return `Invalid output (attempt ${d.attempt}): ${(d.errors || []).slice(0, 2).join("; ")}`;
    case "schema_repaired": return `Output repaired after ${d.attempts} attempts`;
    case "agent_failed": return `Agent failed: ${(d.errors || []).slice(-1)[0] ?? ""}`;
    case "provider_failover": return `Failed over to ${d.to} after ${d.failed_attempts} failed attempt(s)`;
    case "llm_recovered": return `Recovered on ${d.to} after retry`;
    case "llm_retry": return `Retry ${d.provider} in ${d.delay_s}s: ${d.error}`;
    case "circuit_open": return `Circuit opened for ${d.provider} (${d.cooldown_s}s cooldown)`;
    case "provider_skipped": return `Skipped ${d.provider}: ${d.reason}`;
    case "all_providers_failed": return `All providers failed`;
    case "chaos_injected": return `Injected fault: ${d.fault}${d.path ? ` in ${d.path}` : ""}`;
    case "duplicate_suppressed": return `Repeated action "${d.action}" suppressed by the idempotency ledger`;
    case "redaction": return `Redacted before sending: ${JSON.stringify(d.counts)}`;
    case "merged": return `Merged into ${d.into} as ${short(d.sha, 8)}`;
    case "deployed": return `Deployed to ${d.environment}; smoke tests ${d.smoke_ok ? "passed" : "failed"}`;
    case "run_resumed": return `Resumed in ${d.state} after ${d.reason}`;
    case "run_completed": return `Run finished: ${STATE_LABEL[d.state] ?? d.state}`;
    case "system_error": return d.error;
    default: return JSON.stringify(d).slice(0, 200);
  }
}

// ---------------------------------------------------------------- console shell

export default function Console({ runId, navigate }: { runId: string | null; navigate: (path: string) => void }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showNew, setShowNew] = useState(runId === "new");
  const [error, setError] = useState<string | null>(null);
  const selected = runId && runId !== "new" ? runId : null;

  const refreshRuns = useCallback(() => api.runs().then(setRuns).catch((e) => setError(String(e.message))), []);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e.message)));
    refreshRuns();
    const t = setInterval(refreshRuns, 3000);
    return () => clearInterval(t);
  }, [refreshRuns]);

  useEffect(() => { setShowNew(runId === "new"); }, [runId]);

  const select = (id: string | null) => navigate(id ? `/console/${id}` : "/console");
  const counts = {
    waiting: runs.filter((r) => r.waiting).length,
    active: runs.filter((r) => r.running).length,
  };

  return (
    <div className="console">
      <aside className="rail">
        <div className="rail-head">
          <div>
            <div className="eyebrow">Workspace</div>
            <div className="rail-title">Runs</div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => navigate("/console/new")}>New run</button>
        </div>
        <div className="rail-stats">
          <div><span className="num">{runs.length}</span><span className="lbl">total</span></div>
          <div><span className="num">{counts.active}</span><span className="lbl">active</span></div>
          <div><span className="num accent">{counts.waiting}</span><span className="lbl">need you</span></div>
        </div>
        <div className="run-list">
          {runs.length === 0 && <div className="rail-empty">No runs yet.</div>}
          {runs.map((r) => (
            <button key={r.id} className={`run-item ${selected === r.id ? "active" : ""}`} onClick={() => select(r.id)}>
              <div className="run-item-top">
                <span className="run-item-title">{r.title}</span>
                {r.running && <span className="live-dot" title="running" />}
              </div>
              <div className="run-item-meta">
                <Badge tone={stateTone(r.state)}>{STATE_LABEL[r.state] ?? r.state}</Badge>
                <span className="mono">{r.target}</span>
                <span className="sep">·</span>
                <span>{r.mode}</span>
              </div>
            </button>
          ))}
        </div>
        <ChaosPanel />
      </aside>
      <main className="console-main">
        {error && <div className="alert bad" onClick={() => setError(null)}>{error}</div>}
        {showNew && meta && <NewRun meta={meta} onCreated={(id) => { refreshRuns(); select(id); }} />}
        {!showNew && selected && meta && <RunView key={selected} runId={selected} meta={meta} onChange={refreshRuns} />}
        {!showNew && !selected && <EmptyConsole onNew={() => navigate("/console/new")} />}
      </main>
    </div>
  );
}

function EmptyConsole({ onNew }: { onNew: () => void }) {
  return (
    <div className="empty">
      <div className="eyebrow">Operator console</div>
      <h1 className="display-md">Select a run, or start a new one.</h1>
      <p className="lede">A run takes one requirement through planning, coding, testing and review, then waits for
        people to approve the merge and the deploy.</p>
      <div className="row"><button className="btn btn-primary" onClick={onNew}>Start a run</button></div>
    </div>
  );
}

// ---------------------------------------------------------------- new run

function NewRun({ meta, onCreated }: { meta: Meta; onCreated: (id: string) => void }) {
  const scripted = meta.scenarios.filter((s) => s.scripted);
  const [mode, setMode] = useState("mock");
  const [scenarioId, setScenarioId] = useState(scripted[0]?.id ?? "");
  const scenario = meta.scenarios.find((s) => s.id === scenarioId);
  const [custom, setCustom] = useState(false);
  const [requirement, setRequirement] = useState(scenario?.requirement ?? "");
  const [target, setTarget] = useState(scenario?.target ?? meta.targets[0]?.name ?? "");
  const [requestedBy, setRequestedBy] = useState("priya");
  const [gateCriteria, setGateCriteria] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (scenario && !custom) { setRequirement(scenario.requirement); setTarget(scenario.target); }
  }, [scenarioId, custom]); // eslint-disable-line

  const available = mode === "mock" ? scripted : meta.scenarios;
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const body: Json = { mode, requested_by: requestedBy, require_criteria_approval: gateCriteria };
      if (custom) Object.assign(body, { requirement, target, title: requirement.split("\n")[0].slice(0, 80) });
      else body.scenario_id = scenarioId;
      const run = await api.createRun(body);
      onCreated(run.id);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="panel">
      <h2>New run</h2>
      <div className="form">
        <label>Model mode</label>
        <div className="seg">
          {(["mock", "live", "replay"] as const).map((m) => (
            <button key={m} className={mode === m ? "on" : ""} disabled={!meta.modes[m]} onClick={() => { setMode(m); if (m === "mock") setCustom(false); }}
              title={m === "live" && !meta.modes.live ? "Add an API key to .env to enable live mode" : ""}>
              {m === "mock" ? "Mock (offline, scripted)" : m === "live" ? `Live${meta.live_providers.length ? ` (${meta.live_providers.join(", ")})` : " (no key)"}` : "Replay (recorded)"}
            </button>
          ))}
        </div>
        <label>Requirement</label>
        <div className="row">
          <select value={custom ? "__custom" : scenarioId} onChange={(e) => {
            if (e.target.value === "__custom") { setCustom(true); setRequirement(""); setTarget(""); } else { setCustom(false); setScenarioId(e.target.value); }
          }}>
            {available.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.title} ({s.target})</option>)}
            {mode !== "mock" && <option value="__custom">Custom requirement (any repository)…</option>}
          </select>
        </div>
        <textarea rows={9} value={requirement} readOnly={!custom} onChange={(e) => setRequirement(e.target.value)}
          placeholder="Describe the change in plain words, with any rules it must follow." />
        <label>Repository</label>
        {custom ? (
          <RepoPicker meta={meta} value={target} onChange={setTarget} />
        ) : (
          <p className="muted small">
            {scenario?.target ?? "—"}{" "}
            {mode === "mock" ? "· To run on your own repository, switch to Live mode." : "· Pick “Custom requirement…” to use your own repository."}
          </p>
        )}
        <label>Requested by</label>
        <input value={requestedBy} onChange={(e) => setRequestedBy(e.target.value)} />
        <label className="check"><input type="checkbox" checked={gateCriteria} onChange={(e) => setGateCriteria(e.target.checked)} /> Always ask me to confirm acceptance criteria</label>
        {mode === "mock" && <p className="muted small">Mock mode works offline with scripted agent answers. The code edits, tests and scans are real.</p>}
        {err && <div className="alert bad">{err}</div>}
        <button className="primary" disabled={busy || !requirement.trim() || (custom && !target)} onClick={submit}>{busy ? "Starting…" : "Start run"}</button>
      </div>
    </div>
  );
}

// Pick a sample, a saved repo, a zip to upload or a folder on this computer. Every new repo is
// checked first (layout, tests) so problems show up here instead of halfway through a run.
function RepoPicker({ meta, value, onChange }: { meta: Meta; value: string; onChange: (name: string) => void }) {
  const [repos, setRepos] = useState<SavedRepo[]>([]);
  const [how, setHow] = useState<"saved" | "zip" | "folder">("saved");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [check, setCheck] = useState<RepoCheck | null>(null);

  const load = () => api.repos().then(setRepos).catch(() => setRepos([]));
  useEffect(() => { load(); }, []);

  const added = (r: SavedRepo) => {
    setCheck(r.check ?? null);
    load();
    if (r.ok) { onChange(r.name); setHow("saved"); } else onChange("");
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    setErr(null); setCheck(null); setBusy("Unpacking and running the existing tests…");
    try { added(await api.uploadRepo(file)); } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const addFolder = async () => {
    setErr(null); setCheck(null); setBusy("Checking the folder and running the existing tests…");
    try { added(await api.addLocalRepo(path.trim())); } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const remove = async (name: string) => {
    await api.removeRepo(name).catch(() => null);
    if (value === name) onChange("");
    load();
  };

  return (
    <div className="repo-picker">
      <div className="seg">
        <button className={how === "saved" ? "on" : ""} onClick={() => setHow("saved")}>Choose</button>
        <button className={how === "zip" ? "on" : ""} onClick={() => setHow("zip")}>Upload .zip</button>
        <button className={how === "folder" ? "on" : ""} onClick={() => setHow("folder")}>Folder on this computer</button>
      </div>

      {how === "saved" && (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">Select a repository…</option>
          <optgroup label="Samples">
            {meta.targets.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </optgroup>
          {repos.length > 0 && (
            <optgroup label="Your repositories">
              {repos.map((r) => (
                <option key={r.name} value={r.name} disabled={!r.ok || !r.available}>
                  {r.name} · {r.kind === "upload" ? "uploaded" : "folder"}{!r.available ? " (missing)" : !r.ok ? " (failed check)" : ""}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      )}

      {how === "zip" && (
        <div className="drop">
          <input type="file" accept=".zip,application/zip" disabled={!!busy} onChange={(e) => upload(e.target.files?.[0])} />
          <p className="muted small">A zip of a Python repository with pytest tests, up to 50 MB. A GitHub “Download ZIP” works as is.
            It is unpacked into Forge’s own folder and kept for later runs.</p>
        </div>
      )}

      {how === "folder" && (
        <div className="row">
          <input value={path} placeholder="/Users/you/code/my-service" onChange={(e) => setPath(e.target.value)} />
          <button className="ghost" disabled={!!busy || !path.trim()} onClick={addFolder}>Check and add</button>
        </div>
      )}
      {how === "folder" && <p className="muted small">Forge copies the folder for each run and works on the copy, so your files are not changed.</p>}

      {busy && <p className="muted small"><span className="pulse" /> {busy}</p>}
      {err && <div className="alert bad">{err}</div>}
      {check && <RepoCheckCard check={check} />}

      {how === "saved" && repos.length > 0 && (
        <details className="saved-list">
          <summary className="muted small">Manage your repositories ({repos.length})</summary>
          {repos.map((r) => (
            <div key={r.name} className="saved-row small">
              <span><b>{r.name}</b> <span className="muted">· {r.summary}</span></span>
              <button className="ghost" onClick={() => remove(r.name)} title={r.kind === "upload" ? "Deletes the uploaded copy" : "Forgets the folder; files stay"}>Remove</button>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function RepoCheckCard({ check }: { check: RepoCheck }) {
  const t = check.tests;
  return (
    <div className={`repo-check ${check.ok ? "good" : "bad"}`}>
      <div className="small"><b>{check.ok ? "Ready" : "Can't use this repository yet"}</b> · {check.name}</div>
      <div className="small muted">
        {check.python_files} Python files · source {check.source_dirs.join(", ") || "none"} · tests {check.test_dirs.join(", ") || "none"}
        {t && ` · ${t.passed}/${t.total} existing tests pass`}
      </div>
      {check.problems.map((m) => <div key={m} className="small bad-text">• {m}</div>)}
      {check.warnings.map((m) => <div key={m} className="small muted">• {m}</div>)}
    </div>
  );
}

// ---------------------------------------------------------------- run view

function RunView({ runId, meta, onChange }: { runId: string; meta: Meta; onChange: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<ForgeEvent[]>([]);
  const [tab, setTab] = useState("timeline");
  const lastSeq = useRef(0);

  const refresh = useCallback(() => api.run(runId).then(setRun).catch(() => undefined), [runId]);

  useEffect(() => {
    refresh();
    const unsub = subscribe(runId, 0, (e) => {
      if (e.seq <= lastSeq.current) return;
      lastSeq.current = e.seq;
      setEvents((prev) => [...prev, e]);
      if (["state_changed", "gate_waiting", "escalated", "run_completed", "approval_recorded"].includes(e.type)) {
        refresh(); onChange();
      }
    });
    const t = setInterval(refresh, 4000);
    return () => { unsub(); clearInterval(t); };
  }, [runId, refresh, onChange]);

  if (!run) return <div className="panel">Loading…</div>;
  const visible = events.filter((e) => !QUIET.has(e.type));
  const tabs = [
    ["timeline", "Timeline"], ["plan", "Plan & design"], ["diff", "Diff"], ["tests", "Tests"],
    ["security", "Security"], ["review", "Review"], ["evidence", "Evidence"], ["usage", "Usage & cost"],
    ["audit", "Audit"],
  ];

  return (
    <div className="run">
      <header className="run-header">
        <div>
          <div className="muted small">{run.id} · {run.target} · {run.mode} mode · requested by {run.requested_by}</div>
          <h2>{run.title}</h2>
        </div>
        <div className="header-right">
          <Badge tone={stateTone(run.state)}>{STATE_LABEL[run.state] ?? run.state}</Badge>
          {run.running && <span className="muted small"><span className="pulse" /> working</span>}
          <span className="muted small">{run.usage.calls} LLM calls · est. ${run.usage.cost_usd.toFixed(4)}</span>
          {!run.terminal && <AbortButton run={run} onDone={refresh} />}
        </div>
      </header>
      <Pipeline run={run} events={events} meta={meta} />
      {(run.waiting) && <DecisionPanel run={run} onDone={() => { refresh(); onChange(); }} />}
      <nav className="tabs">
        {tabs.map(([k, label]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </nav>
      <section className="tab-body">
        {tab === "timeline" && <Timeline events={visible} />}
        {tab === "plan" && <PlanTab run={run} />}
        {tab === "diff" && <ArtifactView runId={run.id} name="diff" render={(c) => <DiffView diff={c.diff} files={c.files} />} deps={run.updated_at} />}
        {tab === "tests" && <TestsTab run={run} />}
        {tab === "security" && <ArtifactView runId={run.id} name="security" render={(c) => <SecurityView sec={c} accepted={run.data.accepted_risks || []} />} deps={run.updated_at} />}
        {tab === "review" && <ReviewTab run={run} />}
        {tab === "evidence" && <EvidenceTab run={run} />}
        {tab === "usage" && <UsageTab run={run} />}
        {tab === "audit" && <AuditTab run={run} events={events} />}
      </section>
    </div>
  );
}

function AbortButton({ run, onDone }: { run: RunDetail; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className="ghost danger" disabled={busy} onClick={async () => {
      const actor = prompt("Your name (recorded in the audit log)");
      if (!actor) return;
      setBusy(true);
      try { await api.abort(run.id, { actor, reason: "aborted from dashboard" }); } catch (e: any) { alert(e.message); }
      setBusy(false); onDone();
    }}>Abort</button>
  );
}

function Pipeline({ run, events, meta }: { run: RunDetail; events: ForgeEvent[]; meta: Meta }) {
  const visited = useMemo(() => {
    const s = new Set<string>(["INTAKE"]);
    events.forEach((e) => { if (e.type === "state_changed") { s.add(e.data.from); s.add(e.data.to); } });
    return s;
  }, [events]);
  const loops = useMemo(() => {
    const c: Record<string, number> = {};
    events.forEach((e) => { if (e.type === "state_changed" && e.data.to === "IMPLEMENT" && e.data.from !== "DESIGN") c[e.data.from] = (c[e.data.from] || 0) + 1; });
    return c;
  }, [events]);
  const current = run.state === "ESCALATED" ? run.data.escalation?.from_state : run.state;
  const order = meta.pipeline;
  const curIdx = order.indexOf(current);
  return (
    <div className="pipeline">
      {order.map((s, i) => {
        const isGate = s.startsWith("GATE");
        const status = s === current ? (run.state === "ESCALATED" ? "escalated" : run.terminal ? "done" : "current")
          : run.state === "DEPLOYED" || (curIdx > i && visited.has(s)) ? "done" : visited.has(s) && curIdx > i ? "done" : "todo";
        const skipped = s === "GATE_CRITERIA" && !visited.has(s) && curIdx > i;
        return (
          <div key={s} className={`step ${status} ${isGate ? "gate" : ""} ${skipped ? "skipped" : ""}`}>
            <div className="dot">{isGate ? "◆" : status === "done" ? "✓" : i + 1}</div>
            <div className="step-label">{STATE_LABEL[s]}</div>
            {AGENT_OF_STAGE[s] && <div className="step-agent">{AGENT_OF_STAGE[s]}</div>}
            {loops[s] ? <div className="loop" title="times this stage sent work back to implement">↺ {loops[s]}</div> : null}
          </div>
        );
      })}
      {["ESCALATED", "REJECTED", "ABORTED", "FAILED"].includes(run.state) && (
        <div className={`step ${run.state === "ESCALATED" ? "escalated" : "bad"} terminal`}>
          <div className="dot">!</div><div className="step-label">{STATE_LABEL[run.state]}</div>
        </div>
      )}
    </div>
  );
}

function Timeline({ events }: { events: ForgeEvent[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [events.length]);
  return (
    <div className="timeline">
      {events.map((e) => (
        <div key={e.seq} className={`tl-row ${EVENT_TONE[e.type] ?? "muted"}`}>
          <span className="tl-time">{fmtTime(e.ts)}</span>
          <span className="tl-stage">{e.stage ? STATE_LABEL[e.stage] ?? e.stage : ""}</span>
          <span className="tl-agent">{e.agent ?? ""}</span>
          <span className="tl-type">{e.type.replace(/_/g, " ")}</span>
          <span className="tl-text">{describe(e)}</span>
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}

// ---------------------------------------------------------------- decisions

function DecisionPanel({ run, onDone }: { run: RunDetail; onDone: () => void }) {
  const [actor, setActor] = useState(() => localStorage.getItem("forge.actor") || "");
  const [comment, setComment] = useState("");
  const [answers, setAnswers] = useState("");
  const [justification, setJustification] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const wait = run.data.wait || {};
  const esc = run.data.escalation;
  const state = run.state;
  const options: string[] = state === "ESCALATED" ? esc?.options ?? [] : wait.options ?? [];

  const act = async (action: string) => {
    setBusy(true); setErr(null);
    localStorage.setItem("forge.actor", actor);
    try {
      await api.decide(run.id, { action, actor, comment, answers, justification });
      setComment(""); setJustification("");
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const label: Record<string, string> = {
    approve: state === "GATE_MERGE" ? "Approve merge" : state === "GATE_DEPLOY" ? "Approve deploy" : "Confirm criteria",
    revise: "Ask analyst to revise", reject: "Reject", request_changes: "Request changes", retry: "Retry",
    extend_budget: "Extend budget", accept_risk: "Accept risk", approve_scope: "Approve scope", reject_scope: "Reject scope",
    abort: "Abort run",
  };
  const danger = new Set(["reject", "abort", "accept_risk"]);

  return (
    <div className={`decision ${state === "ESCALATED" ? "escalated" : ""}`}>
      <div className="decision-head">
        <span className="decision-icon">{state === "ESCALATED" ? "⚠" : "◆"}</span>
        <div>
          <h3>{state === "ESCALATED" ? "A human decision is needed" : `${STATE_LABEL[state]}: your approval is required`}</h3>
          {state === "ESCALATED" && <p><b>{esc?.reason?.replace(/_/g, " ")}</b>: <span className="pre">{esc?.detail}</span></p>}
          {state === "GATE_CRITERIA" && <p>Review the acceptance criteria in <b>Plan &amp; design</b>. The analyst asked:</p>}
          {state === "GATE_MERGE" && (
            <p>Review the <b>Diff</b> and <b>Evidence</b> tabs. Your approval is bound to diff hash <code>{short(wait.artifact_hash, 16)}</code>;
              if the diff changes, the approval is void. {wait.required_approvals > 1 && <b>High-risk paths changed ({(wait.high_risk_paths || []).join(", ")}): {wait.required_approvals} different approvers needed ({(wait.approvals || []).length} so far). </b>}
              The requester ({run.requested_by}) cannot approve.</p>
          )}
          {state === "GATE_DEPLOY" && <p>Merged to main. Approving runs the simulated staging deployment and smoke tests. The requester cannot approve.</p>}
        </div>
      </div>
      {state === "GATE_CRITERIA" && (wait.questions || []).length > 0 && (
        <ul className="questions">{wait.questions.map((q: string) => <li key={q}>{q}</li>)}</ul>
      )}
      <div className="decision-form">
        <input placeholder="Your name (recorded in the audit log)" value={actor} onChange={(e) => setActor(e.target.value)} />
        {state === "GATE_CRITERIA" && <textarea rows={2} placeholder="Answers to the open questions" value={answers} onChange={(e) => setAnswers(e.target.value)} />}
        <textarea rows={2} placeholder="Comment (required for revise / request changes)" value={comment} onChange={(e) => setComment(e.target.value)} />
        {options.includes("accept_risk") && <textarea rows={2} placeholder="Written justification for accepting the risk (required, min 15 characters)" value={justification} onChange={(e) => setJustification(e.target.value)} />}
        <div className="row wrap">
          {options.map((o) => (
            <button key={o} className={o === "approve" || o === "retry" || o === "approve_scope" ? "primary" : danger.has(o) ? "danger" : ""}
              disabled={busy || !actor.trim()} onClick={() => act(o)}>{label[o] ?? o}</button>
          ))}
        </div>
        {err && <div className="alert bad">{err}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tabs

function useArtifact(runId: string, name: string, deps: unknown) {
  const [data, setData] = useState<{ content: Json; version: number; hash: string } | null | undefined>(undefined);
  useEffect(() => { api.artifact(runId, name).then(setData).catch(() => setData(null)); }, [runId, name, deps]);
  return data;
}

function ArtifactView({ runId, name, render, deps }: { runId: string; name: string; render: (c: Json) => React.ReactNode; deps: unknown }) {
  const art = useArtifact(runId, name, deps);
  if (art === undefined) return <div className="muted">Loading…</div>;
  if (art === null) return <div className="muted">Not produced yet.</div>;
  return <div>
    <div className="muted small art-meta">{name} v{art.version} · sha256 {short(art.hash, 16)}</div>
    {render(art.content)}
  </div>;
}

function PlanTab({ run }: { run: RunDetail }) {
  const analysis = useArtifact(run.id, "analysis", run.updated_at);
  const design = useArtifact(run.id, "design", run.updated_at);
  const a = analysis?.content, d = design?.content;
  return (
    <div className="cols">
      <div>
        <h3>Acceptance criteria</h3>
        {!a && <p className="muted">Not produced yet.</p>}
        {a && <>
          <p>{a.summary}</p>
          <table className="tbl"><thead><tr><th>ID</th><th>Given</th><th>When</th><th>Then</th></tr></thead>
            <tbody>{a.acceptance_criteria.map((c: Json) => <tr key={c.id}><td><b>{c.id}</b></td><td>{c.given}</td><td>{c.when}</td><td>{c.then}</td></tr>)}</tbody></table>
          <h4>Tasks</h4>
          <ul>{a.tasks.map((t: Json) => <li key={t.id}><b>{t.id}</b> {t.title} <span className="muted">({t.criteria.join(", ")})</span><div className="muted small">{t.description}</div></li>)}</ul>
          {a.assumptions?.length > 0 && <><h4>Assumptions</h4><ul>{a.assumptions.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
          {a.open_questions?.length > 0 && <><h4>Open questions</h4><ul>{a.open_questions.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
          {run.data.clarifications && <><h4>Answers</h4><p className="pre">{run.data.clarifications}</p></>}
        </>}
      </div>
      <div>
        <h3>Design and change scope</h3>
        {!d && <p className="muted">Not produced yet.</p>}
        {d && <>
          <p>{d.summary}</p><p className="muted">{d.approach}</p>
          <h4>Impacted components</h4>
          <table className="tbl"><thead><tr><th>File</th><th>Change</th><th>Symbols</th><th>Reason</th></tr></thead>
            <tbody>{d.impacted_components.map((c: Json) => <tr key={c.path}><td><code>{c.path}</code></td><td>{c.change_type}</td><td>{c.symbols.join(", ")}</td><td>{c.reason}</td></tr>)}</tbody></table>
          <h4>Approved scope now</h4>
          <div className="chips">{(run.data.scope || []).map((p: string) => <code key={p} className="chip">{p}</code>)}</div>
          {(run.data.scope_history || []).filter((h: Json) => h.action !== "set").length > 0 && <>
            <h4>Scope changes</h4>
            <ul>{run.data.scope_history.filter((h: Json) => h.action !== "set").map((h: Json, i: number) => <li key={i}><Badge tone={h.action === "approved" ? "ok" : "bad"}>{h.action}</Badge> <code>{h.path}</code> by {h.by}: {h.reason}</li>)}</ul>
          </>}
          {d.out_of_scope?.length > 0 && <><h4>Kept out of scope</h4><ul>{d.out_of_scope.map((o: Json) => <li key={o.path}><code>{o.path}</code> {o.reason}</li>)}</ul></>}
          {d.risks?.length > 0 && <><h4>Design risks</h4><ul>{d.risks.map((r: string) => <li key={r}>{r}</li>)}</ul></>}
        </>}
      </div>
    </div>
  );
}

function DiffView({ diff, files }: { diff: string; files: Json[] }) {
  const lines = (diff || "").split("\n");
  return (
    <div>
      <div className="chips">{files.map((f) => <span key={f.path} className="chip"><code>{f.path}</code> <span className="ok-t">+{f.added}</span> <span className="bad-t">−{f.removed}</span></span>)}</div>
      <pre className="diff">{lines.map((l, i) => {
        const cls = l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : l.startsWith("diff ") ? "file" : "";
        return <div key={i} className={cls}>{l || " "}</div>;
      })}</pre>
    </div>
  );
}

function TestsTab({ run }: { run: RunDetail }) {
  const report = useArtifact(run.id, "test_report", run.updated_at);
  const baseline = useArtifact(run.id, "baseline_tests", run.updated_at);
  const acceptance = useArtifact(run.id, "acceptance_eval", run.updated_at);
  const quality = useArtifact(run.id, "quality", run.updated_at);
  const qa = useArtifact(run.id, "qa_tests", run.updated_at);
  const hist: Json[] = run.data.test_history || [];
  const r = report?.content;
  return (
    <div>
      <div className="stats">
        <Stat label="Latest run" value={r ? (r.ok ? "PASS" : "FAIL") : "–"} tone={r ? (r.ok ? "ok" : "bad") : "muted"} />
        <Stat label="Tests passed" value={r ? `${r.passed}/${r.total}` : "–"} />
        <Stat label="Coverage" value={r?.coverage_pct != null ? `${r.coverage_pct}%` : "–"} sub={baseline?.content ? `baseline ${baseline.content.coverage_pct}%` : ""} />
        <Stat label="Fix iterations" value={String(run.data.fix_iteration || 0)} />
        <Stat label="Hidden acceptance" value={acceptance?.content ? `${acceptance.content.passed}/${acceptance.content.total}` : "–"} tone={acceptance?.content ? (acceptance.content.ok ? "ok" : "bad") : "muted"} sub="never shown to agents" />
        <Stat label="New lint issues" value={quality?.content ? String(quality.content.lint.new_issues.length) : "–"} sub={quality?.content?.lint.tool} />
      </div>
      <h3>Test runs</h3>
      <table className="tbl"><thead><tr><th>#</th><th>Iteration</th><th>Result</th><th>Passed</th><th>Failed</th><th>Coverage</th><th>Failing tests</th></tr></thead>
        <tbody>{hist.map((h, i) => <tr key={i}><td>{i + 1}</td><td>{h.iteration}</td><td><Badge tone={h.ok ? "ok" : "bad"}>{h.ok ? "pass" : "fail"}</Badge>{h.injected && <Badge tone="warn">injected</Badge>}</td><td>{h.passed}</td><td>{h.failed + (h.errors || 0)}</td><td>{h.coverage_pct}%</td><td className="small">{(h.failing || []).join(", ")}</td></tr>)}</tbody></table>
      {r && r.failures?.length > 0 && <><h3>Failures in the latest run</h3>{r.failures.map((f: Json) => <details key={f.test}><summary><code>{f.test}</code></summary><pre className="log">{f.message}</pre></details>)}</>}
      {qa?.content && <><h3>QA tests and criteria</h3><ul>{qa.content.tests.map((t: Json) => <li key={t.path}><code>{t.path}</code> covers {t.criteria.join(", ")} <Badge tone="info">hash-locked</Badge></li>)}</ul></>}
      {(quality?.content?.warnings?.length ?? 0) > 0 && <><h3>Quality warnings</h3><ul>{quality!.content.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul></>}
      {r && <p className="muted small">Sandbox: {r.sandbox}. Command: <code>{(r.command || []).slice(-6).join(" ")}</code></p>}
    </div>
  );
}

function SecurityView({ sec, accepted }: { sec: Json; accepted: string[] }) {
  const findings: Json[] = sec.findings || [];
  return (
    <div>
      <p>Tools: {(sec.tools || []).join(", ")}. Scanned: {(sec.scanned_files || []).map((f: string) => <code key={f} className="chip">{f}</code>)}</p>
      {sec.llm_summary && <p className="muted">LLM review: {sec.llm_summary}</p>}
      {findings.length === 0 ? <p>No findings in changed files.</p> :
        <table className="tbl"><thead><tr><th>Severity</th><th>Source</th><th>Location</th><th>Finding</th><th>CWE</th><th>Status</th></tr></thead>
          <tbody>{findings.map((f) => <tr key={f.id}>
            <td><Badge tone={f.severity === "critical" || f.severity === "high" ? "bad" : f.severity === "medium" ? "warn" : "muted"}>{f.severity}</Badge></td>
            <td>{f.source}</td><td><code>{f.path}:{f.line ?? ""}</code></td><td>{f.title}<div className="muted small">{f.recommendation}</div></td><td>{f.cwe}</td>
            <td>{f.pre_existing ? <Badge tone="muted">pre-existing</Badge> : accepted.includes(f.id) ? <Badge tone="human">accepted risk</Badge> : <Badge tone="info">new</Badge>}</td>
          </tr>)}</tbody></table>}
      <p className="muted small">New high or critical findings stop the run. Findings that were already in the code are shown but don't block. The AI reviewer can add findings but can't remove the scanners' ones.</p>
    </div>
  );
}

function ReviewTab({ run }: { run: RunDetail }) {
  const review = useArtifact(run.id, "review", run.updated_at);
  const release = useArtifact(run.id, "release", run.updated_at);
  const rv = review?.content, rl = release?.content;
  if (!rv) return <p className="muted">Not produced yet.</p>;
  const avg = rv.rubric.reduce((s: number, r: Json) => s + r.score, 0) / rv.rubric.length;
  return (
    <div className="cols">
      <div>
        <h3>Rubric <span className="muted small">(LLM judge, advisory)</span></h3>
        <p>{rv.summary}</p>
        {rv.rubric.map((r: Json) => (
          <div key={r.criterion} className="rubric">
            <div className="rubric-head"><span>{r.criterion.replace(/_/g, " ")}</span><b>{r.score}/5</b></div>
            <div className="bar"><div style={{ width: `${r.score * 20}%` }} /></div>
            <div className="muted small">{r.justification}</div>
          </div>
        ))}
        <p>Average <b>{avg.toFixed(2)}/5</b> · recommendation <Badge tone={rv.recommendation === "approve" ? "ok" : "warn"}>{rv.recommendation}</Badge></p>
      </div>
      <div>
        <h3>Known risks</h3><ul>{rv.known_risks.map((r: string) => <li key={r}>{r}</li>)}</ul>
        <h3>Criteria coverage</h3>
        <ul>{rv.coverage.map((c: Json) => <li key={c.criterion}><b>{c.criterion}</b> <Badge tone={c.status === "covered" ? "ok" : c.status === "partial" ? "warn" : "bad"}>{c.status}</Badge> <span className="muted small">{c.evidence}</span></li>)}</ul>
        {rl && <><h3>Release</h3><p>{rl.release_notes}</p><p><b>Risk:</b> {rl.risk_level}</p><p><b>Rollback:</b> {rl.rollback_plan}</p>
          <ul>{rl.deployment_checks.map((c: string) => <li key={c}>{c}</li>)}</ul></>}
      </div>
    </div>
  );
}

function EvidenceTab({ run }: { run: RunDetail }) {
  const ev = useArtifact(run.id, "evidence", run.updated_at);
  if (!ev?.content) return <p className="muted">The evidence report is created at the release step.</p>;
  return (
    <div>
      <div className="row"><a className="button" href={`/api/runs/${run.id}/evidence.md`} target="_blank" rel="noreferrer">Open as Markdown</a>
        <span className="muted small">evidence v{ev.version} · sha256 {short(ev.hash, 16)}</span></div>
      <Markdown text={ev.content.markdown} />
    </div>
  );
}

function UsageTab({ run }: { run: RunDetail }) {
  const u = run.usage;
  const [calls, setCalls] = useState<Json[]>([]);
  const [open, setOpen] = useState<Json | null>(null);
  useEffect(() => { api.llmCalls(run.id).then(setCalls).catch(() => undefined); }, [run.id, run.updated_at]);
  const maxLat = Math.max(1, ...calls.map((c) => c.latency_ms || 0));
  return (
    <div>
      <div className="stats">
        <Stat label="LLM calls" value={String(u.calls)} sub={`${u.errors} errors · ${u.cache_hits} cache hits`} />
        <Stat label="Tokens" value={(u.prompt_tokens + u.completion_tokens).toLocaleString()} sub={`${u.prompt_tokens.toLocaleString()} in · ${u.completion_tokens.toLocaleString()} out`} />
        <Stat label="Est. cost" value={`$${u.cost_usd.toFixed(4)}`} sub="paid-tier list price; free tiers bill $0" />
        <Stat label="Model time" value={`${(u.latency_ms_total / 1000).toFixed(1)}s`} />
        <Stat label="Providers" value={u.providers.join(", ") || "–"} />
      </div>
      <h3>By agent</h3>
      <table className="tbl"><thead><tr><th>Agent</th><th>Calls</th><th>Errors</th><th>Tokens</th><th>p50 ms</th><th>p95 ms</th><th>Est. $</th></tr></thead>
        <tbody>{Object.entries(u.by_agent).map(([a, s]) => <tr key={a}><td>{a}</td><td>{s.calls}</td><td>{s.errors}</td><td>{(s.prompt_tokens + s.completion_tokens).toLocaleString()}</td><td>{s.p50_ms}</td><td>{s.p95_ms}</td><td>{s.cost_usd.toFixed(4)}</td></tr>)}</tbody></table>
      <h3>Calls <span className="muted small">(click to inspect the exact prompt and response)</span></h3>
      <div className="calls">
        {calls.map((c) => (
          <button key={c.id} className={`call ${c.status !== "ok" ? "err" : ""}`} onClick={() => api.llmCall(run.id, c.id).then(setOpen)}>
            <span className="call-agent">{c.agent}</span>
            <span className="call-prov">{c.provider}{c.cache_hit ? " (cache)" : ""}</span>
            <span className="call-bar"><span style={{ width: `${(100 * (c.latency_ms || 0)) / maxLat}%` }} /></span>
            <span className="small">{Math.round(c.latency_ms)} ms</span>
            <span className="small">{c.status === "ok" ? `${c.prompt_tokens + c.completion_tokens} tok` : c.error?.slice(0, 40)}</span>
          </button>
        ))}
      </div>
      {open && (
        <div className="modal" onClick={() => setOpen(null)}>
          <div className="modal-body" onClick={(e) => e.stopPropagation()}>
            <div className="row"><h3>{open.agent} · {open.provider}/{open.model} · {open.status}</h3><button className="ghost" onClick={() => setOpen(null)}>Close</button></div>
            {(open.request || []).map((m: Json, i: number) => <details key={i} open={m.role !== "system"}><summary>{m.role}</summary><pre className="log">{m.content}</pre></details>)}
            <details open><summary>response</summary><pre className="log">{open.response_text ?? open.error}</pre></details>
          </div>
        </div>
      )}
    </div>
  );
}

function AuditTab({ run, events }: { run: RunDetail; events: ForgeEvent[] }) {
  const [v, setV] = useState<Json | null>(null);
  return (
    <div>
      <p>Each event is linked to the one before it by a hash, so editing or deleting an old event is detected.</p>
      <button onClick={() => api.verify(run.id).then(setV)}>Verify hash chain</button>
      {v && <p><Badge tone={v.ok ? "ok" : "bad"}>{v.ok ? "intact" : `tampered at seq ${v.first_bad_seq}`}</Badge> {v.events} events, head <code>{short(v.head_hash, 20)}</code></p>}
      <h3>Human decisions</h3>
      <table className="tbl"><thead><tr><th>When</th><th>Gate</th><th>Decision</th><th>By</th><th>Bound to hash</th><th>Comment</th></tr></thead>
        <tbody>{run.approvals.map((a) => <tr key={a.id}><td>{fmtTime(a.ts)}</td><td>{a.gate}</td><td>{a.decision}</td><td>{a.approver}</td><td><code>{short(a.artifact_hash, 12)}</code></td><td>{a.comment}</td></tr>)}</tbody></table>
      <h3>All events</h3>
      <table className="tbl small"><thead><tr><th>Seq</th><th>Time</th><th>Type</th><th>Stage</th><th>Agent</th><th>Hash</th><th>Prev</th></tr></thead>
        <tbody>{events.map((e) => <tr key={e.seq}><td>{e.seq}</td><td>{fmtTime(e.ts)}</td><td>{e.type}</td><td>{e.stage}</td><td>{e.agent}</td><td><code>{short(e.hash, 10)}</code></td><td><code>{short(e.prev_hash, 10)}</code></td></tr>)}</tbody></table>
    </div>
  );
}

// ---------------------------------------------------------------- chaos

function ChaosPanel() {
  const [s, setS] = useState<ChaosState | null>(null);
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);
  useEffect(() => {
    api.chaos().then(setS).catch(() => undefined);
    api.providers().then((p) => {
      const names: string[] = [];
      for (const v of Object.values(p)) if (Array.isArray(v)) v.forEach((x: Json) => names.push(x.name));
      setProviders(names);
    }).catch(() => undefined);
  }, [open]);
  if (!s) return null;
  const set = (body: Json) => api.setChaos(body).then(setS);
  const switches: [keyof ChaosState, string][] = [
    ["corrupt_next", "Corrupt next LLM output"], ["fail_tests_next", "Fail next test run"],
    ["plant_vuln_next", "Plant SQL injection in next edit"], ["repeat_next", "Repeat next side effect"],
  ];
  const armed = s.providers_down.length + switches.reduce((n, [k]) => n + Number(s[k] || 0), 0);
  return (
    <div className="chaos">
      <button className="ghost wide" onClick={() => setOpen(!open)}>⚡ Fault injection {armed > 0 && <Badge tone="warn">{armed} armed</Badge>}</button>
      {open && <div className="chaos-body">
        <p className="muted small">Use these to trigger failures on purpose. They show up as "injected" in the timeline.</p>
        <div className="small"><b>Providers</b></div>
        {providers.map((p) => (
          <label key={p} className="check small"><input type="checkbox" checked={s.providers_down.includes(p)} onChange={(e) => set({ provider: p, down: e.target.checked })} /> {p} down</label>
        ))}
        {switches.map(([k, label]) => (
          <div key={k} className="row small chaos-row"><span>{label}</span>
            <button className="ghost" onClick={() => set({ switch: k, count: Number(s[k] || 0) + 1 })}>{s[k] ? `armed ×${s[k]}` : "arm"}</button></div>
        ))}
        <button className="ghost wide" onClick={() => set({ reset: true })}>Reset all</button>
      </div>}
    </div>
  );
}

// ---------------------------------------------------------------- small UI

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: Tone }) {
  return <div className={`stat ${tone ?? ""}`}><div className="stat-label">{label}</div><div className="stat-value">{value}</div>{sub && <div className="stat-sub">{sub}</div>}</div>;
}

// Small Markdown renderer for the evidence report. React escapes the text, so no raw HTML gets through.
function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  const inline = (s: string, key: number): React.ReactNode => {
    const parts = s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
    return <span key={key}>{parts.map((p, j) => p.startsWith("`") ? <code key={j}>{p.slice(1, -1)}</code> : p.startsWith("**") ? <b key={j}>{p.slice(2, -2)}</b> : p)}</span>;
  };
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("# ")) { blocks.push(<h2 key={i}>{l.slice(2)}</h2>); i++; continue; }
    if (l.startsWith("## ")) { blocks.push(<h3 key={i}>{l.slice(3)}</h3>); i++; continue; }
    if (l.startsWith("| ")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!/^\|(-+\|)+$/.test(lines[i].replace(/\s/g, ""))) rows.push(lines[i].slice(1, -1).split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|")));
        i++;
      }
      blocks.push(<table key={i} className="tbl"><thead><tr>{rows[0].map((c, j) => <th key={j}>{c}</th>)}</tr></thead><tbody>{rows.slice(1).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c, j)}</td>)}</tr>)}</tbody></table>);
      continue;
    }
    if (l.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) { items.push(lines[i].slice(2)); i++; }
      blocks.push(<ul key={i}>{items.map((t, j) => <li key={j}>{inline(t, j)}</li>)}</ul>);
      continue;
    }
    if (l.trim()) blocks.push(<p key={i}>{inline(l, i)}</p>);
    i++;
  }
  return <div className="md">{blocks}</div>;
}
