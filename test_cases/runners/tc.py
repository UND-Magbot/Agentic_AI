"""영업 기능 검증 공통 도구 — 백엔드 컨테이너 안에서 실행 중인 서비스(backend:8000·frontend:3000)를 부른다.

- 개발 DB(agent_ai_dev)가 아니면 바로 멈춘다(운영 DB agent_ai 보호).
- 테스트로 만드는 고객사·요청 문구에는 MARK 를 붙이고, 시작·끝에 MARK 로 만든 데이터만 지운다.
- 결과는 /tc/results/<날짜>/<묶음>.json 에 남기고 요약을 화면에 찍는다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.security import create_access_token

B = os.environ.get("TC_BACKEND", "http://backend:8000")
F = os.environ.get("TC_FRONTEND", "http://frontend:3000")
MARK = "TC검증"                         # 테스트 데이터 표시 — 실제 고객사명에 쓰일 일 없는 말
ROOT = Path(os.environ.get("TC_ROOT", "/tc"))
PRIVATE = ROOT / "fixtures" / "private"
SAMPLES = ROOT / "fixtures" / "samples"
DEV_DB = "agent_ai_dev"
LEAK_WORDS = ("Codex", "codex", "GPT", "gpt-", "gemma", "Gemma", "Ollama", "ollama", "Qwen", "qwen", "vLLM")


# PASS 통과 / FAIL 기대와 다름 / ERROR 테스트 중 예외 / CHECK 동작은 기록, 맞는지는 정책 확인 필요 / SKIP 조건 안 됨
STATUSES = ("PASS", "FAIL", "ERROR", "CHECK", "SKIP")


def kst_today() -> dt.date:
    """서버 컨테이너가 UTC 라 날짜는 한국 기준으로 따로 계산한다."""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).date()


def guard_dev_db() -> None:
    if engine.url.database != DEV_DB:
        sys.exit(f"중단: 개발 DB({DEV_DB})가 아닙니다 — 지금 DB {engine.url.database!r}")


@dataclass
class Result:
    id: str
    status: str            # STATUSES 중 하나
    title: str = ""
    detail: str = ""
    sec: float = 0.0


@dataclass
class Suite:
    name: str
    results: list[Result] = field(default_factory=list)
    tokens: dict[str, str] = field(default_factory=dict)
    user_ids: dict[str, int] = field(default_factory=dict)
    client: httpx.AsyncClient | None = None

    # ── 계정 ──
    async def login_all(self) -> None:
        async with SessionLocal() as db:
            rows = (await db.execute(text("SELECT id, username FROM users WHERE is_active"))).all()
        for uid, name in rows:
            self.user_ids[name] = uid
            self.tokens[name] = create_access_token(str(uid))[0]

    def h(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    async def call(self, method: str, path: str, user: str | None = "alex", **kw: Any) -> httpx.Response:
        assert self.client is not None
        headers = kw.pop("headers", {})
        if user:
            headers = {**self.h(user), **headers}
        return await self.client.request(method, B + path, headers=headers, **kw)

    async def page(self, path: str, user: str | None = "alex", **kw: Any) -> httpx.Response:
        """프론트(BFF 포함) 호출 — 로그인 쿠키로."""
        assert self.client is not None
        cookies = {"und_cortex_token": self.tokens[user]} if user else {}
        method = kw.pop("method", "GET")
        return await self.client.request(method, F + path, cookies=cookies, follow_redirects=False, **kw)

    # ── 결과 ──
    def record(self, tc_id: str, ok: bool, title: str, detail: str = "", sec: float = 0.0) -> bool:
        self.results.append(Result(tc_id, "PASS" if ok else "FAIL", title, "" if ok else detail[:600], sec))
        return ok

    def skip(self, tc_id: str, title: str, why: str) -> None:
        self.results.append(Result(tc_id, "SKIP", title, why))

    async def case(self, tc_id: str, title: str, fn: Callable[[], Awaitable[Any]]) -> None:
        """케이스 하나 실행 — fn 이 (ok, detail) 를 돌려주거나 AssertionError 를 던진다. 예외는 ERROR 로 남기고 계속."""
        t = time.time()
        try:
            out = await fn()
            if isinstance(out, tuple) and out and out[0] in ("SKIP", "CHECK"):
                self.results.append(Result(tc_id, out[0], title, out[1], round(time.time() - t, 2)))
                return
            ok, detail = out if isinstance(out, tuple) else (bool(out), "")
            self.record(tc_id, ok, title, detail, round(time.time() - t, 2))
        except AssertionError as e:
            self.record(tc_id, False, title, str(e), round(time.time() - t, 2))
        except (httpx.ReadError, httpx.RemoteProtocolError) as e:   # 서버가 처리 중 예외로 응답 없이 끊음 = 500 계열 결함
            self.record(tc_id, False, title, f"서버가 응답 없이 연결을 끊음(처리 중 오류) — {type(e).__name__}", round(time.time() - t, 2))
        except Exception as e:  # noqa: BLE001 — 케이스 하나가 깨져도 나머지는 돈다
            tb = traceback.format_exc(limit=3)
            self.results.append(Result(tc_id, "ERROR", title, f"{type(e).__name__}: {e}\n{tb}"[:800], round(time.time() - t, 2)))

    def save(self) -> Path:
        day = kst_today().isoformat()
        out = ROOT / "results" / day
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{self.name}.json"
        path.write_text(json.dumps([r.__dict__ for r in self.results], ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def summary(self) -> int:
        n = {s: sum(r.status == s for r in self.results) for s in ("PASS", "FAIL", "ERROR", "SKIP")}
        print(f"\n[{self.name}] PASS {n['PASS']} / FAIL {n['FAIL']} / ERROR {n['ERROR']} / SKIP {n['SKIP']}")
        for r in self.results:
            if r.status != "PASS":
                print(f"  {r.status:5} {r.id} {r.title} :: {r.detail.splitlines()[0] if r.detail else ''}")
        return 1 if (n["FAIL"] or n["ERROR"]) else 0


def leaked(body: str) -> list[str]:
    """화면에 나가면 안 되는 AI 모델명."""
    return [w for w in LEAK_WORDS if w in body]


async def cleanup() -> None:
    """MARK 로 만든 데이터만 지운다(견적·영업 건·이력·승인 요청·발행번호·추천·정정)."""
    like = f"%{MARK}%"
    async with SessionLocal() as db:
        deal_ids = (await db.execute(text("SELECT id FROM sales_deals WHERE customer LIKE :l OR title LIKE :l"),
                                     {"l": like})).scalars().all()
        quote_ids = (await db.execute(text("SELECT id FROM product_quotes WHERE customer LIKE :l "
                                           "OR state->'form'->>'customer' LIKE :l OR deal_id = ANY(:d)"),
                                      {"l": like, "d": list(deal_ids)})).scalars().all()
        rec_ids = (await db.execute(text("SELECT id FROM product_recommendations WHERE request LIKE :l OR result::text LIKE :l"),
                                    {"l": like})).scalars().all()
        if quote_ids:
            await db.execute(text("DELETE FROM doc_numbers WHERE quote_id = ANY(:q)"), {"q": list(quote_ids)})
        if deal_ids:
            await db.execute(text("DELETE FROM approval_requests WHERE deal_id = ANY(:d)"), {"d": list(deal_ids)})
            await db.execute(text("DELETE FROM doc_numbers WHERE deal_id = ANY(:d)"), {"d": list(deal_ids)})
        await db.execute(text("DELETE FROM product_quotes WHERE id = ANY(:q)"), {"q": list(quote_ids)})
        await db.execute(text("DELETE FROM sales_deals WHERE id = ANY(:d)"), {"d": list(deal_ids)})
        await db.execute(text("DELETE FROM sales_deal_trash WHERE deal->>'customer' LIKE :l"), {"l": like})
        await db.execute(text("DELETE FROM product_corrections WHERE recommendation_id = ANY(:r) OR situation LIKE :l "
                              "OR reason LIKE :l"), {"r": list(rec_ids), "l": like})
        await db.execute(text("DELETE FROM product_proposals WHERE recommendation_id = ANY(:r) OR customer LIKE :l"),
                         {"r": list(rec_ids), "l": like})
        await db.execute(text("DELETE FROM product_recommendations WHERE id = ANY(:r)"), {"r": list(rec_ids)})
        await db.commit()


async def run_suite(name: str, body: Callable[[Suite], Awaitable[None]]) -> int:
    guard_dev_db()
    s = Suite(name)
    await s.login_all()
    await cleanup()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
            s.client = c
            await body(s)
    finally:
        if os.environ.get("TC_KEEP") != "1":
            await cleanup()
    path = s.save()
    code = s.summary()
    print(f"결과 파일: {path}")
    return code


def main(name: str, body: Callable[[Suite], Awaitable[None]]) -> None:
    sys.exit(asyncio.run(run_suite(name, body)))
