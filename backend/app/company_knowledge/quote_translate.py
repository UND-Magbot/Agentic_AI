"""해외 견적서의 한글 칸 → AI 영문 번역(사용자 2026-10-14).

칸에는 담당자가 쓴 한글을 그대로 두고, 영어는 state.en_text 에 따로 둔다 — {path: {src, en, by: ai|user}}.
해외 견적서 미리보기·발행 때 번역이 없거나 원문이 바뀐 칸만 사내 AI 가 번역하고(ensure), 견적서에는 영어가 찍힌다(localized).
사람이 미리보기와 'AI 번역' 목록으로 확인하고, 마음에 안 들면 그 칸의 영어를 직접 고친다(set_en, by=user).
이름·회사명도 AI 가 옮기되 확인 필요로 표시한다. 꼭 한글로 둘 칸은 [한글 그대로](state.keep_ko) — 번역하지 않고 발행도 막지 않는다.
AI 답은 코드가 검사한다: 한글이 남았거나, 모델명·숫자(TCV1·MG10·30)가 빠졌거나, 칸 길이를 넘으면 쓰지 않는다 → 그 칸만 발행이 막힌다.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import quote_session as qs
from . import quote_xlsx as qx

HANGUL = re.compile(r"[ᄀ-ᇿㄱ-ㆎ가-힣]")
_TOKEN = re.compile(r"[A-Za-z]*\d[A-Za-z0-9.\-/]*")
# 수량 표기(2EA·3PCS·1SET) — 회사 표기('2 MG10 Grippers')처럼 수량 단어가 바뀌어도 숫자만 남으면 된다(검증 QT-048, 2026-10-15)
_COUNT = re.compile(r"(\d+(?:\.\d+)?)(?:ea|pcs?|sets?|units?|lots?)", re.I)
# 사람 이름·회사명 — AI 가 옮기되(로마자) 틀리기 쉬워 '확인 필요'로 보여 준다
NAME_FIELDS = ("customer", "to", "cc", "contact_name_en")
_FORM = (("customer", "고객사"), ("to", "받는 분(To)"), ("cc", "참조(CC)"), ("contact_name_en", "담당자 영문 이름"),
         ("comments", "Comments"), ("delivery", "납기"), ("place", "납품장소"), ("payment", "결제조건"), ("note", "비고"),
         ("total_note", "Total 아래 문구"), ("bank", "입금 은행"), ("account_holder", "예금주"))
# 칸 길이(저장할 때 자르는 길이와 같게) — 넘으면 AI 답을 쓰지 않는다
_MAX = {"form": 200, "name": qs.SPEC_MAX, "remark": 100, "commodity": 60, "title": 60, "label": 40, "value": qs.SPEC_MAX,
        "text": qs.SPEC_MAX, "unit": 8, "foot": 200}
_AUTO_DESC = ("MASTER T.C", "T.P", *qx._DESC)          # 규격 문구를 코드가 만드는 줄(언어별로 이미 영문)


def has_hangul(v: Any) -> bool:
    return bool(HANGUL.search(str(v or "")))


def _short(v: Any, n: int = 14) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


def cells(state: dict[str, Any]) -> list[dict[str, Any]]:
    """해외 견적서에 찍힐 칸 중 한글이 있는 칸(원문 기준) — [{path, label, text, name}]. name=True 는 이름·회사명(확인 필요).
    국내 견적서면 빈 목록. 코드가 영문으로 바꿔 찍는 칸(기본 비고·물류비/인건비 문구·한글 단위 식/개월 …)은 빼고 본다."""
    f = state.get("form") or {}
    if f.get("lang") != "en":
        return []
    out: list[dict[str, Any]] = []

    def add(path: str, label: str, text: Any, name: bool = False) -> None:
        if has_hangul(text):
            out.append({"path": path, "label": label, "text": str(text), "name": name})

    for k, lab in _FORM:
        v = f.get(k)
        if k == "contact_name_en" and not str(v or "").strip():
            v = f.get("contact_name")                          # 영문 이름이 비면 한글 이름이 찍힌다
        add(f"form.{k}", lab, v, name=k in NAME_FIELDS)
    for i, x in enumerate(qs.clean_notes(f.get("foot_notes"))):
        add(f"form.foot_notes.{i}", f"표 아래 주석 {i + 1}번째", x)
    for ln in state.get("lines") or []:
        nm = _short(ln.get("name") or ln.get("commodity"))
        where = f"품목 '{nm}'" if nm else "품목"
        add(f"line.{ln['id']}.commodity", f"{where} 품목(Commodity)" if ln.get("kind") != "note" else f"{where} 항목명", qx.commodity_of(ln))
        if ln.get("kind") in ("work", "freight"):
            continue                                           # 규격·비고·단위는 코드가 영문으로
        if not (ln.get("key") in _AUTO_DESC and ln.get("kind") in ("product", "option")):
            add(f"line.{ln['id']}.name", f"{where} 규격" if ln.get("kind") != "note" else f"{where} 내용", ln.get("name"))
        if ln.get("remark") and ln.get("remark") != qx._REMARK["ko"].get(ln.get("key"), None):
            add(f"line.{ln['id']}.remark", f"{where} 비고", ln.get("remark"))
        if ln.get("unit") and has_hangul(qs.unit_label(ln["unit"], "en")):
            add(f"line.{ln['id']}.unit", f"{where} 단위", ln["unit"])
    for b in qs.blocks(state):
        name = f"묶음 '{_short(b.get('title'))}'" if b.get("title") else "묶음"
        add(f"block.{b['id']}.title", f"{name} 이름", b.get("title"))
        for i, r in enumerate(b.get("rows") or []):
            add(f"block.{b['id']}.rows.{i}.label", f"{name} {i + 1}번째 줄 항목명", r.get("label"))
            add(f"block.{b['id']}.rows.{i}.value", f"{name} {i + 1}번째 줄 내용", r.get("value"))
        add(f"block.{b['id']}.text", f"{name} 서술형", b.get("text"))
    add("nego.label", "NEGO 설명", (state.get("nego") or {}).get("label"))
    return out


def _kind(path: str) -> str:
    p = path.split(".")
    if p[0] == "form":
        return "foot" if p[1] == "foot_notes" else "form"
    if p[0] == "line":
        return p[2]
    if p[0] == "block":
        return p[2] if len(p) == 3 else p[4]
    return "label"


def get_cell(state: dict[str, Any], path: str) -> str | None:
    p = path.split(".")
    try:
        if p[0] == "form":
            f = state["form"]
            if p[1] == "foot_notes":
                return qs.clean_notes(f.get("foot_notes"))[int(p[2])]
            return str(f.get(p[1]) or "")
        if p[0] == "line":
            ln = next(x for x in state["lines"] if x["id"] == p[1])
            return qx.commodity_of(ln) if p[2] == "commodity" else str(ln.get(p[2]) or "")
        if p[0] == "block":
            b = next(x for x in state.get("blocks") or [] if x.get("id") == p[1])
            return str(b["rows"][int(p[3])].get(p[4]) or "") if p[2] == "rows" else str(b.get(p[2]) or "")
        if p[0] == "nego":
            return str((state.get("nego") or {}).get("label") or "")
    except (StopIteration, KeyError, IndexError, ValueError, TypeError):
        return None
    return None


def set_cell(state: dict[str, Any], path: str, text: str) -> bool:
    """칸 하나를 바꾼다(그 칸의 정리 규칙 그대로). 칸이 없으면 False."""
    if get_cell(state, path) is None:
        return False
    p = path.split(".")
    k = _kind(path)
    if k in ("name", "value", "text"):
        v = qs.clean_spec(text)
    elif k == "unit":
        v = qs.clean_unit(text)
    elif p[0] == "form" and p[1] == "comments":
        v = qs.clean_comments(text)
    else:
        v = re.sub(r"[ \t]+", " ", str(text or "")).strip()[:_MAX.get(k, 200)]
    if p[0] == "form":
        if p[1] == "foot_notes":
            notes = qs.clean_notes(state["form"].get("foot_notes"))
            notes[int(p[2])] = v
            state["form"]["foot_notes"] = qs.clean_notes(notes)
        else:
            state["form"][p[1]] = v
    elif p[0] == "line":
        next(x for x in state["lines"] if x["id"] == p[1])[p[2]] = v
    elif p[0] == "block":
        b = next(x for x in state["blocks"] if x.get("id") == p[1])
        if p[2] == "rows":
            b["rows"][int(p[3])][p[4]] = v
        else:
            b[p[2]] = v
    else:
        state["nego"]["label"] = v
    return True


SYSTEM = """You translate the Korean text in cells of a B2B quotation (UND Robotics — robot tool changers, grippers, automation)
into concise, natural business English as used on an overseas quotation.
Rules:
- Translate meaning, do not add or drop information. Keep it short — each item has a max length ("max"); stay under it.
- Keep model names, part codes, numbers, units, URLs and e-mail addresses exactly (e.g. TCV1, MG10, 4m, www.magbot.kr).
- Keep line breaks ("\\n") where the source has them.
- Korean person and company names: romanize (Revised Romanization) — "(주)유엔디" → "UND Co., Ltd.", "홍길동" → "Gildong Hong",
  "황 과장님" → "Mr. Hwang" (title words like 과장·부장·님 become Mr./Ms. only if obvious, otherwise drop them).
