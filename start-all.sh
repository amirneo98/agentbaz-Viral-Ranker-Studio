#!/usr/bin/env bash
# Viral Ranker Studio — start everything (backend + all editors)
# Usage: ./start-all.sh        (Ctrl+C stops the editors; backend keeps running)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

# pnpm on this machine is broken via corepack; always go through npx.
PNPM="env COREPACK_ENABLE_STRICT=0 npx -y pnpm@10"

wait_http() { # url, name, tries
  local url="$1" name="$2" tries="${3:-30}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null; then
      ok "$name is up"; return 0
    fi
    sleep 2
  done
  warn "$name did not answer at $url"; return 1
}

# ---------------------------------------------------------------- backend ----
info "Starting backend + legacy studio (docker compose, ports 8000/3000)..."
if docker compose -f "$ROOT/docker-compose.yml" --project-directory "$ROOT" up -d 2>&1 | tail -2; then
  wait_http "http://localhost:8000/api/health/" "Backend (:8000)" 40
  wait_http "http://localhost:3000/" "Legacy Studio (:3000)" 20
else
  warn "docker compose failed — is Docker running?"
fi

# --------------------------------------------------------------- editors ----
PIDS=()

if [ -d "$ROOT/third_party/openreel-video" ]; then
  info "Starting OpenReel editor (:5173)..."
  ( cd "$ROOT/third_party/openreel-video" && $PNPM --filter @openreel/web dev >/tmp/openreel-dev.log 2>&1 ) &
  PIDS+=("$!")
else
  warn "third_party/openreel-video not found — skipping OpenReel"
fi

if [ -d "$ROOT/third_party/openvideo-editor" ]; then
  info "Starting OpenVideo editor (:3001)..."
  ( cd "$ROOT/third_party/openvideo-editor" && $PNPM run dev --port 3001 >/tmp/openvideo-dev.log 2>&1 ) &
  PIDS+=("$!")
else
  warn "third_party/openvideo-editor not found — skipping OpenVideo"
fi

wait_http "http://localhost:5173/" "OpenReel editor (:5173)" 40
wait_http "http://localhost:3001/" "OpenVideo editor (:3001)" 40

cat <<EOF

==============================================
  Viral Ranker Studio is running
==============================================
  OpenVideo editor (recommended)  http://localhost:3001
  OpenReel editor                 http://localhost:5173
  Legacy studio                   http://localhost:3000
  Backend API                     http://localhost:8000/api/health/

  Editors write logs to /tmp/openreel-dev.log and /tmp/openvideo-dev.log
  Stop editors: Ctrl+C here (backend keeps running)
  Stop everything: docker compose down
==============================================
EOF

trap 'echo; info "Stopping editors..."; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; exit 0' INT TERM
wait
