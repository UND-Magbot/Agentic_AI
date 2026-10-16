#!/bin/bash
# 영업 기능 검증 전체 실행 — API 묶음(제품 추천·견적서·영업 건) + 브라우저 테스트 → results/<오늘>/ 에 결과와 보고서.
#   bash test_cases/run_all.sh            # 전부
#   bash test_cases/run_all.sh deals      # 묶음 하나(recommend | quotes | quote_sync | deals | e2e)
# 개발 DB(agent_ai_dev)가 아니면 각 묶음이 시작하자마자 멈춘다. 실행 중인 서비스를 부르므로 코드를 고쳤으면 먼저 배포.
cd "$(dirname "$0")/.." || exit 1
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
BACK=(docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 -v ./backend/app:/app/app -v ./test_cases:/tc backend)
TC_DIR="$(pwd -W 2>/dev/null || pwd)/test_cases"
ONLY=${1:-all}
code=0

for suite in recommend quotes quote_sync deals; do
  [[ "$ONLY" == all || "$ONLY" == "$suite" ]] || continue
  echo "== $suite"
  "${BACK[@]}" python "/tc/runners/api/$suite.py" || code=1
done

if [[ "$ONLY" == all || "$ONLY" == e2e ]]; then
  echo "== e2e"
  docker image inspect und_cortex_e2e >/dev/null 2>&1 || docker build -q -t und_cortex_e2e test_cases/runners/e2e
  # next dev 는 허용 호스트(.env ALLOWED_DEV_ORIGINS)로 열어야 화면 스크립트가 돈다 — 그 호스트의 3010 으로 접속
  DEV_HOST=$(grep -E '^ALLOWED_DEV_ORIGINS=' .env | cut -d= -f2 | cut -d, -f1 | tr -d '[:space:]')
  if [[ -z "$E2E_BASE" ]]; then
    if [[ -n "$DEV_HOST" ]]; then E2E_BASE="http://$DEV_HOST:3010"; else E2E_BASE="http://frontend:3000"; fi
  fi
  "${BACK[@]}" python /tc/runners/e2e/prep.py setup \
    && docker run --rm --network und_cortex_und_cortex_net -e PYTHONIOENCODING=utf-8 -e E2E_BASE="$E2E_BASE" -v "$TC_DIR:/tc" und_cortex_e2e \
         python /tc/runners/e2e/run.py || code=1
  "${BACK[@]}" python /tc/runners/e2e/prep.py cleanup        # 실패해도 테스트 데이터·토큰 파일은 지운다
fi

"${BACK[@]}" python /tc/runners/report.py
exit $code
