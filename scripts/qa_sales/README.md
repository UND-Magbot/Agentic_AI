# 영업부 QA 점검 스크립트 (docs/design/sales_qa_log.md)

모두 백엔드 컨테이너 안에서 돌린다. `-v <이 폴더>:/out` 으로 넣고 결과 파일도 /out 에 쓴다.

    # 영업부 자동 테스트 20개 파일 일괄(요약 파일 경로 인자)
    bash scripts/qa_sales/run_sales_tests.sh ./qa_sales_result.txt
    # 로그인 API·화면·BFF·잘못된 입력 점검
    MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -v ./scripts/qa_sales:/out backend python /out/api_smoke.py
    # 제품 추천 평가 10문제(1순위 정답 / 금지 제품 / 묶음 카드)
    ... python /out/rec_bench.py
    # 제품 추천 화면 흐름(추천→대화→재추천→정정→재고) 8항목
    ... python /out/rec_e2e.py
    # 제안서 흐름 실제 끝까지(외부 AI 이미지 포함, 5분 안팎) — 결과 PPT /out/e2e_<tag>_v1.pptx
    ... -e PYTHONUNBUFFERED=1 backend python /out/e2e.py <tag>

실제 AI(사내·외부)를 부르므로 시간이 걸린다. 만든 프로젝트·경험 카드·추천·정정은 끝에 스스로 지운다.
