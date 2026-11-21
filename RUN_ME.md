# How to run Forge on another computer

Works on **macOS** and **Linux**. On **Windows**, use **WSL** (Windows Subsystem for Linux), because the test sandbox uses Unix process controls.

## 1. Install two things (once)

| Tool | How |
|---|---|
| **git** | macOS: `xcode-select --install` · Ubuntu/WSL: `sudo apt install git` |
| **uv** (installs Python for you) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` then open a new terminal |

Windows only: open PowerShell as admin, run `wsl --install`, restart, open "Ubuntu", then do the steps above inside Ubuntu.

Node.js is **not** needed. The dashboard is already built.

## 2. Unzip and start

```bash
unzip forge.zip
cd forge
./start.sh
```

`start.sh` installs Python 3.12 and all dependencies the first time (1–2 minutes), then starts the server.

Open **http://127.0.0.1:8765** in a browser.

Manual alternative to `start.sh`:

```bash
uv python install 3.12
uv sync
uv run forge serve
```

## 3. Try the demo (no API key needed)

1. Click **Open console**, then **New run**.
2. Pick **REQ-PAY-001**, mode **Mock**, requested by `priya`, then **Start run**.
3. At the **Criteria gate**: enter your name and an answer, then click **Confirm criteria**.
4. At the **Merge gate**: approve as `alice`. Approving as `priya` is refused on purpose, because the requester cannot approve their own change.
5. At the **Deploy gate**: approve as `bob`.

## 4. Use real AI (optional)

```bash
cp .env.example .env
```

Open `.env` in any editor and paste free keys:

```
GEMINI_API_KEY=...   # https://aistudio.google.com/apikey
GROQ_API_KEY=...     # https://console.groq.com/keys
```

Restart `./start.sh` and choose **Live** mode in the New run form. Never share or commit `.env`.

## 5. Useful commands

```bash
uv run forge run REQ-INV-001     # run a scenario without the browser
uv run forge eval                # Forge vs single-agent comparison -> eval/results.md
uv run pytest                    # Forge's own 146 tests (about 90 seconds)
uv run forge serve --port 8800   # if port 8765 is busy
```

## 6. Problems

| Problem | Fix |
|---|---|
| `uv: command not found` | Open a new terminal after installing uv, or run `source ~/.local/bin/env` |
| `./start.sh: Permission denied` | `chmod +x start.sh` |
| Port already in use | `uv run forge serve --port 8800` and open that port |
| Live mode greyed out | Add a key to `.env` and restart |
| Start fresh | Stop the server and delete the `.forge` folder |

More detail: `docs/SETUP.md` (full guide), `docs/ARCHITECTURE.md` (how it works), `docs/demo-script.md` (7-minute demo).
