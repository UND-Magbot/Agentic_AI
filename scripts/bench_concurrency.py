"""gemma4:12b 동시 사용자 부하 측정 — 사용자 N 명이 같은 순간 요청할 때 속도·대기·GPU 적재.

작업 두 종류(앱과 같은 조건: think=false):
  chat     : 짧은 질문 + 최대 256토큰 답변
  proposal : 약 5천 토큰 입력 + 최대 512토큰
  둘 다 num_ctx = NUM_CTX(앱 settings.ollama_num_ctx 와 동일, 2026-09-29 24K)

실행: python scripts/bench_concurrency.py [--levels 1,2,4,8,12] [--kinds chat,proposal]
결과: 콘솔 표 + docs/bench/concurrency_<시각>.json
"""
import argparse
import asyncio
import io
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
MODEL = "gemma4:12b"
NUM_CTX = 24576
ROOT = Path(__file__).resolve().parents[1]

CHAT_Q = [
    "연차 사용 보고는 누구에게 어떻게 하면 좋을까?", "법인카드 사용 후 영수증 처리 순서를 알려줘.",
    "협동로봇 도입 시 안전 펜스가 필요한 경우는?", "비전 검사에서 2D와 3D 카메라 차이를 설명해줘.",
    "주간 회의록에 꼭 들어가야 할 항목은?", "AMR과 AGV의 차이를 간단히.", "PLC와 IPC의 역할 차이는?",
    "제안서 표지에 들어갈 항목을 정리해줘.", "공정 사이클 타임을 줄이는 일반적인 방법은?",
    "로봇 가반하중을 고를 때 고려할 점은?", "클린룸 Class 100의 의미를 설명해줘.", "견적서 작성 시 주의점은?",
]
# 제안서형 입력 — 사례집 문체의 긴 참고 자료(약 5천 토큰)
_REF = ("본 사례는 병 충진 라인의 캡 개봉·주입·재체결 공정에 협동로봇과 비전을 적용한 것이다. 기존 공정은 작업자가 "
        "병을 들어 너트러너 아래에 놓고 뚜껑을 푼 뒤 주입 장치로 옮기는 방식이라 중량물 반복 취급과 위치 편차가 문제였다. "
        "컨설팅에서는 비전으로 병 목 기울기와 중심 좌표를 검출하고 로봇이 보정된 위치로 이송하는 구성을 제안했다. ")
PROPOSAL_REF = _REF * 40


def payload(kind: str, i: int) -> dict:
    if kind == "chat":
        return {"model": MODEL, "stream": True, "think": False,
                "messages": [{"role": "system", "content": "당신은 사내 업무 비서입니다. 한국어로 답합니다."},
                             {"role": "user", "content": CHAT_Q[i % len(CHAT_Q)]}],
                "options": {"num_predict": 256, "temperature": 0.3, "num_ctx": NUM_CTX}}
    return {"model": MODEL, "stream": True, "think": False,
            "messages": [{"role": "system", "content": "제안서 작성 보조. 참고 자료를 바탕으로 한국어로 쓴다."},
                         {"role": "user", "content": f"참고 자료:\n{PROPOSAL_REF}\n\n요청 {i}: 위 사례를 참고해 "
                                                     "약액 보틀 주입 공정 제안서의 '현황과 과제' 절을 써줘."}],
            "options": {"num_predict": 512, "temperature": 0.3, "num_ctx": NUM_CTX}}


async def one(client: httpx.AsyncClient, kind: str, i: int, t0: float) -> dict:
    first = None
    final: dict = {}
    async with client.stream("POST", f"{BASE}/api/chat", json=payload(kind, i)) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if first is None and (obj.get("message") or {}).get("content"):
                first = time.perf_counter() - t0
            if obj.get("done"):
                final = obj
    end = time.perf_counter() - t0
    ev, evd = final.get("eval_count", 0), final.get("eval_duration", 1) / 1e9
    return {"ttft": first or end, "total": end, "eval_count": ev, "tok_s": ev / evd if evd else 0,
            "prompt_tokens": final.get("prompt_eval_count", 0),
            "load_s": final.get("load_duration", 0) / 1e9}


async def ps() -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        d = (await c.get(f"{BASE}/api/ps")).json()
    m = next((m for m in d.get("models", []) if m["name"] == MODEL), None)
    return {"vram_gb": round(m["size_vram"] / 1e9, 2), "size_gb": round(m["size"] / 1e9, 2),
            "gpu_pct": round(m["size_vram"] / m["size"] * 100), "ctx": m.get("context_length")} if m else {}


async def level(kind: str, n: int) -> dict:
    timeout = httpx.Timeout(connect=10, read=600, write=30, pool=600)
    limits = httpx.Limits(max_connections=n + 2)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        t0 = time.perf_counter()
        rs = await asyncio.gather(*(one(client, kind, i, t0) for i in range(n)))
        wall = time.perf_counter() - t0
    after = await ps()
    toks = sum(r["eval_count"] for r in rs)
    return {
        "kind": kind, "users": n, "wall_s": round(wall, 1),
        "ttft_median": round(statistics.median(r["ttft"] for r in rs), 1),
        "ttft_max": round(max(r["ttft"] for r in rs), 1),
        "total_max": round(max(r["total"] for r in rs), 1),
        "per_user_tok_s": round(statistics.median(r["tok_s"] for r in rs), 1),
        "throughput_tok_s": round(toks / wall, 1),
        "prompt_tokens": rs[0]["prompt_tokens"], "reload_s": round(max(r["load_s"] for r in rs), 1),
        **after,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2,4,8,12")
    ap.add_argument("--kinds", default="chat,proposal")
    a = ap.parse_args()
    levels = [int(x) for x in a.levels.split(",")]
    results = []
    print("준비:", await ps())
    for kind in a.kinds.split(","):
        await level(kind, 1)  # 워밍업(해당 컨텍스트로 적재)
        for n in levels:
            r = await level(kind, n)
            results.append(r)
            print(f"{kind:8} {n:>2}명 | 전체 {r['wall_s']:>6}s | 첫 글자 중앙 {r['ttft_median']:>5}s 최대 {r['ttft_max']:>5}s"
                  f" | 1인 {r['per_user_tok_s']:>5} tok/s | 서버 {r['throughput_tok_s']:>6} tok/s"
                  f" | GPU {r.get('vram_gb')}GB({r.get('gpu_pct')}%) ctx {r.get('ctx')} | 재적재 {r['reload_s']}s",
                  flush=True)
    # 채팅 ↔ 제안서 번갈아 — 컨텍스트 크기가 달라 모델을 다시 올리는지
    swap = []
    for kind in ("chat", "proposal", "chat"):
        r = await level(kind, 1)
        swap.append({"kind": kind, "reload_s": r["reload_s"], "ttft": r["ttft_median"], "ctx": r.get("ctx")})
    print("번갈아:", swap)
    out = ROOT / "docs/bench" / f"concurrency_{datetime.now():%y%m%d_%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": MODEL, "results": results, "swap": swap}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("저장:", out)


asyncio.run(main())
