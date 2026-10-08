"""제안서 전용 LLM 호출(proposal_llm) 단위 검증 — 서버 불필요(가짜 응답 주입).

검증 대상: 일시 오류 무음 재시도, 영구 오류 즉시 실패, 사고 모드 미지원 자동 해제.
"""
import asyncio
import io
import json
import sys
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import proposal_llm  # noqa: E402
from app.config import settings  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def eq(actual, expected, label: str) -> None:
    if actual == expected:
        PASS.append(label)
    else:
        FAIL.append((label, f"got {actual!r} != {expected!r}"))


OK_BODY = {"message": {"role": "assistant", "content": '{"ok": true}'}}
OOM = (500, {"error": "an error was encountered while running the model: CUDA error: out of memory"})


def run(responses: list, **kw):
    """responses: (status, body) 또는 예외 인스턴스 목록. 호출 순서대로 소비한다."""
    calls: list[dict] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    real_client = httpx.AsyncClient
    proposal_llm.httpx.AsyncClient = lambda **k: real_client(transport=httpx.MockTransport(handler), **k)
    # 컨텍스트는 공용 설정(settings.ollama_num_ctx) 하나로 통일됐다 — 확장 로직 검증은 설정값을 바꿔서 한다.
    real_ctx = settings.ollama_num_ctx
    if "ctx_setting" in kw:
        settings.ollama_num_ctx = kw.pop("ctx_setting")
    try:
        result = asyncio.run(proposal_llm.chat([{"role": "user", "content": "x"}], **kw))
        err = None
    except proposal_llm.ProposalLLMError as e:
        result, err = None, str(e)
    finally:
        proposal_llm.httpx.AsyncClient = real_client
        settings.ollama_num_ctx = real_ctx
    return result, err, calls


proposal_llm.RETRY_DELAYS = (0, 0, 0, 0, 0)     # 테스트에서는 기다리지 않는다

# 1) GPU 메모리 부족 2회 → 3번째 성공: 오류를 올리지 않고 결과만 돌려준다
res, err, calls = run([OOM, OOM, (200, OK_BODY)])
eq(res, '{"ok": true}', "OOM 2회 후 성공 → 결과 반환")
eq(err, None, "OOM 재시도 중 예외 없음")
eq(len(calls), 3, "OOM 재시도 호출 3회")

# 2) 연결 끊김(서버 재시작) → 재시도 후 성공
res, err, calls = run([httpx.ConnectError("refused"), (200, OK_BODY)])
eq(res, '{"ok": true}', "연결 오류 후 성공")
eq(len(calls), 2, "연결 오류 재시도 호출 2회")

# 3) 없는 모델(404) → 재시도 없이 즉시 실패
res, err, calls = run([(404, {"error": "model 'x' not found"})])
eq(len(calls), 1, "404 는 재시도하지 않음")
eq(err is not None and "404" in err, True, "404 는 ProposalLLMError")

# 4) 계속 500 → 재시도 한도(5회) 후 실패
res, err, calls = run([OOM] * 6)
eq(len(calls), 6, "재시도 한도: 최초 1 + 재시도 5")
eq(err is not None and "계속 응답하지 않습니다" in err, True, "한도 초과 시 ProposalLLMError")

# 5) 사고 모드 미지원 모델 → think 빼고 다시 보내 성공
res, err, calls = run([(400, {"error": '"gemma3:12b" does not support thinking'}), (200, OK_BODY)], think=True)
eq(res, '{"ok": true}', "사고 모드 미지원 → 자동 해제 후 성공")
eq(calls[0].get("think"), True, "첫 요청은 think=True")
eq("think" in calls[1], False, "재요청은 think 제거")

# 6) 설정값 반영
res, err, calls = run([(200, OK_BODY)])
eq(calls[0]["model"], settings.proposal_model, "제안서 전용 모델 사용")
eq(calls[0].get("think"), bool(settings.proposal_think), "기본 사고 모드 = 설정값")
eq(calls[0]["options"]["num_ctx"], settings.ollama_num_ctx, "컨텍스트 = 공용 설정값")
res, err, calls = run([(200, OK_BODY)], num_ctx=24576)
eq(calls[0]["options"]["num_ctx"], settings.ollama_num_ctx, "호출부가 넘긴 num_ctx 는 무시(재적재 방지)")
eq(calls[0]["format"], "json", "기본 format=json")
res, err, calls = run([(200, OK_BODY)], think=False, fmt=None)
eq(calls[0].get("think"), False, "think=False 는 명시 전송(gemma4 기본값이 켜짐이라)")
eq("format" in calls[0], False, "fmt=None 이면 format 없음")

# 7) 사고가 길어 답이 비고 길이 한도에 걸림 → 컨텍스트를 넓혀 다시 생성
EMPTY_LEN = {"message": {"role": "assistant", "content": "", "thinking": "..."}, "done_reason": "length"}
res, err, calls = run([(200, EMPTY_LEN), (200, OK_BODY)], ctx_setting=8192)
eq(res, '{"ok": true}', "빈 응답(길이 한도) → 재생성 후 결과")
eq([c["options"]["num_ctx"] for c in calls], [8192, 16384], "num_ctx 두 배로 확장")
res, err, calls = run([(200, EMPTY_LEN)] * 3, ctx_setting=16384, think=False)
eq([c["options"]["num_ctx"] for c in calls[:2]], [16384, 32768], "상한(32K)까지만 확장")
eq(len(calls), 2, "사고 끈 상태에서 상한 도달 후에는 빈 응답을 그대로 반환(호출자가 판단)")

# 8) 생성 한도를 다 쓴 빈 응답(생각이 안 끝남) → 공간 확장 없이 사고 모드 끄고 재시도
EMPTY_PRED = {"message": {"role": "assistant", "content": "", "thinking": "..."},
              "done_reason": "length", "eval_count": 12000}
res, err, calls = run([(200, EMPTY_PRED), (200, OK_BODY)], think=True, ctx_setting=16384, num_predict=12000)
eq(res, '{"ok": true}', "생각 미종료 → 사고 끄고 결과")
eq([(c["think"], c["options"]["num_ctx"]) for c in calls], [(True, 16384), (False, 16384)], "공간은 그대로, 사고만 끔")

# 9) 컨텍스트 상한까지 늘려도 빈 응답 → 마지막으로 사고 끄고 재시도
res, err, calls = run([(200, EMPTY_LEN), (200, EMPTY_LEN), (200, OK_BODY)], think=True, ctx_setting=16384)
eq([(c["think"], c["options"]["num_ctx"]) for c in calls], [(True, 16384), (True, 32768), (False, 32768)],
   "공간 확장 후에도 비면 사고 끔")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, why in FAIL:
    print(f"  FAIL {label}: {why}")
sys.exit(1 if FAIL else 0)
