"""견적서 작성 — AI 와 대화하며 견적 초안을 채우고 고친다(사용자 2026-10-06, 영업부 팀장 요청).

흐름: 제품 추천 [최종 제안 확정] → [견적서 작성] → 추천 결과(선정 모델·구성품·단가표)로 초안 → AI 가 빠진 것을 하나씩 묻는다
(고객사 → 납품 방식 5가지 → 물류비 / 설치 인원·일수·1인 1일 단가 → 다른 추가 항목 → 담당자) → 대화·직접 수정으로 품목 추가·수정·삭제 →
[견적서 발행] = 엑셀(quote_xlsx). 다시 발행하면 같은 견적번호에 수정 차수(revision)를 올리고 이전 판은 이력에 남긴다.

- 질문 순서와 문구는 코드가 정한다(AI 가 질문을 빼먹거나 지어내지 않게). AI 는 사용자의 말을 '변경(ops)'으로 옮기기만 한다.
- 숫자는 사용자가 적은 것만 받는다('50만원' 같은 만·억 단위는 풀어서 대조). 회사 기준이 없는 값(작업 1인 1일 단가 등)은 묻는다.
- 금액은 원화로 다룬다. 해외(영문) 견적은 발행할 때 입력 환율로 달러로 바꾼다(quote_xlsx).
- 최종 견적에 미정·0원을 넣지 않는다(v0.8 C13) — 발행 전 problems 가 비어야 한다.
- 액세서리 수량이 미정(None)이면 줄을 빼지 않고 남겨 발행을 막는다. 조건부 수량(v1.2 conditional_draft)은 담당자가 [수량 확인]하거나
  수량을 고치기 전에는 발행하지 않는다(PGR24 — 조건부 결과를 최종 견적으로 자동 확정하지 않음).
"""
from __future__ import annotations

import copy
import re
from typing import Any

from . import product_prices as pp
from . import quote_xlsx as qx

FORM_FIELDS = {   # 대화·직접 수정으로 바꿀 수 있는 머리 칸 → (라벨, 종류)
    "customer": ("고객사", "text"), "to": ("받는 분(To)", "text"), "cc": ("참조(CC)", "text"), "subject": ("파일명 내용", "text"),
    "initials": ("담당자 이니셜", "text"), "contact_name": ("담당자 이름", "text"), "contact_title": ("직함", "text"),
    "contact_mobile": ("휴대폰", "text"), "contact_email": ("이메일", "text"), "delivery": ("납기", "text"),
    "place": ("납품장소", "text"), "payment": ("결제조건", "text"), "comments": ("Comments", "text"), "note": ("비고", "text"),
    "lang": ("언어", "lang"), "fx_rate": ("환율(원/달러)", "number"),
    # 고객사가 우리에게 입금할 계좌(사용자 2026-10-08) — 기본은 회사 계좌(product_prices.QUOTE_ISSUER), 견적마다 바꿀 수 있다
    "bank": ("입금 은행", "text"), "account_no": ("입금 계좌번호", "text"), "account_holder": ("예금주", "text"),
}
BANK_FIELDS = ("bank", "account_no", "account_holder")


def bank_defaults(lang: str) -> dict[str, str]:
    """언어별 회사 기본 입금 계좌(국내 = 국민은행 원화 계좌, 해외 = 영문 송금 정보)."""
    iss = pp.quote_form(lang)["issuer"]
    return {k: str(iss.get(k) or "") for k in BANK_FIELDS}
WORK_NAME = {"ko": "설치 인건비", "en": "Installation labor"}
# UND 설치 인건비 회사 기준(사용자 2026-10-07): 1인 8시간(1일) 80만원 — 설치 납품이면 단가를 묻지 않고 이 값으로(대화·화면에서 바꿀 수 있음)
WORK_RATE = 800_000
FREIGHT_NAME = {"ko": "물류비", "en": "Freight"}
# 납품 방식(사용자 2026-10-06, 영업팀 기준) — 1 택배: 품목 그대로 / 2 화물 직납: 물류비 추가 / 3 우리 인원이 설치하며 납품: 인건비 추가 /
# 4 화물로 보내고 우리 인원이 설치: 물류비 + 인건비 / 5 별도 협의: 품목 그대로(사용자 2026-10-08). 고르면 품목 줄이 맞춰진다. 납품장소는 방식과 관계없이
# '직납 및 배차 비용 별도 (화물 비용 별도)'(사용자 2026-10-06 — 모든 경우, quote_xlsx.DEFAULT_TERMS).
DELIVERY = {
    1: {"label": "택배 발송", "freight": False, "work": False},
    2: {"label": "화물 직납", "freight": True, "work": False},
    3: {"label": "UND 인원 설치 납품", "freight": False, "work": True},
    4: {"label": "화물 발송 + UND 인원 설치", "freight": True, "work": True},
    5: {"label": "별도 협의", "freight": False, "work": False},
}
DELIVERY_QUESTION = ("제품은 어떻게 납품하나요? 1) 택배로 보냄  2) 화물로 직납  3) 저희 인원이 설치하면서 납품  "
                     "4) 화물로 보내고 저희 인원이 설치하러 감  5) 별도 협의")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(억|천만|백만|만|천)")
