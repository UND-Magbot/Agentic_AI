"""외부 AI 전송 관문(external_gateway) 검증 — 서버·LLM·DB 불필요(가짜 주입).

사용자 결정: 고객사명·지역·고객 제품명(+인명·연락처)은 가명, 공정 설명·장비 브랜드는 그대로, 금액 제거,
자동 전송 + 기록. 가림 단계가 실패하면 보내지 않는다.
"""
import asyncio
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import external_gateway as gw, proposal_llm  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if ok else FAIL.append((label, detail)))


REQ = ("㈜대광소결금속 구미공장 소결 라인 자동화 요청. 담당 김철수 과장님(010-1234-5678, kim@daekwang.co.kr). "
       "주소 경상북도 구미시 산호대로 253. 자사 제품 '시원C1' 파레트(700kg)를 AMR로 이송. "
       "한화로봇 14kg, Keyence 비전 검토. 예산 3억원, 단가 12,000원을 넘지 않게. 3 원통 지그 사용. "
       "생산 팀장이 현장 확인 예정. 협력사 동남정밀과 연동.")
LLM_OUT = {"companies": ["대광소결금속", "동남정밀", "한화로봇"],       # 장비 브랜드(한화로봇)는 모델이 넣어도 유지
           "products": ["시원C1", "지어낸제품X"],                         # 글에 없는 이름은 무시
           "places": ["구미공장"], "people": ["김철수"]}
calls = {"llm": 0}


async def fake_llm(messages, **kw):
    calls["llm"] += 1
    return json.dumps(LLM_OUT, ensure_ascii=False)


async def fake_vendors():
    return {"동남정밀", "무관한거래처"}


records: list[dict] = []


async def fake_record(endpoint, mode, payload, counts, user_id=None, status="sent"):
    records.append({"endpoint": endpoint, "mode": mode, "payload": payload, "counts": counts, "status": status,
                    "user_id": user_id})


proposal_llm.chat = fake_llm
gw._vendor_names = fake_vendors
gw.record = fake_record

# ── 규칙 탐지 ───────────────────────────────────────────────────────────────
ents = gw.regex_entities(REQ)
check("대광소결금속" in ents["company"], "㈜ 법인 표기 회사명(표기를 뗀 이름으로)", str(ents["company"]))
check(any("구미시 산호대로" in p for p in ents["place"]), "주소", str(ents["place"]))
check("김철수" in ents["person"], "이름+직함님", str(ents["person"]))
check("생산" not in ents["person"], "'생산 팀장'은 사람 이름 아님", str(ents["person"]))
check(not any("달성" in p for p in gw.regex_entities("목표 달성 여부와 기장 확인")["place"]),
      "'목표 달성' 같은 일반 단어는 지명 아님")

# ── text 모드: 가명 + 복원 ──────────────────────────────────────────────────
masked, restore = asyncio.run(gw.outbound({"question": REQ, "context": "대광소결금속 과거 이력"}, endpoint="/v1/ask"))
q, c = masked["question"], masked["context"]
for secret in ("대광소결금속", "김철수", "010-1234-5678", "kim@daekwang", "산호대로", "시원C1", "동남정밀", "3억", "12,000원"):
    check(secret not in q and secret not in c, f"외부로 안 나감: {secret}", q)
for keep in ("한화로봇 14kg", "Keyence", "700kg", "AMR", "소결 라인", "3 원통"):
    check(keep in q, f"공정 설명·장비는 유지: {keep}", q)
check("고객사A" in q and "고객사A" in c, "같은 이름은 필드가 달라도 같은 가명", f"{q} | {c}")
check(gw.MONEY_MASK in q and gw.CONTACT_MASK in q, "금액·연락처 제거 표시")
check("지어낸제품X" not in str(records[-1]), "모델이 지어낸 이름은 무시")
back = restore({"answer": "고객사A 지역A 라인에 제품A 이송", "description": "고객사A 설명", "other": "고객사A"})
check(back["answer"].startswith("대광소결금속") or "대광소결금속" in back["answer"], "답에서 원래 이름 복원", back["answer"])
check(back["other"] == "고객사A", "복원은 answer/description 만")
rec = records[-1]
check(rec["status"] == "sent" and rec["endpoint"] == "/v1/ask" and "대광소결금속" not in json.dumps(rec, ensure_ascii=False),
      "기록에는 가명 처리본만(원래 이름 저장 안 함)", json.dumps(rec, ensure_ascii=False)[:200])
