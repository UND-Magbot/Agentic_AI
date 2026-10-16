# 영업 기능 검증 (test_cases)

영업 기능(제품 추천·견적서·영업 건 관리)을 **사용 관점에서** 검증한다. 기능을 만들며 같이 짠 `scripts/test_*.py` 와는 별개로,
케이스는 기존 테스트를 보지 않고 화면·API·업무 규칙만 보고 썼다. 제안서·재무·공통(경비·연차 등)은 범위 밖.

## 폴더

| 경로 | 내용 | git |
|---|---|---|
| `cases/*.md` | 기능별 테스트 케이스(번호·전제·절차·기대 결과·방식) + 코드에서 본 의심 지점 | 올리지 않음(`*.md`) |
| `runners/tc.py` | 공통 도구 — 개발 DB 확인, 로그인 토큰, 결과 기록, 테스트 데이터 정리 | 올림 |
| `runners/api/` | API 시나리오 테스트(케이스 번호로 결과를 남김) | 올림 |
| `runners/e2e/` | 브라우저 테스트(Playwright) | 올림 |
| `fixtures/samples/` | 가짜로 만든 샘플 파일(가상 고객사·가상 단가) | 올림 |
| `fixtures/private/` | 실제 자료가 필요한 테스트용 — 없으면 그 케이스는 SKIP | **제외** |
| `results/<날짜>/` | 실행 결과(json·보고서·스크린샷) | **제외** |
| `defects.md` | 찾은 오류 목록·상태 | **제외** (공개 저장소에 미수정 오류를 올리지 않음) |

## 규칙

- **개발 DB(agent_ai_dev)에서만** 돈다. 다른 DB면 시작하자마자 멈춘다(운영 DB agent_ai 보호).
- 테스트로 만드는 고객사·요청 문구에는 `TC검증` 을 붙이고, 시작·끝에 이 표시가 있는 데이터만 지운다.
  (`TC_KEEP=1` 이면 끝에 지우지 않음 — 화면에서 직접 확인할 때)
- 실행 중인 서비스(backend:8000, frontend:3000)를 그대로 부른다. 코드를 고쳤으면 먼저 배포한다.
- 실제 AI 를 부르는 케이스가 있어 시간이 걸린다.

## 실행

```bash
# 전체(API + 브라우저) — 결과는 results/<오늘>/ 에
bash test_cases/run_all.sh

# API 묶음 하나만
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -e PYTHONPATH=/app -e PYTHONIOENCODING=utf-8 \
  -v ./backend/app:/app/app -v ./test_cases:/tc backend python /tc/runners/api/<묶음>.py
```

결과 상태: PASS 통과 / FAIL 기대와 다름 / ERROR 테스트 도중 예외 / SKIP 조건이 안 돼 건너뜀(이유 기록).
