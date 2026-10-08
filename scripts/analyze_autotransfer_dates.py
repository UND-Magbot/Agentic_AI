# -*- coding: utf-8 -*-
"""자동이체/자동출금 — 자동생성 예측 ↔ 수작업 원본의 거래처·금액·날짜 정합 분석.

두 질문에 답한다.
  Q1. 자동생성한 자동이체 예측이 수작업 입력과 거래처·금액이 일치했나?
  Q2. 비영업일(주말·공휴일) 보정 기준이 반영됐나? 원본과 방향이 같나?

핵심 발견(데이터로 입증):
  - 고정금액 정기 자동이체(에스원·웹케시·이카운트·엘지유플러스 등)는 거래처·금액 정확 일치.
  - 변동금액(대출이자·전기료·4대보험·카드값)은 자동생성이 의도적 공란(드래프트)=설계상 정상.
  - **비영업일 보정 방향이 원본과 반대**: 명목 결제일이 주말이면
      · 수작업 원본 = 다음 영업일로 **밀기**(예: 7/5 일 → 7/6 월)
      · 자동생성   = 직전 영업일로 **당기기**(예: 7/5 일 → 7/3 금)
    자동이체(은행 자동출금)는 실제로 다음 영업일에 빠지므로 원본(밀기)이 맞고,
    현재 규칙(지출=직전영업일)은 일반 집행에는 맞아도 자동이체에는 부적합.

원본 미변경. 작업 복사본만 읽음. compare_plan_vs_manual 의 정규화/렌더 헬퍼 재사용.
실행: python scripts/analyze_autotransfer_dates.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import openpyxl

import compare_plan_vs_manual as C  # 정규화·렌더 헬퍼·복사본 경로
from app.finance import plan_generator as PG

PAY_AUTO = {"자동이체", "자동출금"}
WD = "월화수목금토일"
TARGETS = [(2026, 6), (2026, 7)]
SHEET = "자금계획_원화"


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def _is_biz(d: datetime.date) -> bool:
    return d.weekday() < 5 and d not in PG.HOLIDAYS_2026


def read_auto(path, sheet, y, mo):
    wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
    rows = list(wb[sheet].iter_rows(values_only=True))
    wb.close()
    markers, yr = PG._resolve_marker_years(rows)
    out = []
    for k in range(len(markers)):
        if yr[k] != y or markers[k][1] != mo:
            continue
        day = markers[k][2]
        s = markers[k][0]
        e = PG._block_end(rows, s)
        for i in range(s, e):
            r = rows[i]
            pay = str(PG.PR._cell(r, PG.C_EXP_PAY) or "").strip()
            if pay not in PAY_AUTO:
                continue
            ven = str(PG.PR._cell(r, PG.C_EXP_VENDOR) or "").strip()
            desc = str(PG.PR._cell(r, PG.C_EXP_DESC) or PG.PR._cell(r, PG.C_EXP_ITEM) or "").strip()
            out.append({
                "vkey": C.norm_vendor(ven), "vendor": ven, "desc": desc, "pay": pay,
                "amount": _num(PG.PR._cell(r, PG.C_EXP_AMOUNT)), "day": day,
            })
    return out


def match(manual, gen):
    """거래처 기준 매칭. 금액동일 우선, 다음 적요 앞부분 유사, 다음 거래처만."""
    pool = list(gen)
    pairs, missed = [], []
    for m in manual:
        best, bj = -1, None
        for j, g in enumerate(pool):
            if g["vkey"] != m["vkey"]:
                continue
            sc = 0
            if m["amount"] is not None and g["amount"] is not None and abs(m["amount"] - g["amount"]) <= 1:
                sc += 3
            if m["desc"][:6] == g["desc"][:6]:
                sc += 1
            if sc > best:
                best, bj = sc, j
        if bj is None:
            missed.append(m)
        else:
            pairs.append((m, pool.pop(bj)))
    return pairs, missed, pool  # pool = 생성만(추가)


def classify_date(y, mo, dm, dg):
    """매칭쌍 날짜 차이 사유 분류."""
    if dm == dg:
        return "동일"
    lo, hi = min(dm, dg), max(dm, dg)
    has_nonbiz = any(not _is_biz(datetime.date(y, mo, d)) for d in range(lo + 1, hi))
    # 생성이 더 이르고(직전영업일 당김) 그 사이에 비영업일 → 원본은 다음영업일 밀기
    if dg < dm and has_nonbiz:
        return "방향상이"      # 원본=다음영업일 / 생성=직전영업일
    if abs(dm - dg) <= 2:
        return "학습오차"      # 명목 결제일 학습 ±1~2일
    return "기타"


def analyze():
    OP = C.COPIES / "원본_자금계획_260624.xlsx"
    GP = C.COPIES / "생성_자금계획_6-7월.xlsx"
    result = {}
    amt_match = amt_diff = amt_draft = 0
    for (y, mo) in TARGETS:
        m = read_auto(OP, SHEET, y, mo)
        g = read_auto(GP, SHEET, y, mo)
        pairs, missed, extra = match(m, g)
        cls = {"동일": [], "방향상이": [], "학습오차": [], "기타": []}
        for mm, gg in pairs:
            cls[classify_date(y, mo, mm["day"], gg["day"])].append((mm, gg))
            if gg["amount"] is None:
                amt_draft += 1
            elif mm["amount"] is not None and abs(mm["amount"] - gg["amount"]) <= 1:
                amt_match += 1
            else:
                amt_diff += 1
        result[(y, mo)] = {
            "manual_n": len(m), "gen_n": len(g), "pairs": pairs,
            "missed": missed, "extra": extra, "cls": cls,
        }
    return result, {"amt_match": amt_match, "amt_diff": amt_diff, "amt_draft": amt_draft}


# ── 목업 ──────────────────────────────────────────────────────────────────────
def render(result, amt):
    rowh = 28
    diffs = []
    for (y, mo), R in result.items():
        for cat in ("방향상이", "학습오차", "기타"):
            for mm, gg in R["cls"][cat]:
                diffs.append((y, mo, cat, mm, gg))
    W = 1360
    H = 360 + (len(diffs) + 6) * rowh
    img, d = C.canvas(W, H)
    y0 = C.header(d, 48, 36, "자동이체 — 자동생성 예측 ↔ 수작업 원본 (거래처·금액·날짜)",
                  "Q1 거래처·금액 정합 / Q2 비영업일 보정 방향 검증 · 원화 6·7월 자동이체/자동출금")
    y0 += 8

    # 상단 요약 카드
    n_same = sum(len(R["cls"]["동일"]) for R in result.values())
    n_dir = sum(len(R["cls"]["방향상이"]) for R in result.values())
    n_lrn = sum(len(R["cls"]["학습오차"]) for R in result.values())
    n_etc = sum(len(R["cls"]["기타"]) for R in result.values())
    n_pair = n_same + n_dir + n_lrn + n_etc
    cards = [
        ("금액 정합", f"고정 {amt['amt_match']}건 일치", C.OK_BD,
         f"변동 {amt['amt_draft']}건 공란(정상)·불일치 {amt['amt_diff']}건"),
        ("날짜 동일", f"{n_same}/{n_pair}", C.ACCENT, "매칭쌍 중 같은 날"),
        ("비영업일 방향상이", f"{n_dir}건", C.DIFF_BD, "원본=다음영업일 / 생성=직전영업일"),
        ("명목일 학습오차", f"{n_lrn}건", C.DRAFT_BD, "±1~2일 (둘 다 영업일)"),
    ]
    cw = (W - 96 - 3 * 16) // 4
    for i, (t, big, col, sub) in enumerate(cards):
        x0 = 48 + i * (cw + 16)
        d.rounded_rectangle([x0, y0, x0 + cw, y0 + 124], radius=14, fill=C.CARD, outline=C.GRID, width=1)
        d.text((x0 + 16, y0 + 14), t, font=C.f(14, True), fill=C.MUT)
        d.text((x0 + 16, y0 + 40), big, font=C.f(30, True), fill=col)
        for j, ln in enumerate(C._wrap_txt(d, sub, C.f(12), cw - 28) if hasattr(C, "_wrap_txt") else [sub]):
            d.text((x0 + 16, y0 + 86 + j * 16), ln, font=C.f(12), fill=C.MUT)
    y = y0 + 150

    # 규칙 설명 배너
    d.rounded_rectangle([48, y, W - 48, y + 56], radius=10, fill=C.DIFF_BG, outline=C.DIFF_BD, width=1)
    d.text((64, y + 8),
           "핵심: 명목 결제일이 주말/공휴일일 때 —  수작업 원본은 「다음 영업일」로 밀고,  자동생성은 「직전 영업일」로 당긴다 (방향 반대).",
           font=C.f(14, True), fill=C.DIFF_BD)
    d.text((64, y + 32),
           "자동이체는 실제로 다음 영업일에 출금되므로 원본(밀기)이 맞다. 현재 규칙 ‘지출=직전영업일’은 일반 집행용이라 자동이체엔 부적합.",
           font=C.f(13), fill=C.INK)
    y += 74

    # 날짜 차이 표
    cols = [("월", 60), ("사유", 130), ("거래처", 220), ("적요", 460),
            ("원본일(요일)", 150), ("생성일(요일)", 150), ("금액", 140)]
    x = 48
    for ct, cwid in cols:
        d.rectangle([x, y, x + cwid, y + 26], fill=C.HDR_BG, outline=C.GRID)
        d.text((x + 6, y + 5), ct, font=C.f(12, True), fill=C.HDR_TX); x += cwid
    y += 26
    catcol = {"방향상이": (C.DIFF_BG, C.DIFF_BD), "학습오차": (C.DRAFT_BG, C.DRAFT_BD),
              "기타": (C.MISS_BG, C.MISS_BD)}
    order = {"방향상이": 0, "학습오차": 1, "기타": 2}
    for (y_, mo, cat, mm, gg) in sorted(diffs, key=lambda r: (order[r[2]], r[1], r[3]["day"])):
        x = 48
        bg, bd = catcol[cat]
        dm, dg = mm["day"], gg["day"]
        wdm = WD[datetime.date(y_, mo, dm).weekday()]
        wdg = WD[datetime.date(y_, mo, dg).weekday()]
        amt_txt = "공란" if gg["amount"] is None else C.fmt(gg["amount"])
        vals = [f"{mo}월", cat, mm["vendor"] or mm["vkey"], mm["desc"],
                f"{mo}/{dm:02d}({wdm})", f"{mo}/{dg:02d}({wdg})", amt_txt]
        for (ct, cwid), val in zip(cols, vals):
            cell = bg if ct == "사유" else C.CARD
            d.rectangle([x, y, x + cwid, y + rowh], fill=cell, outline=C.GRID)
            fo = C.f(12, True) if ct == "사유" else C.f(12)
            col = bd if ct == "사유" else C.INK
            d.text((x + 6, y + 6), C.ellipse(d, str(val), fo, cwid - 12), font=fo, fill=col)
            x += cwid
        y += rowh
    C._save(img, "04_autotransfer_dates.png")


# ── 리포트 섹션 ────────────────────────────────────────────────────────────────
def write_section(result, amt):
    n = lambda cat: sum(len(R["cls"][cat]) for R in result.values())
    n_same, n_dir, n_lrn, n_etc = n("동일"), n("방향상이"), n("학습오차"), n("기타")
    n_pair = n_same + n_dir + n_lrn + n_etc
    man = sum(R["manual_n"] for R in result.values())
    gen = sum(R["gen_n"] for R in result.values())
    miss = sum(len(R["missed"]) for R in result.values())
    extra = sum(len(R["extra"]) for R in result.values())

    def dir_rows():
        out = []
        for (y, mo), R in result.items():
            for mm, gg in sorted(R["cls"]["방향상이"], key=lambda p: p[0]["day"]):
                dm, dg = mm["day"], gg["day"]
                wdm = WD[datetime.date(y, mo, dm).weekday()]
                wdg = WD[datetime.date(y, mo, dg).weekday()]
                out.append(f"- {mo}월 **{mm['vendor'] or mm['vkey']}** · {mm['desc'][:30]} "
                           f": 원본 {mo}/{dm:02d}({wdm}) ↔ 생성 {mo}/{dg:02d}({wdg})")
        return "\n".join(out)

    md = f"""

