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

## Documentation

| File | What's in it |
|---|---|
| [RUN_ME.md](RUN_ME.md) | Install and run on another computer |
| [docs/SETUP.md](docs/SETUP.md) | Full setup, live mode with API keys, configuration, troubleshooting |
| [docs/TESTING.md](docs/TESTING.md) | Step-by-step manual test of every requirement |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works |
| [docs/agents.md](docs/agents.md) | What each agent receives, returns and may change |
| [docs/demo-script.md](docs/demo-script.md) | A 7-minute demo plan |
| [docs/adr/](docs/adr) | Design decisions |
| [report/forge-report.tex](report/forge-report.tex) | Final report (LaTeX) |
| [eval/results.md](eval/results.md) | Forge compared with a single AI prompt |

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

## Who decides what

| Decision | Who | Why |
|---|---|---|
| Acceptance criteria and tasks | Analyst, with a person answering open questions | The business intent belongs to people |
| Which files change, and how | Architect | It's advice and easy to undo |
| Code edits in the allowed files | Developer, on its own branch | Easy to undo, and checked by tests and scans |
| Extra files | Architect; sensitive files and deadlocks go to a person | Keeps the change small |
| Fix attempts | Agents, up to 3 per run | After that, a person |
| Accepting a security risk | A person other than the requester, with a written reason | Someone has to own that decision |
| Merge | A person; two people for sensitive files | It changes shared code |
| Deploy | A person, separately from merge | It affects customers |

## Your own repository

Forge works on any Python repository that has pytest tests. In the console, choose **Live**, pick
**Custom requirement**, then under **Repository** either:

- **Upload .zip**: a zip of the repo (a GitHub "Download ZIP" works). It is unpacked into
  `.forge/repos/<name>/` and kept for later runs.
- **Folder on this computer**: a path such as `~/code/my-service`. Forge records the path and copies
  the folder for each run, so your files are not changed.

Each repo is checked when you add it: Forge finds the source and test folders (including the `src/`
layout), runs the existing tests on a copy and lists any problems, such as missing packages. Forge runs
tests with its own Python, so install the repo's test dependencies there first
(`uv pip install -r requirements.txt`). A `forge.toml` in the repo is optional; it lets you mark
protected and high-risk files (see `samples/payments-service/forge.toml`).

From the command line:

```bash
uv run forge repo add ~/Downloads/my-service.zip    # or a folder path
uv run forge repo list
uv run forge run --repo my-service --requirement "Add ..." --mode live
```

Mock mode only runs the two sample scenarios, because its agent answers are written for them.

## Assumptions and limits

- Deployment is simulated: the merge goes into the run's own copy of the repo, and the tests run there
  as a smoke test. The original sample folders are never changed.
- Approver names are typed in. A real setup would take them from SSO.
- Tests run in a process sandbox (clean environment, resource limits, timeout, no internet for Python
  code). It is not a container.
- Mock mode uses scripted agent answers. Live mode uses real models.
- Only Python repositories with pytest tests are supported (ast, pytest, ruff, bandit). Uploads are
  limited to 50 MB zipped and 200 MB unpacked.
- Cost is an estimate at paid prices. Free tiers cost nothing but have small daily limits.
- Tasks are stored in Forge, not sent to Jira or similar.
- Card-number and secret masking is pattern based.
- Live mode: the inventory task passes; the payments task can still fail when the backup model
  returns broken JSON, in which case Forge stops and asks a person.

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
