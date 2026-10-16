"""브라우저 테스트(Playwright) — 화면을 실제로 열어 누른다. e2e 이미지(Dockerfile)에서 실행, prep.py setup 다음에.

    docker run --rm --network und_cortex_und_cortex_net -v ./test_cases:/tc und_cortex_e2e python /tc/runners/e2e/run.py

결과: results/<날짜>/e2e.json + 화면 캡처 results/<날짜>/e2e_*.png
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

# 개발 서버(next dev)는 허용한 호스트(ALLOWED_DEV_ORIGINS)가 아니면 화면 스크립트를 막아 화면이 안 뜬다 —
# run_all.sh 가 .env 의 허용 호스트로 E2E_BASE 를 넘긴다.
F = os.environ.get("E2E_BASE", "http://frontend:3000").rstrip("/")
DAY = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).date().isoformat()
OUT = Path("/tc/results") / DAY
STATE = json.loads((OUT / ".e2e_state.json").read_text(encoding="utf-8"))
LEAK = re.compile(r"Codex|GPT|gemma|Ollama|Qwen|vLLM", re.I)
RULE_CODE = re.compile(r"\b(PGR|JF|[QRTCFUJAPOGSE])\d{2}\b")
BAD_PAGE = ("Application error", "Internal Server Error", "Unhandled Runtime Error")
results: list[dict] = []


def record(tc_id: str, status: str, title: str, detail: str = "", sec: float = 0) -> None:
    results.append({"id": tc_id, "status": status, "title": title, "detail": detail[:600], "sec": sec})


def open_as(browser, user: str):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    ctx.add_cookies([{"name": "und_cortex_token", "value": STATE["tokens"][user], "domain": urlparse(F).hostname, "path": "/"}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    return ctx, page, errors


def close_popups(page: Page) -> None:
    """들어가자마자 뜨는 알림(입금 예정일 지난 건·승인 요청)을 닫는다."""
    for name in ("입금 예정일 지난 건",):
        dlg = page.get_by_role("alertdialog", name=name)
        if dlg.count() and dlg.is_visible():
            dlg.get_by_role("button", name="확인").click()


def settle(page: Page) -> None:
    """탭 내용이 불러와질 때까지(불러오는 중 문구가 사라질 때까지) 기다린 뒤 알림을 닫는다."""
    loading = page.get_by_text(re.compile("불러오는 중"))
    try:
        loading.first.wait_for(state="hidden", timeout=60000)
    except Exception:  # noqa: BLE001 — 문구가 없던 화면
        pass
    close_popups(page)


def run_case(tc_id: str, title: str, fn) -> None:
    t = time.time()
    try:
        out = fn()
        status, detail = ("PASS", "") if out is True else (out if isinstance(out, tuple) else ("FAIL", str(out)))
        record(tc_id, status, title, detail, round(time.time() - t, 1))
    except Exception as e:  # noqa: BLE001
        record(tc_id, "ERROR", title, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}", round(time.time() - t, 1))


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def page_ok(tc_id: str, user: str, path: str, title: str):
            def fn():
                ctx, page, errors = open_as(browser, user)
                try:
                    page.goto(F + path, wait_until="networkidle", timeout=60000)
                    settle(page)
                    page.screenshot(path=str(OUT / f"e2e_{tc_id}.png"), full_page=True)
                    body = page.inner_text("body")
                    bad = [b for b in BAD_PAGE if b in body]
                    leak = sorted(set(LEAK.findall(body)))
                    codes = sorted(set(m.group(0) for m in RULE_CODE.finditer(body)))
                    real_err = [e for e in errors if not any(w in e for w in ("favicon", "401", "webpack-hmr"))]   # 개발 서버 HMR 은 제외
                    ok = not bad and not leak and not codes and not real_err and "/login" not in page.url
                    return True if ok else ("FAIL", f"주소 {page.url} 오류 화면 {bad} 모델명 {leak} 규칙 번호 {codes} 콘솔 {real_err[:3]}")
                finally:
                    ctx.close()
            run_case(tc_id, title, fn)

        page_ok("E2E-01", "lucas", "/deals", "영업 건 관리 화면 열림(멤버)")
        page_ok("E2E-02", "alex", "/deals", "영업 건 관리 화면 열림(관리자)")
        page_ok("E2E-03", "lucas", "/proposals/recommend", "제품 추천 화면 열림")

        def finance_blocked():
            ctx, page, errors = open_as(browser, "finance_admin")
            try:
                page.goto(F + "/deals", wait_until="networkidle", timeout=60000)
                settle(page)
                page.screenshot(path=str(OUT / "e2e_DL-057-ui.png"), full_page=True)
                body = page.inner_text("body")
                shows_deals = STATE["customer"]["payment"] in body
                crashed = any(b in body for b in BAD_PAGE) or any("pageerror" in e for e in errors)
                if shows_deals or crashed:
                    return ("FAIL", f"영업 건 보임={shows_deals} 화면 오류={crashed} {errors[:2]}")
                return True
            finally:
                ctx.close()
        run_case("DL-057-UI", "재무 관리자는 영업 건 화면에서 막힘(화면 깨짐 없이)", finance_blocked)

        def admin_buttons():
            seen = {}
            for user in ("lucas", "alex"):
                ctx, page, _ = open_as(browser, user)
                try:
                    page.goto(F + "/deals", wait_until="networkidle", timeout=60000)
                    settle(page)
                    seen[user] = page.get_by_role("button", name="삭제", exact=True).count() > 0
                finally:
                    ctx.close()
            return True if seen == {"lucas": False, "alex": True} else ("FAIL", f"[삭제] 버튼 보임 {seen}")
        run_case("DL-058", "[삭제] 버튼은 관리자에게만", admin_buttons)

        def payment_form():
            ctx, page, _ = open_as(browser, "lucas")
            try:
                page.goto(F + "/deals", wait_until="networkidle", timeout=60000)
                settle(page)
                page.get_by_text(STATE["customer"]["payment"]).first.click()
                page.get_by_role("button", name="+ 입금 확인").click()
                box = page.get_by_label("이 입금으로 완납")
                before = box.is_checked()
                page.get_by_label("입금액").fill("300000")
                after = box.is_checked()
                page.screenshot(path=str(OUT / "e2e_DL-036.png"))
                if after:
                    return ("FAIL", f"계산서 100만 원 건: 폼 열 때 완납 체크={before}, 입금액을 300,000 으로 바꾼 뒤에도 완납 체크={after} "
                                    "→ 그대로 저장하면 일부 입금이 완납 처리됨")
                return True
            finally:
                ctx.close()
        run_case("DL-036", "일부 입금으로 고치면 '완납' 체크가 풀림", payment_form)

        browser.close()

    (OUT / "e2e.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    n = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "ERROR", "CHECK", "SKIP")}
    print("\n[e2e] " + " / ".join(f"{k} {v}" for k, v in n.items()))
    for r in results:
        if r["status"] != "PASS":
            print(f"  {r['status']:5} {r['id']} {r['title']} :: {r['detail'].splitlines()[0] if r['detail'] else ''}")
    return 1 if (n["FAIL"] or n["ERROR"]) else 0


if __name__ == "__main__":
    sys.exit(main())