_UNIT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000}
_NO_RE = re.compile(r"^\s*(없음|없어|없습니다|없어요|아니|아뇨|필요\s*없|안\s*해|no\b)", re.I)


class QuoteSessionError(ValueError):
    """사람이 읽는 이유."""


def said_numbers(text: str) -> set[float]:
    """말에 적힌 숫자 — '1,380'·'2.5'·'50만원'(→500000)·'1.5억' 모두."""
    out = {float(n.replace(",", "")) for n in _NUM_RE.findall(text or "") if n.replace(",", "").replace(".", "", 1).isdigit()}
    for n, u in _MONEY_RE.findall(text or ""):
        out.add(float(n) * _UNIT[u])
    return out


def _said(v: Any, nums: set[float]) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if any(abs(f - n) < 1e-6 for n in nums) else None


# ── 초안 ──────────────────────────────────────────────────────────────────────

MANUAL_COMMENTS = "www.magbot.kr\n- 제품 공급 건"


def draft_state(accessories: dict[str, Any], *, model: str, payload_kg: float | None, form: dict[str, Any] | None = None,
                manual: bool = False) -> dict[str, Any]:
    """추천 구성품(지금 규칙·단가표로 다시 만든 것) → 견적 초안. 체크하지 않은 옵션(IB)은 options 에 두고 대화로 넣을 수 있다."""
    lines, options = [], []
    for it in accessories.get("items") or []:
        if it.get("qty") == 0 and not it.get("optional"):     # 필요 없음이 확인된 0 만 뺀다 — 미정(None)은 남겨 발행을 막는다
            continue
        k = qx._key(it)
        row = {"kind": "option" if it.get("optional") else "product", "key": k, "name": it["name"],
               "qty": it.get("qty"), "unit_price": it.get("unit_price"), "remark": qx._REMARK["ko"].get(k, ""),
               "price_status": it.get("price_status")}
        if it.get("conditional"):
            row.update(conditional=True, confirmed=False, basis=it.get("basis") or "")
        if it.get("optional") and not it.get("included"):
            options.append(row)
        else:
            lines.append(row)
    for i, ln in enumerate(lines, 1):
        ln["id"] = f"L{i}"
    d = qx.DEFAULT_TERMS["ko"]
    f = {k: "" for k in FORM_FIELDS} | {"lang": "ko", "fx_rate": None, "comments": d["comments"], "delivery": d["delivery"],
                                         "place": d["place"], "payment": d["payment"]} | bank_defaults("ko")
    if manual:                    # 수기 견적(사용자 2026-10-08) — 툴체인저 문구 대신 일반 공급 문구, 건명은 담당자가
        f.update(comments=MANUAL_COMMENTS, subject="")
    for k, v in (form or {}).items():
        if k in FORM_FIELDS and v not in (None, ""):
            f[k] = v
    return {"model": model, "payload_kg": payload_kg, "applicable": bool(accessories.get("applicable")) or manual, "form": f,
            "lines": lines, "options": options, "work": {"needed": None, "people": None, "days": None, "rate": None},
            "delivery": {"case": None, "freight": None}, "asked": [], "next_id": len(lines) + 1, "manual": manual}


def _new_id(state: dict[str, Any]) -> str:
    state["next_id"] = int(state.get("next_id") or len(state["lines"]) + 1) + 1
    return f"L{state['next_id'] - 1}"


def _delivery(state: dict[str, Any]) -> dict[str, Any]:
    return state.setdefault("delivery", {"case": None, "freight": None})


