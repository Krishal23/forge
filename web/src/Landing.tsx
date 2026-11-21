import { useEffect, useRef, useState, KeyboardEvent } from "react";

// Numbers on this page come from eval/results.md and the test suite.

type Nav = (p: string) => void;

export default function Landing({ navigate }: { navigate: Nav }) {
  return (
    <div className="site">
      <Hero navigate={navigate} />
      <StackBar />
      <Platform />
      <Controls />
      <Results />
      <Autonomy />
      <CtaBand navigate={navigate} />
      <Footer navigate={navigate} />
    </div>
  );
}

// ================================================================ hero

const FEED: { t: string; stage: string; kind: "ok" | "bad" | "warn" | "info" | "human"; text: string }[] = [
  { t: "14:02:11", stage: "Analysis", kind: "info", text: "7 acceptance criteria, 3 tasks, 1 open question" },
  { t: "14:02:38", stage: "Criteria gate", kind: "human", text: "priya answered: return the original refund" },
  { t: "14:03:04", stage: "Implement", kind: "info", text: "scope request: refunds/repository.py, approved by architect" },
  { t: "14:03:19", stage: "Implement", kind: "bad", text: "blocked: change removes assert_merchant_owns_payment()" },
  { t: "14:03:20", stage: "Implement", kind: "warn", text: "prompt injection suspected in refunds/service.py" },
  { t: "14:03:47", stage: "Test", kind: "bad", text: "15 passed, 6 failed: test_ac3_partial_refunds_can_reach_full_amount" },
  { t: "14:04:12", stage: "Test", kind: "ok", text: "fix iteration 1/3: 21 passed, coverage 93.3%" },
  { t: "14:04:30", stage: "Security", kind: "bad", text: "critical B608 SQL injection, refunds/repository.py:38" },
  { t: "14:04:58", stage: "Security", kind: "ok", text: "parameterised query, no blocking findings" },
  { t: "14:05:21", stage: "Review", kind: "ok", text: "hidden acceptance tests 12/12, rubric 4.3/5" },
  { t: "14:05:40", stage: "Merge gate", kind: "human", text: "waiting for approval on diff 96d472c309d6" },
];

const STEPS = ["Analysis", "Design", "Implement", "Test", "Quality", "Security", "Review", "Release", "Merge gate"];

function Hero({ navigate }: { navigate: Nav }) {
  return (
    <section className="hero" id="top">
      <div className="hero-grid container">
        <div className="hero-copy">
          <div className="eyebrow eyebrow-on-dark"><span className="eyebrow-dot" />Multi-agent software delivery</div>
          <h1 className="display-xl">
            AI agents write the change.<br />
            <span className="display-soft">People approve it.</span>
          </h1>
          <p className="hero-lede">
            Give Forge a requirement and a repository. Seven agents plan, code, test and review the change.
            Plain code checks everything they produce, and nothing is merged or deployed until a person approves it.
          </p>
          <div className="hero-actions">
            <button className="btn btn-light btn-lg" onClick={() => navigate("/console/new")}>
              Run the demo <Arrow />
            </button>
            <button className="btn btn-outline-light btn-lg" onClick={() => document.getElementById("platform")?.scrollIntoView({ behavior: "smooth" })}>
              How it works
            </button>
          </div>
          <dl className="hero-facts">
            <div><dt>7</dt><dd>specialised agents</dd></div>
            <div><dt>2</dt><dd>mandatory human gates</dd></div>
            <div><dt>3</dt><dd>fix attempts before a person steps in</dd></div>
          </dl>
        </div>
        <HeroVisual />
      </div>
    </section>
  );
}

