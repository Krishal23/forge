# Forge

Forge takes a product requirement and a Python repository and produces a reviewed, tested code change.
Seven AI agents do the work: analyst, architect, developer, QA, security, reviewer and release. A plain
Python program (the orchestrator) runs them in order, checks everything they return, limits how often
they can retry, and stops for a person to approve the merge and the deploy.

It was built for the Pine Labs case study round, problem "Autonomous Enterprise Engineering Organisation".

New machine? Read [RUN_ME.md](RUN_ME.md), then run `./start.sh`.

```
requirement → analyst → [criteria gate] → architect → developer → QA + tests → lint → security
            → reviewer → release → [merge gate] → merge → [deploy gate] → deploy
```
Test, lint, security and review failures go back to the developer, a limited number of times.
When a limit is reached, the run waits for a person.

## Quick start

```bash
uv sync
uv run forge serve        # then open http://127.0.0.1:8765
```

Open the console, click **New run**, pick `REQ-PAY-001` in **Mock** mode and start it. Mock mode works
offline with scripted agent answers; the code edits, tests and scans are real. For real models, copy
`.env.example` to `.env`, add a free Gemini or Groq key, and choose **Live**.

Other commands:

```bash
uv run forge run REQ-INV-001            # run a scenario without the browser
uv run forge eval                       # Forge vs a single prompt, written to eval/results.md
uv run pytest                           # Forge's own tests (160)
cd web && npm install && npm run build  # rebuild the dashboard (a built copy is already included)
```

## What the payments demo shows

`REQ-PAY-001` asks for partial refunds on `samples/payments-service`. One run hits every failure case
from the problem statement:

| Case from the problem statement | What happens in the run | How Forge handles it |
|---|---|---|
| Architect and developer disagree | The developer needs `refunds/repository.py`, which the architect didn't list | The developer asks for the file, the architect decides; sensitive files and repeated disputes go to a person |
| Change to an unrelated file | The developer follows a comment in the repo that says to delete the ownership check | The edit is blocked: wrong file, and it removes a security check |
| Generated code fails tests | An off-by-one (`>=` instead of `>`) fails QA's tests | Error output goes back to the developer, up to 3 times |
| Critical security issue | The developer copies an old string-built SQL query | The scan blocks it, one automatic fix is tried, then a person decides |
| Malformed agent output | Fault switch *Corrupt next LLM output* | The error is sent back for a corrected answer, up to 2 times |
| Model provider goes down | Fault switch *provider down* | Retry, then switch to another provider; if all are down the run pauses |
| Repeated action | Fault switch *Repeat next side effect* | The second attempt is skipped and logged |

The run stops at the merge gate with an evidence report: criteria and their tests, test runs, coverage,
hidden test results, lint and security findings, the reviewer's scores, known risks and a rollback plan.
The person who asked for the change can't approve it, the approval only counts for the diff that was
reviewed, and deployment is a separate approval.

`REQ-INV-001` (reservation expiry on `samples/inventory-service`) runs the same pipeline on a second
codebase.

## Main parts

| Part | File | Job |
|---|---|---|
| States | `forge/state.py` | Which step can follow which. Merge and deploy are only reachable from their gates. |
| Orchestrator | `forge/orchestrator.py` | Runs each step, checks the results, handles retries and escalation |
| Agents | `forge/agents/` | Prompts and output formats for each role |
| Output formats | `forge/schemas.py` | Pydantic models every agent answer must match |
| LLM gateway | `forge/llm/` | Budget check, redaction, cache, retries, provider failover, usage logging |
| Tools | `forge/tools/` | Code index, per-run git copy, edit apply, policy checks, test lock, sandbox, lint and security scans |
| Storage | `forge/store.py` | SQLite: runs, event log, artifacts, model calls, approvals |
| API | `forge/api.py` | REST and live events for the dashboard, fault switches |
| Dashboard | `web/` | React app: landing page and console |

## Layout

```
forge/            orchestrator, agents, gateway, tools, storage, API, CLI
web/              dashboard (React + Vite)
samples/          two sample repositories and the demo requirements
eval/hidden/      acceptance tests the agents never see
eval/baseline.py  comparison with a single AI prompt
tests/            tests for Forge itself
docs/             guides and design decisions
report/           final report (LaTeX)
```