def _sync_work(state: dict[str, Any]) -> None:
    """납품 방식 → 물류비 줄(화물)·'설치 인건비' 줄(인원 × 일수 = 인·일 수량, 1인 1일 단가). 해당 없으면 줄을 뺀다.
    인원·일수·단가 값은 방식을 바꿔도 지우지 않는다(다시 설치로 바꾸면 그대로)."""
    spec = DELIVERY.get(_delivery(state).get("case"))
    w = state["work"]
    if spec:
        w["needed"] = spec["work"]
    state["lines"] = [ln for ln in state["lines"] if ln["kind"] not in ("work", "freight")]
    if spec and spec["freight"]:
        fr = _delivery(state).get("freight")
        state["lines"].append({"id": "F1", "kind": "freight", "key": "freight", "name": FREIGHT_NAME["ko"], "qty": 1,
                               "unit_price": fr, "remark": spec["label"], "price_status": "set" if fr else "none"})
    if w.get("needed") and not w.get("rate"):
        w["rate"] = WORK_RATE
    if w.get("needed") and w.get("people") and w.get("days"):
        state["lines"].append({"id": "W1", "kind": "work", "key": "work", "name": WORK_NAME["ko"],
                               "qty": int(w["people"]) * int(w["days"]), "unit": "인·일", "unit_price": w.get("rate"),
                               "remark": f"{w['people']}명 × {w['days']}일", "price_status": "set" if w.get("rate") else "none"})


# ── 다음 질문 · 발행 가능 여부 ─────────────────────────────────────────────────────

def next_question(state: dict[str, Any]) -> dict[str, Any] | None:
    """AI 가 지금 물을 것 — 순서: 고객사 → 납품 방식(5가지) → 물류비 → 설치 인원·일수 → 1인 1일 단가 → 다른 추가 항목 → 담당자 → 환율.
    choices 가 있으면 화면이 버튼으로 보여 준다(누르면 그 글이 답으로 간다)."""
    f, w, asked = state["form"], state["work"], set(state.get("asked") or [])
    dv = _delivery(state)
    spec = DELIVERY.get(dv.get("case"))
    if not str(f.get("customer") or "").strip():
        return {"id": "customer", "text": "견적서를 받을 고객사와 받는 분(To)을 알려 주세요. 예: 가나테크, 김철수 대표님"}
    if state.get("manual") and not any(ln["kind"] in ("product", "extra", "option") for ln in state["lines"]):
        return {"id": "items", "text": "견적에 넣을 품목을 알려 주세요 — 품명·수량·단가. 예: MG10 그리퍼 2개 150만원. "
                                       "왼쪽 [+ 항목]이나 [단가표에서 추가]로 직접 넣어도 됩니다."}
    if state.get("manual") and not str(f.get("subject") or "").strip():
        return {"id": "subject", "text": "견적서 파일명·건명에 넣을 제품명을 알려 주세요. 예: MG 그리퍼"}
    if not spec:
        return {"id": "delivery", "text": DELIVERY_QUESTION,
                "choices": [f"{k}. {v['label']}" for k, v in DELIVERY.items()]}
    if spec["freight"] and not dv.get("freight"):
        return {"id": "freight", "text": "화물로 보내는 물류비는 얼마로 할까요? 예: 15만원"}
    if spec["work"] and not (w.get("people") and w.get("days")):
        return {"id": "work_detail", "text": "설치하러 가는 인원과 일수를 알려 주세요. 예: 2명 3일"}
    if spec["work"] and not w.get("rate"):
        return {"id": "work_rate", "text": f"설치 인건비 — 1인 1일 단가는 얼마로 할까요? (회사 기준: 1인 8시간 {WORK_RATE:,}원)"}
    if "extra" not in asked:
        return {"id": "extra", "text": "출장비·교육비 등 다른 추가 항목이 있나요? 있으면 항목·금액을, 없으면 '없음'이라고 해 주세요.",
                "choices": ["없음"]}
    if not all(str(f.get(k) or "").strip() for k in ("initials", "contact_name", "contact_mobile", "contact_email")):
        return {"id": "contact", "text": "견적서에 넣을 담당자 정보를 알려 주세요 — 이니셜 2자(견적번호용)·이름·직함·휴대폰·이메일. "
                                         "예: HG, 홍길동 이사, 010-0000-0000, name@example.com"}
    if f.get("lang") == "en" and not f.get("fx_rate"):
        return {"id": "fx", "text": "해외(달러) 견적이라 환율(원/달러)이 필요합니다. 예: 1380"}
    return None


