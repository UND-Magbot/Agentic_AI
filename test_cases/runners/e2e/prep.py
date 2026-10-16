"""브라우저 테스트 준비·정리 — 백엔드 컨테이너에서 실행(DB·API 접근).

    python /tc/runners/e2e/prep.py setup     # 로그인 토큰 + 화면에서 쓸 테스트 건을 만들어 results/<날짜>/.e2e_state.json 에
    python /tc/runners/e2e/prep.py cleanup   # 테스트 데이터 지우고 상태 파일(토큰 포함) 삭제

상태 파일에는 짧게 쓰는 로그인 토큰이 들어 있어 results/(git 제외)에 두고 끝나면 지운다.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

sys.path.insert(0, "/tc/runners")
import tc  # noqa: E402

USERS = ("lucas", "alex", "finance_admin")


def state_path():
    return tc.ROOT / "results" / tc.kst_today().isoformat() / ".e2e_state.json"


async def setup() -> None:
    tc.guard_dev_db()
    await tc.cleanup()
    s = tc.Suite("e2e-prep")
    await s.login_all()
    d = tc.kst_today().isoformat()
    async with httpx.AsyncClient(timeout=60) as c:
        s.client = c
        # DL-036: 계산서 100만 원 발행된 수주 건 — [+ 입금 확인] 폼의 '완납' 체크 기본값 확인용
        r = await s.call("POST", "/v1/sales-deals", "lucas", json={
            "customer": f"{tc.MARK}입금폼", "owner": "lucas", "quote_date": d,
            "items": [{"name": "TCV1", "unit_price": 1000000, "qty": 1}]})
        pay = r.json()["id"]
        await s.call("POST", f"/v1/sales-deals/{pay}/won", "alex", json={"won": True, "date": d})
        await s.call("POST", f"/v1/sales-deals/{pay}/invoices", "alex", json={"date": d, "supply": 1000000})
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tokens": {u: s.tokens[u] for u in USERS}, "deals": {"payment": pay},
                                "customer": {"payment": f"{tc.MARK}입금폼"}}, ensure_ascii=False), encoding="utf-8")
    print("준비 완료", path)


async def cleanup() -> None:
    tc.guard_dev_db()
    await tc.cleanup()
    state_path().unlink(missing_ok=True)
    print("정리 완료")


if __name__ == "__main__":
    asyncio.run({"setup": setup, "cleanup": cleanup}[sys.argv[1]]())
