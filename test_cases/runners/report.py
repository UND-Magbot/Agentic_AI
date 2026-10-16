"""오늘 결과(results/<날짜>/*.json)를 모아 보고서 results/<날짜>/report.md 를 만든다."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/tc/runners")
import tc  # noqa: E402

ORDER = ("FAIL", "ERROR", "CHECK", "SKIP", "PASS")
NAMES = {"recommend": "제품 추천", "quotes": "견적서", "quote_sync": "올린 견적서 이어 고치기", "deals": "영업 건 관리",
         "e2e": "브라우저"}


def main() -> None:
    day = tc.kst_today().isoformat()
    out = tc.ROOT / "results" / day
    suites = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json")) if p.stem in NAMES}
    lines = [f"# 영업 기능 검증 결과 {day}", "", "| 묶음 | " + " | ".join(ORDER) + " |", "|---|" + "---|" * len(ORDER)]
    for name, rs in suites.items():
        lines.append(f"| {NAMES[name]} | " + " | ".join(str(sum(r['status'] == s for r in rs)) for s in ORDER) + " |")
    for status, head in (("FAIL", "기대와 다름(결함 후보)"), ("ERROR", "테스트 중 오류"), ("CHECK", "정책 확인 필요"), ("SKIP", "건너뜀")):
        rows = [(NAMES[n], r) for n, rs in suites.items() for r in rs if r["status"] == status]
        if rows:
            lines += ["", f"## {head} ({len(rows)})", ""]
            lines += [f"- **{r['id']}** [{n}] {r['title']} — {r['detail'].splitlines()[0] if r['detail'] else ''}" for n, r in rows]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4 + len(suites)]))
    print(f"보고서: {out / 'report.md'}")


if __name__ == "__main__":
    main()