---

# 부록 A. 자동이체/자동출금 정합 분석 (거래처·금액·날짜)

- 대상: 원화 자금계획 6·7월의 납부방법 = `자동이체`/`자동출금` 라인
- 재현: `python scripts/analyze_autotransfer_dates.py` → `mockups/04_autotransfer_dates.png`
- 원본 미변경(복사본만 읽음)

## Q1. 거래처·금액은 일치했나?

| 항목 | 결과 |
|---|---|
| 자동이체 라인 수 | 원본 {man}건 vs 생성 {gen}건 |
| 거래처 매칭(월 단위) | 매칭 {n_pair}건 · 원본만(미생성) {miss}건 · 생성만(추가) {extra}건 |
| 고정금액 일치 | {amt['amt_match']}건 (에스원·웹케시·이카운트·엘지유플러스·제일소방방재·퇴직연금 등 정확 일치) |
| 변동금액 공란(드래프트) | {amt['amt_draft']}건 — 대출이자·전기료·4대보험·카드값 등, **설계상 정상**(수기확정 대상) |
| 금액 불일치 | {amt['amt_diff']}건 |

→ **고정금액 정기 자동이체는 거래처·금액이 정확히 일치**한다. 변동금액은 의도적 공란이다.
   미생성 {miss}건의 대부분은 과제비 자계좌이체(시장확대형 3차년도 연구활동비·출장비 등
   비정형 다건)와 일회성 외화환전으로, 안정적 월 반복 템플릿이 아니라 학습 대상에서 빠졌다.

