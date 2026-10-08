"""Codex 위임 fast-path 의도 감지·후처리 단위 검증 — 브리지/인프라 불필요(순수 함수)."""
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# config 가 import 시점에 환경변수를 읽으므로 import 전에 브리지 설정을 주입한다.
os.environ["CODEX_BRIDGE_URL"] = "http://bridge.test"
os.environ["CODEX_BRIDGE_TOKEN"] = "test-token"

from app import codex_delegate as cd  # noqa: E402
from app.config import settings  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def eq(actual, expected, label: str) -> None:
    if actual == expected:
        PASS.append(label)
    else:
        FAIL.append((label, f"got {actual!r} != {expected!r}"))


def detect(text: str, attachments: bool = False):
    r = cd.detect_intent([{"role": "user", "content": text}], has_attachments=attachments)
    return (r.kind, r.text) if r else None


def kind(text: str, attachments: bool = False):
    r = detect(text, attachments)
    return r[0] if r else None


# 1) 명령형 접두 — 접두를 떼고 종류 결정, 첨부가 있어도 우선
eq(detect("@이미지 주유소 순찰 로봇"), ("image", "주유소 순찰 로봇"), "@이미지 명령")
eq(detect("/검색 2026 최저임금"), ("search", "2026 최저임금"), "/검색 명령")
eq(detect("@Codex: 계약 리스크 분석해줘"), ("ask", "계약 리스크 분석해줘"), "@Codex 명령(대소문자·콜론)")
eq(detect("@이미지 첨부 참고해서 그려줘", attachments=True), ("image", "첨부 참고해서 그려줘"), "명령은 첨부보다 우선")
eq(detect("@이미지"), None, "빈 명령은 무시")

# 2) 자연어 — 이미지
eq(kind("회사 소개용 로고 이미지 만들어줘"), "image", "로고 이미지 생성")
eq(detect("개념도 이미지 그려줘"), None, "개념도는 전용 흐름")
eq(detect("이미지 생성 어떻게 해?"), None, "방법 질문은 제외")
eq(detect("로고 이미지 만들어줘", attachments=True), None, "첨부 요청은 기존 흐름에 양보")

# 3) 자연어 — 웹 검색
eq(kind("오늘 원달러 환율 알려줘"), "search", "시점+변동 데이터")
eq(kind("웹에서 4족 로봇 도입 사례 검색해줘"), "search", "웹 명시 검색")
eq(detect("사칙에서 최근 규정 뉴스 찾아줘"), None, "사내 자료 질의는 제외")
eq(kind("인터넷으로 GS칼텍스 주가 찾아줘"), "search", "인터넷으로 … 찾아줘")
eq(detect("웹사이트 문구 검토해줘"), None, "웹사이트는 웹 검색 아님")

# 4) 자연어 — 심층 분석
eq(kind("코덱스한테 이 문제 물어봐"), "ask", "코덱스한테")
eq(detect("codex 가 뭐야"), None, "codex 자체에 대한 질문 오탐 방지")

# 5) 일반 대화·기존 기능은 건드리지 않음
eq(detect("영업이익률이 뭐야?"), None, "일반 질문")
eq(detect("연차 메일 작성해줘"), None, "연차 메일")

# 6) 브리지 미설정이면 전부 비활성
settings.codex_bridge_url = ""
eq(detect("@이미지 로봇"), None, "브리지 미설정 → 비활성")
settings.codex_bridge_url = "http://bridge.test"

# 6-1) '@본문' — 명령어 없이 '@'/'/' 로 시작하면 Codex 위임 (2026-09-28 사용자 요구)
REAL = ("@한화로봇 14kg Class 100 되는지 확인 기존 전용장비에서 센서 쓰던것을 비전으로 대체 … "
        "약액 주입 210초. CT 검토 필요. 공정에 대한 개념도를 그려줄래?")
r = cd.detect_intent([{"role": "user", "content": REAL}], has_attachments=False)
eq(r and r.kind, "image", "@ + 개념도 요청 → Codex 이미지(사내 gemma 개념도 아님)")
eq(bool(r) and r.text.startswith(cd._CONCEPT_HEADER) and "약액 주입 210초" in r.text, True,
   "개념도 지시 + 요청 원문 포함")
