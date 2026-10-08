#!/usr/bin/env bash
# ============================================================
# UND Cortex Smoke Test (bash)
# req.md §13.5 인수 기준 자동 검증.
# ============================================================
set -uo pipefail

cd "$(dirname "$0")/.."

PASS=0
FAIL=0
LOG=()

check() {
  local name="$1"; shift
  printf "  %-40s" "$name"
  if "$@" > /tmp/smoke_out 2>&1; then
    PASS=$((PASS+1))
    LOG+=("PASS  $name")
    echo "PASS"
  else
    FAIL=$((FAIL+1))
    local msg
    msg=$(tail -3 /tmp/smoke_out | tr '\n' ' ')
    LOG+=("FAIL  $name :: $msg")
    echo "FAIL"
    echo "      $msg"
  fi
}

echo
echo "============================================================"
echo " UND Cortex Smoke Test"
echo "============================================================"

t_containers_healthy() {
  for s in postgres redis minio backend frontend; do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "und_cortex_${s}" 2>/dev/null || echo "missing")
    if [[ "$status" != "healthy" && "$status" != "none" ]]; then
      echo "$s status=$status"
      return 1
    fi
  done
}

t_pg_isready() {
  docker compose exec -T postgres pg_isready -U cortex
}

t_pgvector() {
  out=$(docker compose exec -T postgres psql -U cortex -d cortex -tAc "SELECT extname FROM pg_extension WHERE extname='vector';" 2>&1 | tr -d '[:space:]')
  [[ "$out" == "vector" ]] || { echo "got: $out"; return 1; }
}

t_redis_ping() {
  local pw
  pw=$(grep -E '^REDIS_PASSWORD=' .env | cut -d= -f2-)
  out=$(docker compose exec -T redis redis-cli -a "$pw" --no-auth-warning ping 2>&1 | tr -d '[:space:]')
  [[ "$out" == "PONG" ]] || { echo "got: $out"; return 1; }
}

t_minio_health() {
  curl -fsS http://localhost:9001/minio/health/live > /dev/null
}

t_backend_health() {
  out=$(curl -fsS http://localhost:8002/health) || return 1
  echo "$out" | grep -q '"ok":true' || { echo "body: $out"; return 1; }
}

t_frontend_root() {
  curl -fsS http://localhost:3010 -o /dev/null
}

t_frontend_chat() {
  # 200 만 확인. vLLM 미기동 시 응답 본문이 에러 메시지여도 라우팅 자체는 통과로 본다.
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:3010/api/chat \
    -H "Content-Type: application/json" \
    -d '{"domain":"auto","history":[],"text":"ping"}')
  [[ "$code" == "200" ]] || { echo "status=$code"; return 1; }
}

t_backend_vllm_config() {
  out=$(curl -fsS http://localhost:8002/health) || return 1
  echo "$out" | grep -q '"vllm"' || { echo "vllm url missing: $out"; return 1; }
}

check "containers all healthy"            t_containers_healthy
check "postgres pg_isready"               t_pg_isready
check "pgvector extension installed"      t_pgvector
check "redis ping"                        t_redis_ping
check "minio /minio/health/live"          t_minio_health
check "backend /health"                   t_backend_health
check "frontend root reachable"           t_frontend_root
check "frontend /api/chat smoke"          t_frontend_chat
check "backend reports vllm config"       t_backend_vllm_config

echo
echo "============================================================"
echo " 결과: PASS=$PASS FAIL=$FAIL"
echo "============================================================"

if (( FAIL > 0 )); then
  for line in "${LOG[@]}"; do
    [[ "$line" == FAIL* ]] && echo "$line"
  done
  exit 1
fi
exit 0
