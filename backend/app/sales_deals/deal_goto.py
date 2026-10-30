# -*- coding: utf-8 -*-
"""채팅에서 영업 건 찾아 화면으로 보내기(사용자 2026-10-28).

'OO 견적서 수정하려면 어떻게 해?'에 방법 설명 대신 — 영업 건을 DB 에서 찾아 지금 상태를 알리고
[견적서 고치기로 이동] 같은 버튼(채팅 말풍선이 그리는 `[이름](/proposals/recommend?…)` 표시)을 붙인다.
이동은 버튼을 눌러야만(다른 건을 잘못 열지 않게). 대여 목록처럼 /v1/generate 의 결정론 fast-path — AI 가 상태를 지어내지 않게.

할 일 4가지: 견적서 수정(quote) · 거래명세서(statement) · 출하 기록(ship) · 건 열기(open).
대상 이름이 없으면('견적서 수정하는 방법') 가로채지 않고 AI 대화로 넘긴다.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import service as svc

BASE = "/proposals/recommend"
MAX_SHOW = 5

_ACTION = re.compile(r"수정|고치|고쳐|고칠|바꾸|바꿔|변경|재발행|발급|발행|작성|만들|등록|입력|열어|열기|열고|보여|찾아|확인|이동|들어가|가고\s*싶")
_STATEMENT = re.compile(r"(거래\s*)?명세서")
_SHIP = re.compile(r"출하")
_QUOTE = re.compile(r"견적")
# 건 열기 — '건' 한 글자만으로는 잡지 않는다(재무 '이 건 확인' 등). 영업 건·프로젝트, 또는 건을 열기·보기·상태,
# 대여 시작일·기간·회수처럼 건 상세에서 바꾸는 것(검증 2026-10-28)
_DEAL = re.compile(r"영업\s*건|프로젝트|건\s*(을|를)?\s*(좀\s*)?(열|보여|찾|상태)|건\s*(으로|에)\s*(이동|들어가|가)|"
                   r"대여\s*(시작일|기간|끝나는|조건)|회수")
# 일반 질문(양식·예시)이나 다른 기능(추천·대여 목록) — 가로채지 않는다
_NOT = re.compile(r"양식|템플릿|예시|샘플|추천|대여\s*(나간|중인)|단가표")

# 이름이 아닌 말 — 이 조각이 들어 있으면 버린다(동작·질문·대상 종류)
_FILLER = re.compile(
    r"수정|고치|고쳐|고칠|바꾸|바꿔|변경|재발행|발급|발행|작성|만들|등록|입력|열어|열기|열고|보여|찾아|확인|이동|들어가|"
    r"하려|하고|싶|어떻게|어떡|방법|해야|해줘|해주|할까|할래|하면|되나|돼|있|알려|주세|줘|좀|채팅|여기|지금|다시|"
    r"견적|명세서|출하|기록|영업|프로젝트|고객사|상태|페이지|화면|최신|버전|부탁|가능|필요|싶어|어디|무엇|뭐|"
    # 질문에 흔한 말(검증 2026-10-28 — '거래명세서 발급 절차가 어떻게 돼?'의 '절차'를 건 이름으로 찾았다)
    r"절차|순서|언제|규칙|기준|가장|최근|무슨|어느|전부|모든|요즘|이번|지난|저번|그럼|그건|대여|시작일|기간|회수|끝나는|조건|"
    # 화면 이동 말(사용자 2026-10-28 — 화면으로 가는 말은 screen_goto 가 받는다)
    r"목록|리스트|수기|탭|메뉴", re.I)
_STOP = {"건", "그", "이", "저", "내", "우리", "제", "거", "것", "ai", "s/n", "sn", "해", "좀"}
_JOSA = ("에서", "으로", "이랑", "한테", "께서", "까지", "부터", "의", "을", "를", "이", "가", "은", "는", "에", "로", "과",
         "와", "랑", "도", "만", "건", "쪽", "꺼")
# 바로 뒤에 이 말이 오면 앞 말은 건을 가리키는 이름('삼성전자 견적서') · 발행번호·건 번호는 그 자체로 이름
_DOCWORD = re.compile(r"견적|명세서|출하|영업|프로젝트|대여|^건")
_DOCNO = re.compile(r"^((QT|TS)-?)?[A-Z]{2}\d{8}(-\d{1,3})?$", re.I)
# 앞 답의 이동 버튼 · '응 이동해줘' 같은 짧은 승낙
_LINK = re.compile(r"\[([^\]\n]{1,40})\]\((/proposals/(?:recommend\?[\w=&-]+|new))\)")
_YES = re.compile(r"^(응|어|네|넵|예|ㅇㅇ|ㅇㅋ|좋아|그래|오케이|ok|가자|이동|열어|가\s*줘|해\s*줘|부탁)", re.I)


def _kind(t: str, follow: bool = False) -> str | None:
    """follow = 바로 앞 답이 건 하나를 보여 줬음 — '그럼 거래명세서는?'처럼 동작 말이 없어도 그 건 이야기."""
    if _NOT.search(t) or not (follow or _ACTION.search(t)):
        return None
    if _STATEMENT.search(t):
        return "statement"
    if _SHIP.search(t):
        return "ship"
    if _QUOTE.search(t):
        return "quote"
    if _DEAL.search(t):
        return "open"
    return None


def _strip_josa(w: str) -> str:
    for j in _JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def names(t: str) -> tuple[list[str], bool]:
    """말에서 건을 가리키는 이름 조각(고객사·건명·발행번호)과, 그중 확실히 이름인 것(문서 말 바로 앞·번호)이 있는지."""
    words = re.sub(r"[\"'“”‘’「」『』\[\](){}<>,.?!~·:;]", " ", t).split()
    out, lead = [], False
    for i, raw in enumerate(words):
        w = _strip_josa(raw.strip("-"))
        if len(w) < 2 or w.lower() in _STOP or _FILLER.search(w):
            continue
        out.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _DOCNO.match(w) or _DOCWORD.search(nxt) or raw.endswith("건"):
            lead = True
    return out, lead


def detect(messages: list[dict]) -> dict | None:
    """마지막 사용자 말이 '어느 건의 무엇을 하고 싶다'면 {kind, names, lead[, deal_id]} · 앞 답의 버튼에 '응' 하면 {kind: repeat}.
    이름이 없으면 None(AI 가 답한다)."""
    if not messages or messages[-1].get("role") != "user":
        return None
    t = str(messages[-1].get("content") or "").strip()
    prev = str(messages[-2].get("content") or "") if len(messages) > 1 and messages[-2].get("role") == "assistant" else ""
    links = _LINK.findall(prev)
    # '응 이동해줘' — 채팅은 화면을 직접 열 수 없으니 버튼을 다시 준다(검증 2026-10-28: AI 가 '이동합니다'라고만 답했다)
    if links and len(t) <= 20 and _YES.match(t) and not (_QUOTE.search(t) or _STATEMENT.search(t) or _SHIP.search(t)):
        return {"kind": "repeat", "links": links}
    prev_deals = {int(x) for x in re.findall(r"[?&]deal=(\d+)", prev)}
    kind = _kind(t, follow=len(prev_deals) == 1)
    if not kind:
        return None
    ns, lead = names(t)
    if not ns and len(prev_deals) == 1:             # '그 건 출하 기록도 고쳐야 해' — 앞 답의 그 건
        return {"kind": kind, "names": [], "lead": True, "deal_id": next(iter(prev_deals))}
    return {"kind": kind, "names": ns, "lead": lead} if ns else None


def _norm(s: Any) -> str:
    s = str(s or "").lower()
    s = re.sub(r"\(주\)|㈜|주식회사", "", s)
    return re.sub(r"[\s\-_.·()]", "", s)


def match(rows: list[dict], ns: list[str]) -> list[dict]:
    """이름 조각이 가장 많이 맞는 건들(새로 만든 건이 위). 하나도 안 맞으면 []."""
    keys = [_norm(n) for n in ns if len(_norm(n)) >= 2]
    scored = []
    for r in rows:
        hay = " ".join(_norm(r.get(k)) for k in ("customer", "title", "deal_no", "display_no", "statement_no", "legacy_no"))
        score = sum(1 for k in keys if k in hay)
        if score:
            scored.append((score, r))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [r for s, r in scored if s == top]


def _won(v: Any) -> str:
    return f"{round(v):,}" if isinstance(v, (int, float)) else "-"


def status_line(r: dict, quotes: list[dict]) -> str:
    """건 한 줄 상태 — 단계 · 견적서 · 거래명세서 · 출하 · 입금."""
    bits = [r["stage"]]
    issued = [q for q in quotes if q.get("quote_no")]
    if issued:
        q = issued[-1]
        bits.append(f"견적서 v{(q.get('revision') or 0) + 1} 발행")
    elif quotes:
        bits.append("견적서 작성 중(발행 전)")
    elif r.get("display_no"):
        bits.append("견적서 발행")
    if r.get("won"):
        bits.append(f"거래명세서 {r['statement_no']} 발급" if r.get("statement_no") else "거래명세서 발급 전")
        n = len(r.get("shipments") or [])
        bits.append(f"출하 {n}회" if n else "출하 전")
        fl = r.get("flow") or {}
        if fl.get("total"):
            bits.append(f"입금 {_won(fl.get('paid'))} / {_won(fl.get('total'))}원")
    return " · ".join(bits)


def _btn(label: str, query: str) -> str:
    return f"[{label}]({BASE}?{query})"


def _deal_tab(r: dict) -> str:
    return "deals"          # 대여 건도 제품 영업 건 관리 탭(사용자 2026-10-30, 예전엔 대여 관리 탭)


def actions(kind: str, r: dict, quotes: list[dict]) -> tuple[str | None, list[str]]:
    """그 건에서 할 일 → (안내 한 줄 | None, 버튼들). 지금 할 수 없으면 이유와 대신 갈 곳(건 열기)."""
    open_btn = _btn("건 열기", f"tab={_deal_tab(r)}&deal={r['id']}")
    if r.get("dropped") or r["stage"] == "드랍":
        return "드랍한 건입니다 — 다시 진행하려면 관리자에게 건 상세의 [되살리기]를 요청해 주세요.", [open_btn]
    if kind == "open":
        return None, [open_btn]
    if kind == "quote":
        if not quotes:
            if r.get("kind") == "recommend":
                return "아직 견적서를 만들지 않은 건입니다 — 건을 열어 [견적서 작성]으로 시작합니다.", [open_btn]
            return "이 건은 프로그램에서 작성한 견적서가 없습니다(올린 파일·예전 기록) — 건을 열어 견적서를 다시 올리거나 바꿉니다.", [open_btn]
        q = ([x for x in quotes if x.get("quote_no")] or quotes)[-1]
        if q.get("mine") is False:
            # 발행 전·어디에도 안 이어진 남의 작성 중 견적서 — 쓴 사람만 열린다(_quote_row, 사용자 2026-10-28)
            who = f"{q['writer']}님" if q.get("writer") else "작성자"
            return f"이 견적서는 {who}이 아직 작성 중(발행 전)이라 {who}만 열 수 있습니다 — 건을 열어 확인하거나 {who}께 요청해 주세요.", [open_btn]
        label = "견적서 고치기로 이동" if q.get("status") == "issued" else "견적서 이어서 작성"
        note = "완납까지 끝난 건이라 고쳐서 발행할 때 한 번 더 확인합니다." if r["stage"].startswith("완료") else None
        return note, [_btn(label, f"tab=quote&quote={q['id']}"), open_btn]
    # 거래명세서·출하 — 수주 진행 화면(수주 확정한 새 절차 건만)
    if not r.get("won"):
        return "아직 수주 확정 전입니다 — 건을 열어 [수주 확정]을 먼저 해 주세요.", [open_btn]
    if not (r.get("flow") or {}).get("steps"):
        return "수주 진행 절차로 다루지 않는 건입니다(엑셀에서 옮긴 예전 건 등) — 건 상세에서 기록을 봅니다.", [open_btn]
    if kind == "statement":
        label = "거래명세서로 이동" if not r.get("statement_no") else "거래명세서 확인·다시 발급으로 이동"
        return None, [_btn(label, f"tab=order&deal={r['id']}"), open_btn]
    note = None if r.get("statement_no") else "거래명세서 발급 전입니다 — 보통은 명세서를 발급한 뒤 출하를 기록합니다(먼저 기록해도 됩니다)."
    label = "출하 기록 수정으로 이동" if r.get("shipments") else "출하 기록 작성으로 이동"
    return note, [_btn(label, f"tab=order&deal={r['id']}&focus=ship"), open_btn]


WHAT = {"quote": "견적서 수정", "statement": "거래명세서", "ship": "출하 기록", "open": "건"}


def _head(r: dict) -> str:
    no = r.get("display_no") or r.get("deal_no") or "번호 미부여"
    name = " · ".join(v for v in (r.get("customer"), r.get("title")) if v)
    return f"{name} ({no})" + (" · 대여" if r.get("biz") == "rental" else "")


async def _quotes(db: AsyncSession, r: dict, user_id: int | None) -> list[dict]:
    """건의 견적서들 + mine(이 사람이 견적서 작업 화면을 열 수 있는지 — product_recommend._quote_row 와 같은 규칙:
    작성자 본인, 또는 영업부끼리 같이 쓰는 견적서(발행·건 연결·확정 제안)). user_id 를 모르면 mine 을 두지 않는다."""
    from ..company_knowledge import product_recommend as pr

    quotes = await svc.deal_quotes(db, r.get("proposal_id"), r["id"])
    if user_id is None or not quotes:
        return quotes
    rows = {x["id"]: dict(x) for x in (await db.execute(text(
        "SELECT id, user_id, status, deal_id, recommendation_id FROM product_quotes WHERE id = ANY(:i)"),
        {"i": [q["id"] for q in quotes]})).mappings().all()}
    staff = await pr.sales_staff(db, user_id)
    out = []
    for q in quotes:
        row = rows.get(q["id"]) or {}
        mine = row.get("user_id") in (None, user_id) or (staff and await pr.quote_shared(db, row))
        out.append({**q, "mine": mine})
    return out


async def answer(db: AsyncSession, intent: dict, today: dt.date, user_id: int | None = None) -> str | None:
    """채팅 답. None = 건을 못 찾았고 이름도 확실하지 않음('절차'·'언제' 같은 말) — AI 대화로 넘긴다."""
    kind = intent["kind"]
    if kind == "repeat":
        return ("채팅에서는 화면을 바로 열 수 없어 버튼으로 이동합니다 — 아래 버튼을 눌러 주세요.\n"
                + "  ".join(f"[{label}]({href})" for label, href in intent["links"]))
    ns = intent["names"]
    rows = (await svc.list_deals(db, today))["rows"]
    found = [r for r in rows if r["id"] == intent["deal_id"]] if intent.get("deal_id") else match(rows, ns)
    said = "·".join(ns)
    if not found and not intent.get("lead"):
        return None
    if not found:
        out = [f"'{said}'(으)로 찾은 영업 건이 없습니다. 고객사·건 이름이나 발행번호(QT-…)로 다시 말씀해 주세요."]
        if kind == "quote":
            out.append("\n새 견적서라면 수기 작성으로 시작할 수 있습니다.\n" + _btn("견적서 수기 작성", "tab=quote&new=manual"))
        recent = [r for r in rows if not r.get("dropped")][:MAX_SHOW]
        if recent:
            out.append("\n최근 건:")
            for r in recent:
                out.append(f"· {_head(r)} — {r['stage']}  " + _btn("건 열기", f"tab={_deal_tab(r)}&deal={r['id']}"))
        return "\n".join(out)
    shown = found[:MAX_SHOW]
    out = []
    if len(found) == 1:
        r = found[0]
        quotes = await _quotes(db, r, user_id)
        note, btns = actions(kind, r, quotes)
        out.append(f"검색해 보니 {_head(r)} 건이 있습니다.")
        out.append(f"지금 상태: {status_line(r, quotes)}")
        if note:
            out.append(note)
        elif kind != "open":
            out.append(f"이 건의 {WHAT[kind]} 화면으로 이동할까요?")
        else:
            out.append("이 건을 열까요?")
        out.append("  ".join(btns))
        return "\n".join(out)
    out.append(f"'{said}'(으)로 {len(found)}건을 찾았습니다. {WHAT[kind]} 할 건을 골라 주세요."
               + (f" (최근 {MAX_SHOW}건만 보입니다 — 더 자세히 말씀해 주시면 좁힙니다)" if len(found) > MAX_SHOW else ""))
    for i, r in enumerate(shown, 1):
        quotes = await _quotes(db, r, user_id)
        note, btns = actions(kind, r, quotes)
        out.append(f"\n{i}. {_head(r)}")
        out.append(f"   {status_line(r, quotes)}")
        if note:
            out.append(f"   {note}")
        out.append("   " + "  ".join(btns))
    return "\n".join(out)