check(rec["counts"].get("company", 0) >= 2 and rec["counts"].get("money", 0) == 2, "종류별 가린 개수 기록", str(rec["counts"]))

# ── image 모드: 일반어(그림 속 글자는 복원 불가) ────────────────────────────
masked_i, restore_i = asyncio.run(gw.outbound({"prompt": REQ}, endpoint="/v1/image"))
p = masked_i["prompt"]
check("고객사A" not in p and "고객사" in p and "대광소결금속" not in p, "이미지는 일반어 '고객사'", p)
check(restore_i({"description": "고객사 라인"})["description"] == "고객사 라인", "이미지 모드는 복원 없음")

# ── search 모드: 치환 없이 기록만 ───────────────────────────────────────────
calls["llm"] = 0
masked_s, _ = asyncio.run(gw.outbound({"query": "대광소결금속 최근 뉴스"}, endpoint="/v1/search"))
check(masked_s["query"] == "대광소결금속 최근 뉴스" and calls["llm"] == 0, "검색어는 그대로(로컬 추출도 안 함)")
check(records[-1]["mode"] == "search", "검색도 기록")

# 한 줄이 조각 크기보다 길면 겹쳐 자른다 — 경계에 걸친 이름이 어느 한 조각에는 온전히 들어간다
long_line = "가" * (gw.LLM_CHUNK - 3) + "삼성웰스토리" + "나" * 100
pieces = gw._chunks(long_line)
check(len(pieces) == 2 and any("삼성웰스토리" in p_ for p_ in pieces) and "".join(pieces).count("삼성웰스토리") >= 1,
      "긴 한 줄을 자를 때 경계에 걸친 이름이 잘리지 않음", str([len(p_) for p_ in pieces]))

# ── codex_client._post: 브리지로 실제 나가는 본문 ─────────────────────────────
from app import codex_client  # noqa: E402

posted: list[tuple[str, dict]] = []
REPLY = {"/v1/ask": {"answer": "고객사A 지역A 라인 검토 결과"},
         "/v1/image": {"image_base64": "iVBORw0KGgo=", "mime": "image/png", "description": "고객사 라인 컨셉"},
         "/v1/search": {"answer": "검색 답", "results": []}}


class FakeResp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json, headers):
        path = url[url.index("/v1/"):]
        posted.append((path, json))
        return FakeResp(REPLY[path])


codex_client.httpx.AsyncClient = FakeClient
codex_client.is_configured = lambda: True
codex_client.settings.codex_bridge_url = codex_client.settings.codex_bridge_url or "http://bridge"
proposal_llm.chat = fake_llm

answer = asyncio.run(codex_client.ask(REQ, "대광소결금속 과거 이력", user_id=5))
check(records[-1]["endpoint"] == "/v1/ask" and records[-1].get("user_id") == 5, "요청자 user_id 가 기록까지 전달", str(records[-1])[:120])
body = json.dumps(posted[-1][1], ensure_ascii=False)
check(all(s not in body for s in ("대광소결금속", "김철수", "010-1234-5678", "3억", "시원C1")),
      "ask: 브리지로 나간 본문에 원래 이름·연락처·금액 없음", body[:200])
check("Keyence" in body and "소결 라인" in body, "ask: 공정·장비 설명은 그대로 전송")
check(answer.startswith("대광소결금속"), "ask: 답은 원래 이름으로 복원되어 돌아옴", answer)