eq(detect("@이번 분기 AMR 도입 리스크 정리해줘"), ("ask", "이번 분기 AMR 도입 리스크 정리해줘"), "@ + 일반 요청 → ask")
eq(detect("/배치도 그려줘 컨베이어 2대 로봇 1대")[0], "image", "/ + 배치도 → 이미지")
eq(detect("@개념도 그려줘", attachments=True), None, "첨부 있는 @ 요청은 기존 첨부 흐름에 양보")
eq(detect("공정 개념도 그려줘 약액 주입 210초"), None, "@ 없는 자연어 개념도는 계속 사내 gemma")
eq(len(cd.draw_prompt("가" * 3000)) <= 2000 and cd.draw_prompt("가" * 3000).endswith(cd._TRUNC_MARK), True,
   "그리기 프롬프트는 브리지 한도(2000자) 안으로 자르고 표시")

# 6-2) 개념도 2단계 — 설계(ask) → 그리기(image). 실패 시 원문 바로 그리기.
import asyncio  # noqa: E402

calls: list[tuple[str, str]] = []


async def fake_ask(question, context="", **kw):
    calls.append(("ask", question))
    return "**제목** 비전 기반 보틀 주입 공정\n4. 비전 구성안: V1 상부 고정형 3D (권장)"


async def fake_ask_fail(question, context="", **kw):
    calls.append(("ask", question))
    raise cd.codex_client.CodexError("브리지 오류")


async def fake_image(prompt, **kw):
    calls.append(("image", prompt))
    return b"\x89PNG", "image/png", "개념도를 그렸습니다."


async def fake_save(data, mime, filename, user_id):
    calls.append(("save", filename))
    return 777


real = (cd.codex_client.ask, cd.codex_client.generate_image, cd._save_attachment)
cd.codex_client.generate_image, cd._save_attachment = fake_image, fake_save
try:
    seen_uid = []
    cd.codex_client.ask = lambda q, context="", **kw: (seen_uid.append(kw.get("user_id")), fake_ask(q))[1]
    real_img = cd.codex_client.generate_image
    cd.codex_client.generate_image = lambda p, **kw: (seen_uid.append(kw.get("user_id")), real_img(p))[1]
    out = asyncio.run(cd.run_image(r.text, 3, lambda s: None))
    cd.codex_client.generate_image = real_img
    eq(seen_uid, [3, 3], "설계·그리기 모두 요청자(user_id) 전달 — 외부 전송 기록용")
    eq([c[0] for c in calls], ["ask", "image", "save"], "설계 → 그리기 → 저장 순서")
    eq("약액 주입 210초" in calls[0][1] and "(권장)" in calls[0][1], True, "설계 질문에 원문 + 권장 표시 규칙")
    eq(calls[1][1].startswith(cd._DRAW_GUIDE) and "V1 상부 고정형 3D (권장)" in calls[1][1]
       and "**" not in calls[1][1], True, "그리기 프롬프트 = 지시 + 설계서(마크다운 제거)")
    eq(len(calls[1][1]) <= 2000, True, "그리기 프롬프트 2000자 이하")
    eq(cd._CONCEPT_HEADER not in calls[1][1], True, "내부 표시가 그림 프롬프트에 새지 않음(제목 오염 방지)")
    eq(calls[2][1].startswith("codex_개념도_") and "먼저 설계한 뒤" in out
       and "/api/attachments/777/download" in out, True, "개념도 파일명·안내·링크")
    calls.clear()
    cd.codex_client.ask = fake_ask_fail
    out = asyncio.run(cd.run_image(r.text, 3, lambda s: None))
    eq([c[0] for c in calls], ["ask", "image", "save"], "설계 실패해도 그림은 생성")
    eq(calls[1][1].startswith(cd._CONCEPT_GUIDE) and "설계 단계가 실패" in out, True, "실패 시 원문 바로 그리기 + 안내")
    calls.clear()
    asyncio.run(cd.run_image("로고 이미지", 3, lambda s: None))
    eq([c[0] for c in calls], ["image", "save"], "일반 이미지는 설계 단계 없음")
finally:
    cd.codex_client.ask, cd.codex_client.generate_image, cd._save_attachment = real

# 7) 마크다운 기호 제거
eq(cd._plain_text("## 결론\n**1주 전** 공유"), "결론\n1주 전 공유", "굵게·제목 제거")
eq(cd._plain_text("금액 2*3 = 6"), "금액 2*3 = 6", "단일 별표 보존")
eq(cd._plain_text("- `1억 × 8% = 800만`"), "- 1억 × 8% = 800만", "인라인 코드 백틱 제거")

print("\n".join(f"  PASS {p}" for p in PASS))
if FAIL:
    print(f"\n==== {len(PASS)} pass / {len(FAIL)} fail ====")
    for item in FAIL:
        print("  FAIL", item)
    raise SystemExit(1)
print(f"\n==== {len(PASS)} pass / 0 fail ====\nALL OK")