## Q2. 비영업일 보정 기준은 반영됐나? — **반영됐으나 방향이 원본과 반대**

매칭쌍 {n_pair}건 중 날짜까지 같은 건은 **{n_same}건**뿐이고, 나머지는 다음으로 갈린다.

| 사유 | 건수 | 설명 |
|---|---|---|
| 비영업일 **방향상이** | **{n_dir}건** | 명목 결제일이 주말/공휴일 → 원본은 **다음 영업일(밀기)**, 생성은 **직전 영업일(당기기)** |
| 명목일 학습오차(±1~2일) | {n_lrn}건 | 과거 6개월 결제일 학습값이 원본 기입일과 1~2일 차이(둘 다 영업일) |
| 기타/거래처 매칭 모호 | {n_etc}건 | 대출계좌 다건 등 |

**방향상이**가 핵심이다. 자동생성은 README/코드 규칙대로 *지출=직전 영업일(당김)* 을 적용했지만,
수작업 원본은 자동이체를 *다음 영업일(밀기)* 로 기입했다. 자동이체(은행 자동출금)는 명목일이
비영업일이면 실제로 **다음 영업일에 출금**되므로 **원본(밀기)이 실제와 맞다**. 현재의
‘지출=직전영업일’ 규칙은 우리가 직접 집행하는 일반 지출(주말 전 미리 송금)에는 타당하나,
**자동이체/자동출금에는 부적합**하다.

