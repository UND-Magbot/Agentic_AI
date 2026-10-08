#!/usr/bin/env bash
# ============================================================
# UND Cortex - start script (Linux / macOS / WSL / git-bash)
# Usage:
#   ./start.sh         - start base services only
#   ./start.sh --gpu   - include vLLM (NVIDIA GPU + nvidia-container-toolkit required)
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

PROFILE_ARGS=()
WAIT_TIMEOUT=180
if [[ "${1:-}" == "--gpu" ]]; then
  PROFILE_ARGS=(--profile gpu)
  WAIT_TIMEOUT=900
fi

echo "[1/5] Checking Docker..."
if ! docker info > /dev/null 2>&1; then
  echo "  ERROR: Docker is not running. Start Docker Desktop / dockerd first."
  exit 1
fi
echo "  OK"

echo "[2/5] Checking .env file..."
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    echo "  .env created from .env.example. Please review the values."
  else
    echo "  ERROR: .env.example not found."
    exit 1
  fi
else
  echo "  OK"
fi

echo "[3/5] Pulling external images..."
# 받을 수 없는 이미지(예: minio/minio 가 Docker Hub 에서 내려감)는 로컬 이미지로 계속 진행.
# 로컬에도 없으면 [5/5] up 단계에서 실패한다.
docker compose "${PROFILE_ARGS[@]}" pull --ignore-buildable --ignore-pull-failures \
  || echo "  WARN: some images could not be pulled - using local images."

echo "[4/5] Building local images..."
docker compose "${PROFILE_ARGS[@]}" build

echo "[5/5] Starting containers, waiting for healthy, timeout ${WAIT_TIMEOUT}s..."
if ! docker compose "${PROFILE_ARGS[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT"; then
  echo
  echo "  ERROR: not all services became healthy in time."
  echo "  recent logs, last 100 lines:"
  docker compose "${PROFILE_ARGS[@]}" logs --tail=100
  exit 1
fi

echo
echo "============================================================"
echo " UND Cortex is up"
echo "============================================================"
echo " Web UI         : http://localhost:3010"
echo " Backend API    : http://localhost:8002"
echo " API docs       : http://localhost:8002/docs"
echo " MinIO console  : http://localhost:9001"
echo " Postgres       : localhost:5432"
echo " Redis          : localhost:6379"
if [[ "${1:-}" == "--gpu" ]]; then
  echo " vLLM           : http://localhost:8001"
fi
echo "============================================================"
echo " Logs   docker compose logs -f SERVICE_NAME"
echo " Stop   ./stop.sh        or  ./stop.sh --clean"
echo " Smoke  ./scripts/smoke.sh"
echo "============================================================"
