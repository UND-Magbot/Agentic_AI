# -*- coding: utf-8 -*-
"""구매 건 기록 → 진행 단계·현재 상태·알림(사람이 고르는 칸이 아니라 기록에서 계산 — sales_deals/status.py 와 같은 방식).

단계(사용자 2026-11-02 → 2026-11-11 개편):
- 견적: 발주할 최종 견적서 파일을 올리면 끝. 고쳐 받은 견적서로 언제든 교체할 수 있다(예전 파일은 이전 견적서로 남김).
- 재가: 대표님 재가 서류를 첨부하고 [재가 완료]. 첨부가 있어야 누를 수 있다.
- 발주: 회사 발주서 양식으로 작성·발행(PO-LU20261117-01, 2026-11-17 전에 나간 것은 UND-LU20261102-01). 엑셀에서 고친 발주서 파일로도 발행(번호는 파일의 Order No.).
  국내는 입고 예정일을 꼭 적어야 다음 단계로 넘어간다 — 리스트·상세에 보이고, 지나면 알림.
  해외는 같은 칸을 '납기일'로 적을 수 있지만 선택(사용자 2026-11-11 — 안 적어도 넘어감). 적으면 지남 알림은 같다.
- 발주 뒤 단계는 정석 순서만 안내하고 순서와 상관없이 기록한다(입고가 송금보다 먼저 올 수도 있음).
  · 국내: 송금(% 회차마다 [송금 확인]) → 배송([배송 시작] → 배송 중) → 입고.
    거래명세서·세금계산서는 '선택' — 받으면 첨부, 선결제·선발행으로 못 끊는 건은 그 사유를 기록. 완료 조건에 넣지 않는다.
  · 해외: 제조사 송금([제조사 송금 확인], 송금 확인증 첨부는 선택) → 배송([배송 시작]) → 통관 서류(원산지 증명서 1순위,
    없으면 [증빙 자료 없음] 뒤 CI·Packing List 등 기타 서류 여러 개, 첨부 없이도 [통관 서류 제출 완료]) →
    관세사 송금([관세사 송금 확인]) → 입고. 거래명세서·세금계산서 없음.
  · 배송: 공급사 연락(전화 등)으로 배송이 시작된 걸 알면 [배송 시작] — 상태가 '배송 중'. 입고 기록이 있으면 배송은 끝난 것으로 본다.
  · 입고: 출하 기록처럼 입고 기록(날짜·운송·받은 사람·보관 위치·품목별 수량·사진·S/N)을 남긴다. 부분 입고는 여러 번,
    품목을 다 받거나 '입고 완료'로 남기면 끝. S/N 은 입고 기록을 쓸 때, 또 기록을 남긴 뒤에도 따로 등록할 수 있다.
- 완료(권장안 2026-11-11): 꼭 할 단계(국내 송금·배송·입고, 해외 제조사 송금·배송·통관 서류·관세사 송금·입고)가 모두 끝나면.
  입고만으로 완료하지 않는다 — 잔금처럼 입고 뒤에 송금하는 건이 '완료'로 묻히지 않게.
금액은 발주서 공급가액(VAT 별도) 기준. 국내 발주서를 'VAT 포함'으로 냈으면(사용자 2026-11-20) 공급가액 = 합계 ÷ 1.1,
송금 회차의 부가세 포함 금액은 발주서 합계를 그대로 나눈다(인쇄된 금액과 원 단위까지 맞게).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .. import money
from ..sales_deals.flow import clean_terms  # noqa: F401 — 결제 조건 검사는 판매와 같다(구분·% 합 100·예정일)
from .po_xlsx import item_name

VAT_RATE = 0.1
PAY_TOLERANCE = 1_000          # 송금 수수료 등 원 단위 차이 — 판매(flow.PAY_TOLERANCE)와 같게

# 단계 이름 — 해외는 송금이 '제조사 송금'(관세사 송금과 구분)
STEP_LABELS = {"quote": "견적", "approval": "재가", "order": "발주", "pay": "송금", "ship": "배송", "customs": "통관 서류",
               "broker": "관세사 송금", "receipt": "입고", "statement": "거래명세서", "tax": "세금계산서"}
OVERSEAS_LABELS = {**STEP_LABELS, "pay": "제조사 송금"}
STEP_ORDER = ("quote", "approval", "order", "pay", "ship", "receipt", "statement", "tax")            # 국내(명세서·계산서는 선택)
OVERSEAS_ORDER = ("quote", "approval", "order", "pay", "ship", "customs", "broker", "receipt")      # 해외 — 입고에서 끝
OPTIONAL = ("statement", "tax")                                                                    # 완료 조건에 안 넣음
STAGES = ["견적 대기", "재가 대기", "발주 대기", "송금 대기", "제조사 송금 대기", "배송 대기", "배송 중", "통관 서류 대기",
          "관세사 송금 대기", "입고 대기", "완료", "취소"]
# 거래명세서·세금계산서를 못 끊는 경우(사용자 2026-11-11) — 첨부 없이 사유만 기록. prepaid_balance 는 예전 기록(선결제 잔액)
DOC_SKIP_MODES = {"prepaid": "선결제", "pre_issued": "선발행"}
DOC_MODE_LABELS = {**DOC_SKIP_MODES, "prepaid_balance": "선결제 잔액"}


def expected_date(d: dict) -> str | None:
    """입고 예정일(YYYY-MM-DD) — 발주 단계에서 꼭 적는다(사용자 2026-11-02). 리스트·상세에 보인다."""
    return (d.get("po") or {}).get("expected_date") or None


def vat_included(d: dict) -> bool:
    """발행한 국내 발주서를 'VAT 포함' 금액으로 냈는지(사용자 2026-11-20). 해외는 늘 아니다."""
    return bool(((d.get("po") or {}).get("header") or {}).get("vat_included")) and not overseas(d)


def po_total(d: dict) -> float | None:
    """발행한 발주서의 공급가액 합계(VAT 별도). 발주 전이면 None. VAT 포함 발주서는 합계 ÷ 1.1."""
    po = d.get("po") or {}
    if not (po.get("no") and po.get("total") is not None):
        return None
    total = float(po["total"])
    return float(money.supply_of(total)) if vat_included(d) else total


def with_vat(v: float) -> float:
    """공급가액 → 부가세 포함(원 미만 절삭, money.py — 사용자 2026-11-20)"""
    return float(money.with_vat(v))


def _check_view(c: dict | None) -> dict | None:
    if not c:
        return None
    f = c.get("file") or {}
    return {**{k: v for k, v in c.items() if k != "file"}, "filename": f.get("filename"), "uploaded_by": f.get("uploaded_by")}


def terms(d: dict) -> list[dict[str, Any]]:
    """결제 회차 + 금액(공급가액 × %, 마지막 회차가 끝전)·부가세 포함 금액·확인 여부."""
    raw = d.get("pay_terms") or []
    total = po_total(d) or 0
    gross = float((d.get("po") or {}).get("total") or 0) if vat_included(d) else None   # VAT 포함 발주서 — 인쇄된 합계
    pays = d.get("payments") or []
    out, used, used_vat = [], 0, 0
    for i, t in enumerate(raw):
        last = i == len(raw) - 1
        # 원화는 원 미만 절삭(사용자 2026-11-20), 마지막 회차가 끝전. 달러는 예전 그대로
        cut = round if overseas(d) else money.won
        amt = cut(total * (t.get("pct") or 0) / 100) if not last else cut(total - used)
        used += amt
        amt_vat = with_vat(amt)
        if gross is not None:                          # 부가세 포함 금액은 발주서 합계를 나눠 인쇄된 금액과 맞춘다
            amt_vat = float(money.won(gross * (t.get("pct") or 0) / 100) if not last else money.won(gross - used_vat))
            used_vat += amt_vat
        got = [p for p in pays if p.get("term") == i]
        last = got[-1] if got else None
        out.append({"index": i, "label": t.get("label"), "pct": t.get("pct"), "when": t.get("when") or "",
                    "expected_date": t.get("expected_date"), "amount": amt, "amount_vat": amt_vat,
                    "confirmed": bool(got), "paid_date": last.get("date") if last else None,
                    "mismatch": bool(last and last.get("mismatch")), "mismatch_note": (last or {}).get("mismatch_note"),
                    # 지출내역 대조 결과는 회차(pay_terms[i].check)에 둔다 — 저장소 키는 화면에 내보내지 않는다
                    "check": _check_view(t.get("check"))})
    return out


def overdue(d: dict, today: dt.date) -> list[dict[str, Any]]:
    out = []
    for t in terms(d):
        due = t.get("expected_date")
        if due and not t["confirmed"] and due < today.isoformat():
            out.append({**t, "days": (today - dt.date.fromisoformat(due)).days})
    return out


RECEIPT_PURPOSES = ("sale", "dev")                  # 판매용 입고 · 보유 후 자체 개발용 입고(사용자 2026-11-18)


def purpose_of(r: dict) -> str:
    """입고 기록의 목적 — 예전 기록(구분 없음)은 판매용으로 본다(사용자 2026-11-18)."""
    return r.get("purpose") if r.get("purpose") in RECEIPT_PURPOSES else "sale"


def merge_receipts(recs: list[dict]) -> list[dict]:
    """입고 기록은 목적(판매용·자체 개발용)마다 한 건(사용자 2026-11-18). 목적별로 _merge_one 으로 합친다."""
    out = []
    for purpose in RECEIPT_PURPOSES:
        group = [r for r in recs if purpose_of(r) == purpose]
        out += [{**r, "purpose": purpose} for r in _merge_one(group)]
    return out


def parts_of(ln: dict) -> list[dict]:
    """품목 줄의 나눈 줄(사용자 2026-11-19 — [같은 품목 줄 나누기]: 같은 품목을 따로 관리할 몇 줄로. 판매용·자체 개발용 같게).
    [{qty, customer, owner, sw_owner, note, photos}] — 나누지 않았으면 줄 전체가 한 줄. 재고 목록은 이 나눈 줄 하나 = 목록 한 줄.
    예전 꼴 buyers(대마다 하나씩)는 1대짜리 나눈 줄로 읽는다."""
    qty = float(ln.get("qty") or 0)
    base = {"customer": ln.get("customer") or "", "owner": ln.get("owner") or "", "sw_owner": ln.get("sw_owner") or "",
            "note": ln.get("note") or "", "photos": []}
    parts = [x for x in ln.get("parts") or [] if isinstance(x, dict)]
    if not parts and ln.get("buyers"):
        parts = [{**x, "qty": 1} for x in ln["buyers"] if isinstance(x, dict)]
    if not parts:
        return [{**base, "qty": qty}]
    return [{**base, **{k: v for k, v in x.items() if k in base}, "qty": float(x.get("qty") or 0)} for x in parts]


def units_of_parts(ln: dict) -> list[tuple[int, dict]]:
    """나눈 줄을 한 대씩 펼침 — [(나눈 줄 번호, 그 줄)] 길이 = 나눈 줄 수량(올림)의 합."""
    out: list[tuple[int, dict]] = []
    for i, pt in enumerate(parts_of(ln)):
        out += [(i, pt)] * max(math.ceil(pt["qty"] - 1e-9), 0)
    return out


def _merge_one(recs: list[dict]) -> list[dict]:
    """입고 기록은 한 건(사용자 2026-11-17 — 나눠 들어오는 일이 거의 없어 1차·2차를 없앰). 예전에 나눠 남긴 기록은 하나로 합친다:
    품목 줄별 수량은 더하고 사진·S/N 은 이어 붙이며, 날짜·운송·받은 사람·보관 위치는 가장 최근 기록, 메모는 이어 붙인다.
    [입고 완료]는 하나라도 있으면 완료 — 닫은 범위(closed)는 줄마다 가장 많이 닫은 것(예전 꼴 closed 없는 완료가 있으면 없음 = 전부 닫힘).
    처음 남긴 사람·때(by·at)는 첫 기록 것 — 화면이 고치기·지우기를 보낼 때 같은 기록인지 보는 값(at)이 바뀌지 않게."""
    if len(recs) <= 1:
        return recs
    first, last = recs[0], recs[-1]
    lines: dict[Any, dict] = {}
    for r in recs:
        for n, ln in enumerate(r.get("lines") or []):
            # 자유 형식 줄(발주 품목과 연결 없음)은 이름으로 묶는다
            k = ln.get("line_no") if ln.get("line_no") is not None else ("free", str(ln.get("name") or n))
            cur = lines.get(k)
            if cur is None:
                lines[k] = {**ln, "qty": float(ln.get("qty") or 0), "photos": list(ln.get("photos") or []),
                            "serials": list(ln.get("serials") or []), "units": list(ln.get("units") or [])}
                continue
            cur["photos"] += [x for x in ln.get("photos") or [] if x not in cur["photos"]]
            cur["serials"] += [x for x in ln.get("serials") or [] if x not in cur["serials"]]
            # 판매용 — 나눈 줄이 있거나 고객사가 다르면 나눈 줄(parts)로 이어 붙인다(앞 기록 고객사·사진을 잃지 않게)
            if "customer" in ln or "customer" in cur or cur.get("parts") or ln.get("parts"):
                a, b = parts_of(cur), parts_of(ln)
                differ = len({(x["customer"], x["owner"], x["sw_owner"], x["note"]) for x in a + b}) > 1
                if cur.get("parts") or ln.get("parts") or cur.get("buyers") or ln.get("buyers") or differ:
                    cur["parts"] = a + b
                    cur.pop("buyers", None)
            cur["qty"] = float(cur.get("qty") or 0) + float(ln.get("qty") or 0)
            cur["units"] += list(ln.get("units") or [])
            cur["name"] = ln.get("name") or cur.get("name")
    pick = lambda key: next((r.get(key) for r in reversed(recs) if r.get(key)), last.get(key))   # noqa: E731
    out = {**last, "date": max(str(r.get("date") or "") for r in recs) or last.get("date"),
           "carrier": pick("carrier") or "", "receiver": pick("receiver") or "", "location": pick("location") or "",
           "tracking": [t for r in recs for t in (r.get("tracking") or [])][:10],
           "note": " / ".join(str(r.get("note") or "").strip() for r in recs if str(r.get("note") or "").strip())[:500],
           "lines": list(lines.values()),
           "by": first.get("by"), "at": first.get("at"), "merged": len(recs)}
    out.pop("closed", None)
    finals = [r for r in recs if r.get("final")]
    out["final"] = bool(finals)
    if finals and all("closed" in r for r in finals):
        closed: dict[str, Any] = {}
        for r in finals:
            for k, v in (r.get("closed") or {}).items():
                q = float((v.get("qty") if isinstance(v, dict) else v) or 0)
                old = closed.get(k)
                oq = float((old.get("qty") if isinstance(old, dict) else old) or 0) if old is not None else -1
                if q >= oq:
                    closed[k] = v
        out["closed"] = closed
    return [out]


def received(d: dict) -> dict[int, float]:
    """발주 품목 줄(line_no)별 지금까지 입고한 수량 — 입고 기록(receipt.records)의 합."""
    out: dict[int, float] = {}
    for r in (d.get("receipt") or {}).get("records") or []:
        for ln in r.get("lines") or []:
            if ln.get("line_no") is not None and ln.get("qty"):
                out[ln["line_no"]] = out.get(ln["line_no"], 0) + float(ln["qty"])
    return out


def receipt_open(d: dict) -> list[int]:
    """아직 입고가 남은 발주 품목 줄(line_no) — 발주 수량만큼 받지 않았고, [입고 완료]로 닫지도 않은 줄.
    [입고 완료]는 누른 때의 발주 수량까지만 닫는다(closed, 사용자 2026-11-16) — 뒤에 품목을 더하거나 수량을 늘리면 늘어난 만큼 다시 열린다.
    closed 가 없는 예전 [입고 완료] 기록은 전부 닫은 것으로 본다.
    닫은 줄은 품목 이름도 기억한다(QA 2026-11-16) — 다시 발행해 그 자리에 다른 품목이 들어오면(B → C) 닫힌 줄이 아니라 새로 열린 줄.
    이름 없이 수량만 남긴 기록(같은 날 먼저 저장한 꼴 {줄: 수량})은 이름 검사 없이 수량만 본다."""
    recs = (d.get("receipt") or {}).get("records") or []
    finals = [r for r in recs if r.get("final")]
    if any("closed" not in r for r in finals):
        return []
    closed: dict[int, list[tuple[float, str | None]]] = {}
    for r in finals:
        for k, v in (r.get("closed") or {}).items():
            q, name = (v.get("qty"), v.get("name")) if isinstance(v, dict) else (v, None)
            closed.setdefault(int(k), []).append((float(q or 0), name))
    items = (d.get("po") or {}).get("items") or []
    got = received(d)

    def shut(i: int, it: dict) -> bool:
        want = float(it.get("qty") or 0)
        return any(want <= q + 1e-9 and (name is None or _name_key(name) == _name_key(item_name(it))) for q, name in closed.get(i, []))

    return [i for i, it in enumerate(items) if got.get(i, 0) < float(it.get("qty") or 0) and not shut(i, it)]


def _name_key(v: Any) -> str:
    """품목 이름 비교 — 대소문자·띄어쓰기 차이는 같은 이름(재발행 줄 지키기와 같은 기준)."""
    return " ".join(str(v or "").split()).upper()


def receipt_done(d: dict) -> bool:
    """입고 완료 — 발주 품목 수량을 모두 받았거나, [입고 완료]로 닫았을 때(닫은 뒤 늘어난 품목·수량은 다시 입고 대기 — receipt_open)."""
    recs = (d.get("receipt") or {}).get("records") or []
    if not recs:
        return False
    items = (d.get("po") or {}).get("items") or []
    if not items and not any(r.get("final") for r in recs):
        return False
    return not receipt_open(d)


def quote_done(d: dict) -> bool:
    """견적 단계 끝 — 견적서 파일이 있거나, 없이 넘어가기를 눌렀다(approval.quote_skip, 표 구조는 그대로)."""
    return bool(d.get("quote_files")) or bool((d.get("approval") or {}).get("quote_skip"))


def overseas(d: dict) -> bool:
    return d.get("region") == "overseas"


def step_order(d: dict) -> tuple[str, ...]:
    return OVERSEAS_ORDER if overseas(d) else STEP_ORDER


def labels(d: dict) -> dict[str, str]:
    return OVERSEAS_LABELS if overseas(d) else STEP_LABELS


def shipping(d: dict) -> dict:
    """배송 시작 기록 — receipt.shipping(입고와 같은 칸에 둔다, 표 구조는 그대로)."""
    return (d.get("receipt") or {}).get("shipping") or {}


def in_transit(d: dict) -> bool:
    """배송 중 — [배송 시작]을 눌렀고 아직 입고 기록이 없다(첫 입고가 들어오면 '입고 대기'·완료로)."""
    return bool(shipping(d).get("date")) and not ((d.get("receipt") or {}).get("records"))


def doc_state(d: dict, key: str) -> dict[str, Any]:
    """거래명세서·세금계산서 상태 — file(첨부함) · skip(선결제·선발행 등 사유 기록) · none. 리스트 '서류' 칸·상세에 쓴다."""
    v = d.get("statement" if key == "statement" else "tax_invoice") or {}
    files = v.get("files") or []
    mode = v.get("mode")
    if files:
        return {"state": "file", "label": "첨부", "files": len(files)}
    if mode in DOC_MODE_LABELS:
        return {"state": "skip", "label": DOC_MODE_LABELS[mode], "mode": mode, "note": v.get("note") or ""}
    if v.get("done_at"):                                   # 예전 기록: 첨부 없이 [확인]
        return {"state": "skip", "label": "첨부 없이 확인", "mode": "normal", "note": ""}
    return {"state": "none", "label": "미등록"}


def _done(d: dict) -> dict[str, bool]:
    po = d.get("po") or {}
    pays = d.get("payments") or []
    cu = d.get("customs") or {}
    # 송금 — 국내는 % 회차를 모두 [송금 확인], 해외는 [제조사 송금 확인] 한 번. 예전 '한 번에 확인' 기록(simple)도 끝난 것으로
    ts = terms(d)
    pay = any(p.get("simple") for p in pays) or (not overseas(d) and bool(ts) and all(t["confirmed"] for t in ts))
    # 견적 — 견적서를 올렸거나 [견적서 없이 넘어가기](사용자 2026-11-11: 첨부 없이도 다음 단계로 — 영업팀 테스트·급한 건)
    return {"quote": quote_done(d),
            "approval": bool((d.get("approval") or {}).get("done_at")),
            # 발주는 발주서 발행 + 입고 예정일이 있어야 끝(국내 — 사용자 2026-11-02). 해외 납기일은 선택(2026-11-11)
            "order": bool(po.get("no")) and (bool(po.get("expected_date")) or overseas(d)),
            "pay": pay,
            # 배송 — [배송 시작]을 눌렀거나, 연락을 못 받았어도 입고 기록이 있으면 끝
            "ship": bool(shipping(d).get("date")) or bool((d.get("receipt") or {}).get("records")),
            "customs": bool(cu.get("done_at")),
            "broker": bool((cu.get("broker") or {}).get("date")),
            "receipt": receipt_done(d),
            "statement": doc_state(d, "statement")["state"] != "none",
            "tax": doc_state(d, "tax")["state"] != "none"}


def done_map(d: dict) -> dict[str, bool]:
    """단계별 끝남 여부(service 에서 중복 기록 막기에 씀)."""
    return _done(d)


def required(d: dict) -> tuple[str, ...]:
    """완료에 꼭 필요한 단계(명세서·계산서는 선택)."""
    return tuple(k for k in step_order(d) if k not in OPTIONAL)


def steps(d: dict) -> list[dict[str, Any]]:
    """[{key, label, done, ready, note, optional}] — ready = 지금 기록할 수 있음. 견적·재가·발주는 앞 단계가 끝나야,
    그 뒤는 발주만 끝나면(순서 자유)."""
    done = _done(d)
    lab = labels(d)
    ordered = done["order"]
    ready = {"quote": True, "approval": done["quote"], "order": done["approval"]}
    notes: dict[str, str | None] = {k: (doc_state(d, k)["label"] if done[k] else None) for k in OPTIONAL}
    if d.get("pay_terms") and not overseas(d):
        ts = terms(d)
        notes["pay"] = f"{sum(t['confirmed'] for t in ts)}/{len(ts)}회차"
    if (d.get("po") or {}).get("no") and not ordered:              # 국내만(해외 납기일은 선택)
        notes["order"] = "입고 예정일 필요"
    if in_transit(d):
        notes["ship"] = "배송 중"
    recs = (d.get("receipt") or {}).get("records") or []
    if recs and not done["receipt"]:
        notes["receipt"] = "부분 입고"
    return [{"key": k, "label": lab[k], "done": done[k], "ready": ready.get(k, ordered) and not d.get("dropped"),
             "note": notes.get(k), "optional": k in OPTIONAL} for k in step_order(d)]


def stage(d: dict) -> str:
    """현재 상태 — 물건이 오는 중이면 '배송 중', 아니면 꼭 할 단계 중 아직 안 끝난 첫 단계('송금 대기' …), 다 끝나면 완료.
    발주 뒤 단계는 순서와 상관없이 기록되므로 다른 남은 일은 stage_note 로 따로 보인다."""
    if d.get("dropped"):
        return "취소"
    done = _done(d)
    if done["order"] and in_transit(d):
        return "배송 중"
    nxt = next((k for k in required(d) if not done[k]), None)
    return f"{labels(d)[nxt]} 대기" if nxt else "완료"


def stage_note(d: dict) -> str:
    """상태 칸 아래 작은 글 — 발주 뒤 꼭 할 일 중 상태 이름에 안 드러난 남은 일(예: 배송 중인데 잔금 송금 남음)."""
    done = _done(d)
    if d.get("dropped") or not done["order"]:
        return ""
    lab, st = labels(d), stage(d)
    left = [lab[k] for k in required(d)
            if not done[k] and f"{lab[k]} 대기" != st and not (k == "receipt" and st == "배송 중")]
    return f"{' · '.join(left)} 남음" if left else ""


def alerts(d: dict, today: dt.date) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if d.get("dropped"):
        return out
    due = expected_date(d)
    if due and not receipt_done(d) and due < today.isoformat():
        out.append({"code": "RECEIPT_DUE", "level": "alert",
                    "text": f"{'납기일' if overseas(d) else '입고 예정일'} {due} 지남({(today - dt.date.fromisoformat(due)).days}일) — 입고 확인 필요"})
    for t in overdue(d, today):
        out.append({"code": "OVERDUE", "level": "alert",
                    "text": f"{t['label']} {t['pct']:g}% 송금 예정일 {t['expected_date']} 지남({t['days']}일)"})
    for t in terms(d):
        if t["mismatch"]:
            out.append({"code": "PAY_MISMATCH", "level": "review",
                        "text": f"{t['label']} {t['pct']:g}% — 지출내역과 발주서가 일치하지 않은 채 넘어감"})
    return out
