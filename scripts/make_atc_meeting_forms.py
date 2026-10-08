"""MAGBOT 툴체인저 '첫 고객 미팅 질문지' 양식 + 작성 샘플 5개(+ 추가 질문을 모두 채운 N.1 판) 생성(영업부 질문지 v0.2 1·2쪽 기준).

    python scripts/make_atc_meeting_forms.py   → docs/atc_meeting/*.docx, frontend/public/forms/ 에 빈 양식 사본

영업사원이 미팅 때 채운 파일을 회사 제품 추천 > ATC 탭에 첨부하면 AI 가 읽어 요약·추출하고 선정 규칙으로 후보를 찾는다.
샘플 5개는 가상 고객(실제 회사·수치 아님)으로, 상황을 서로 다르게 구성했다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "atc_meeting"
PUBLIC = ROOT / "frontend" / "public" / "forms"
GRAY = RGBColor(0x8A, 0x8F, 0x98)
FONT = "맑은 고딕"

QUESTIONS = [
    ("1", "어떤 로봇을 사용하나요?", "제조사 / 모델 / 수량(대) / 협동 · 산업용 · 모름 — 모델명을 모르면 로봇 명판 사진 요청. 다른 모델은 한 줄 더."),
    ("2", "어떤 작업에 사용하나요?", "이송 · 조립 · 가공 · 검사 · 기타 / 작업 설명"),
    ("3", "교체해서 사용할 툴은 몇 개인가요?", "총 ___개 — 같은 모델이라도 실제 교체하는 수량. 예비품은 따로."),
    ("4", "툴별 크기와 무게는 얼마인가요?", "2쪽 '툴별 정보' 표에 툴마다 적습니다(가로×세로×높이 mm, 드는 제품을 뺀 툴 무게 kg)."),
    ("5", "한 번에 드는 제품의 최대 무게는 얼마인가요?", "2쪽 표에 툴마다 — 여러 개를 함께 들면 합계. 제품을 들지 않는 툴은 '해당 없음'."),
    ("6", "로봇 운전 속도는 얼마인가요?", "최대 설정값 ___ 단위 ___ — 고객 화면 값 그대로. 모르면 속도 화면 사진 요청."),
    ("7", "작업 중 툴을 기울이거나 뒤집나요?", "있음 · 없음 · 모름 — 툴마다 다르면 2쪽 표에."),
    ("8", "툴에 전기, 공압, 센서가 필요한가요?", "전기 / 공압 / 센서 또는 제어 신호 각각 있음 · 없음 · 모름 — 2쪽 표에 툴마다."),
    ("9", "기본 케이블 길이로 충분한가요?", "툴체인저→컨트롤러 4m 충분 · 부족 · 모름 / 컨트롤러→PLC 또는 로봇 1m 충분 · 부족 · 모름"),
    ("10", "특별한 사용 환경이 있나요?", "해당 없음 · 철가루 · 물이나 기름 · 고온 · 기타 · 모름 — 사진이 있으면 함께."),
]
FOLLOWUPS = [
    # 추가 질문 문구는 액세서리 통합 선정 v1.2 junior_questions 기준(2026-10-08) — 첫 칸(조건)은 그대로(코드가 이 글로 찾는다)
    ("전기가 필요함", "전압과 전류(정격·피크/최대)가 적힌 라벨이나 사양서가 있나요? 같은 전원을 동시에 쓰는 장치는 합쳐서."),
    ("센서나 신호가 필요함", "연결할 기기·센서의 이름과 수량, 실제로 연결할 입력 DI·출력 DO 수는?"),
    ("전기 또는 센서를 사용함", "실제로 쓸 제어 방식(I/O · RS485 2선/4선 · 기타)과 0V 외 공통선(COM·SG)·접지가 있나요? 배선도·선 색상표가 있나요?"),
    ("공압이 필요함", "실제 공급 압력(bar·MPa)은? 복동·단동·밸브 내장, 밸브 위치(로봇측·툴측), 툴당 그리퍼 수, 블로우·진공 추가 라인은?"),
    ("기울이거나 뒤집음", "해당 자세를 사진이나 영상으로 보여줄 수 있나요?"),
    ("누르거나 끼우거나 자름", "어떤 작업인지 설명이나 영상을 받을 수 있나요?"),
    ("케이블 길이가 부족함", "부족한 구간은 몇 m가 필요한가요?"),
    ("정보를 모름", "모델명이나 사진 또는 도면을 받을 수 있나요?"),
]


def _font(run, size=10, bold=False, color=None):
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _para(doc, text, size=10, bold=False, color=None, space_after=4):
    p = doc.add_paragraph()
    _font(p.add_run(text), size, bold, color)
    p.paragraph_format.space_after = Pt(space_after)
    return p


def _shade(cell, hex_color):
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc.append(shd)


def _cell(cell, text, size=9.5, bold=False, color=None, fill=None):
    cell.text = ""
    _font(cell.paragraphs[0].add_run(text), size, bold, color)
    if fill:
        _shade(cell, fill)


def _height(row, pt):
    trPr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(int(pt * 20)))
    h.set(qn("w:hRule"), "atLeast")
    trPr.append(h)


def _table(doc, header, rows, widths=None, row_height=None):
    t = doc.add_table(rows=1 + len(rows), cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        _cell(t.rows[0].cells[i], h, bold=True, fill="E8EDF5")
    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row):
            _cell(t.rows[r].cells[c], v or "")
        if row_height:
            _height(t.rows[r], row_height)
    return t


def build(path: Path, a: dict | None = None) -> None:
    """a=None 이면 빈 양식, 있으면 그 답으로 채운 샘플."""
    a = a or {}
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Pt(48)
        s.top_margin = s.bottom_margin = Pt(36)

    # ── 1쪽 ──
    _para(doc, "MAGBOT 툴체인저", 11, True, RGBColor(0x1F, 0x4E, 0xA8), 0)
    _para(doc, "첫 고객 미팅 질문지", 18, True, space_after=2)
    _para(doc, "단품 선정용 · 아는 내용만 적고, 모르면 “모름”으로 표시하세요. 툴별 정보는 2쪽에 적습니다.", 9, color=GRAY, space_after=8)
    _table(doc, ["고객사", "작성자", "미팅일"], [[a.get("customer", ""), a.get("writer", ""), a.get("date", "")]])
    _para(doc, "", space_after=4)
    # 질문 칸에 질문 + 회색 안내, 답변 칸은 비워 둔다(빈 양식) / 답을 채운다(샘플)
    t = _table(doc, ["질문", "답변"], [["", a.get(f"q{no}", "")] for no, _, _ in QUESTIONS], row_height=None if a else 24)
    for r, (no, q, hint) in zip(t.rows[1:], QUESTIONS):
        c = r.cells[0]
        c.text = ""
        _font(c.paragraphs[0].add_run(f"{no}. {q}"), 9.5, True)
        _font(c.add_paragraph().add_run(hint), 8, color=GRAY)
        r.cells[0].width = Pt(230)
        r.cells[1].width = Pt(270)

    # ── 2쪽 ──
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    _para(doc, "툴별 정보", 14, True, space_after=2)
    _para(doc, "툴별로 작성합니다. 같은 툴·같은 사용 조건이면 한 줄로 묶고 수량을 적어도 됩니다. 툴 무게는 드는 제품을 제외한 "
               "무게입니다(사진·도면을 함께 받아 포함된 부품 확인). 제품을 들지 않는 툴은 제품 무게를 “해당 없음”으로.", 9, color=GRAY)
    tools = a.get("tools") or [{}, {}, {}]
    _table(doc, ["툴 이름과 모델", "수량", "크기 mm (가로×세로×높이)", "툴 무게 kg", "제품 최대 총무게 kg"],
           [[t.get("name", f"툴 {i + 1}"), t.get("qty", ""), t.get("size", ""), t.get("mass", ""), t.get("wp", "")]
            for i, t in enumerate(tools)], row_height=None if a else 20)
    _para(doc, "", space_after=4)
    _para(doc, "기울임: 있음 / 없음 / 모름 · 전기·공압·센서: 유 = 있음 / 무 = 없음 / ? = 모름. 전기 구동이 없어도 센서는 사용할 수 있습니다.", 9, color=GRAY)
    _table(doc, ["툴", "사용 로봇", "기울임 또는 뒤집기", "전기", "공압", "센서 또는 신호"],
           [[t.get("name", f"툴 {i + 1}"), t.get("robot", ""), t.get("tilt", ""), t.get("power", ""), t.get("air", ""),
             t.get("sensor", "")] for i, t in enumerate(tools)], row_height=None if a else 22)
    _para(doc, "", space_after=4)
    _para(doc, "해당하는 경우에만 추가로 질문", 11, True, space_after=2)
    fu = a.get("followups") or {}
    _table(doc, ["고객 답변", "다음 질문", "답변"], [[c, q, fu.get(c, "")] for c, q in FOLLOWUPS])
    _para(doc, "", space_after=4)
    _para(doc, "그 밖의 메모 (시스템·툴스탠드 요청, 예비품, 고객 요청 등)", 11, True, space_after=2)
    _table(doc, ["메모"], [[a.get("memo", "")]], row_height=28 if not a else None)
    doc.save(path)


# 샘플 다시 작성(사용자 2026-10-02: JSON 규칙대로 올바른 추천이 나오게). 모두 가상 고객·가상 툴(실제 제품 사양을 빌리지 않음).
# 각 샘플이 보여 줄 JSON 규칙과 기대 결과는 docs/atc_meeting/샘플_기대결과.md · scripts/test_atc_meeting.py 에 둔다.
# - 전원은 회로마다 연속·피크 전류를 적고, 1A 초과는 '경계 사례'(샘플4)에만 둔다(R12: 핀 1개 1A, 초과는 내부 검토).
# - 센서만 있는 툴도 센서 전원 회로를 적는다(R11 '전원 공급선'). DI/DO 수는 '센서나 신호' 칸에 툴마다 적는다(R11 신호선).
# - 공압은 표준 범위 1~5bar(R14). 5~6bar(P05)·6bar 이상은 경계 사례(샘플4)에만.
SAMPLES = [
    # 1) 협동로봇 표준 — 툴 2개, 추가 질문 표를 비워 둠(미팅 때 못 물어봄 → AI 가 대화에서 전기·신호·공압을 묻는지 확인)
    ("샘플1_협동_사출품이송", {
        "customer": "(가상) 한빛플라스틱", "writer": "김영업", "date": "2026-10-02",
        "q1": "두산로보틱스 M1013 1대 / 협동", "q2": "이송 — 사출기에서 성형품을 꺼내 컨베이어에 올리고, 가끔 박스 포장 툴로 교체",
        "q3": "총 2개", "q4": "2쪽 표 참고", "q5": "2쪽 표 참고", "q6": "최대 설정값 60 / 단위 % (티칭펜던트 속도 화면)",
        "q7": "없음", "q8": "2쪽 표 참고", "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "전동 그리퍼", "qty": "1", "size": "220 × 90 × 150", "mass": "0.8", "wp": "0.5",
             "robot": "M1013", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
            {"name": "진공 그리퍼 (흡착패드 4개)", "qty": "1", "size": "300 × 200 × 120", "mass": "1.2", "wp": "2 (0.5kg × 4개)",
             "robot": "M1013", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
        ],
        "followups": {},
        "memo": "예비 툴플레이트 1개 추가 요청",
    }),
    # 2) 산업용 고중량 — 기울임(R08)·케이블 부족(R17)·철가루 환경, 툴 3개(A 2개 + B 1개)
    ("샘플2_산업용_차체패널", {
        "customer": "(가상) 대성오토", "writer": "박영업", "date": "2026-10-02",
        "q1": "현대로보틱스 HS220 2대 / 산업용", "q2": "이송 — 프레스 후 차체 패널을 지그로 이송, 패널을 세워서 내려놓음",
        "q3": "총 3개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 40 / 단위 %", "q7": "있음 (패널을 세움, 약 60°)",
        "q8": "2쪽 표", "q9": "툴체인저→컨트롤러 4m 부족(약 6m 필요) / 컨트롤러→PLC 1m 충분", "q10": "철가루 (프레스 라인 분진)",
        "tools": [
            {"name": "패널 그리퍼 A (자석+흡착)", "qty": "2", "size": "1200 × 800 × 250", "mass": "45", "wp": "30",
             "robot": "HS220 (2대 공용)", "tilt": "있음", "power": "유", "air": "유", "sensor": "유"},
            {"name": "패널 그리퍼 B", "qty": "1", "size": "1000 × 700 × 250", "mass": "40", "wp": "25",
             "robot": "HS220", "tilt": "있음", "power": "유", "air": "유", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "툴마다 24V 1회로, 연속 0.9A, 피크 1A (전자석 컨트롤러 사양서)",
                      "센서나 신호가 필요함": "툴마다 근접센서 4개, 흡착 압력 스위치 1개",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음(0V 공용), 배선도 요청함", "공압이 필요함": "5bar, 호스 2개, 독립 유로 2개(흡착 ON/OFF·블로우)",
                      "기울이거나 뒤집음": "영상 받음(60° 세움)", "케이블 길이가 부족함": "툴체인저→컨트롤러 6m"},
        "memo": "",
    }),
    # 3) 정보 부족 — '모름'·'?'·툴 수 불일치(R10) → 후보 보류, 자료 요청(R20: 모르는 값을 0 으로 채우지 않음)
    ("샘플3_정보부족_조립", {
        "customer": "(가상) 미래전자", "writer": "이신입", "date": "2026-10-02",
        "q1": "모델 모름(명판 사진 요청함) / 협동", "q2": "조립 — 케이스에 부품을 끼우는 작업이라고 함",
        "q3": "총 3개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "모름 (속도 화면 사진 요청)", "q7": "모름",
        "q8": "모름", "q9": "모름 / 모름", "q10": "모름",
        "tools": [
            {"name": "핑거 그리퍼 (모델 모름)", "qty": "1", "size": "모름", "mass": "약 1.5", "wp": "0.3",
             "robot": "", "tilt": "모름", "power": "?", "air": "?", "sensor": "?"},
            {"name": "드라이버 툴", "qty": "1", "size": "모름", "mass": "모름", "wp": "해당 없음",
             "robot": "", "tilt": "모름", "power": "?", "air": "?", "sensor": "?"},
        ],
        "followups": {"정보를 모름": "고객이 다음 주에 툴 사진·도면 보내주기로 함", "누르거나 끼우거나 자름": "부품 끼우기(압입), 영상 요청함"},
        "memo": "툴 3번째는 아직 미정이라고 함",
    }),
    # 4) 경계 사례 — 압력 5.5bar(P05)·RS-485(R13)·피크 2A(R12)·기울임(R08)·PLC 케이블 2m(R17)·물기 → 후보는 나오되 내부 검토
    ("샘플4_압력경계_통신", {
        "customer": "(가상) 그린바이오", "writer": "최영업", "date": "2026-10-02",
        "q1": "Universal Robots UR10e 1대 / 협동", "q2": "검사 — 바이알 병을 집어 비전 검사대에 올림, 검사 후 트레이에 정렬",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 50 / 단위 %", "q7": "툴 1은 없음, 툴 2는 있음(병을 기울여 라벨 확인)",
        "q8": "2쪽 표", "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 부족(2m 필요)", "q10": "물이나 기름 (세척수 튐)",
        "tools": [
            {"name": "전동 그리퍼 (RS-485 제어)", "qty": "1", "size": "150 × 80 × 180", "mass": "1.1", "wp": "0.4 (0.2kg × 2병)",
             "robot": "UR10e", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
            {"name": "진공 그리퍼", "qty": "1", "size": "160 × 160 × 140", "mass": "0.9", "wp": "0.2",
             "robot": "UR10e", "tilt": "있음", "power": "무", "air": "유", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "전동 그리퍼: 24V 1회로, 연속 0.8A, 피크 2A (그리퍼 사양서) / 진공 그리퍼: 센서 전원 24V 1회로, 최대 0.1A",
                      "센서나 신호가 필요함": "진공 그리퍼: 진공 확인 센서 1개",
                      "전기 또는 센서를 사용함": "전동 그리퍼: RS-485 / 진공 그리퍼: DIO", "공압이 필요함": "5.5bar, 호스 1개, 독립 유로 1개",
                      "기울이거나 뒤집음": "약 45° 기울임, 영상 받음", "케이블 길이가 부족함": "컨트롤러→PLC 2m"},
        "memo": "",
    }),
    # 5) 협동로봇 2종·툴 4개 — 전기 조건(전원 칸)만 못 받아 옴 → AI 가 그 툴들에 전원·신호 수를 묻는지 확인
    ("샘플5_다수툴_혼합로봇", {
        "customer": "(가상) 성진물류", "writer": "정영업", "date": "2026-10-02",
        "q1": "한화로보틱스 HCR-12 1대 / 협동, 레인보우로보틱스 RB10 1대 / 협동",
        "q2": "이송 — 박스·파우치를 집어 적재(팔레타이징), 툴 자동 교체", "q3": "총 4개", "q4": "2쪽 표", "q5": "2쪽 표",
        "q6": "HCR-12 최대 60% / RB10 최대 60%", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "고온 (여름철 창고 약 45℃)",
        "tools": [
            {"name": "흡착 그리퍼 (같은 모델)", "qty": "2", "size": "400 × 300 × 150", "mass": "2.5", "wp": "5",
             "robot": "HCR-12, RB10 각 1", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
            {"name": "핑거 그리퍼", "qty": "1", "size": "200 × 120 × 160", "mass": "1.5", "wp": "3",
             "robot": "HCR-12", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
            {"name": "마그네틱 그리퍼", "qty": "1", "size": "Ø120 × 80", "mass": "1.8", "wp": "4",
             "robot": "RB10", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
        ],
        "followups": {"센서나 신호가 필요함": "흡착 그리퍼: 진공 스위치 1개 / 핑거 그리퍼: 열림·닫힘 센서 2개 / 마그네틱 그리퍼: ON/OFF 명령 신호",
                      "전기 또는 센서를 사용함": "DIO", "공압이 필요함": "4bar, 호스 2개, 독립 유로 1개(흡착 ON/OFF)"},
        "memo": "툴스탠드도 같이 견적 요청(시스템) / 예비 툴플레이트 2개",
    }),
    # ── 샘플6~10 (사용자 2026-10-02: 다른 툴체인저 모델이 추천되게) — 추가 질문까지 모두 채운 판. 무게(R02)와 로봇 종류로 계열·단계가 갈린다.
    # 6) 협동 · 툴측 12kg → 자동 TCV2(16kg) — 속도 50%(협동 70 이하, 잠정 P01)는 상향 없음
    ("샘플6_협동_박스팔레타이징", {
        "customer": "(가상) 동해식품", "writer": "한영업", "date": "2026-10-02",
        "q1": "두산로보틱스 H2017 1대 / 협동", "q2": "이송 — 완제품 박스를 팔레트에 적재(팔레타이징), 박스 크기가 바뀌면 툴 교체",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 50 / 단위 %", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "박스 흡착 그리퍼", "qty": "1", "size": "600 × 400 × 200", "mass": "5", "wp": "7 (박스 1개)",
             "robot": "H2017", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
            {"name": "포크형 그리퍼", "qty": "1", "size": "500 × 350 × 250", "mass": "4", "wp": "6",
             "robot": "H2017", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "박스 흡착 그리퍼: 센서 전원 24V 1회로, 최대 0.1A / 포크형 그리퍼: 24V 1회로, 연속 0.8A, 피크 1A (사양서)",
                      "센서나 신호가 필요함": "박스 흡착 그리퍼: 진공 스위치 DI 1, 흡착 ON/OFF DO 1 / 포크형 그리퍼: 포크 위치 센서 DI 2, 열기·닫기 DO 2",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음(배선도 받음)", "공압이 필요함": "4bar, 호스 2개, 독립 유로 1개(흡착 ON/OFF)"},
        "memo": "",
    }),
    # 7) 협동 · 툴측 18kg → 자동 TCV3(25kg)
    ("샘플7_협동_대형부품이송", {
        "customer": "(가상) 서진금속", "writer": "오영업", "date": "2026-10-02",
        "q1": "화낙 CRX-30iA 1대 / 협동", "q2": "이송 — 프레스 가공한 브래킷 묶음을 적재대로 이송, 품번이 바뀌면 툴 교체",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 40 / 단위 %", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "자석 그리퍼", "qty": "1", "size": "450 × 300 × 180", "mass": "8", "wp": "10",
             "robot": "CRX-30iA", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
            {"name": "클램프 그리퍼", "qty": "1", "size": "400 × 250 × 200", "mass": "6", "wp": "8",
             "robot": "CRX-30iA", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "자석 그리퍼: 24V 1회로, 연속 0.9A, 피크 1A (전자석 사양서) / 클램프 그리퍼: 센서 전원 24V 1회로, 최대 0.1A",
                      "센서나 신호가 필요함": "자석 그리퍼: 흡착 확인 DI 1, 자석 ON/OFF DO 1 / 클램프 그리퍼: 클램프 위치 DI 2, 열기·닫기 DO 2",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음",
                      "공압이 필요함": "0.5MPa, 호스 2개, 복동 실린더, 로봇측 밸브, 그리퍼 1개, 추가 라인 없음"},
        "memo": "",
    }),
    # 8) 협동 · 툴측 32kg → 자동 계열 최대 30kg 초과(R09) → 다른 계열 M-LTC-0040A(40kg) 참고 + 협동용 범위 초과 검토
    ("샘플8_협동_고중량_범위초과", {
        "customer": "(가상) 태광산업", "writer": "윤영업", "date": "2026-10-02",
        "q1": "화낙 CR-35iB 1대 / 협동", "q2": "이송 — 주물 부품을 가공기에 넣고 빼기, 부품 종류에 따라 툴 교체",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 30 / 단위 %", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "주물 그리퍼 A", "qty": "1", "size": "500 × 400 × 300", "mass": "12", "wp": "20",
             "robot": "CR-35iB", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
            {"name": "주물 그리퍼 B", "qty": "1", "size": "450 × 350 × 300", "mass": "10", "wp": "15",
             "robot": "CR-35iB", "tilt": "없음", "power": "무", "air": "유", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "툴마다 센서 전원 24V 1회로, 최대 0.1A",
                      "센서나 신호가 필요함": "툴마다 클램프 위치 DI 2, 열기·닫기 DO 2",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음", "공압이 필요함": "5bar, 호스 2개, 독립 유로 2개(열기·닫기)"},
        "memo": "",
    }),
    # 9) 산업용 · 툴측 130kg → 기본 TCHK150 → 속도 45%(R05, 잠정 P01) +1단계 TCHK220
    ("샘플9_산업용_도어이송", {
        "customer": "(가상) 한성모터스", "writer": "강영업", "date": "2026-10-02",
        "q1": "화낙 R-2000iC/210F 1대 / 산업용", "q2": "이송 — 차체 도어를 행어에서 지그로 이송, 차종에 따라 툴 교체",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 45 / 단위 %", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "도어 그리퍼 (세단)", "qty": "1", "size": "1400 × 900 × 350", "mass": "60", "wp": "70",
             "robot": "R-2000iC", "tilt": "없음", "power": "유", "air": "유", "sensor": "유"},
            {"name": "도어 그리퍼 (SUV)", "qty": "1", "size": "1500 × 1000 × 350", "mass": "55", "wp": "60",
             "robot": "R-2000iC", "tilt": "없음", "power": "유", "air": "유", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "툴마다 24V 1회로, 연속 0.6A, 피크 0.9A (전자석 클램프 사양서)",
                      "센서나 신호가 필요함": "툴마다 도어 유무 센서 DI 3, 클램프 ON/OFF DO 2",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음", "공압이 필요함": "5bar, 호스 2개, 독립 유로 2개(열기·닫기)"},
        "memo": "",
    }),
    # 10) 산업용 · 툴측 260kg → TCHK220 초과(R09) → M-LTC 기본 0300G → 속도 40%(R05, 잠정 P01) +1단계 0630F
    ("샘플10_산업용_초고중량", {
        "customer": "(가상) 대한중공업", "writer": "임영업", "date": "2026-10-02",
        "q1": "쿠카 KR 500 R2830 1대 / 산업용", "q2": "이송 — 대형 철판 블록을 절단기에서 적치대로 이송, 블록 크기에 따라 툴 교체",
        "q3": "총 2개", "q4": "2쪽 표", "q5": "2쪽 표", "q6": "최대 40 / 단위 %", "q7": "없음", "q8": "2쪽 표",
        "q9": "툴체인저→컨트롤러 4m 충분 / 컨트롤러→PLC 1m 충분", "q10": "해당 없음",
        "tools": [
            {"name": "대형 자석 그리퍼", "qty": "1", "size": "2000 × 1200 × 400", "mass": "110", "wp": "150",
             "robot": "KR 500", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
            {"name": "중형 자석 그리퍼", "qty": "1", "size": "1500 × 1000 × 400", "mass": "90", "wp": "100",
             "robot": "KR 500", "tilt": "없음", "power": "유", "air": "무", "sensor": "유"},
        ],
        "followups": {"전기가 필요함": "툴마다 24V 1회로, 연속 0.9A, 피크 1A (전자석 컨트롤러 사양서)",
                      "센서나 신호가 필요함": "툴마다 흡착 확인 DI 2, 자석 ON/OFF DO 1",
                      "전기 또는 센서를 사용함": "DIO, 공통선 없음"},
        "memo": "",
    }),
]


# 샘플 N.1 — 같은 미팅에서 '해당하는 경우 추가 질문'에 빠짐없이 답한 판(AI 가 더 물을 추가 질문이 없어야 함).
# 포고핀 산정(R11)에 필요한 전원 회로·전류와 DI/DO 수까지 툴마다 적는다. 샘플3은 원본에서 이미 해당 추가 질문(정보를 모름·끼우기)에
# 다 답해 따로 만들지 않는다. 기울임이 있으면 무게중심 확인용 도면(F01)도 묻기 때문에 '정보를 모름' 줄(사진·도면 받기)에 답을 적는다.
FULL_FOLLOWUPS = {
    "샘플1_협동_사출품이송": {
        "전기가 필요함": "전동 그리퍼: 24V 1회로, 연속 0.6A, 피크 0.9A (사양서) / 진공 그리퍼: 센서 전원 24V 1회로, 최대 0.05A",
        "센서나 신호가 필요함": "전동 그리퍼: 열림·닫힘 확인 DI 2, 열기·닫기 명령 DO 2 / 진공 그리퍼: 진공 확인 스위치 DI 1, DO 0",
        "전기 또는 센서를 사용함": "DIO, 공통선 없음(배선도 받음)", "공압이 필요함": "4bar, 호스 1개 (이젝터), 독립 유로 1개"},
    "샘플2_산업용_차체패널": {
        "센서나 신호가 필요함": "툴마다 근접센서 4개·흡착 압력 스위치 1개 → DI 5, 전자석·흡착 ON/OFF → DO 2",
        "정보를 모름": "패널 그리퍼 A·B 사진·도면 받음(세운 자세 무게중심 확인용)"},
    "샘플4_압력경계_통신": {
        "센서나 신호가 필요함": "전동 그리퍼: 완료 신호 DI 1, DO 0(나머지는 RS-485 통신) / 진공 그리퍼: 진공 확인 센서 DI 1, 흡착 ON/OFF DO 1",
        "전기 또는 센서를 사용함": "전동 그리퍼: RS-485 2선식, 공통선 없음 / 진공 그리퍼: DIO, 공통선 없음",
        "정보를 모름": "진공 그리퍼 사진·도면 받음(기울임 자세 확인용)"},
    "샘플5_다수툴_혼합로봇": {
        "전기가 필요함": "흡착 그리퍼: 센서 전원 24V 1회로, 최대 0.05A / 핑거 그리퍼: 24V 1회로, 연속 0.5A, 피크 0.8A / 마그네틱 그리퍼: 24V 1회로, 연속 0.7A, 피크 0.9A",
        "센서나 신호가 필요함": "흡착 그리퍼: 진공 스위치 DI 1, DO 0 / 핑거 그리퍼: 열림·닫힘 DI 2, 열기·닫기 DO 2 / 마그네틱 그리퍼: DI 0, ON/OFF DO 1",
        "전기 또는 센서를 사용함": "DIO, 공통선 없음(0V 공용)"},
}


def full_samples() -> list[tuple[str, dict]]:
    out = []
    for name, ans in SAMPLES:
        if name in FULL_FOLLOWUPS:
            no, rest = name.split("_", 1)
            out.append((f"{no}.1_{rest}_추가질문완료", {**ans, "followups": {**ans["followups"], **FULL_FOLLOWUPS[name]}}))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    blank = OUT / "MAGBOT_툴체인저_첫고객미팅_질문지_양식.docx"
    build(blank)
    shutil.copy(blank, PUBLIC / "magbot_atc_meeting_form.docx")
    for name, ans in SAMPLES + full_samples():
        build(OUT / f"{name}.docx", ans)
    print("wrote", blank.name, "+", len(SAMPLES), "samples +", len(full_samples()), "full →", OUT)


if __name__ == "__main__":
    main()