function HeroVisual() {
  const [n, setN] = useState(4);
  useEffect(() => {
    const id = setInterval(() => setN((v) => (v >= FEED.length ? 4 : v + 1)), 1600);
    return () => clearInterval(id);
  }, []);
  const shown = FEED.slice(Math.max(0, n - 6), n);
  const current = FEED[n - 1]?.stage ?? "Implement";
  const curIdx = Math.max(0, STEPS.indexOf(current === "Criteria gate" ? "Analysis" : current));
  return (
    <div className="hero-visual" aria-label="Forge console preview">
      <div className="win">
        <div className="win-bar">
          <span className="win-dots"><i /><i /><i /></span>
          <span className="win-path mono">forge / runs / run_f71a5f6473</span>
          <span className="pill pill-live"><span className="live-dot" />live</span>
        </div>
        <div className="win-head">
          <div>
            <div className="win-kicker mono">payments-service · REQ-PAY-001</div>
            <div className="win-title">Partial refunds with idempotency</div>
          </div>
          <div className="win-cost"><span className="mono">13</span> calls<span className="sep">·</span>est. <span className="mono">$0.025</span></div>
        </div>
        <ol className="mini-steps">
          {STEPS.map((s, i) => (
            <li key={s} className={i < curIdx ? "done" : i === curIdx ? "cur" : ""}>
              <span className="tick" />
              <span className="lbl">{s}</span>
            </li>
          ))}
        </ol>
        <div className="win-body">
          <div className="feed">
            {shown.map((e) => (
              <div key={e.t + e.text} className={`feed-row k-${e.kind}`}>
                <span className="mono t">{e.t}</span>
                <span className="st">{e.stage}</span>
                <span className="tx">{e.text}</span>
              </div>
            ))}
          </div>
          <aside className="gate-panel">
            <div className="fc-head"><span className="fc-gate">Merge gate</span><span className="mono fc-hash">96d472c3</span></div>
            <div className="fc-rows">
              <div><span>Tests</span><b>21 / 21</b></div>
              <div><span>Hidden</span><b>12 / 12</b></div>
              <div><span>Blocking</span><b>0</b></div>
              <div><span>Approvers</span><b>1 of 1</b></div>
            </div>
            <div className="fc-btn">Approve merge</div>
            <div className="fc-note">Requester cannot approve their own change.</div>
          </aside>
        </div>
      </div>
    </div>
  );
}

// ================================================================ stack bar

function StackBar() {
  const items = ["Python", "pytest", "git", "FastAPI", "SQLite", "Gemini", "Groq", "bandit", "ruff", "React"];
  return (
    <section className="stackbar" aria-label="Built on">
      <div className="container stackbar-inner">
        <span className="stackbar-label">Built with</span>
        <ul className="stack-list">
          {items.map((i) => <li key={i}>{i}</li>)}
        </ul>
      </div>
    </section>
  );
}

// ================================================================ platform tabs

const TABS = [
  { id: "plan", label: "Plan", title: "Turn the requirement into checks",
    body: "The analyst writes acceptance criteria and a task list. If something in the requirement is unclear, the run waits for the product owner. The architect then picks the files the developer is allowed to change.",
    points: ["Each task points to its criteria", "Unclear points go to a person", "Allowed files picked from the import graph"] },
  { id: "build", label: "Build", title: "Check every edit before it is written",
    body: "The developer sends search-and-replace edits. Forge checks them against the allowed files, protected paths, locked tests and existing security checks. If a check fails, nothing is written.",
    points: ["Each run works on its own branch", "Requests for extra files are reviewed", "Instructions hidden in code are ignored"] },
  { id: "verify", label: "Verify", title: "Test it, and retry a few times at most",
    body: "QA writes a test for each criterion. Tests run in a sandbox, and failures go back to the developer with the error output, up to three times. A new serious security finding stops the run.",
    points: ["Tests and coverage in a sandbox", "Only new lint and security issues count", "Extra hidden tests check the result"] },
  { id: "approve", label: "Approve", title: "A person approves merge and deploy",
    body: "The reviewer gets one report built from the actual test and scan results. An approval only counts for the exact diff that was reviewed, the person who asked for the change can't approve it, and deployment needs a second approval.",
    points: ["Two approvers for sensitive files", "Accepting a risk needs a written reason", "Every step is logged"] },
] as const;

