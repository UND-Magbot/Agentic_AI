"""제안서 작업 프로젝트(proposal_project) 검증.

- 기본: 카탈로그(문항 48·폼 12 묶음·필수 문항) — 서버 불필요.
- --db : 실제 DB 로 생성·조회·입력 검증·문항 갱신 이력 (backend 컨테이너에서 실행)
    docker compose run --rm --no-deps -v ./scripts:/scripts backend python /scripts/test_proposal_project.py --db
"""
import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
for cand in (ROOT / "backend", Path("/app")):
    if (cand / "app").is_dir():
        sys.path.insert(0, str(cand))
        break

from app.proposal_project import catalog, extract  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


# ── 1) 카탈로그 ─────────────────────────────────────────────────────────────
codes_in_form = [c for s in catalog.FORM_SECTIONS for c in s.codes]
check(len(catalog.CODES) == 48, "문항 48개 (P01~P48)")
check(len(catalog.FORM_SECTIONS) == 12, "공통 질문 폼 12 묶음 (가이드 입력 양식)")
check(sorted(codes_in_form) == sorted(catalog.CODES), "모든 문항이 폼 묶음에 한 번씩",
      str(set(catalog.CODES) ^ set(codes_in_form)))
check({c for c, q in catalog.QUESTIONS.items() if q.required} == {"P02", "P03", "P06", "P12", "P13"},
      "컨셉 진행 조건 문항 = 목적·범위·대상·인계·대안")
notes = catalog.guide_notes()
check(all(f"P{i:02d}" in notes for i in range(1, 49)) and all(f"K{i:02d}" in notes for i in range(1, 9)),
      "가이드 원문 예시·AI 규칙 56개 로드")
check("세척" not in " ".join(q.question for q in catalog.QUESTIONS.values()),
      "일반 질문에 사례 전용 표현(세척기 등) 없음")
payload = catalog.catalog_payload()
check(set(payload) == {"sections", "questions", "statuses", "asset_roles", "essential", "optional"}, "프론트 카탈로그 형식")


