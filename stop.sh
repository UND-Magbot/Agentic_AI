#!/usr/bin/env bash
# ============================================================
# UND Cortex 종료 스크립트
# 사용법:
#   ./stop.sh          - 컨테이너만 정리 (볼륨 보존)
#   ./stop.sh --clean  - 컨테이너 + 볼륨 + 고아 컨테이너 모두 제거
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

DOWN_ARGS=()
if [[ "${1:-}" == "--clean" ]]; then
  DOWN_ARGS+=(-v --remove-orphans)
fi

echo "[1/2] 컨테이너 종료..."
docker compose --profile gpu down "${DOWN_ARGS[@]}"

echo "[2/2] 잔여 컨테이너 확인..."
docker compose ps

echo
echo "============================================================"
echo " UND Cortex 종료 완료"
if [[ "${1:-}" == "--clean" ]]; then
  echo " 볼륨 (postgres/redis/minio/vllm/node_modules) 가 모두 제거되었습니다."
else
  echo " 볼륨은 보존되었습니다. 완전 초기화는 ./stop.sh --clean"
fi
echo "============================================================"