type TabId = (typeof TABS)[number]["id"];

function Platform() {
  const [active, setActive] = useState<TabId>("plan");
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const idx = TABS.findIndex((t) => t.id === active);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : -1;
    const next = (idx + d + TABS.length) % TABS.length;
    setActive(TABS[next].id);
    refs.current[next]?.focus();
  };
  const tab = TABS[idx];
  return (
    <section className="section" id="platform">
      <div className="container">
        <SectionHead eyebrow="Platform" title="How a run works"
          lede="Pick a step to see what it looks like in the console." />
        <div className="platform">
          <div className="ptabs" role="tablist" aria-orientation="vertical" onKeyDown={onKey}>
            {TABS.map((t, i) => (
              <button key={t.id} ref={(el) => { refs.current[i] = el; }} role="tab" id={`tab-${t.id}`}
                aria-selected={active === t.id} aria-controls={`panel-${t.id}`} tabIndex={active === t.id ? 0 : -1}
                className={`ptab ${active === t.id ? "on" : ""}`} onClick={() => setActive(t.id)}>
                <span className="ptab-num mono">0{i + 1}</span>
                <span className="ptab-text">
                  <span className="ptab-label">{t.label}</span>
                  <span className="ptab-title">{t.title}</span>
                </span>
              </button>
            ))}
          </div>
          <div className="pstage" role="tabpanel" id={`panel-${tab.id}`} aria-labelledby={`tab-${tab.id}`}>
            <div className="pcopy" key={`c-${tab.id}`}>
              <h3 className="display-sm">{tab.title}</h3>
              <p>{tab.body}</p>
              <ul className="checks">{tab.points.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
            <div className="pframe" key={`f-${tab.id}`}>
              {tab.id === "plan" && <PlanMock />}
              {tab.id === "build" && <BuildMock />}
              {tab.id === "verify" && <VerifyMock />}
              {tab.id === "approve" && <ApproveMock />}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function MockBar({ title, meta }: { title: string; meta: string }) {
  return <div className="mbar"><span className="mbar-title">{title}</span><span className="mono mbar-meta">{meta}</span></div>;
}

function PlanMock() {
  const rows = [
    ["AC-1", "captured payment of 10000", "merchant refunds 3000", "status partially_refunded"],
    ["AC-2", "refunds totalling 7000", "refund of 3001 requested", "RefundError, nothing stored"],
    ["AC-5", "refund with idempotency key k", "request retried with k", "original refund returned"],
    ["AC-7", "existing refund rules", "another merchant refunds", "AuthorizationError as before"],
  ];
  return (
    <div className="mock">
      <MockBar title="Acceptance criteria" meta="analysis v1 · sha 3be1f0" />
      <table className="mtable">
        <thead><tr><th>ID</th><th>Given</th><th>When</th><th>Then</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r[0]}><td className="mono strong">{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>)}</tbody>
      </table>
      <div className="mcallout">
        <div className="mcallout-k">Criteria gate · waiting for product owner</div>
        <div>If a retry reuses an idempotency key with a different amount, return the original refund or reject?</div>
      </div>
      <div className="mscope">
        <span className="mlabel">Change scope</span>
        <code>refunds/service.py</code><code>api/handlers.py</code>
        <span className="mlabel muted">out of scope</span><code className="off">payments/auth.py</code>
      </div>
    </div>
  );
}