- Common terms: 식 → lot, 개월 → months, 대 → unit, 개 → ea, 인·일 → man-day, 별도 → excluded/separately,
  납품 → delivery, 설치 → installation, 발주 → order, 협의 → to be discussed, 부가세 별도 → VAT excluded.
- Follow the company's overseas quotation style (spacing between number and unit, "Label: value", plural nouns):
  "페이로드 5kg" → "Payload: 5 kg", "마스터 툴체인저 1개" → "1 Master Tool Changer", "툴 플레이트 2개" → "2 Tool Plates",
  "커플러 포함" → "Couplers included", "공장 인도" → "Ex-work".
- The output must contain no Korean characters.
Answer JSON only: {"items":[{"id":<same id>,"en":"<English>"}]}"""


def _ok(src: str, en: str, limit: int) -> str | None:
    """AI 답을 쓸 수 있는지 — 안 되면 이유."""
    if not en.strip():
        return "빈 답"
    if has_hangul(en):
        return "한글이 남음"
    if len(en) > limit:
        return "칸 길이 초과"
    low = re.sub(r"\s+", "", en.lower())                   # '5kg' → '5 kg'(해외 견적서 표기)는 그대로 있는 것으로
    lost = [t for t in _TOKEN.findall(src) if t.lower().rstrip(".-/") not in low
            and not ((m := _COUNT.fullmatch(t.rstrip(".-/"))) and re.search(rf"(?<![\d.]){re.escape(m.group(1))}(?![\d.])", en))]
    if lost:
        return f"모델명·숫자 빠짐({', '.join(lost[:3])})"
    return None


async def _ask(todo: list[dict[str, Any]], chat) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """한글 칸들 → AI 영어 ({path: en}, 못 바꾼 칸 [{path,label,why}]). AI 가 응답하지 않으면 chat 의 예외 그대로."""
    from ..diagram.llm_spec import _extract_json

    items = [{"id": i, "text": c["text"], "max": _MAX.get(_kind(c["path"]), 200)} for i, c in enumerate(todo)]
    data = _extract_json(await chat([{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)}],
                                    num_predict=min(6000, 400 + 120 * len(items)), temperature=0.1))
    got = {}
    for x in (data.get("items") or []) if isinstance(data, dict) else []:
        if isinstance(x, dict) and isinstance(x.get("id"), int) and isinstance(x.get("en"), str):
            got[x["id"]] = x["en"].replace("\r", "").strip()
    ok, failed = {}, []
    for i, c in enumerate(todo):
        en = got.get(i)
        why = "AI 답 없음" if en is None else _ok(c["text"], en, items[i]["max"])
        if why:
            failed.append({"path": c["path"], "label": c["label"], "why": why})
        else:
            ok[c["path"]] = en
    return ok, failed


def overview(state: dict[str, Any]) -> list[dict[str, Any]]:
    """해외 견적서의 한글 칸과 영어 — [{path, label, text, name, en, by, keep}].
    en=None 은 아직 번역 전(처음이거나 원문을 고침) — 다음 미리보기·발행 때 AI 가 번역한다. keep=True 는 [한글 그대로]."""
    store = state.get("en_text") or {}
    keep = set(state.get("keep_ko") or [])
    # [한글 그대로]는 고를 때의 글에 묶는다 — 주석처럼 번호로 찾는 칸은 앞줄을 지우면 다음 글이 그 번호로 와서
    # 번역 없이 한글로 나갔다(검증 QT-053, 2026-10-15). 예전 기록(글 없음)은 칸만 본다
    keep_src = state.get("keep_src") or {}
    out = []
    for c in cells(state):
        e = store.get(c["path"]) or {}
        fresh = e.get("src") == c["text"] and isinstance(e.get("en"), str)
        kept = c["path"] in keep and keep_src.get(c["path"], c["text"]) == c["text"]
        out.append({**c, "en": e["en"] if fresh else None, "by": e.get("by") if fresh else None, "keep": kept})
    return out


def untranslated(state: dict[str, Any]) -> list[dict[str, Any]]:
    """영어가 없는 칸([한글 그대로] 제외) — 발행을 막는다."""
    return [c for c in overview(state) if c["en"] is None and not c["keep"]]


def localized(state: dict[str, Any]) -> dict[str, Any]:
    """견적서에 찍을 상태 — 한글 칸을 저장된 영어로 바꾼 복사본(해외 견적서만, [한글 그대로]·번역 전 칸은 원문)."""
    if (state.get("form") or {}).get("lang") != "en":
        return state
    new = copy.deepcopy(state)
    for c in overview(state):
        if c["en"] is not None and not c["keep"]:
            set_cell(new, c["path"], c["en"])
    return new


def _pruned(state: dict[str, Any]) -> dict[str, Any]:
    """지금 한글이 아닌 칸의 번역·[한글 그대로] 표시는 지운다(칸을 직접 영어로 고쳤거나 줄을 지운 경우)."""
    live = {c["path"] for c in cells(state)}
    state["en_text"] = {k: v for k, v in (state.get("en_text") or {}).items() if k in live}
    state["keep_ko"] = [k for k in (state.get("keep_ko") or []) if k in live]
    state["keep_src"] = {k: v for k, v in (state.get("keep_src") or {}).items() if k in state["keep_ko"]}
    return state


async def ensure(state: dict[str, Any], chat) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """번역 전 칸만 AI 로 번역해 저장 → (새 상태, 번역한 칸 [{path,label,text,en}], 못 바꾼 칸 [{path,label,why}]).
    할 칸이 없으면 같은 상태 그대로."""
    todo = untranslated(state)
    if not todo:
        return state, [], []
    ok, failed = await _ask(todo, chat)
    new = copy.deepcopy(state)
    store = new.setdefault("en_text", {})
    done = []
    for c in todo:
        if c["path"] in ok:
            store[c["path"]] = {"src": c["text"], "en": ok[c["path"]], "by": "ai"}
            done.append({"path": c["path"], "label": c["label"], "text": c["text"], "en": ok[c["path"]]})
    return _pruned(new), done, failed


def set_en(state: dict[str, Any], path: str, *, en: str | None = None, keep: bool | None = None) -> dict[str, Any]:
    """담당자가 고친 영어(en) · [한글 그대로](keep) → 새 상태. en='' 은 번역을 지워 다음에 AI 가 다시 번역.
    없는 칸·한글이 섞인 영어·칸 길이 초과는 ValueError."""
    c = next((x for x in cells(state) if x["path"] == path), None)
    if c is None:
        raise ValueError("한글이 있는 칸이 아닙니다 — 화면을 새로 고쳐 주세요.")
    new = copy.deepcopy(state)
    if en is not None:
        store = new.setdefault("en_text", {})
        en = en.replace("\r", "").strip()
        if not en:
            store.pop(path, None)
        else:
            if has_hangul(en):
                raise ValueError("영어 칸에 한글이 있습니다. 한글로 두려면 [한글 그대로]를 고르세요.")
            if len(en) > _MAX.get(_kind(path), 200):
                raise ValueError(f"영어가 칸 길이({_MAX.get(_kind(path), 200)}자)를 넘습니다.")
            store[path] = {"src": c["text"], "en": en, "by": "user"}
    if keep is not None:
        ks = [k for k in (new.get("keep_ko") or []) if k != path]
        new["keep_ko"] = [*ks, path] if keep else ks
        src = {k: v for k, v in (new.get("keep_src") or {}).items() if k != path}
        new["keep_src"] = {**src, path: c["text"]} if keep else src
    return _pruned(new)
