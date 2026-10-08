"""컨설팅 사례집 PDF 파서(ingest_casebook) + 사례 선택 규칙(casebook) 검증 — DB/임베딩 불필요.

원본 PDF(docs/sources/참고. 2024년도 컨설팅 사례집.pdf)를 직접 파싱해 구조·KPI 짝짓기·읽기 순서를 확인한다.
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.casebook import _GENERIC_KW, _title_hits  # noqa: E402
from app.ingest_casebook import (  # noqa: E402
    CASEBOOKS, SECTIONS, casebook_for, parse_pdf, parse_program_pages, to_chunks,
)

PDF = ROOT / "docs" / "sources" / "참고. 2024년도 컨설팅 사례집.pdf"
PDF23 = ROOT / "docs" / "sources" / "[컨설팅사례집] 2023년.pdf"
PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def eq(actual, expected, label: str) -> None:
    if actual == expected:
        PASS.append(label)
    else:
        FAIL.append((label, f"got {actual!r} != {expected!r}"))


def ok(cond: bool, label: str, detail: str = "") -> None:
    (PASS.append(label) if cond else FAIL.append((label, detail)))


cases = parse_pdf(PDF)
CB = casebook_for(PDF)
program = parse_program_pages(PDF, CB)
chunks = to_chunks(CB, cases, program)
by_no = {c.case_no: c for c in cases}

# 구조
eq(len(cases), 23, "사례 23건 검출")
eq([c.pdf_pages[0] for c in cases], list(range(9, 54, 2)), "사례 시작 PDF 쪽 9,11,…,53")
eq(cases[0].printed_pages, "16-19", "#1 인쇄 쪽수")
eq(cases[-1].printed_pages, "104-107", "#23 인쇄 쪽수")
ok(
    all(int(a.printed_pages.split("-")[1]) + 1 == int(b.printed_pages.split("-")[0]) for a, b in zip(cases, cases[1:])),
    "인쇄 쪽수 연속",
)
for c in cases:
    ok(bool(c.title) and bool(c.company), f"#{c.case_no} 제목·회사명", f"{c.title!r} {c.company!r}")
    ok(not c.company.startswith("설립") and len(c.company) < 20, f"#{c.case_no} 회사명 형태", c.company)
    for sec in SECTIONS:
        ok(len(c.sections.get(sec, "")) >= 100, f"#{c.case_no} {sec} 본문", str(len(c.sections.get(sec, ""))))
    ok(len(c.kpis) >= 3, f"#{c.case_no} KPI 3개 이상", str(c.kpis))

eq(by_no[6].company, "대한정밀공업㈜", "#6 정보 박스 위치 변동(소개글 길이) 대응")
eq(by_no[20].company, "㈜유비벨록스", "#20 정보 박스 위치 변동 대응")
eq(by_no[20].company_info.get("전화"), "본사) 02)3470–4835 공장) 043)533-8931", "#20 여러 줄 전화 연장")
eq(by_no[1].company_info.get("소재지"), "경상남도 창원시 마산합포구 진북면 산단 1길 129", "#1 주소 줄바꿈 연장")

# KPI 좌표 짝짓기 — 원본 이미지로 확인한 값(사례 #2, 22쪽)
k2 = {k["label"]: (k["value"], k["unit"], k["direction"]) for k in by_no[2].kpis}
eq(k2.get("일일생산량 (KPI)"), ("16.6", "%", "증가"), "#2 일일생산량 16.6% 증가")
eq(k2.get("신규작업인원 (명)"), ("3", "명", "감소"), "#2 신규작업인원 3명 감소")
eq(k2.get("공정불량률 (%)"), ("N/A", "", ""), "#2 공정불량률 N/A")
eq(k2.get("투자회수 (연간)"), ("3.7", "년", ""), "#2 투자회수 3.7년")
ok("[도입 효과 KPI]" in by_no[2].sections["컨설팅 결과"], "KPI 가 결과 섹션에 포함")
ok("16.6" not in by_no[2].sections["컨설팅 결과"].split("[도입 효과 KPI]")[0], "KPI 원시 span 은 본문에서 제외")

# 읽기 순서 — 인터뷰 2단 질문이 한 줄로 섞이지 않음
for c in cases:
    iv = c.sections["기업인-전문가 인터뷰"]
    ok("무엇입니까? Q" not in iv and "어떠합니까? Q" not in iv, f"#{c.case_no} 인터뷰 2단 분리")
ok("현장 상황에 적합한 AMR 선정" in by_no[2].sections["컨설팅 결과"], "#2 결과 본문 추출")

# 지원사업 안내 쪽 — 인포그래픽 수치·항목 열 짝짓기(원본 이미지 확인값)
eq([p["pdf_page"] for p in program], [3, 6, 7, 8], "안내 쪽 3·6·7·8")
p7 = next(p["text"] for p in program if p["pdf_page"] == 7)
for pair in ("생산성 향상: 61.1%", "불량률 감소: 70.7%", "제조원가 절감: 48.6%", "납기준수 상승: 14.1%"):
    ok(pair in p7, f"p7 인포그래픽 {pair}")

# chunk
eq(len(chunks), 23 * 4 + 4, "chunk 수 = 사례×4 섹션 + 안내 4")
eq(len({c["source_label"] for c in chunks}), len(chunks), "source_label 고유(UNIQUE 키 충돌 없음)")
ok(all(c["content"].startswith(("[컨설팅 사례 2024-", "[로봇 지원사업 안내 2024]")) for c in chunks), "chunk 머리말")
ok(max(len(c["content"]) for c in chunks) < 6000, "chunk 길이 임베딩 한도 내")

# ── 2023 사례집 — 항목명/값 열 분리 박스, 세로 머리글, 9pt 수치+단위 한 조각 KPI ──
eq(casebook_for(PDF23).year, 2023, "파일명으로 연도 선택")
eq(casebook_for(PDF).source_path, CASEBOOKS[2024].source_path, "2024 source_path 유지(재적재 호환)")
c23 = parse_pdf(PDF23)
by23 = {c.case_no: c for c in c23}
eq(len(c23), 20, "2023 사례 20건")
eq(c23[0].printed_pages, "18-21", "2023 #1 인쇄 쪽수")
for c in c23:
    ok(bool(c.title) and c.company and "사례집" not in c.company and "사례집" not in c.industry,
       f"2023 #{c.case_no} 세로 머리글 제외", f"{c.company!r} {c.industry!r}")
    ok({"설립일자", "대표", "소재지"} <= set(c.company_info), f"2023 #{c.case_no} 정보 박스", str(c.company_info))
    ok("공정" not in c.company_info.get("홈페이지", ""), f"2023 #{c.case_no} 박스 아래 흐름도 제외",
       c.company_info.get("홈페이지", ""))
    ok(len(c.sections["기업인-전문가 인터뷰"]) >= 300, f"2023 #{c.case_no} 인터뷰('기업인 . 전문가') 분리")
    for k in c.kpis:
        ok(k["value"] == "N/A" or k["value"].replace(".", "").isdigit(), f"2023 #{c.case_no} KPI 값이 수치", str(k))
eq(by23[1].company, "대선주조㈜", "2023 #1 회사명")
eq(by23[1].company_info, {"설립일자": "1930. 07. 25", "대표": "조우현", "소재지": "부산광역시 기장군 장안읍 기장대로 1909",
                          "전화": "051)500-0114", "홈페이지": "http://www.c1.co.kr"}, "2023 #1 '대 표' 띄어쓴 항목명")
k1 = {k["label"]: (k["value"], k["unit"], k["direction"]) for k in by23[1].kpis}
eq(k1.get("생산성 (생산 C/T)"), ("6.67", "%", "증가"), "2023 #1 KPI 수치·단위 분리")
eq(k1.get("작업인원 (명)"), ("7.5", "명", "감소"), "2023 #1 작업인원")
eq(by23[2].kpis[0]["value"], "33", "2023 #2 타일 위 본문 문장을 수치로 오인하지 않음")
eq(sum(1 for c in c23 if not c.kpis), 4, "2023 KPI 타일 없는 사례 4건(원문에 '기대효과' 서술만)")
p23 = parse_program_pages(PDF23, casebook_for(PDF23))
eq([p["pdf_page"] for p in p23], [4, 7, 8, 9], "2023 안내 쪽")
ch23 = to_chunks(casebook_for(PDF23), c23, p23)
eq(len(ch23), 20 * 4 + 4, "2023 chunk 수")
m20 = [c["metadata"] for c in ch23 if c["metadata"].get("case_no") == 20]
ok(m20 and all(m["industry"] == "" for m in m20), "2023 #20 원본 오기 업종 태그 비움")
ok(all(c["metadata"]["industry"] for c in ch23 if c["metadata"].get("case_no") == 10), "2023 #10 업종은 유지")
ok(not ({c["source_label"] for c in ch23} & {c["source_label"] for c in chunks}), "연도 간 라벨 충돌 없음")

# 사례 선택 규칙 — 제목 키워드 일치
eq(_title_hits("용접 로봇 자동화 숙련공 부족", "알루미늄 패널폼 용접 공정"), 1, "제목 '용접' 일치")
eq(_title_hits("주유소 4족 보행 로봇 순찰", "로봇 활용 칫솔모 적재 공정"), 0, "무관 공정은 일반어(로봇)로 일치 안 함")
# 2026-09-28 제보: 업종 설명까지 보던 때 "센터 잡은 후"→"임상교육센터", "PLC 제어"→"로봇 제어기" 오일치
imp = next(c for c in cases if c.case_no == 19)
eq(_title_hits("비전으로 X,Y,Z 센터 잡은 후 너트러너로 뚜껑 풀기. 제어는 PLC/IPC", imp.title), 0,
   "약액 요청이 임플란트 사례 제목에 걸리지 않음(업종 설명 미사용)")
ok({"로봇", "자동화", "공정"} <= _GENERIC_KW, "일반어 제외 목록")

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for label, detail in FAIL:
    print(f"  ✗ {label}: {detail}")
sys.exit(1 if FAIL else 0)