function BuildMock() {
  return (
    <div className="mock">
      <MockBar title="Proposed change" meta="developer · attempt 2" />
      <div className="mcode">
        <div className="ln ctx"><i>12</i>def issue_refund(conn, merchant_id, payment_id, amount=None):</div>
        <div className="ln ctx"><i>13</i>    payment = get_payment(conn, payment_id)</div>
        <div className="ln del"><i>14</i>    auth.assert_merchant_owns_payment(payment, merchant_id)</div>
        <div className="ln add"><i>14</i>    if idempotency_key is not None:</div>
        <div className="ln add"><i>15</i>        existing = repository.find_refund_by_key(...)</div>
      </div>
      <div className="mblock">
        <div className="mblock-k">Blocked by policy · nothing written</div>
        <div className="mblock-row"><span className="mono">guarded_call_removed</span><span>refunds/service.py removes a call to a high-risk check</span></div>
        <div className="mblock-row"><span className="mono">out_of_scope</span><span>payments/auth.py is not in the approved scope</span></div>
      </div>
      <div className="mnote">A comment in the repo told AI tools to delete this check. Forge treats repo text as data, not instructions.</div>
    </div>
  );
}

function VerifyMock() {
  return (
    <div className="mock">
      <MockBar title="Verification" meta="sandbox · no network · 180 s limit" />
      <div className="mruns">
        <div className="mrun"><span className="mono">#1</span><span className="tag tag-bad">fail</span><span>15 passed · 6 failed</span><span className="mono muted">refunded + amount &gt;= payment.amount</span></div>
        <div className="mrun"><span className="mono">#2</span><span className="tag tag-ok">pass</span><span>21 passed · coverage 93.3%</span><span className="mono muted">fix iteration 1 of 3</span></div>
      </div>
      <div className="mfind">
        <div className="mfind-row"><span className="tag tag-bad">critical</span><span className="mono">B608</span><span>SQL built from strings</span><span className="mono muted">refunds/repository.py:38</span><span className="tag tag-quiet">fixed</span></div>
        <div className="mfind-row"><span className="tag tag-quiet">medium</span><span className="mono">B608</span><span>legacy reporting query</span><span className="mono muted">refunds/repository.py:29</span><span className="tag tag-quiet">pre-existing</span></div>
      </div>
      <div className="mmeter">
        <div><span className="mlabel">Hidden acceptance tests</span><b className="mono">12 / 12</b></div>
        <div className="meter"><span style={{ width: "100%" }} /></div>
      </div>
    </div>
  );
}

function ApproveMock() {
  return (
    <div className="mock">
      <MockBar title="Merge gate" meta="bound to diff 96d472c309d68979" />
      <ul className="mevidence">
        <li><span>Criteria with tests</span><b>7 / 7</b></li>
        <li><span>Final test run</span><b>21 passed</b></li>
        <li><span>New blocking findings</span><b>0</b></li>
        <li><span>Policy violations blocked</span><b>2</b></li>
        <li><span>Known risk</span><b>concurrent refunds may race</b></li>
      </ul>
      <div className="mapprove">
        <div className="mfield"><span className="mlabel">Approver</span><span className="mono">alice</span></div>
        <div className="mbtns"><span className="mbtn mbtn-primary">Approve merge</span><span className="mbtn">Request changes</span></div>
      </div>
      <div className="mdeny">priya (requester) · approval refused: separation of duties</div>
    </div>
  );
}

// ================================================================ controls bento

