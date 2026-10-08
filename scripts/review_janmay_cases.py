# -*- coding: utf-8 -*-
"""1~5월 원본 자금계획에서 비영업일 보정이 실제로 일어난 케이스 전수 검토.

확인 항목
  A. 말일 비영업일(1·2·5월 말일=주말)에서 담당자 규칙대로 처리됐나?
     ① 말일성 지출 → 그 달 마지막 영업일(1/30·2/27·5/29)
     ② 말일성 수입 → 익월 첫 영업일(2/2·3/3·6/1)
  B. 중순(말일 아님) 비자동 결제(송금 등)가 주말 명목일에 걸린 '미확정 sliver' 케이스가 있나?
     있으면 실제로 직전/다음 중 어디로 갔나.

방법: 전체 이력(2025-09~2026-07)으로 스트림 명목일 N=median 을 안정 추정하고,
      그 보정 이벤트 중 **2026-01~05** 에 발생한 것만 카테고리별로 열거.
원본만 읽음(수정 없음). 실행: python scripts/review_janmay_cases.py
"""
from __future__ import annotations

import datetime
import sys
import statistics

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_placement_rules as AU
import learn_autotransfer_rule as L

WD = "월화수목금토일"
JANMAY = {(2026, m) for m in range(1, 6)}


def events(streams, category):
    """스트림별 N=median(전이력) 으로, 1~5월에 발생한 비영업일 보정 이벤트를 모은다."""
    rows = []
    for key, obs in streams.items():
        bymonth = {}
        for (y, mo, day, amt, txt, ven, pay) in obs:
            bymonth.setdefault((y, mo), (day, amt, txt, ven))
        days = [v[0] for v in bymonth.values()]
        if len(days) < 4:
            continue
        N = int(round(statistics.median(days)))
        for (y, mo), (day, amt, txt, ven) in sorted(bymonth.items()):
            if (y, mo) not in JANMAY:
                continue
            try:
                nd = datetime.date(y, mo, N)
            except ValueError:
                continue
            if L._is_biz(nd):
                continue  # 명목일이 영업일 → 보정 없음
            nb, pb = L._next_biz(nd), L._prev_biz(nd)
            lb = L._last_biz_of_month(y, mo)
            if nb.month == mo and day == nb.day:
                tag = "다음영업일"
            elif day == lb.day and nb.month != mo:
                tag = "월말→마지막영업일"
            elif day == pb.day:
                tag = "직전영업일"
            else:
                tag = "기타"
            rows.append({
                "cat": category, "vendor": ven or key[0], "N": N, "month": mo,
                "nom": nd, "nomwd": WD[nd.weekday()], "actual": day,
                "actwd": WD[datetime.date(y, mo, day).weekday()], "tag": tag,
                "monthend": N >= 28, "amt": amt, "txt": txt,
            })
    return rows


def main():
    cats = AU.collect()
    ev = (events(cats["exp_auto"], "지출·자동이체")
          + events(cats["exp_other"], "지출·일반(송금)")
          + events(cats["income"], "수입"))

    print("=" * 80)
    print("1~5월 자금계획 비영업일 보정 케이스 전수 검토 (원본, 명목일=전이력 median)")
    print("=" * 80)

    # A. 말일 케이스
    me = [e for e in ev if e["monthend"]]
    print(f"\n■ A. 말일성(N≥28) 보정 케이스 — {len(me)}건")
    if not me:
        print("   (해당 없음)")
    for e in sorted(me, key=lambda x: (x["month"], x["cat"])):
        ok = (("지출" in e["cat"] and e["tag"] == "월말→마지막영업일")
              or ("수입" in e["cat"] and e["tag"] == "다음영업일"))
        flag = "✓담당자규칙" if ok else f"⚠{e['tag']}"
        print(f"   {e['month']}월 [{e['cat']}] {e['vendor'][:16]:16s} 명목 {e['nom']}({e['nomwd']})"
              f" → 실제 {e['month']}/{e['actual']:02d}({e['actwd']})  {flag}  | {e['txt'][:26]}")

    # B. 중순 비자동 결제 주말 케이스(미확정 sliver)
    mid_other = [e for e in ev if (not e["monthend"]) and e["cat"] == "지출·일반(송금)"]
    print(f"\n■ B. 중순 비자동 결제(송금) 주말 케이스 — {len(mid_other)}건 (미확정 sliver)")
    if not mid_other:
        print("   (해당 없음)")
    nxt = sum(1 for e in mid_other if e["tag"] == "다음영업일")
    prv = sum(1 for e in mid_other if e["tag"] == "직전영업일")
    for e in sorted(mid_other, key=lambda x: (x["month"], -(x["amt"] or 0))):
        print(f"   {e['month']}월 {e['vendor'][:16]:16s} 명목 {e['nom']}({e['nomwd']})"
              f" → 실제 {e['month']}/{e['actual']:02d}({e['actwd']})  ▶ {e['tag']}"
              f"  | {('' if e['amt'] is None else format(e['amt'],',.0f'))}  {e['txt'][:22]}")
    if mid_other:
        print(f"   └ 방향: 다음영업일 {nxt} / 직전영업일 {prv} / 기타 {len(mid_other)-nxt-prv}")

    # C. 중순 자동이체 주말 케이스(참고 — 이미 R1으로 보강됨)
    mid_auto = [e for e in ev if (not e["monthend"]) and e["cat"] == "지출·자동이체"]
    an = sum(1 for e in mid_auto if e["tag"] == "다음영업일")
    ap = sum(1 for e in mid_auto if e["tag"] == "직전영업일")
    print(f"\n■ C. (참고) 중순 자동이체 주말 케이스 — {len(mid_auto)}건: 다음 {an} / 직전 {ap}")

    # 수입 중순
    mid_inc = [e for e in ev if (not e["monthend"]) and e["cat"] == "수입"]
    inx = sum(1 for e in mid_inc if e["tag"] == "다음영업일")
    ipv = sum(1 for e in mid_inc if e["tag"] == "직전영업일")
    print(f"■ C. (참고) 중순 수입 주말 케이스 — {len(mid_inc)}건: 다음 {inx} / 직전 {ipv}")


if __name__ == "__main__":
    main()
