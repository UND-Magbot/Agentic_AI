# -*- coding: utf-8 -*-
"""재무팀 기술영업 조회 전용(사용자 2026-11-24) — 권한 규칙 단위 확인(DB·로그인 없이).

- 조회(require_sales_read): 영업부·전체·최고 관리자 + 재무팀, 그 밖(설계·개발)은 거절
- 쓰기(require_sales): 재무팀은 '조회만' 문구로 거절
- 라우터 공통 규칙(deny_readonly_write): 재무팀은 GET·HEAD 외 모두 거절, 다른 부서는 통과
- 관리자 판정(is_admin): 재무팀 관리자는 기술영업에선 관리자 아님
- 기술영업 API 네 곳(영업 건·구매 건·재고·제품 추천)의 router 에 공통 규칙이 걸려 있는지, GET 라우트가 조회 권한인지
  docker compose run --rm -T --no-deps -e PYTHONPATH=/app -v ./backend/app:/app/app -v ./scripts:/scripts backend python /scripts/test_sales_readonly.py
"""
import sys
from types import SimpleNamespace

from fastapi import HTTPException

from app import api_product_recommend, api_purchases, api_sales_deals as sd, api_stock
from app.models import UserDomain, UserRole

res: list[bool] = []


def check(ok: bool, name: str, *more) -> None:
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, *more)


def user(domain: UserDomain, role: UserRole = UserRole.member):
    return SimpleNamespace(domain=domain, role=role)


def allowed(fn, *args) -> tuple[bool, str]:
    try:
        fn(*args)
        return True, ""
    except HTTPException as e:
        return False, str(e.detail)


FIN, FIN_ADMIN = user(UserDomain.finance), user(UserDomain.finance, UserRole.domain_admin)
SALES, ALL, DESIGN = user(UserDomain.sales), user(UserDomain.all), user(UserDomain.design)
SUPER_FIN = user(UserDomain.finance, UserRole.superadmin)

check(all(allowed(sd.require_sales_read, u)[0] for u in (FIN, FIN_ADMIN, SALES, ALL, SUPER_FIN)), "조회 — 재무팀·영업부·전체·최고 관리자 허용")
ok, msg = allowed(sd.require_sales_read, DESIGN)
check(not ok and "재무팀" in msg, "조회 — 다른 부서(설계)는 거절", msg)
ok, msg = allowed(sd.require_sales, FIN)
check(not ok and "조회만" in msg, "쓰기 — 재무팀은 '조회만' 문구로 거절", msg)
check(allowed(sd.require_sales, SALES)[0] and allowed(sd.require_sales, SUPER_FIN)[0], "쓰기 — 영업부·최고 관리자 허용")

req = lambda m: SimpleNamespace(method=m)  # noqa: E731
check(allowed(sd.deny_readonly_write, req("GET"), FIN)[0] and allowed(sd.deny_readonly_write, req("HEAD"), FIN)[0], "공통 규칙 — 재무팀 GET 허용")
for m in ("POST", "PUT", "PATCH", "DELETE"):
    ok, msg = allowed(sd.deny_readonly_write, req(m), FIN_ADMIN)
    check(not ok and "조회만" in msg, f"공통 규칙 — 재무팀(관리자 포함) {m} 거절", msg[:30])
check(allowed(sd.deny_readonly_write, req("POST"), SALES)[0] and allowed(sd.deny_readonly_write, req("POST"), DESIGN)[0]
      and allowed(sd.deny_readonly_write, req("POST"), SUPER_FIN)[0], "공통 규칙 — 다른 부서·최고 관리자는 그대로")
check(not sd.is_admin(FIN_ADMIN) and sd.is_admin(user(UserDomain.sales, UserRole.domain_admin)) and sd.is_admin(SUPER_FIN),
      "관리자 판정 — 재무팀 관리자는 기술영업에선 관리자 아님")

for mod in (sd, api_purchases, api_stock, api_product_recommend):
    deps = [d.dependency for d in mod.router.dependencies]
    check(sd.deny_readonly_write in deps, f"{mod.__name__} — 공통 규칙이 router 에 걸림")
    gets_with_write_only = []
    for r in mod.router.routes:
        if "GET" in getattr(r, "methods", set()):
            calls = {d.call for d in r.dependant.dependencies}
            if sd.require_sales in calls:
                gets_with_write_only.append(r.path)
    check(not gets_with_write_only, f"{mod.__name__} — GET 라우트는 조회 권한(영업부 전용 검사 없음)", gets_with_write_only[:5])

print(f"PASS {sum(res)} / FAIL {len(res) - sum(res)}")
sys.exit(0 if all(res) else 1)
