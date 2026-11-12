#!/usr/bin/env bash
# Start the API (:8110, with reload) and the web app (:5290) for development.
# The three stores are expected on their ports; start them with: docker compose up -d
# Without them everything still starts and the Status page shows them as down.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x backend/.venv/bin/uvicorn ] || [ ! -d frontend/node_modules ]; then
  echo "Run 'make setup' first." >&2
  exit 1
fi

# Node 22 or newer. If the shell's node is older and nvm is installed, switch.
if [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -lt 22 ] \
   && [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  nvm use 22 >/dev/null
fi

trap 'kill 0' EXIT INT TERM

(cd backend && exec .venv/bin/uvicorn app.main:app_factory --factory --reload --port "${BI_PORT:-8110}") &
(cd frontend && exec npm run dev) &

echo "API  http://localhost:${BI_PORT:-8110}/healthz"
echo "Web  http://localhost:5290"
wait
