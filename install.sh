#!/usr/bin/env bash
# Viral Ranker Studio — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/amirneo98/agentbaz-Viral-Ranker-Studio/main/install.sh | bash
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

REPO_URL="https://github.com/amirneo98/agentbaz-Viral-Ranker-Studio.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/viral-ranker-studio}"

echo "=============================================="
echo "   Viral Ranker Studio — Installer"
echo "   Ranking/compilation video studio (Docker)"
echo "=============================================="

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || fail "git is not installed. Run: sudo apt install -y git"

if ! command -v docker >/dev/null 2>&1; then
    warn "Docker not found. Installing Docker Engine..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo systemctl enable --now docker
    ok "Docker installed: $(docker --version)"
else
    ok "Docker found: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
    fail "Docker Compose v2 plugin is required. Install it, then re-run this script."
fi
ok "Docker Compose: $(docker compose version | head -1)"

# non-root docker access
if ! docker info >/dev/null 2>&1; then
    if [ "$(id -u)" != "0" ]; then
        warn "Current user cannot talk to the Docker daemon."
        info "Adding user '$USER' to the 'docker' group (sudo password may be asked)..."
        sudo usermod -aG docker "$USER" || fail "Could not add user to docker group"
        warn "Group change needs a new login session. Log out and back in (or run 'newgrp docker'), then re-run this script."
        exit 0
    fi
fi
ok "Docker access verified"

# --- optional: NVIDIA GPU ---------------------------------------------------
GPU_ARGS=""
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    ok "NVIDIA GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    if docker info 2>/dev/null | grep -qi nvidia; then
        ok "NVIDIA container runtime is active → hardware-accelerated encoding (NVENC) enabled"
        GPU_ARGS="--gpu"
    else
        warn "NVIDIA container runtime not configured; falling back to CPU encoding (libx264)."
        warn "For NVENC: install nvidia-container-toolkit and restart Docker."
    fi
else
    warn "No NVIDIA GPU detected; rendering will use CPU (libx264). Everything else works normally."
fi

# --- clone ------------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Existing installation found at $INSTALL_DIR — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Cloning into $INSTALL_DIR ..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
ok "Code ready at $INSTALL_DIR (branch: $(git branch --show-current))"

# --- environment ------------------------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env
    ok "Created .env from template (defaults are fine for local use)"
fi

mkdir -p bgm data media
[ -f bgm/.gitkeep ] || touch bgm/.gitkeep

# --- build & start ----------------------------------------------------------
info "Building containers (first build takes a few minutes)..."
docker compose build
info "Starting stack..."
docker compose up -d

info "Waiting for backend health check..."
for i in $(seq 1 60); do
    if curl -fsS http://localhost:8000/api/health/ >/dev/null 2>&1; then
        ok "Backend is healthy"
        break
    fi
    [ "$i" = 60 ] && { docker compose logs --tail=50 backend; fail "Backend did not become healthy in 60s"; }
    sleep 2
done

echo
echo "=============================================="
echo -e "${GREEN} Viral Ranker Studio is running! ${NC}"
echo "=============================================="
echo "  Open the studio:   http://localhost:3000"
echo "  API health:        http://localhost:8000/api/health/"
echo "  Install dir:       $INSTALL_DIR"
echo
echo "  Drop BGM tracks (mp3/wav) into:  $INSTALL_DIR/bgm/"
echo "    then: docker compose restart backend"
echo
echo "  Stop:    cd $INSTALL_DIR && docker compose down"
echo "  Update:  cd $INSTALL_DIR && git pull && docker compose up -d --build"
echo "=============================================="