image, mime, desc = asyncio.run(codex_client.generate_image("대광소결금속 구미공장 소결 라인 로봇 셀"))
prompt_sent = posted[-1][1]["prompt"]
check("대광소결금속" not in prompt_sent and "고객사" in prompt_sent, "image: 일반어로 가려서 전송", prompt_sent)
check(image.startswith(b"\x89PNG") and desc == "고객사 라인 컨셉", "image: 이미지·설명은 그대로(복원 없음)")

asyncio.run(codex_client.search("협동로봇 용접 사례"))
check(posted[-1] == ("/v1/search", {"query": "협동로봇 용접 사례"}) and records[-1]["mode"] == "search",
      "search: 검색어 그대로 전송 + 기록")

# ── fail-closed ─────────────────────────────────────────────────────────────
async def broken_llm(messages, **kw):
    raise RuntimeError("ollama down")

proposal_llm.chat = broken_llm
try:
    asyncio.run(gw.outbound({"question": REQ}, endpoint="/v1/ask"))
    check(False, "가림 실패 시 전송 차단")
except gw.GatewayError as e:
    check("전송하지 않았습니다" in str(e), "가림 실패 시 전송 차단(GatewayError)", str(e))
check(records[-1]["status"] == "blocked" and "대광" not in json.dumps(records[-1], ensure_ascii=False),
      "차단도 기록(원문 없이)", str(records[-1]))
n_posted = len(posted)
try:
    asyncio.run(codex_client.ask(REQ))
    check(False, "_post: 가림 실패면 CodexError")
except codex_client.CodexError as e:
    check("전송하지 않았습니다" in str(e) and len(posted) == n_posted,
          "_post: 가림 실패면 브리지 호출 없이 CodexError(호출부 기존 폴백으로)", str(e))

# ── 회귀(2026-09-29): 지시문+자료를 한 글로 합치면 모델이 빈 목록을 내 고객사명이 그대로 나갔다 ─────────────
INSTR = "너는 수석 엔지니어다. 대안을 제안하라. 출력은 JSON 객체 하나만 쓴다: {\"alternatives\": []}"
DATA = "P01 고객사: 삼성웰스토리, 부서: InnovationLab\nP06 식기 약 8종"


async def distracted_llm(messages, **kw):
    """지시문이 섞인 글에는 빈 목록을 내는(실측과 같은) 모델."""
    body = messages[-1]["content"]
    if "대안을 제안하라" in body:
        return json.dumps({"companies": [], "orgs": [], "products": [], "places": [], "people": []})
    names = {"companies": [], "orgs": []}
    if "삼성웰스토리" in body:
        names["companies"].append("삼성웰스토리")
    if "InnovationLab" in body:
        names["orgs"].append("InnovationLab")
    return json.dumps(names, ensure_ascii=False)

proposal_llm.chat = distracted_llm
m = asyncio.run(gw.mask({"question": INSTR, "context": DATA}, "text"))
check("삼성웰스토리" not in m.payload["context"] and "InnovationLab" not in m.payload["context"]
      and m.mapping.get("고객사A") == "삼성웰스토리" and m.mapping.get("부서A") == "InnovationLab",
      "필드마다 따로 추출 — 지시문이 섞여도 고객사·부서명 가림", str(m.payload))

long_ctx = ("공정 설명 줄입니다.\n" * 2500) + DATA           # 약 3만 자 뒤에 고객사명
m = asyncio.run(gw.mask({"question": "요약", "context": long_ctx}, "text"))
check("삼성웰스토리" not in m.payload["context"], "긴 자료도 끝까지 조각내 검사(앞 6000자 제한 없음)")


async def wrong_shape_llm(messages, **kw):
    return json.dumps({"alternatives": [{"name": "A"}]})     # 지시문을 따라 엉뚱한 형식으로 답함

proposal_llm.chat = wrong_shape_llm
try:
    asyncio.run(gw.mask({"question": INSTR, "context": DATA}, "text"))
    check(False, "형식이 틀린 추출 결과는 차단")
except gw.GatewayError:
    check(True, "형식이 틀린 추출 결과는 '이름 없음' 으로 보지 않고 차단")
proposal_llm.chat = fake_llm

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
