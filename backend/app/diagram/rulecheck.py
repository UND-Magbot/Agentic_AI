"""요청 원문 ↔ 개념도 스펙 결정론 대조 — LLM 자기 판정에 기대지 않는 누락 검출.

실측(2026-09-28, gemma4:12b): LLM 이 항목을 14개만 뽑고(26b 는 22개) 스스로 "14/14 반영" 으로 판정해
불량배출 존·액 3.7kg·금요일 일정이 빠진 채 통과했다. 추출과 판정을 같은 모델이 하면 누락이 안 잡힌다.

그래서 원문에서 **글자로 확인 가능한 것**은 코드가 직접 뽑고 코드가 대조한다.
  - 수치+단위 (1.5kg, 210초), 인증 등급 (Class 100), 영문 장비·브랜드명 (Keyence, PLC)
  - 기한 (금주 금요일까지, 10/2)
  - 화살표(→, ->)로 이어진 공정 단계 — 단계 핵심어의 절반 이상이 스펙에 있어야 반영
  - 구조 규칙 — "불량/치우기" 가 있으면 불량 배출 설비, "로봇" 이 있으면 로봇 설비 등
보완 생성 후에도 남은 항목은 backfill() 이 스펙에 직접 넣는다 (조용히 빠지는 항목이 없게).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .spec import ConceptMapSpec, Flow, SpecTable, Station

# --- 원문 항목 --------------------------------------------------------------


@dataclass
class RuleItem:
    id: str
    kind: str                 # fact / question / deliverable / process / structure
    text: str                 # 사람이 읽는 항목명
    evidence: str             # 원문 인용
    must: list[str]           # 정규화 후 스펙 글자에 있어야 하는 토큰 (전부 또는 min_hits 개)
    min_hits: int = 0         # 0 이면 전부
    station_kind: str = ""    # structure 항목: 있어야 하는 설비 종류
    covered: bool = False
    auto_filled: bool = False


_UNITS = r"kg|g|t|초|분|시간|hr|h|sec|min|mm|cm|m|ea|대|개|kw|w|v|a|bar|mpa|°c|℃|%|l|ml|rpm|ppm"
# 단위 뒤에는 조사만 올 수 있다 — "4대를" 은 수량, "100 대응" 은 수량이 아니다.
_NUM_UNIT_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*({_UNITS})(?![a-z])(?![가-힣])|"
    rf"(\d+(?:[.,]\d+)?)\s*({_UNITS})(?=[을를이가은는에의와과도로만씩간])", re.IGNORECASE)
_CLASS_RE = re.compile(r"class\s*(\d+)", re.IGNORECASE)
_LATIN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9\-]{1,}\b")
_DEADLINE_RE = re.compile(
    r"((?:금주|이번\s*주|다음\s*주|차주)?\s*[월화수목금토일]요일(?:\s*까지)?"
    r"|\d{1,2}\s*월\s*\d{1,2}\s*일(?:\s*까지)?|\d{1,2}/\d{1,2}(?:\s*까지)?)"
)
_STEP_SPLIT_RE = re.compile(r"\s*(?:->|→|=>|⇒)\s*")

# 영문 토큰 중 장비명이 아닌 것 (단위·축·일반 약어).
_LATIN_STOP = {
    "x", "y", "z", "xy", "xyz", "kg", "ea", "mm", "cm", "sec", "min", "ok", "ng", "vs", "etc",
    "class", "the", "and", "or", "to", "of",
}
# 한글 표기 ↔ 영문 표기. 어느 쪽이든 스펙에 있으면 반영으로 본다.
_ALIASES: dict[str, tuple[str, ...]] = {
    "리얼센스": ("realsense",), "인텔": ("intel",), "키엔스": ("keyence",), "한화": ("hanwha",),
    "코그넥스": ("cognex",), "화낙": ("fanuc",), "두산": ("doosan",), "유니버설로봇": ("ur",),
    "realsense": ("리얼센스",), "intel": ("인텔",), "keyence": ("키엔스",), "hanwha": ("한화",),
    "cognex": ("코그넥스",), "fanuc": ("화낙",), "doosan": ("두산",),
}
# 공정 용어 동의어 묶음 — 원문 "뚜껑 잠그기" 가 스펙에 "캡 재체결" 로 쓰여도 반영으로 본다.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("뚜껑", "캡", "cap", "마개"),
    ("잠그기", "잠금", "체결", "재체결", "밀봉", "캐핑", "클로징"),
    ("풀기", "개봉", "분리", "해체", "디캐핑"),
    ("필링", "주입", "충전", "filling", "투여"),
    ("치우기", "배출", "제거", "리젝", "reject", "폐기"),
    ("불량", "실패", "ng"),
    ("액주입장비", "액주입장치", "주입장치", "주입장비", "주입존", "필러", "filler"),
    ("이동", "이송", "운반", "핸들링"),
    ("투입", "공급", "로딩", "인입"),
    ("센터", "센터링", "정렬", "위치보정", "좌표"),
    ("검출", "검사", "감지", "인식", "판정"),
    ("기울어져", "기울기", "기울어짐", "경사", "틸트"),
    ("보틀", "병", "bottle"),
    ("놓기", "안착", "거치", "내려놓기"),
    ("너트러너", "nutrunner", "너트 러너"),
)
_SYNONYMS: dict[str, tuple[str, ...]] = {w: g for g in _SYNONYM_GROUPS for w in g}
_KOREAN_BRANDS = ("리얼센스", "인텔", "키엔스", "한화", "코그넥스", "화낙", "두산")

# 구조 규칙: 원문 키워드 → 라인에 있어야 하는 설비 종류.
_STRUCTURE_RULES: tuple[tuple[str, str, str], ...] = (
    (r"불량|치우|리젝|reject", "reject", "불량 배출 설비"),
    (r"로봇|robot", "robot", "로봇 설비"),
    (r"뚜껑|캡|너트\s*러너|캐핑|capping", "capper", "캡 개봉·체결 설비"),
    (r"주입|필링|충전|filling", "filler", "주입 설비"),
)

# 공정 단계 핵심어에서 빼는 조사·어미·일반어.
_JOSA = ("으로", "에서", "에게", "까지", "부터", "하고", "해서", "하여", "한후", "한다", "하기",
         "해야함", "해야", "되는지", "하는", "되는", "한", "을", "를", "이", "가", "은", "는",
         "에", "의", "로", "와", "과", "도", "후", "시")
_STEP_STOP = {"확인", "경우", "기존", "사용", "진행", "작업", "잡은", "놓기", "하기", "잡은후"}


def _num_units(text: str) -> list[tuple[re.Match, str]]:
    """수치+단위 매치와 정규 토큰("1.5kg")."""
    out = []
    for m in _NUM_UNIT_RE.finditer(text):
        num = (m.group(1) or m.group(3)).replace(",", ".")
        out.append((m, num + (m.group(2) or m.group(4))))
    return out


def normalize(s: str) -> str:
    """대조용 정규화 — 소문자, 공백·구두점 제거, 쉼표 소수점 통일."""
    s = s.lower().replace(",", ".")
    s = s.replace("℃", "°c")
    return re.sub(r"[\s\-_·•:;()\[\]{}\"'`~!?/\\|<>]+", "", s)


def spec_text(spec: ConceptMapSpec) -> str:
    """스펙의 모든 글자(이미지 경로 제외)를 정규화해 이어 붙인다."""
    out: list[str] = []

    def walk(v, key: str = "") -> None:
        if isinstance(v, str):
            if key not in ("src", "symbol", "id", "at", "kind", "badge_tone", "style", "dst"):
                out.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)

    walk(spec.model_dump())
    return normalize(" ".join(out))


def _has(text: str, token: str) -> bool:
    t = normalize(token)
    if t in text:
        return True
    alts = _ALIASES.get(t, ()) + _SYNONYMS.get(t, ())
    return any(normalize(a) in text for a in alts)


def _strip_josa(word: str) -> str:
    for j in _JOSA:
        if len(word) > len(j) + 1 and word.endswith(j):
            return word[: -len(j)]
    return word


def _step_keywords(segment: str) -> list[str]:
    words = re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9]+", segment)
    out = []
    for w in words:
        w = _strip_josa(w)
        if len(w) >= 2 and w not in _STEP_STOP and w.lower() not in _LATIN_STOP and w not in out:
            out.append(w)
    return out


def extract(request: str) -> list[RuleItem]:
    """요청 원문 → 글자로 확인 가능한 항목."""
    items: list[RuleItem] = []
    seen: set[str] = set()

    def add(kind: str, text: str, evidence: str, must: list[str], **kw) -> None:
        key = kind + "|" + "|".join(normalize(m) for m in must) + kw.get("station_kind", "")
        if key in seen:
            return
        seen.add(key)
        items.append(RuleItem(id=f"r{len(items) + 1}", kind=kind, text=text,
                              evidence=evidence.strip(), must=must, **kw))

    for m, tok in _num_units(request):
        add("fact", tok, _around(request, m.start(), m.end()), [tok])
    for m in _CLASS_RE.finditer(request):
        add("question", f"Class {m.group(1)}", _around(request, m.start(), m.end()),
            [f"class{m.group(1)}"])
    for m in _LATIN_RE.finditer(request):
        w = m.group(0)
        if w.lower() in _LATIN_STOP or _NUM_UNIT_RE.fullmatch(w):
            continue
        add("fact", w, _around(request, m.start(), m.end()), [w])
    for b in _KOREAN_BRANDS:
        i = request.find(b)
        if i >= 0:
            add("fact", b, _around(request, i, i + len(b)), [b])
    for m in _DEADLINE_RE.finditer(request):
        d = re.sub(r"\s+", " ", m.group(1)).strip()
        # 요일 단어만 핵심으로 본다 — "금주 금요일까지" 는 "금요일" 이 있으면 반영.
        core = re.search(r"[월화수목금토일]요일|\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}/\d{1,2}", d)
        add("deliverable", f"일정: {d}", d, [core.group(0) if core else d])

    segments = [s for s in _STEP_SPLIT_RE.split(request) if s.strip()]
    if len(segments) >= 2:
        for seg in segments:
            # 한 단계 안에 문장이 이어지면 첫 문장만 단계로 본다.
            head = re.split(r"[.\n]", seg.strip())[0]
            kws = _step_keywords(head)
            if len(kws) >= 2:
                add("process", f"공정: {head[:30]}", head, kws, min_hits=(len(kws) + 1) // 2)

    low = request.lower()
    for pat, kind, label in _STRUCTURE_RULES:
        m = re.search(pat, low)
        if m:
            add("structure", label, _around(request, m.start(), m.end()), [], station_kind=kind)
    return items


_CLAUSE_END_RE = re.compile(r"[,.\n]|->|→|=>")


def _decimal_mark(text: str, m: re.Match) -> bool:
    """1.5 / 3,7 처럼 숫자 사이의 구두점은 구절 경계가 아니다."""
    i = m.start()
    return (m.group(0) in ",." and 0 < i < len(text) - 1
            and text[i - 1].isdigit() and text[i + 1].isdigit())


def _around(text: str, start: int, end: int) -> str:
    """매치가 든 구절(쉼표·마침표·화살표 사이)을 원문 근거로 쓴다."""
    left = max((m.end() for m in _CLAUSE_END_RE.finditer(text, 0, start)
                if not _decimal_mark(text, m)), default=0)
    right = next((m.start() for m in _CLAUSE_END_RE.finditer(text, end)
                  if not _decimal_mark(text, m)), len(text))
    clause = text[left:right].strip()
    if len(clause) <= 40:
        return clause
    # 긴 구절은 매치 앞뒤 단어만 남긴다.
    a = text.rfind(" ", left, max(left, start - 12)) + 1 or left
    b = text.find(" ", min(right, end + 12), right)
    return text[a: b if b > 0 else right].strip()


# --- 대조 -------------------------------------------------------------------


def verify(spec: ConceptMapSpec, items: list[RuleItem]) -> None:
    text = spec_text(spec)
    kinds = {s.kind for s in spec.line_layout.stations} if spec.line_layout else set()
    callout_text = normalize(" ".join(" ".join(c.lines) for c in spec.line_layout.callouts)) \
        if spec.line_layout else ""
    for it in items:
        if it.kind == "structure":
            # 로봇은 설비로 안 그리고 콜아웃(사양 박스)으로만 달아도 드러난 것으로 본다.
            it.covered = it.station_kind in kinds or (
                it.station_kind == "robot" and ("로봇" in callout_text or "robot" in callout_text))
            continue
        hits = sum(_has(text, m) for m in it.must)
        need = it.min_hits or len(it.must)
        it.covered = hits >= need


def cross_check(spec: ConceptMapSpec, llm_text: str) -> bool:
    """LLM 이 '반영' 으로 판정한 항목의 숫자·영문 토큰이 실제로 스펙에 있는지.

    토큰이 없으면(순수 한글 항목) 판정을 그대로 믿는다.
    """
    text = spec_text(spec)
    toks = [tok for _, tok in _num_units(llm_text)]
    toks += [w for w in _LATIN_RE.findall(llm_text) if w.lower() not in _LATIN_STOP]
    return all(_has(text, t) for t in toks)


# --- 요청에 없는 수치 가림 ---------------------------------------------------
# 사례집·확정본을 참고 자료로 넣으면 다른 기업의 수치(KPI·C/T·중량)가 개념도로 옮겨질 수 있다.
# 프롬프트 금지만으로는 모델이 지킨다는 보장이 없어 코드가 가린다.

_COUNT_UNITS = {"ea", "대", "개"}
_MASK = "(확인 필요)"


def _num(tok: str) -> tuple[float, str]:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(.+)", normalize(tok))
    return (float(m.group(1)), m.group(2)) if m else (float("nan"), "")


def _allowed_numbers(spec: ConceptMapSpec, request: str) -> set[str]:
    """요청 수치 + 같은 단위끼리의 합계 + 그림 속 개수(카메라 수, 1)."""
    req = [_num(t) for _, t in _num_units(request)]
    allowed = {normalize(t) for _, t in _num_units(request)}
    for i, (a, ua) in enumerate(req):
        for b, ub in req[i + 1:]:
            if ua == ub:
                allowed.add(normalize(f"{a + b:g}{ua}"))
    n_cams = len(spec.line_layout.cameras) if spec.line_layout else 0
    for n in {1, n_cams} - {0}:
        allowed |= {f"{n}{u}" for u in _COUNT_UNITS}
    return allowed


def mask_unsupported_numbers(spec: ConceptMapSpec, request: str) -> tuple[ConceptMapSpec, list[str]]:
    """요청 원문에 근거 없는 수치+단위를 '(확인 필요)' 로 바꾼 스펙과 바꾼 토큰 목록."""
    allowed = _allowed_numbers(spec, request)
    masked: list[str] = []

    def fix(s: str) -> str:
        out, last = [], 0
        for m, tok in _num_units(s):
            if normalize(tok) in allowed:
                continue
            out.append(s[last:m.start()] + _MASK)
            last = m.end()
            masked.append(tok)
        return "".join(out) + s[last:] if out else s

    def walk(v, key: str = ""):
        if isinstance(v, str):
            return v if key in ("src", "symbol", "id", "at", "kind", "badge_tone", "style") else fix(v)
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return v

    fixed = ConceptMapSpec.model_validate(walk(spec.model_dump()))
    return fixed, list(dict.fromkeys(masked))


# --- 참고 사례 표현 유출 제거 ------------------------------------------------
# 2026-09-28 실측: 약액 병 요청에 2024-15 '세라믹 제품 검사공정' 을 참고시키자 제목이
# "세라믹 카트리지/보틀 …" 로 나옴. 사례 제목의 업종 명사가 요청에 없는데 결과에 있으면 지운다.

_CASE_TERM_GENERIC = {
    "공정", "생산", "생산공정", "자동화", "로봇", "시스템", "검사", "검사공정", "제조", "라인", "조립",
    "제품", "구축", "활용", "도입", "작업", "개선", "위한", "제작", "공급", "적재", "이송", "가공",
}


def case_terms(case_titles: list[str], request: str) -> list[str]:
    """사례 제목의 고유 단어 중 요청 원문에 없는 것."""
    req = normalize(request)
    out: list[str] = []
    for title in case_titles:
        for w in _step_keywords(title):
            if w in _CASE_TERM_GENERIC or len(w) < 2 or normalize(w) in req or w in out:
                continue
            out.append(w)
    return out


def strip_case_terms(spec: ConceptMapSpec, request: str,
                     case_titles: list[str]) -> tuple[ConceptMapSpec, list[str]]:
    """결과 글자에서 요청에 없는 사례 고유 단어를 지운 스펙과 지운 단어 목록."""
    terms = case_terms(case_titles, request)
    if not terms:
        return spec, []
    pat = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)))
    removed: list[str] = []

    def fix(s: str) -> str:
        hits = pat.findall(s)
        if not hits:
            return s
        removed.extend(hits)
        return re.sub(r"\s{2,}", " ", pat.sub("", s)).strip(" /·-")

    def walk(v, key: str = ""):
        if isinstance(v, str):
            return v if key in ("src", "symbol", "id", "at", "kind", "badge_tone", "style") else fix(v)
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return v

    fixed = ConceptMapSpec.model_validate(walk(spec.model_dump()))
    return fixed, list(dict.fromkeys(removed))


# --- 강제 보완 --------------------------------------------------------------

_REVIEW_TITLE = "검토 사항"


def review_table(spec: ConceptMapSpec) -> SpecTable:
    for t in spec.tables:
        if t.title.replace(" ", "") == _REVIEW_TITLE.replace(" ", "") and len(t.headers) >= 3:
            return t
    t = SpecTable(title=_REVIEW_TITLE, headers=["항목", "내용", "상태"], rows=[])
    spec.tables.append(t)
    return t


def _add_station(spec: ConceptMapSpec, kind: str) -> None:
    line = spec.line_layout
    if line is None:
        return
    ids = {s.id for s in line.stations}
    sid = kind if kind not in ids else f"{kind}_auto"
    label = {"reject": "불량 배출", "robot": "로봇", "capper": "캡 개봉·체결",
             "filler": "주입 장치"}.get(kind, kind)
    st = Station(id=sid, kind=kind, label=label, sublabel="(요청 원문 기준 추가)",
                 width=0.8, show_zone_label=True)
    # 불량 배출은 주입 설비 바로 뒤, 나머지는 배출 앞에 둔다.
    anchor = next((i for i, s in enumerate(line.stations) if s.kind == "filler"), None)
    if kind == "reject" and anchor is not None:
        line.stations.insert(anchor + 1, st)
        line.flows.append(Flow(src=line.stations[anchor].id, dst=sid, style="dashed",
                               label="불량 시"))
    else:
        out = next((i for i, s in enumerate(line.stations) if s.kind == "outfeed"),
                   len(line.stations))
        line.stations.insert(out, st)


def backfill(spec: ConceptMapSpec, items: list[RuleItem]) -> list[str]:
    """남은 누락을 스펙에 직접 넣는다. 넣은 항목명 목록을 돌려준다."""
    filled: list[str] = []
    notes: list[str] = []
    for it in items:
        if it.covered:
            continue
        if it.kind == "structure":
            _add_station(spec, it.station_kind)
        elif it.kind in ("fact", "deliverable"):
            notes.append(it.evidence if it.kind == "fact" else it.text)
        else:  # question / process
            review_table(spec).rows.append([it.text.removeprefix("공정: "), it.evidence[:40],
                                             "확인 필요"])
        it.covered = it.auto_filled = True
        filled.append(it.text)
    if notes:
        extra = "※ 요청 원문: " + " / ".join(dict.fromkeys(n.strip() for n in notes))
        spec.footnote = f"{spec.footnote} | {extra}" if spec.footnote else extra
    return filled


@dataclass
class RuleReport:
    items: list[RuleItem] = field(default_factory=list)

    @property
    def missing(self) -> list[RuleItem]:
        return [i for i in self.items if not i.covered]

    @property
    def auto_filled(self) -> list[str]:
        return [i.text for i in self.items if i.auto_filled]