### 방향상이 사례 (원본 ↔ 생성)
{dir_rows()}

## 권고 (규칙 보강)

1. **납부방법이 자동이체/자동출금이면 비영업일 보정을 ‘다음 영업일(밀기)’로** 적용한다
   (일반 집행 지출의 ‘직전 영업일’과 분기). `build_day_blocks`의 `_expense_day` 를
   납부방법 기반으로 분기하면 방향상이 {n_dir}건이 대부분 해소된다.
2. 명목 결제일은 최빈값 외에 **‘직전월 실제 기입일’ 우선** 등으로 ±1일 학습오차를 줄인다.
3. 과제비 자계좌이체(연구활동비·출장비)는 금액·건수가 비정형이라 자동 템플릿 대신
   **마감/과제 모듈에서 별도 주입**하는 편이 정확하다.
"""
    rp = C.OUTDIR / "compare_report.md"
    text = rp.read_text(encoding="utf-8")
    marker = "\n\n---\n\n# 부록 A."
    if marker.strip() in text:
        text = text.split("\n\n---\n\n# 부록 A.")[0]
    rp.write_text(text.rstrip() + md, encoding="utf-8")
    print(f"  ✓ 부록 A 추가 → {rp.relative_to(ROOT)}")


def main():
    print("[자동이체 날짜 분석]")
    result, amt = analyze()
    C.OUTDIR.mkdir(parents=True, exist_ok=True)
    C.MOCK.mkdir(parents=True, exist_ok=True)
    render(result, amt)
    write_section(result, amt)
    n = lambda cat: sum(len(R["cls"][cat]) for R in result.values())
    print(f"\n  금액: 고정일치 {amt['amt_match']} · 변동공란 {amt['amt_draft']} · 불일치 {amt['amt_diff']}")
    print(f"  날짜: 동일 {n('동일')} · 방향상이 {n('방향상이')} · 학습오차 {n('학습오차')} · 기타 {n('기타')}")


if __name__ == "__main__":
    main()
