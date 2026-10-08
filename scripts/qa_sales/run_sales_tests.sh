#!/bin/bash
# 영업부 테스트 전체 실행 — 결과 요약을 $1 에 쓴다.
cd /c/workspace/und_cortex
OUT=${1:-./qa_sales_result.txt}
: > "$OUT"
for t in test_casebook test_codex_delegate test_company_knowledge test_concept_map_flow test_concept_plan test_confirmed test_diagram_checklist test_diagram_drawio test_diagram_rulecheck test_experience test_external_gateway test_product_images test_product_recommend test_project_deck test_proposal_body test_proposal_dialog test_proposal_llm test_proposal_project test_proposal_workflow test_robot_specs; do
  start=$(date +%s)
  res=$(MSYS_NO_PATHCONV=1 timeout 900 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 -v ./backend/app:/app/app -v ./scripts:/scripts -v ./docs:/docs backend python /scripts/$t.py --db 2>&1)
  code=$?
  sec=$(( $(date +%s) - start ))
  summary=$(echo "$res" | grep -E "PASS [0-9]+ / FAIL|pass / [0-9]+ fail|[0-9]+ pass / [0-9]+ fail|ALL OK|passed|failed|OK \(|FAILED" | tail -2 | tr '\n' ' ')
  echo "== $t exit=$code ${sec}s :: $summary" >> "$OUT"
  echo "$res" | grep -E "✗|  FAIL|FAIL:|Traceback|Error|assert" | grep -v "회사 경험 회상 실패\|GPT 호출 실패\|문구 실패\|JSON 객체를 찾지" | head -12 | sed 's/^/    /' >> "$OUT"
done
echo DONE >> "$OUT"