# ── 2) DB 통합 (--db) ───────────────────────────────────────────────────────
async def db_flow() -> None:
    import io as _io

    from sqlalchemy import text

    from app.database import SessionLocal
    from app.migrations import run_migrations
    from app.proposal_project import service
    from app.storage import make_object_key, put_object

    async with SessionLocal() as db:
        await run_migrations(db)
        uid = (await db.execute(text("SELECT id FROM users WHERE username='alex'"))).scalar_one()
        other = (await db.execute(text("SELECT id FROM users WHERE username='finance_admin'"))).scalar_one()

        async def att(name: str, mime: str, owner: int) -> int:
            data = b"\x89PNG\r\n" if mime.startswith("image/") else b"%PDF-1.4"
            key = make_object_key(owner, name)
            put_object(key=key, data=_io.BytesIO(data), length=len(data), mime=mime)
            return int((await db.execute(
                text("INSERT INTO attachments (user_id, bucket, object_key, original_filename, mime, size_bytes) "
                     "VALUES (:u, 'und-cortex-attachments', :k, :n, :m, :s) RETURNING id"),
                {"u": owner, "k": key, "n": name, "m": mime, "s": len(data)})).scalar_one())

        img = await att("robot_ref.png", "image/png", uid)
        pdf = await att("company.pdf", "application/pdf", uid)
        foreign = await att("other.png", "image/png", other)
        await db.commit()
        made: list[int] = []

        async def expect_error(label: str, **kw) -> None:
            try:
                made.append(await service.create_project(db, **kw))
                check(False, label)
            except service.ProjectError:
                await db.rollback()
                check(True, label)

        base = dict(user_id=uid, title="약액 보틀 주입 자동화", request_text="",
                    intake={"project": "고객사 A, 실증 목적"}, assets=[])
        auto = await service.create_project(db, **{**base, "title": " "})
        made.append(auto)
        check((await service.get_project(db, auto))["title"] == service.AUTO_TITLE, "제목을 비우면 임시 제목")
        ans = await service.create_project(db, **{**base, "intake": {"P06": "500ml 유리병 2종", "P08": "시간당 1,200개"}})
        made.append(ans)
        pa = await service.get_project(db, ans)
        check({c: (pa["items"][c]["status"], pa["items"][c]["source"]) for c in ("P06", "P08", "P07")}
              == {"P06": ("confirmed", "form"), "P08": ("confirmed", "form"), "P07": ("empty", "")},
              "필수 질문 답은 곧바로 '확인됨'(출처 form)으로 저장", str(pa["items"]["P06"]))
        await expect_error("모르는 문항 코드 거부", **{**base, "intake": {"P99": "x"}})
        check([g["title"] for g in catalog.catalog_payload()["essential"]][0] == "고객과 목적"
              and len(catalog.ESSENTIAL_CODES) == 14, "첫 화면 필수 질문 14개(6묶음)")
        opt = catalog.catalog_payload()["optional"]
        opt_q = [q for g in opt for q in g["questions"]]
        check([g["title"] for g in opt] == ["그리퍼와 툴 구성", "비전(카메라) 구성"]
              and [q["code"] for q in opt_q] == ["P21", "P22", "P23", "P24", "P17", "P18"]
              and not any(q["required"] for q in opt_q) and all(g["note"] for g in opt)
              and not any(w in q["question"] + q["example"] for q in opt_q for w in ("양팔", "식기", "좌우", "세척")),
              "첫 화면 맨 아래 선택 질문: 그리퍼 4개 + 비전 2개(안내 문구), 특정 공정 표현 없음", str(opt_q))
        sel = await service.create_project(db, **{**base, "intake": {"P21": "핑거 그리퍼 우선"}})
        made.append(sel)
        ps = await service.get_project(db, sel)
        check((ps["items"]["P21"]["status"], ps["items"]["P21"]["source"]) == ("confirmed", "form"),
              "선택 질문 답도 첫 화면 답으로 저장", str(ps["items"]["P21"]))
        exc = next(g for g in catalog.catalog_payload()["essential"] if g["title"].startswith("예외 대응"))
        check([q["code"] for q in exc["questions"]] == ["P29", "P30"]
              and "미취출" in exc["questions"][0]["question"] and "재개" in exc["questions"][1]["question"]
              and not any(q["required"] for q in exc["questions"]),
              "예외 대응 2문항: 미취출·인식 실패(P29) / 정지·재개(P30), 선택 입력", str(exc))
        srcs = extract.build_sources({"intake": {"P29": "센서 감지 후 정지"}, "request_text": ""}, {})
        check(any("미취출" in s.label for s in srcs), "묶은 질문 문구로 자료 읽기 근거가 됨(P32 도 채울 수 있게)",
              str([s.label for s in srcs]))
        only_files = await service.create_project(db, **{**base, "intake": {}, "request_text": "",
                                                         "assets": [service.AssetInput(pdf, "content")]})
        made.append(only_files)
        check(True, "요청 원문 없이 첨부만으로도 생성")
        await expect_error("폼·원문 모두 비면 거부", **{**base, "intake": {}, "request_text": ""})
        await expect_error("모르는 폼 항목 거부", **{**base, "intake": {"hack": "x"}})
        await expect_error("남의 첨부 거부", **{**base, "assets": [service.AssetInput(foreign, "appearance")]})
        await expect_error("외부 전송은 이미지만 (PDF 거부)",
                           **{**base, "assets": [service.AssetInput(pdf, "content", external_ok=True)]})
        await expect_error("가격 근거 이미지는 외부 전송 불가",
                           **{**base, "assets": [service.AssetInput(img, "price", external_ok=True)]})
        dim_ok = await service.create_project(db, **{**base, "assets": [service.AssetInput(img, "dimension", external_ok=True)]})
        made.append(dim_ok)
        check(True, "치수 이미지도 외부 전송 허용 가능(사용자 결정)")
        await expect_error("모르는 첨부 용도 거부", **{**base, "assets": [service.AssetInput(img, "secret")]})

        pid = await service.create_project(
            db, user_id=uid, title="약액 보틀 주입 자동화",
            request_text="한화로봇 14kg Class 100 되는지 확인 … 약액 주입 210초",
            intake={"project": "고객사 A, 실증 목적", "volume": "시간당 17병"},
            assets=[service.AssetInput(img, "appearance", "로봇 외형", external_ok=True),
                    service.AssetInput(pdf, "content", "회사소개서")])
        made.append(pid)
        p = await service.get_project(db, pid, user_id=uid)
        check(p is not None and p["title"] == "약액 보틀 주입 자동화", "생성·조회")
        check(len(p["items"]) == 48 and all(v["status"] == "empty" for v in p["items"].values()),
              "문항 48개가 '비어 있음' 으로 생성")
        check(p["intake"] == {"project": "고객사 A, 실증 목적", "volume": "시간당 17병"}, "폼 답 저장")
        check([(a["role"], a["external_ok"]) for a in p["assets"]] == [("appearance", True), ("content", False)],
              "첨부 용도·외부 전송 허용 저장")
        check(p["output"]["max_pages"] == 10, "출력 기본값 10쪽")
        check({"alternatives", "images", "pages", "quote_lines"} <= set(p), "PPT 계약 필드 자리")
        check(await service.get_project(db, pid, user_id=other) is None, "남의 프로젝트 조회 불가")

        await service.set_item(db, pid, "P08", value="시간당 17병", status="confirmed", source="form",
                               evidence="공통 질문 폼(수량)", unit="병/시간", nature="고객 요구", reason="폼 추출")
        await db.commit()
        p = await service.get_project(db, pid, user_id=uid)
        it = p["items"]["P08"]
        check((it["value"], it["status"], it["version"], it["unit"]) == ("시간당 17병", "confirmed", 2, "병/시간"),
              "문항 갱신(값·상태·버전·단위)", str(it))
        hist = (await db.execute(text("SELECT old_status, new_status, reason FROM project_item_history "
                                      "WHERE project_id = :p"), {"p": pid})).all()
        check([tuple(h) for h in hist] == [("empty", "confirmed", "폼 추출")], "변경 이력 기록")
        try:
            await service.set_item(db, pid, "P08", value="x", status="bogus", source="form")
            check(False, "모르는 상태 거부")
        except service.ProjectError:
            check(True, "모르는 상태 거부")
        lst = await service.list_projects(db, uid)
        check(any(r["id"] == pid and r["filled"] == 1 and r["total"] == 48 for r in lst), "목록에 채움 현황")

        # 정리 — 테스트로 만든 행만 지운다.
        await db.execute(text("DELETE FROM proposal_projects WHERE id = ANY(:ids)"), {"ids": made})
        await db.execute(text("DELETE FROM attachments WHERE id = ANY(:ids)"), {"ids": [img, pdf, foreign]})
        await db.commit()


if "--db" in sys.argv:
    asyncio.run(db_flow())

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, d in FAIL:
    print("  FAIL", label, d)
sys.exit(1 if FAIL else 0)