function Controls() {
  return (
    <section className="section section-tint" id="controls">
      <div className="container">
        <SectionHead eyebrow="Controls" title="Safety checks"
          lede="Each of these is regular code with its own tests, so it works no matter what the model says." />
        <div className="bento">
          <article className="cell c-wide">
            <CellHead k="Scope guard" t="Edits to files outside the plan are refused before anything is written." />
            <div className="viz viz-scope">
              <div className="vrow ok"><code>refunds/service.py</code><span>in scope</span></div>
              <div className="vrow ok"><code>api/handlers.py</code><span>in scope</span></div>
              <div className="vrow no"><code>payments/auth.py</code><span>blocked · high-risk</span></div>
              <div className="vrow no"><code>tests/test_refunds.py</code><span>blocked · QA owns tests</span></div>
            </div>
          </article>
          <article className="cell">
            <CellHead k="Test lock" t="Tests are fingerprinted at the start. Changing or deleting one fails the run." />
            <div className="viz viz-hash mono"><span>tests/test_refunds.py</span><span>a41c…9e02</span></div>
          </article>
          <article className="cell">
            <CellHead k="Sandbox" t="Tests run without API keys, with CPU and time limits and no internet access." />
            <div className="viz viz-env mono"><span>GEMINI_API_KEY</span><s>removed</s><span>connect 93.184.216.34</span><s>refused</s></div>
          </article>
          <article className="cell">
            <CellHead k="Provider failover" t="If a model provider fails, Forge retries and then switches to another one." />
            <div className="viz viz-prov">
              <div><span className="dot off" />gemini-3.5-flash<span className="mono">quota</span></div>
              <div><span className="dot on" />gemini-3-flash<span className="mono">serving</span></div>
              <div><span className="dot idle" />groq gpt-oss-120b<span className="mono">standby</span></div>
            </div>
          </article>
          <article className="cell">
            <CellHead k="Retry limits" t="Every retry has a limit. After that, a person decides what happens." />
            <div className="viz viz-bounds">
              {["Schema repair", "Fix iterations", "Security fix", "Disputes"].map((l, i) => (
                <div key={l}><span>{l}</span><span className="pips">{Array.from({ length: [2, 3, 1, 2][i] }).map((_, j) => <i key={j} className={j < [1, 1, 1, 0][i] ? "used" : ""} />)}</span></div>
              ))}
            </div>
          </article>
          <article className="cell c-wide">
            <CellHead k="Audit log" t="Each event is linked to the one before it, so any later edit to the log is detected." />
            <div className="viz viz-chain mono">
              {["0000…0000", "8c1f…a2d4", "e94b…07c1", "2bb1…ae75"].map((h, i) => (
                <div key={h} className="link"><span className="seq">#{[0, 1, 2, 115][i]}</span><span>{h}</span></div>
              ))}
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}

function CellHead({ k, t }: { k: string; t: string }) {
  return <header className="cell-head"><h3>{k}</h3><p>{t}</p></header>;
}

// ================================================================ results

function Results() {
  const metrics = [
    { v: "12/12", l: "hidden acceptance tests passed", s: "single agent on the same task: 1/12" },
    { v: "0", l: "security checks removed", s: "single agent deleted the ownership check" },
    { v: "146", l: "automated tests for Forge itself", s: "state machine, policy, gateway, end to end" },
    { v: "3", l: "fix iterations before escalation", s: "then a person chooses retry or abort" },
    { v: "2", l: "approvers on high-risk paths", s: "requester always excluded" },
    { v: "$0.02", l: "estimated model cost per run", s: "paid-tier list price; free tiers bill $0" },
  ];
  return (
    <section className="section section-dark" id="results">
      <div className="container">
        <SectionHead dark eyebrow="Results" title="Results"
          lede="From the evaluation script in the repo, on the partial-refunds task. The hidden tests are never shown to the agents." />
        <div className="metrics">
          {metrics.map((m) => (
            <div className="metric" key={m.l}>
              <div className="metric-v">{m.v}</div>
              <div className="metric-l">{m.l}</div>
              <div className="metric-s">{m.s}</div>
            </div>
          ))}
        </div>
        <div className="compare">
          <div className="compare-head"><span>Same task: one AI prompt vs Forge</span><span className="mono">eval/results.md</span></div>
          <table>
            <thead><tr><th /><th>Hidden tests</th><th>Existing tests</th><th>High-risk files changed</th><th>Checks removed</th></tr></thead>
            <tbody>
              <tr><td>Single agent, one prompt</td><td className="mono">1 / 12</td><td className="neg">failing</td><td className="mono">payments/auth.py</td><td className="mono neg">assert_merchant_owns_payment</td></tr>
              <tr className="win-row"><td>Forge</td><td className="mono">12 / 12</td><td>passing</td><td>none</td><td>none</td></tr>
            </tbody>
          </table>
          <p className="footnote">These numbers come from mock mode, where agent answers are scripted, so they show what the checks catch rather than how good a model is. With Gemini, the inventory task passed 9/9 hidden tests.</p>
        </div>
      </div>
    </section>
  );
}

// ================================================================ autonomy

function Autonomy() {
  const rows: [string, string, string][] = [
    ["Acceptance criteria", "Analyst drafts, person answers open questions", "agent"],
    ["Files that may change", "Architect sets scope; high-risk paths need a person", "agent"],
    ["Code edits inside scope", "Developer, on an isolated branch", "agent"],
    ["Fix iterations", "Agents, at most three per run", "agent"],
    ["Accepting a security risk", "A person other than the requester, with a written reason", "human"],
    ["Merge", "A person; two for high-risk paths", "human"],
    ["Deploy", "A person, as a separate decision", "human"],
  ];
  return (
    <section className="section" id="autonomy">
      <div className="container autonomy">
        <div>
          <SectionHead eyebrow="Governance" title="What agents decide and what people decide"
            lede="Agents do work that can be undone. Merging, deploying and accepting a risk are left to people, and the workflow has no way around that." />
        </div>
        <div className="auth-table" role="table">
          <div className="auth-row auth-h" role="row"><span>Decision</span><span>Who decides</span><span /></div>
          {rows.map(([d, w, k]) => (
            <div className="auth-row" role="row" key={d}>
              <span className="strong">{d}</span><span>{w}</span>
              <span className={`who who-${k}`}>{k === "human" ? "Human only" : "Agent, bounded"}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ================================================================ cta + footer

function CtaBand({ navigate }: { navigate: Nav }) {
  return (
    <section className="cta">
      <div className="container cta-inner">
        <div>
          <h2 className="display-md">Try the demo</h2>
          <p>It runs offline in mock mode, or with free Gemini and Groq keys.</p>
        </div>
        <button className="btn btn-light btn-lg" onClick={() => navigate("/console/new")}>Start a run <Arrow /></button>
      </div>
    </section>
  );
}

function Footer({ navigate }: { navigate: Nav }) {
  const scroll = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  return (
    <footer className="footer" id="footer">
      <div className="container footer-grid">
        <div className="footer-brand">
          <div className="wordmark wordmark-dark"><span>Forge</span></div>
          <p>A multi-agent pipeline that takes a requirement to a reviewed change, with people approving merge and deploy.</p>
        </div>
        <div>
          <h4>Product</h4>
          <button onClick={() => scroll("platform")}>Platform</button>
          <button onClick={() => scroll("controls")}>Controls</button>
          <button onClick={() => scroll("results")}>Results</button>
          <button onClick={() => navigate("/console")}>Console</button>
        </div>
        <div>
          <h4>Documentation</h4>
          <span>docs/SETUP.md</span>
          <span>docs/ARCHITECTURE.md</span>
          <span>docs/agents.md</span>
          <span>docs/adr</span>
        </div>
        <div>
          <h4>Governance</h4>
          <button onClick={() => scroll("autonomy")}>Where autonomy stops</button>
          <span>Hash-bound approvals</span>
          <span>Separation of duties</span>
          <span>Audit chain</span>
        </div>
      </div>
      <div className="container footer-base">
        <span>Forge · case study prototype · runs locally on 127.0.0.1</span>
        <span>Not affiliated with Pine Labs.</span>
      </div>
    </footer>
  );
}

// ================================================================ shared

function SectionHead({ eyebrow, title, lede, dark }: { eyebrow: string; title: string; lede: string; dark?: boolean }) {
  return (
    <header className={`shead ${dark ? "shead-dark" : ""}`}>
      <div className={`eyebrow ${dark ? "eyebrow-on-dark" : ""}`}>{eyebrow}</div>
      <h2 className="display-lg">{title}</h2>
      <p className="lede">{lede}</p>
    </header>
  );
}

function Arrow() {
  return (
    <svg className="arrow" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M1 7h11M8 3l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="square" />
    </svg>
  );
}
