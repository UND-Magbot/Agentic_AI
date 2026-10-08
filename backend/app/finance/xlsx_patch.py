# -*- coding: utf-8 -*-
"""xlsx 셀 외과적 패치 — zip/XML 수준에서 특정 셀만 수정.

배경: openpyxl 의 load→save 라운드트립은 도형(drawings/VML), 외부 데이터 연결
(connections/queryTables), 스레드 댓글, customXml, 프린터 설정 등을 드롭·변형해
Excel 이 "복구/제거" 경고를 띄운다(원본 85개 엔트리 → 59개로 손실 실측).

해결: 복사본(shutil.copy2 로 원본과 바이트 동일)에서 **대상 시트 XML 한 엔트리만**
교체하고 나머지 zip 엔트리는 원본 바이트 그대로 복사한다. 기입할 셀이 없으면
파일을 아예 건드리지 않아 복사본이 원본과 100% 동일 → 손상 원천 차단.

지원: 숫자 셀(<v>) / 텍스트 셀(inlineStr). 기존 셀 스타일(s 속성) 보존.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def col_to_index(col: str) -> int:
    """열 문자 → 1-indexed 정수. 'A'→1, 'O'→15, 'AA'→27."""
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def split_ref(ref: str) -> tuple[str, int]:
    """'O10' → ('O', 10)."""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    if not m:
        raise ValueError(f"잘못된 셀 참조: {ref}")
    return m.group(1).upper(), int(m.group(2))


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt_num(v: float) -> str:
    if isinstance(v, bool):
        raise TypeError("bool 은 숫자 셀로 기입 불가")
    if float(v).is_integer():
        return str(int(v))
    return repr(float(v))


def _sheet_xml_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    """워크북에서 시트명 → 'xl/worksheets/sheetN.xml' 경로 해석."""
    wb_xml = zf.read("xl/workbook.xml").decode("utf-8")
    m = re.search(
        rf'<sheet[^>]*\bname="{re.escape(sheet_name)}"[^>]*\br:id="([^"]+)"',
        wb_xml,
    )
    if not m:
        # name/r:id 속성 순서가 반대인 경우도 시도.
        m = re.search(
            rf'<sheet[^>]*\br:id="([^"]+)"[^>]*\bname="{re.escape(sheet_name)}"',
            wb_xml,
        )
    if not m:
        raise KeyError(f"워크북에 시트 '{sheet_name}' 없음")
    rid = m.group(1)
    rels = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rm = re.search(rf'<Relationship[^>]*\bId="{re.escape(rid)}"[^>]*\bTarget="([^"]+)"', rels)
    if not rm:
        raise KeyError(f"rels 에 {rid} 타겟 없음")
    target = rm.group(1).lstrip("/")
    if not target.startswith("xl/"):
        target = "xl/" + target
    return target


def _style_attr(attrs: str) -> str:
    """셀 속성 문자열에서 스타일(s=) 만 보존해 반환(예: ' s=\"5\"')."""
    m = re.search(r'\bs="(\d+)"', attrs or "")
    return f' s="{m.group(1)}"' if m else ""


def _build_cell(ref: str, value, is_text: bool, style: str) -> str:
    if is_text:
        return (f'<c r="{ref}"{style} t="inlineStr">'
                f'<is><t xml:space="preserve">{_esc(value)}</t></is></c>')
    return f'<c r="{ref}"{style}><v>{_fmt_num(value)}</v></c>'


def _patch_one(xml: str, ref: str, value, is_text: bool) -> str:
    """시트 XML 에서 셀 ref 를 value 로 교체(없으면 행에 삽입). 변경된 XML 반환."""
    col, row = split_ref(ref)
    col_idx = col_to_index(col)

    # 1) 기존 셀이 있으면 교체(자체닫힘 / 내용 포함 모두).
    cell_re = re.compile(
        rf'<c r="{ref}"((?:\s+[\w:]+="[^"]*")*)\s*(?:/>|>.*?</c>)', re.DOTALL
    )
    m = cell_re.search(xml)
    if m:
        new_cell = _build_cell(ref, value, is_text, _style_attr(m.group(1)))
        return xml[:m.start()] + new_cell + xml[m.end():]

    # 2) 셀 없음 → 해당 행을 찾아 열 순서에 맞게 삽입.
    new_cell = _build_cell(ref, value, is_text, "")
    row_open = re.search(rf'<row r="{row}"((?:\s+[\w:]+="[^"]*")*)\s*(/?)>', xml)
    if row_open and row_open.group(2) != "/":
        # 행 내부에서 col_idx 보다 큰 첫 셀 앞에 삽입.
        row_start = row_open.end()
        row_end = xml.index("</row>", row_start)
        inner = xml[row_start:row_end]
        insert_at = len(inner)
        for cm in re.finditer(r'<c r="([A-Za-z]+)\d+"', inner):
            if col_to_index(cm.group(1)) > col_idx:
                insert_at = cm.start()
                break
        inner = inner[:insert_at] + new_cell + inner[insert_at:]
        return xml[:row_start] + inner + xml[row_end:]

    if row_open and row_open.group(2) == "/":
        # 빈 자체닫힘 행 → 셀 포함 행으로 확장.
        full = row_open.group(0)
        opened = full[:-2] + ">" + new_cell + "</row>"
        return xml[:row_open.start()] + opened + xml[row_open.end():]

    # 3) 행 자체가 없음 → sheetData 끝에 삽입(드문 경우, 순서 무관하게 추가).
    new_row = f'<row r="{row}">{new_cell}</row>'
    sd = xml.index("</sheetData>")
    return xml[:sd] + new_row + xml[sd:]


def _set_full_recalc(wb_xml: str) -> str:
    """workbook.xml 의 calcPr 에 fullCalcOnLoad='1' 보장(없으면 sheets 뒤 삽입)."""
    if "<calcPr" in wb_xml:
        def _fix(m: re.Match) -> str:
            tag = m.group(0)
            if "fullCalcOnLoad" in tag:
                return re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', tag)
            return tag[:-2].rstrip() + ' fullCalcOnLoad="1"/>' if tag.endswith("/>") \
                else tag.replace(">", ' fullCalcOnLoad="1">', 1)
        return re.sub(r"<calcPr\b[^>]*/?>", _fix, wb_xml, count=1)
    # calcPr 부재 → </sheets> 뒤에 삽입(스키마상 sheets 다음 위치 허용).
    if "</sheets>" in wb_xml:
        return wb_xml.replace(
            "</sheets>", '</sheets><calcPr calcId="0" fullCalcOnLoad="1"/>', 1
        )
    return wb_xml


def _clear_one(xml: str, ref: str) -> str:
    """셀 ref 의 값을 비운다(스타일 보존, 자체닫힘 빈 셀로). 없으면 그대로."""
    cell_re = re.compile(
        rf'<c r="{ref}"((?:\s+[\w:]+="[^"]*")*)\s*(?:/>|>.*?</c>)', re.DOTALL
    )
    m = cell_re.search(xml)
    if not m:
        return xml
    empty = f'<c r="{ref}"{_style_attr(m.group(1))}/>'
    return xml[:m.start()] + empty + xml[m.end():]


def _refresh_one(xml: str, ref: str, value) -> str:
    """수식 셀의 캐시값(<v>)만 올바른 계산값으로 교체(수식 <f> 보존).

    배경: 메인 복사본은 O(당일잔액)만 정적으로 채우고 N(증감액)·소계·합계는 수식
    (=O-M, =SUM(...))으로 둔 채 Excel 의 fullCalcOnLoad 재계산에 의존한다. 그런데
    입력 파일의 수식 캐시가 낡아(예: O 를 지운 상태로 저장돼 =O-M 가 -전일잔액으로 캐시)
    재계산을 하지 않는 뷰어에서는 그 음수 캐시가 그대로 보인다. → 종속 수식 셀의 캐시를
    올바른 값으로 갱신해 어떤 뷰어에서도 정상 표시되게 한다(수식은 보존, Excel 재계산도 그대로).

    셀이 없으면 무시. 수식이 없는 셀이면 숫자 셀로 기록(폴백).
    """
    cell_re = re.compile(
        rf'<c r="{ref}"((?:\s+[\w:]+="[^"]*")*)\s*(?:/>|>(.*?)</c>)', re.DOTALL
    )
    m = cell_re.search(xml)
    if not m:
        return xml
    attrs, inner = m.group(1) or "", m.group(2)
    style = _style_attr(attrs)
    if inner:
        fmatch = re.search(r"<f\b[^>]*(?:/>|>.*?</f>)", inner, re.DOTALL)
        if fmatch:
            new_cell = (f'<c r="{ref}"{style}>{fmatch.group(0)}'
                        f'<v>{_fmt_num(value)}</v></c>')
            return xml[:m.start()] + new_cell + xml[m.end():]
    new_cell = _build_cell(ref, value, is_text=False, style=style)  # 수식 없음 → 숫자
    return xml[:m.start()] + new_cell + xml[m.end():]


def patch_cells(
    xlsx_path: Path,
    sheet_name: str,
    num_updates: dict[str, float] | None = None,
    text_updates: dict[str, str] | None = None,
    clear: list[str] | None = None,
    formula_cache: dict[str, float] | None = None,
) -> int:
    """복사본의 한 시트에서 지정 셀들을 외과적으로 수정. 변경한 셀 수 반환.

    Args:
        xlsx_path: 수정할 .xlsx (반드시 복사본 — 호출 전 원본과 분리되어 있어야 함).
        sheet_name: 대상 시트명(예 '05-29').
        num_updates: {셀참조: 숫자} 매핑.
        text_updates: {셀참조: 문자열} 매핑.
        clear: 값을 비울 셀 참조 목록(스타일 보존). 신규 일자 시트 초기화 등에 사용.
        formula_cache: {셀참조: 값} — 수식 셀의 캐시값(<v>)만 갱신(수식 보존). O 변경에
            종속된 N·소계·합계의 낡은 캐시를 올바른 값으로 맞춰 비(非)재계산 뷰어 오표시 방지.

    Returns:
        변경한 셀 수(formula_cache 갱신은 '기입'이 아니므로 제외). 0 이면 파일 미변경.
    """
    num_updates = num_updates or {}
    text_updates = text_updates or {}
    clear = clear or []
    formula_cache = formula_cache or {}
    if not num_updates and not text_updates and not clear and not formula_cache:
        return 0  # 변경 없음 → 원본 바이트 동일 유지(손상 원천 차단)

    xlsx_path = Path(xlsx_path)
    with zipfile.ZipFile(xlsx_path) as zf:
        sheet_path = _sheet_xml_path(zf, sheet_name)
        xml = zf.read(sheet_path).decode("utf-8")
        infos = zf.infolist()
        data = {zi.filename: zf.read(zi.filename) for zi in infos}

    count = 0
    for ref in clear:
        xml = _clear_one(xml, ref)
        count += 1
    for ref, val in num_updates.items():
        xml = _patch_one(xml, ref, val, is_text=False)
        count += 1
    for ref, val in text_updates.items():
        xml = _patch_one(xml, ref, val, is_text=True)
        count += 1
    for ref, val in formula_cache.items():
        xml = _refresh_one(xml, ref, val)  # 수식 캐시 갱신(count 제외)
    data[sheet_path] = xml.encode("utf-8")

    # 값 셀(O)을 바꾸면 종속 수식(N=O-M, 소계/Total/일계표)의 캐시값이 낡으므로,
    # 워크북에 fullCalcOnLoad 를 켜서 Excel 이 열 때 전 수식을 재계산하게 한다.
    wb_key = "xl/workbook.xml"
    if wb_key in data:
        data[wb_key] = _set_full_recalc(data[wb_key].decode("utf-8")).encode("utf-8")

    tmp = xlsx_path.with_suffix(".xlsx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for zi in infos:
            zout.writestr(zi, data[zi.filename])
    tmp.replace(xlsx_path)
    return count
