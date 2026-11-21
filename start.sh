#!/usr/bin/env bash
# One-command start for Forge on macOS / Linux / WSL.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  for p in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -x "$p/uv" ] && export PATH="$p:$PATH"
  done
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then open a new terminal and run ./start.sh again."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: installing Python 3.12 and dependencies (1-2 minutes)..."
  uv python install 3.12
  uv sync
fi

[ -f .env ] || echo "Tip: copy .env.example to .env and add a free Gemini/Groq key for live mode."

PORT="${FORGE_PORT:-8765}"
echo "Starting Forge on http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
exec uv run forge serve --port "$PORT"