def problems(state: dict[str, Any]) -> list[str]:
    """발행을 막는 이유(C13 — 미정·0원 금지, 필수 머리 값)."""
    out = []
    if not state.get("applicable") and not state.get("manual"):
        out.append("유선 자동 툴체인저(TCC1~TCV4) 구성이 아니라 제품 단가가 없습니다 — 별도 견적 필요")
    for ln in state["lines"]:
        if ln["kind"] in ("work", "freight"):
            continue                                        # 아래에서 납품 방식 기준으로 따로 본다
        if not ln.get("qty") or ln["qty"] < 1:
            out.append(f"{ln['name']} — 수량 미정")
        elif ln.get("conditional") and not ln.get("confirmed"):
            out.append(f"{ln['name']} — 조건부 수량 {ln['qty']}(근거 확인 후 [수량 확인] 또는 수량 수정)")
        elif not ln.get("unit_price") or ln["unit_price"] <= 0:
            out.append(f"{ln['name']} — " + ("단가표 가격 미정" if ln.get("price_status") == "unset" else "단가 없음"))
    if not state["lines"]:
        out.append("품목이 없습니다")
    f = state["form"]
    for k in ("customer", "contact_name", "contact_mobile", "contact_email"):
        if not str(f.get(k) or "").strip():
            out.append(f"{FORM_FIELDS[k][0]} 비어 있음")
    if not re.fullmatch(r"[A-Z][A-Za-z]", str(f.get("initials") or "")):
        out.append("담당자 이니셜(영문 2자, 예: VT) 필요")
    spec = DELIVERY.get(_delivery(state).get("case"))
    if not spec:
        out.append("납품 방식(택배·화물 직납·설치 납품·화물+설치·별도 협의) 선택 필요")
    else:
        if spec["freight"] and not _delivery(state).get("freight"):
            out.append("물류비 금액 필요")
        w = state["work"]
        if spec["work"] and not (w.get("people") and w.get("days") and w.get("rate")):
            out.append("설치 인건비 — 인원·일수·1인 1일 단가 필요")
    if f.get("lang") == "en":
        if not f.get("fx_rate"):
            out.append("해외(달러) 견적 환율 필요")
        if pp.quote_form("en")["missing"]:
            out.append("영문 회사 정보 미등록")
    return out


def subtotal(state: dict[str, Any]) -> int:
    return int(sum((ln.get("qty") or 0) * (ln.get("unit_price") or 0) for ln in state["lines"]))


# ── 변경 적용(대화·직접 수정 공통) ───────────────────────────────────────────────

def apply_ops(state: dict[str, Any], ops: Any, message: str | None) -> tuple[dict[str, Any], list[str]]:
    """변경 목록 → 새 상태와 사람이 읽는 반영 목록. message 가 있으면(대화) 숫자는 그 말에 적힌 것만 받는다."""
    st = copy.deepcopy(state)
    nums = said_numbers(message) if message is not None else None
    done: list[str] = []

    def num(v: Any) -> float | None:
        if nums is None:
            try:
                return float(v) if v is not None and v != "" else None
            except (TypeError, ValueError):
                return None
        return _said(v, nums)

    def set_case(case: int, why: str | None = None) -> None:
        dv = _delivery(st)
        if dv.get("case") == case:
            return
        dv["case"] = case
        done.append(f"납품 방식: {case}. {DELIVERY[case]['label']}" + (f"({why})" if why else ""))

    by_id = {ln["id"]: ln for ln in st["lines"]}
    for op in (ops if isinstance(ops, list) else [])[:20]:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind == "set" and op.get("field") in FORM_FIELDS:
            field, typ = op["field"], FORM_FIELDS[op["field"]][1]
            # 대화에서 우리 쪽 담당자 칸은 담당자를 물을 때나 '담당'이라고 말할 때만 — 고객 쪽 받는 분을 담당자로 넣는 실수 방지
            if (message is not None and (field.startswith("contact_") or field == "initials")
                    and (next_question(state) or {}).get("id") != "contact" and "담당" not in message):
                continue
            v = op.get("value")
            if typ == "number":
                v = num(v)
                if v is None or v <= 0:
                    continue
            elif typ == "lang":
                if v not in ("ko", "en"):
                    continue
            else:
                v = re.sub(r"\s+", " ", str(v or "")).strip()[:200]
                if field == "initials":
                    v = v.upper()[:2]
                if not v:
                    continue
                if (message is not None and field == "account_no"
                        and re.sub(r"\D", "", v) not in re.sub(r"\D", "", message)):
                    continue                                # 말에 없는 계좌번호는 넣지 않는다
            st["form"][field] = v
            if field == "lang":
                st["form"].update({k: qx.DEFAULT_TERMS[v][k] for k in ("comments", "delivery", "place", "payment")})
                st["form"].update(bank_defaults(v))          # 국내↔해외를 바꾸면 그 언어의 회사 기본 계좌로
            done.append(f"{FORM_FIELDS[field][0]}: {v}")
        elif kind == "delivery":
            try:
                case = int(op.get("case"))
            except (TypeError, ValueError):
                case = None
            if case in DELIVERY:
                set_case(case)
            fr = num(op.get("freight"))
            if fr is not None and fr > 0 and DELIVERY.get(_delivery(st).get("case"), {}).get("freight"):
                _delivery(st)["freight"] = fr
                done.append(f"물류비: {fr:,.0f}원")
            _sync_work(st)
            by_id = {ln["id"]: ln for ln in st["lines"]}
        elif kind == "work":
            w = st["work"]
            case = _delivery(st).get("case")
            if op.get("needed") is False and case in (3, 4):          # 설치 빼기 → 3→1, 4→2
                set_case(case - 2, "설치 제외")
            elif op.get("needed") is True and case in (None, 1, 2):     # 설치 넣기 → 1→3, 2→4
                set_case((case or 1) + 2, "설치 포함")
            for k, lab in (("people", "작업 인원"), ("days", "작업 일수"), ("rate", "1인 1일 단가")):
                v = num(op.get(k))
                if v is not None and v > 0:
                    w[k] = int(v) if k != "rate" else v
                    if _delivery(st).get("case") in (None, 1, 2):            # 인원·단가를 말하면 설치가 들어간 방식으로
                        set_case((_delivery(st).get("case") or 1) + 2, "설치 포함")
                    done.append(f"{lab}: {int(v):,}" + ("명" if k == "people" else "일" if k == "days" else "원"))
            _sync_work(st)
            by_id = {ln["id"]: ln for ln in st["lines"]}
        elif kind == "add":
            name = re.sub(r"\s+", " ", str(op.get("name") or "")).strip()[:60]
            price, qty = num(op.get("unit_price")), num(op.get("qty") or 1) if op.get("qty") not in (None, 1) else 1
            if not name or price is None or price <= 0 or not qty or qty < 1:
                continue
            if re.search(r"물류|운송|화물|freight", name, re.I):          # 물류비는 납품 방식과 함께 — 1→2, 3→4
                case = _delivery(st).get("case")
                if case in (None, 1, 3):
                    set_case((case or 1) + 1, "물류비 추가")
                _delivery(st)["freight"] = price * int(qty)
                done.append(f"물류비: {price * int(qty):,.0f}원")
                _sync_work(st)
                by_id = {x["id"]: x for x in st["lines"]}
                continue
            ln = {"id": _new_id(st), "kind": "extra", "key": "extra", "name": name, "qty": int(qty), "unit_price": price,
                  "remark": re.sub(r"\s+", " ", str(op.get("remark") or "")).strip()[:100], "price_status": "set"}
            st["lines"].append(ln)
            by_id[ln["id"]] = ln
            st["asked"] = list({*st.get("asked", []), "extra"})
            done.append(f"추가: {name} {int(qty)} × {price:,.0f}원")
        elif kind == "update" and op.get("line") in by_id:
            ln = by_id[op["line"]]
            if ln["kind"] == "work":
                continue                                    # 인건비는 인원·일수·단가로만 바꾼다
            if ln["kind"] == "freight":
                v = num(op.get("unit_price"))
                if v is not None and v > 0:
                    _delivery(st)["freight"] = v
                    done.append(f"물류비: {v:,.0f}원")
                    _sync_work(st)
                    by_id = {x["id"]: x for x in st["lines"]}
                continue
            for k in ("qty", "unit_price"):
                if op.get(k) is not None:
                    v = num(op[k])
                    if v is not None and v > 0:
                        ln[k] = int(v) if k == "qty" else v
                        ln["price_status"] = "set" if k == "unit_price" else ln.get("price_status")
                        if k == "qty" and ln.get("conditional"):
                            ln["confirmed"] = True              # 담당자가 수량을 정했으면 확인한 것
                        done.append(f"{ln['name']} {'수량' if k == 'qty' else '단가'}: {v:,.0f}")
            if op.get("confirmed") is True and ln.get("conditional") and not ln.get("confirmed") and ln.get("qty"):
                ln["confirmed"] = True
                done.append(f"{ln['name']} 수량 {ln['qty']} 확인")
            for k in ("name", "remark"):
                if isinstance(op.get(k), str) and (ln["kind"] == "extra" or k == "remark"):
                    ln[k] = re.sub(r"\s+", " ", op[k]).strip()[:100 if k == "remark" else 60]
                    done.append(f"{ln['name']} {'비고' if k == 'remark' else '이름'} 수정")
        elif kind == "remove" and op.get("line") in by_id:
            ln = by_id.pop(op["line"])
            st["lines"] = [x for x in st["lines"] if x["id"] != ln["id"]]
            if ln["kind"] == "option":
                st["options"].append({k: v for k, v in ln.items() if k != "id"})
            case = _delivery(st).get("case")
            if ln["kind"] == "work" and case in (3, 4):
                set_case(case - 2, "설치 제외")
            elif ln["kind"] == "freight" and case in (2, 4):
                set_case(case - 1, "물류비 제외")
            if ln["kind"] in ("work", "freight"):
                _sync_work(st)
                by_id = {x["id"]: x for x in st["lines"]}
            done.append(f"삭제: {ln['name']}")
        elif kind == "option":
            name = str(op.get("name") or "")
            if op.get("include") is True:
                opt = next((o for o in st["options"] if name and (name.lower() in o["name"].lower() or name.upper() == o["key"])), None)
                if opt:
                    st["options"].remove(opt)
                    ln = {**opt, "id": _new_id(st)}
                    st["lines"].append(ln)
                    by_id[ln["id"]] = ln
                    done.append(f"옵션 포함: {opt['name']}")
            elif op.get("include") is False:
                ln = next((x for x in st["lines"] if x["kind"] == "option" and name and (name.lower() in x["name"].lower() or name.upper() == x["key"])), None)
                if ln:
                    st["lines"].remove(ln)
                    st["options"].append({k: v for k, v in ln.items() if k != "id"})
                    done.append(f"옵션 제외: {ln['name']}")
        elif kind == "no_extra":
            st["asked"] = list({*st.get("asked", []), "extra"})
            done.append("다른 추가 항목 없음")
    return st, done


def edit_ops(state: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """화면에서 직접 고친 내용(머리·품목·작업) → 변경 목록. 바뀐 것만, 숫자 대조는 하지 않는다(사람이 직접 입력)."""
    ops: list[dict[str, Any]] = []
    for k, v in (payload.get("form") or {}).items():
        if k in FORM_FIELDS and v != state["form"].get(k) and v not in (None, ""):
            ops.append({"op": "set", "field": k, "value": v})
    dv = payload.get("delivery")
    if isinstance(dv, dict) and (dv.get("case") != _delivery(state).get("case") or dv.get("freight") != _delivery(state).get("freight")):
        ops.append({"op": "delivery", "case": dv.get("case"), "freight": dv.get("freight")})
    w = payload.get("work")
    if isinstance(w, dict) and any(w.get(k) != state["work"].get(k) for k in ("people", "days", "rate")):
        ops.append({"op": "work", **{k: w.get(k) for k in ("people", "days", "rate")}})
    if isinstance(payload.get("lines"), list):
        cur = {ln["id"]: ln for ln in state["lines"] if ln["kind"] not in ("work", "freight")}
        seen = set()
        for x in payload["lines"]:
            if not isinstance(x, dict):
                continue
            if x.get("id") in cur:
                seen.add(x["id"])
                ln = cur[x["id"]]
                ch = {k: x[k] for k in ("qty", "unit_price", "name", "remark") if k in x and x[k] != ln.get(k)}
                if x.get("confirmed") is True and ln.get("conditional") and not ln.get("confirmed"):
                    ch["confirmed"] = True
                if ch:
                    ops.append({"op": "update", "line": x["id"], **ch})
            elif not x.get("id"):
                ops.append({"op": "add", **{k: x.get(k) for k in ("name", "qty", "unit_price", "remark")}})
        ops += [{"op": "remove", "line": i} for i in cur if i not in seen]
    for name in payload.get("include_options") or []:
        ops.append({"op": "option", "name": name, "include": True})
    return ops


def delivery_case(text: str) -> int | None:
    """'1'·'2번'·'1. 택배 발송'(버튼 글)·'택배로'·'화물 직납'·'설치하면서 납품'·'화물로 보내고 설치'·'별도 협의' → 1~5. 모호하면 None."""
    t = text.strip()
    m = re.match(r"^\s*([1-5])\s*(번|\.|\)|$)", t)
    if m:
        return int(m.group(1))
    def said(word: str) -> bool:                      # 말했고 부정하지 않음('화물은 안 써요'·'설치 없이'·'택배 말고' 제외)
        return bool(re.search(word, t)) and not re.search(rf"({word})\S{{0,3}}\s*(안\s|안해|안\s*써|없|말고|아니|빼|제외)", t)

    if re.search(r"별도\s*협의|추후\s*협의|협의\s*후", t):
        return 5
    freight, install, courier = said(r"화물|직납"), said(r"설치|인원"), said(r"택배")
    if freight and install:
        return 4
    if freight:
        return 2
    if install and not courier:
        return 3
    if courier and not install:
        return 1
    return None


def quick_ops(state: dict[str, Any], message: str) -> list[dict[str, Any]] | None:
    """지금 질문에 대한 확실한 꼴의 답은 코드로 읽는다(AI 없이) — 납품 방식 번호·말, '2명 3일', '50만원', '없음'."""
    q = next_question(state)
    if not q:
        return None
    m = message.strip()
    if q["id"] == "delivery":
        case = delivery_case(m)
        if case:
            p, d = re.search(r"(\d+)\s*명", m), re.search(r"(\d+)\s*일", m)
            ops = [{"op": "delivery", "case": case}]
            if p or d:
                ops.append({"op": "work", "people": p and float(p.group(1)), "days": d and float(d.group(1))})
            return ops
    if q["id"] == "freight":
        n = [x for x in said_numbers(m) if x >= 1000]
        if len(n) == 1:
            return [{"op": "delivery", "freight": n[0]}]
    if q["id"] == "work_detail":
        p, d = re.search(r"(\d+)\s*명", m), re.search(r"(\d+)\s*일", m)
        if p or d:
            return [{"op": "work", "people": p and float(p.group(1)), "days": d and float(d.group(1))}]
    if q["id"] == "work_rate":
        n = sorted(said_numbers(m))
        if n and len({x for x in n if x >= 1000}) == 1:
            return [{"op": "work", "rate": max(n)}]
    if q["id"] == "extra" and _NO_RE.match(m):
        return [{"op": "no_extra"}]
    if q["id"] == "fx":
        n = [x for x in said_numbers(m) if 100 <= x <= 100000]
        if len(n) == 1:
            return [{"op": "set", "field": "fx_rate", "value": n[0]}]
    return None


# ── AI 에게 줄 상황 ──────────────────────────────────────────────────────────────

QUOTE_SYSTEM = """너는 유엔디로보틱스 견적 담당이다. [견적 상태]와 [지금 묻는 질문]을 보고 사용자의 마지막 [말]을 견적 변경(ops)으로 옮긴다.
JSON 으로만 답한다. [말]에 분명히 있는 내용만 옮기고 지어내지 않는다. 숫자는 [말]에 적힌 그대로(원 단위; '50만원'은 500000).
ops 종류:
- {"op":"set","field":<customer|to|cc|subject|initials|contact_name|contact_title|contact_mobile|contact_email|delivery|place|payment|comments|note|lang|fx_rate|bank|account_no|account_holder>,"value":...}
  (lang 은 "ko" 또는 "en". contact_* 는 견적을 '보내는' 우리 회사(유엔디로보틱스) 담당자다 — '담당자는 …'처럼 분명할 때만.
   견적을 '받는' 고객사 쪽 사람('○○ 대표님께', '○○님 앞으로')은 to, 회사 이름은 customer.
   '홍길동 이사'처럼 이름과 직함이 같이 오면 contact_name 과 contact_title 로 나눈다.
   bank·account_no·account_holder 는 고객사가 우리에게 돈을 보낼 '입금 계좌'(견적서의 Bank·Account No. 칸)다 — 결제조건(payment)과 다르다.
   '계좌 바꿔 줘'·'입금 계좌는 신한은행 …' 이면 이 칸들을 바꾼다. 계좌번호는 [말]에 적힌 그대로. 새 계좌번호를 말하지 않았으면 ops 를 비우고
   answer 로 새 은행·계좌번호·예금주를 물어본다. 계좌 변경을 거절하거나 내부 정책·담당 부서 확인 같은 말을 지어내지 않는다.)
- {"op":"delivery","case":1|2|3|4|5,"freight":물류비}  — 납품 방식: 1 택배 발송(품목 그대로), 2 화물 직납(물류비 추가),
  3 우리 인원이 설치하며 납품(설치 인건비 추가), 4 화물로 보내고 우리 인원이 설치(물류비+인건비), 5 별도 협의(품목 그대로). freight 는 물류비 금액(말에 있을 때만).
- {"op":"work","people":명,"days":일,"rate":1인1일단가}  — 설치 인건비(인원·일수·단가). 설치를 빼라고 하면 {"op":"work","needed":false}
- {"op":"add","name":"물류비","qty":1,"unit_price":100000,"remark":""}  — 새 항목(수기 견적의 제품 품목도 이것으로 — 품명·수량·단가가 [말]에 있을 때만)
- {"op":"update","line":"L3","qty":3}  — 품목 수량·단가·이름·비고 바꾸기(line 은 [견적 상태]의 줄 id)
- {"op":"update","line":"L3","confirmed":true}  — 조건부 수량을 담당자가 '맞다'고 확인할 때만
- {"op":"remove","line":"L5"}  — 품목 빼기
- {"op":"option","name":"IB","include":true|false}  — [넣을 수 있는 옵션]을 넣거나 빼기
- {"op":"no_extra"}  — 다른 추가 항목이 없다고 할 때
질문·잡담이면 ops 를 비우고 answer 에 2~3문장 존댓말로 답한다([견적 상태]에 있는 내용만, 수치를 지어내지 않음).
answer 는 줄바꿈(\\n)·목록·**강조**를 쓸 수 있다 — 한 문단 1~2문장, 항목은 한 줄에 하나, 핵심 단어 2~5곳만 **…**.
{"ops": [], "answer": ""}"""


def describe(state: dict[str, Any]) -> str:
    f = state["form"]
    rows = "\n".join(f"- {ln['id']} [{ln['kind']}] {ln['name']} · 수량 {ln.get('qty')} · 단가 {ln.get('unit_price')}원"
                     + (f" · 비고 {ln['remark']}" if ln.get("remark") else "") for ln in state["lines"])
    opts = ", ".join(f"{o['name']}({o.get('unit_price')}원)" for o in state["options"]) or "(없음)"
    head = ", ".join(f"{FORM_FIELDS[k][0]}={f.get(k)}" for k in FORM_FIELDS if f.get(k) not in (None, ""))
    w, dv = state["work"], _delivery(state)
    case = DELIVERY.get(dv.get("case"))
    return (f"선정 모델: {state['model'] or '(수기 견적 — 품목을 담당자가 직접 넣음)'}\n머리: {head}\n품목:\n{rows or '(없음)'}\n넣을 수 있는 옵션: {opts}\n"
            f"납품 방식: {(str(dv['case']) + '. ' + case['label']) if case else '(아직 안 정함)'}, 물류비={dv.get('freight')}\n"
            f"설치 인건비: 인원={w.get('people')}, 일수={w.get('days')}, 1인1일단가={w.get('rate')}\n"
            f"합계(원화): {subtotal(state):,}원")


# ── 발행용 줄(엑셀) ───────────────────────────────────────────────────────────────

def render_lines(state: dict[str, Any], lang: str, fx_rate: float | None) -> list[dict[str, Any]]:
    """견적 상태 → quote_xlsx 줄(언어별 문구, 해외는 달러 환산). 본체·액세서리 → 옵션 → 물류비 → 설치 인건비 → 추가 항목 순."""
    pay = f" (Payload {state['payload_kg']:g}Kg)" if state.get("payload_kg") is not None else ""
    order = {"product": 0, "option": 1, "freight": 2, "work": 3, "extra": 4}
    out = []
    for ln in sorted(state["lines"], key=lambda x: order.get(x["kind"], 9)):
        k = ln["key"]
        if k == "MASTER T.C":
            desc = f"{state['model']}\n- MASTER T.C{pay}"
        elif k == "T.P":
            desc = f"{state['model']}\n- Tool side T.C{pay}"
        elif ln["kind"] == "work":
            desc = WORK_NAME[lang]
        elif ln["kind"] == "freight":
            desc = FREIGHT_NAME[lang]
        elif k in qx._DESC:
            desc = qx._DESC[k]
        else:
            desc = ln["name"]
        if ln["kind"] == "work":
            p, d = state["work"]["people"], state["work"]["days"]
            std = state["work"].get("rate") == WORK_RATE            # 회사 기준 단가면 기준을 비고에
            remark = (f"{p} persons × {d} days" + (" (8h/day per person)" if std else "")) if lang == "en" \
                else f"{p}명 × {d}일" + (" (1인 8H 기준)" if std else "")
        elif ln["kind"] == "freight":
            remark = "Freight transport" if lang == "en" else ln.get("remark") or ""
        elif ln["kind"] in ("product", "option") and ln.get("remark") == qx._REMARK["ko"].get(k, ""):
            remark = qx._REMARK[lang].get(k, "")             # 기본 비고는 언어에 맞게, 사용자가 고친 비고는 그대로
        else:
            remark = ln.get("remark") or ""
        krw = float(ln["unit_price"])
        unit = round(krw / fx_rate, 2) if lang == "en" else krw
        out.append({"key": k, "desc": desc, "qty": int(ln["qty"]), "unit_price": unit, "unit_price_krw": krw,
                    "amount": round(int(ln["qty"]) * unit, 2), "remark": remark, "option": ln["kind"] == "option",
                    **({"unit": "man-day" if lang == "en" else "인·일"} if ln["kind"] == "work" else {})})
    return out
