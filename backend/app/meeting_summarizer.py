"""회의 전사문 → 주간 회의록 2개 섹션 요약 (vLLM).

STT 로 만든 전사문을 자체 호스팅 vLLM(Qwen)으로 요약해 회의록 양식의 두 칸:
- 회의 주요 내용
- 지시사항
을 채울 한국어 텍스트로 변환한다. 모든 LLM 호출은 Model Router(vllm_client)를
거친다(req.md §12-4).

긴 전사문(모델 컨텍스트 초과) 대응: map-reduce.
1) map  — 전사문을 청크로 나눠 청크별 핵심 불릿 메모 생성.
2) reduce — 메모를 합쳐 최종 2개 섹션 생성.
전사문이 한 청크에 들어가면 map 단계를 건너뛰고 곧장 reduce.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .company_context import build_llm_context_block
from .config import settings
from .llm import stream_chat

# 회사 컨텍스트 블록 — 모듈 로드 시 한 번 빌드. 회의 요약 시 system prompt 부록으로
# 덧붙여, STT 가 받아쓴 변형 표기(맥뽓·맥보트·오토씽 등)를 회사 정규 명칭으로
# 통일하고, 회의 주제를 회사 사업(맥봇 PoC·AMR 관제 등) 맥락으로 해석하도록 돕는다.
_CONTEXT_BLOCK = build_llm_context_block()

logger = logging.getLogger("meeting_summarizer")

# 단일 호출 / 청크 한도를 settings.max_model_len 기반으로 동적 산출.
# 한국어 ~1.5자/토큰 기준, (max_model_len - sys 1000tok - out 1500tok - safety 128tok) * 1.5자.
# max_model_len=8192 면 기존 하드코딩(8000/2500)과 동일하게 유지(min 캡)하고,
# 4096 등 작은 값이면 자동으로 줄여 전사문 무음 절단을 방지한다(감사 발견 대응).
def _compute_char_budgets(max_model_len: int) -> tuple[int, int]:
    budget_tokens = max(max_model_len - 1000 - 1500 - 128, 512)
    single = min(8000, int(budget_tokens * 1.5))
    chunk = min(2500, single)
    return single, chunk


_SINGLE_CALL_MAX_CHARS, _CHUNK_CHARS = _compute_char_budgets(settings.max_model_len)
if settings.max_model_len < 8192:
    logger.warning(
        "[summarize] max_model_len=%d < 8192 — 긴 회의 전사가 잘릴 수 있어 한도를 축소했습니다 "
        "(single_call=%d자, chunk=%d자). 품질을 위해 vLLM/MAX_MODEL_LEN 을 8192 로 권장.",
        settings.max_model_len, _SINGLE_CALL_MAX_CHARS, _CHUNK_CHARS,
    )

# 섹션 구분 마커 — LLM 출력 파싱용. 일반 한국어 본문엔 등장하지 않는 형태.
_MAIN_MARKER = "[회의 주요 내용]"
_DIRECTIVES_MARKER = "[지시사항]"


@dataclass
class MeetingSummary:
    """요약 결과. 두 필드는 그대로 xlsx 의 D5/D13 에 들어간다."""

    main_content: str
    directives: str


# ── 청크 분할 (순수 함수, 테스트 가능) ─────────────────────────────────────
def split_transcript(text: str, max_chars: int = _CHUNK_CHARS) -> list[str]:
    """전사문을 max_chars 이하 청크로 분할. 문장 경계(마침표/물음표/줄바꿈) 우선."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # max_chars 부근에서 가장 가까운 문장 경계로 후퇴.
            window = text[start:end]
            cut = max(
                window.rfind(". "), window.rfind("? "),
                window.rfind("! "), window.rfind("\n"),
            )
            # 경계가 청크 절반 이후에 있을 때만 사용(너무 짧은 청크 방지).
            if cut > max_chars // 2:
                end = start + cut + 1
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


# ── Markdown 강조 제거 (순수 함수) ─────────────────────────────────────────
# LLM 이 가끔 '**굵게**', '## 제목', '`코드`' 같은 markdown 을 출력하지만 Excel 셀에서는
# 그대로 별표/샵으로 노출되어 가독성이 떨어진다. 표시용 기호만 벗기고 텍스트는 보존.
_MD_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_MD_BOLD_UNDER_RE = re.compile(r"__([^_\n]+?)__")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_MD_ITALIC_UNDER_RE = re.compile(r"(?<![A-Za-z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-z0-9_])")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
# 단독 별표/샵으로 시작하는 줄(LLM 이 가끔 '*' / '##' 만 남기는 경우).
_LEADING_MARKER_RE = re.compile(r"^[\s]*[#*]+[\s]*$", re.MULTILINE)
# 3줄 이상 연속 공백 줄 → 1줄로 정리. Excel 셀에서 시각적 공백이 과하게 보이는 것 방지.
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _strip_markdown(text: str) -> str:
    if not text:
        return text
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_BOLD_UNDER_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_ITALIC_UNDER_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _LEADING_MARKER_RE.sub("", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


# ── 섹션 파싱 (순수 함수, 테스트 가능) ─────────────────────────────────────
# LLM 이 마커를 markdown 으로 감싸 출력하는 변형(2026-05-21 관측):
#   '**[회의 주요 내용]**' / '## [회의 주요 내용]' / '[회의 주요 내용]:'
# 이를 표준 마커로 정규화한 뒤 본문 파싱에 들어간다.
# 주의: 마커 앞뒤 markdown 표기는 같은 줄 안에서만 흡수한다(\s* 가 newline 까지 먹으면
# 직전 줄의 inline-code 닫는 backtick 을 잘못 잡아먹는 사고가 있어 [ \t]* 으로 제한).
_MARKER_VARIANT_MAIN = re.compile(
    r"(?:[*_#`]{1,3}[ \t]*)?\[\s*회의\s*주요\s*내용\s*\](?:[ \t]*[:：])?(?:[ \t]*[*_#`]{1,3})?"
)
_MARKER_VARIANT_DIR = re.compile(
    r"(?:[*_#`]{1,3}[ \t]*)?\[\s*지시\s*사항\s*\](?:[ \t]*[:：])?(?:[ \t]*[*_#`]{1,3})?"
)


def _normalize_markers(text: str) -> str:
    """마커 변형(`**[회의 주요 내용]**`, `## [지시사항]` 등)을 표준 마커로 통일."""
    text = _MARKER_VARIANT_MAIN.sub(_MAIN_MARKER, text)
    text = _MARKER_VARIANT_DIR.sub(_DIRECTIVES_MARKER, text)
    return text


def parse_sections(llm_output: str) -> MeetingSummary:
    """LLM 출력에서 두 섹션을 분리.

    기대 형식:
        [회의 주요 내용]
        ...
        [지시사항]
        ...

    마커가 없으면 전체를 main_content 로, directives 는 안내 문구로 채운다.
    """
    text = _normalize_markers((llm_output or "").strip())
    di = text.find(_DIRECTIVES_MARKER)
    mi = text.find(_MAIN_MARKER)

    if di >= 0:
        main_part = text[:di]
        directives = text[di + len(_DIRECTIVES_MARKER):].strip()
    else:
        main_part = text
        directives = ""

    if mi >= 0 and (di < 0 or mi < di):
        main_content = main_part[mi + len(_MAIN_MARKER):].strip()
    else:
        main_content = main_part.strip()

    # 마커 잔재(혹시 본문에 한 번 더 등장) 제거.
    main_content = main_content.replace(_MAIN_MARKER, "").strip()
    directives = directives.replace(_DIRECTIVES_MARKER, "").strip()

    # Markdown 강조 기호 제거 — Excel 셀에는 평문이 들어가야 하므로 '**굵게**' 같은
    # 표기가 그대로 별표로 노출되는 것을 막는다. prompt 에 금지를 명시했지만 LLM 이
    # 가끔 흘리므로 안전망. 한국어 본문은 ** 를 사용할 일이 없어 false positive 위험 없음.
    main_content = _strip_markdown(main_content)
    directives = _strip_markdown(directives)

    # '별도 지시사항 없음' 줄 제거 — LLM 이 번호('1.')·불릿('-')을 붙이거나,
    # 실제 지시 항목과 함께 이 문구를 덧붙이는 경우가 있다. 줄 단위로 제거하고
    # 남는 내용이 없으면 아래에서 안내 문구로 대체된다.
    _no_dir_re = re.compile(r"^[\s\d.)\-]*\(?\s*별도\s*지시사항\s*없")
    directives = "\n".join(
        ln for ln in directives.splitlines() if not _no_dir_re.match(ln.strip())
    ).strip()

    if not directives:
        directives = "(녹취에서 별도 지시사항이 확인되지 않았습니다.)"
    if not main_content:
        main_content = "(요약 생성에 실패했습니다. 녹음 상태를 확인해 주세요.)"
    return MeetingSummary(main_content=main_content, directives=directives)


# ── vLLM 호출 헬퍼 ─────────────────────────────────────────────────────────
# map 은 0.4(빈 응답으로 즉시 EOS 가는 경로를 줄이기 위해 약간 다양성 부여),
# reduce 는 0.3 유지(최종 출력 안정성). 둘 다 < 0.1 이면 어려운 입력에서 즉시 빈 응답.
_MAP_TEMPERATURE = 0.4
_REDUCE_TEMPERATURE = 0.3


async def _collect(
    system_prompt: str,
    user_text: str,
    *,
    temperature: float = _REDUCE_TEMPERATURE,
) -> str:
    """stream_chat 를 끝까지 모아 단일 문자열로 반환."""
    parts: list[str] = []
    async for delta in stream_chat(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_text}],
        temperature=temperature,
    ):
        parts.append(delta)
    return "".join(parts).strip()


_MAP_SYSTEM = (
    "당신은 회의록 정리 보조입니다. 아래는 음성 인식으로 만든 회의 녹취의 일부이며 "
    "오탈자나 잘못 인식된 단어가 섞여 있을 수 있습니다. 전체 맥락을 고려해 이 부분에서 "
    "논의된 핵심 사항을 한국어 불릿(-)으로 간결히 정리하세요.\n"
    "필수 출력 규칙(반드시 준수):\n"
    "- 녹취 일부에 회의 관련 내용(발표·논의·결정·지시·공유)이 한 문장이라도 있으면, "
    "반드시 최소 1개 이상의 불릿(-)을 출력한다. 빈 응답을 내지 않는다.\n"
    "- 이 부분이 인사말·잡담·도입부뿐이라 정리할 내용이 정말 없으면 정확히 한 줄로 "
    "'- (이 부분은 잡담/도입부)' 를 출력한다.\n"
    "환각 차단 규칙(절대 위반 금지):\n"
    "- 녹취에 실제로 나온 내용만 적는다. 녹취에 없는 회사명·부서명·제품명·인명·"
    "숫자(매출, 비율, 일정 등)를 지어내지 않는다.\n"
    "- 아래 [명칭 정규화 사전] 은 표기 통일 용도다. 사전에 있는 파트너 로봇·제품 "
    "라인업의 역할·납품·업데이트·전략 같은 사실을 녹취에 없는데 회의 내용으로 "
    "추가하는 것은 환각이며 금지. 사전은 '들렸을 때 어떻게 적느냐' 에만 쓴다.\n"
    "- 녹취가 불분명해 구체값을 알 수 없으면 모호한 표현(예: '국내외 전시회 참가 "
    "성과 공유')으로만 적고, 추측한 구체적 사실로 채우지 않는다.\n"
    "- 잡담·인사말은 제외한다(요약 대상 아님). 불릿 외 다른 말은 출력하지 않는다.\n\n"
    + _CONTEXT_BLOCK
)

# map 1차 실패(1자 빈 응답 등) 시 사용하는 완화 프롬프트. 1차의 '엄격 사실 규칙' 으로
# 모델이 자기검열해 즉시 EOS 로 끝나는 사고를 회피한다. 회의록 정확도는 reduce 단계의
# 동일 규칙으로 보장되므로, 여기서는 '청크에 등장한 키워드/주제어' 위주의 단순 나열만
# 요구해 빈 응답 가능성을 최소화한다.
_MAP_SYSTEM_FALLBACK = (
    "다음은 회의 녹취의 일부입니다. 음성 인식 오류가 섞여 있을 수 있지만, 이 부분에서 "
    "언급된 주제·키워드·논의 흐름을 한국어 불릿(-)으로 5~10줄 정도 단순 나열하세요. "
    "정확한 사실 정리는 다음 단계에서 수행하므로, 여기서는 '무엇이 언급됐는지' 만 "
    "빠짐없이 나열하면 됩니다. 반드시 한 개 이상의 불릿을 출력하세요. "
    "불릿 외 다른 말은 출력하지 않습니다."
)

_REDUCE_SYSTEM = (
    "당신은 회사 주간 회의록을 작성하는 보조입니다. 아래 회의 전사문(음성 인식 결과)을 "
    "읽고 주간 회의록 보고 양식의 두 항목을 충실히 작성하세요.\n\n"
    "[전사문 특성]\n"
    "- 입력 텍스트는 1시간 가까운 회의를 음성 인식한 결과로, 오인식(잘못 받아쓴 단어)이 "
    "곳곳에 섞여 있다. 어색한 단어가 보여도 앞뒤 맥락으로 '실제 회의에서 무슨 주제를 "
    "이야기한 것인지' 의미 단위로 추론해 정리한다.\n"
    "- 예: '낙지'·'경수고'·'재화' 같이 어색한 단어가 보여도, 회사 사업 맥락에서 의미가 "
    "통하는 주제(전시회·품질/가격·시장 진출 등)로 추론해 자연스러운 한국어로 정리한다. "
    "어색한 단어를 그대로 적지 말고 추론한 의미로 표현한다.\n\n"
    "[출력 형식]\n"
    "출력은 다음 두 머리표로 정확히 두 부분으로 나눈다.\n"
    f"{_MAIN_MARKER}\n"
    f"{_DIRECTIVES_MARKER}\n"
    f"- {_MAIN_MARKER} 아래: 회의에서 논의·공유·발표·결정된 내용을 정리한다. 주제별로 "
    "'1.', '2.' 번호를 매겨 짧은 명사형 제목을 적고, 각 주제의 세부 내용은 그 아래 "
    "'- ' 불릿으로 적는다.\n"
    f"- {_DIRECTIVES_MARKER} 아래: 회의에서 지시·요청·결정·당부된 실행 항목을 같은 "
    "방식으로 정리한다. 즉 '1.' 번호로 묶음 제목을 적고 그 아래 '- ' 세부 불릿을 둔다. "
    "평면적으로 '1. 2. 3.' 만 나열하지 않는다. 실행 항목이 정말 없으면 '(별도 지시사항 "
    "없음)' 한 줄만 적는다.\n\n"
    "[문체 규칙 — 가장 중요. 위반하면 회의록 작성 실패로 본다]\n"
    "이 항목은 담당자가 직접 작성한 정답 회의록의 톤과 일치시키기 위한 핵심 규칙이다.\n"
    "- 줄글로 풀어 쓰지 않는다. 짧은 명사구·명사형 종결로 끊어 적는다.\n"
    "- 평서형 종결('~다.', '~한다.', '~했다.', '~이다.', '~필요하다.', '~된다.', "
    "'~합니다.', '~입니다.') 을 절대 쓰지 않는다.\n"
    "- 권장 종결 형태: 명사 그대로 끝내거나(예: '인원 충원 예정', '품질 검토 필요'), "
    "'~ㅁ'·'~함'·'~필요'·'~중요'·'~예정'·'~검토'·'~확보'·'~논의'·'~계획' 같은 "
    "명사화 종결을 사용한다.\n"
    "  좋은 예: '제품의 신뢰성 품질에 대한 판단: 고객이 아닌 우리 스스로 하는것'\n"
    "  좋은 예: '6월1일, 2명 인원 합류 외 계속해서 필요 인원 충원 예정'\n"
    "  좋은 예: '업무 능률 향상을 위한 생성형 AI 활용'\n"
    "  나쁜 예: '회사를 더 경쟁력 있게 만들기 위한 노력을 지속한다.' (평서형 — 금지)\n"
    "  나쁜 예: '국내외 시장에서의 성공을 위해 다양한 요소들을 고려해야 한다.' (평서형 — 금지)\n"
    "- 콜론(':') 구조 적극 활용: '핵심 개념: 보조 설명' 형식으로 한 줄을 짧게 끊는다.\n"
    "- 한 불릿은 가능하면 30~80자 안에서 끝낸다. 한 줄에 두 문장 이상 넣지 않는다.\n"
    "- 카테고리 제목(번호 항목) 사이에는 빈 줄을 한 줄 둔다(시각 구분).\n\n"
    "[사실 보존 우선순위 — 추상 일반화 금지]\n"
    "- 회의에 등장한 구체적 사실(날짜·인원수·부서명·금액·일정·고유명사) 은 다른 어떤 "
    "내용보다 우선해 보존한다. 예: '6월1일 2명 인원 합류', '5/30 마감', '경주외동팀'.\n"
    "- 추상적 일반론('성공을 위해 다양한 요소를 고려', '경쟁력 강화를 위한 노력') 으로 "
    "구체 사실을 대체하지 않는다. 회의에 구체값이 있으면 반드시 그대로 적는다.\n\n"
    "[분량 가이드]\n"
    f"- 1시간 회의에는 보통 4~6개 주제가 등장한다. {_MAIN_MARKER} 는 최소 3개 이상의 "
    "번호 항목으로 정리한다. 주제 1~2개로 줄여 짧게 끝내는 것은 회의록 작성 실패다.\n"
    "- 각 주제마다 2~4줄의 세부 불릿(-) 으로 구체화한다. 제목 한 줄만 적고 끝내지 "
    "않는다.\n"
    f"- {_DIRECTIVES_MARKER} 는 '금주 예정 사항' 처럼 묶음 제목 1~3개로 그룹화하고, "
    "각 묶음 아래 '- ' 불릿으로 실제 지시/실행 항목을 둔다.\n"
    "- '번호 항목 + 세부 불릿' 양식의 빈 자리표시('주제', '세부 내용' 같은 더미)는 "
    "절대 출력하지 않는다. 항상 전사문에서 추론한 실제 내용으로 채운다.\n"
    "- 전사문 마지막 1/3 구간(회의 마무리)에도 중요한 내용(국내외 진출 계획·신규 "
    "사이트·중국·독일·미국 같은 글로벌 확장·다음 주차 일정 등)이 자주 들어간다. "
    "끝부분을 가볍게 보고 누락하지 말고, 전사문 전체를 균형 있게 다룬다.\n\n"
    "[출력 표기 규칙]\n"
    "- Markdown 강조 기호(**, __, ##, ` 등) 를 절대 사용하지 않는다. Excel 셀에 들어가는 "
    "평문이므로, '**굵게**' 가 아니라 그대로 '굵게' 라고 쓰면 된다. 번호('1.')와 "
    "불릿('- ') 외 다른 서식 기호는 출력하지 않는다.\n\n"
    "[환각 차단 — 반드시 준수]\n"
    "- 전사문에 없는 회사명·부서명·제품명·인명·숫자(매출·비율·일정 등)를 지어내지 "
    "않는다. 단, '의미 단위 추론' 은 환각이 아니다. 오인식된 단어를 자연스러운 한국어 "
    "표현으로 정리하는 것은 정상 작업이다.\n"
    "- 아래 [명칭 정규화 사전] 은 '들렸을 때 어떻게 쓰느냐' 의 표기 통일용이다. "
    "사전에 적힌 파트너 로봇·제품 라인업의 사업 사실(역할·납품·소프트웨어 업데이트·"
    "모델 출시·설계 변경 등) 을 전사문에 없는데 회의 내용으로 추가하는 것은 환각이며 "
    "금지. 예: 전사문에 'AutoXing AMR 새 모델' 발언이 없는데 '납품 라인업 업데이트: "
    "AutoXing AMR 새 모델 설계 변경' 항목을 만드는 것 — 금지.\n"
    "- 잡담·인사말·끝맺음('수고하셨습니다' 등) 은 회의록에서 제외한다.\n"
    "- 두 머리표 외에 다른 머리말·설명·메타 발언을 덧붙이지 않는다.\n\n"
    "[톤 예시 — 정답 회의록의 형태]\n"
    "다음은 담당자가 직접 작성한 정답 회의록의 발췌다. 톤·구조·종결 형식을 그대로 "
    "따른다(내용은 새 회의에 맞춰 다시 쓴다).\n"
    f"{_MAIN_MARKER}\n"
    "1. 전시회 준비\n"
    "- 제품의 신뢰성 품질에 대한 판단: 고객이 아닌 우리 스스로 하는것\n"
    "- 현장에서 일어나는 수많은 일들을 예측하는 것은 어려움: 우리 스스로 고민, 준비가 필요함\n"
    "- 좋은 제품을 만들기 위한 노력, 방향성에 대한 고민 필요\n"
    "\n"
    "2. 성과 창출\n"
    "- 회사는 결과를 내야하는 곳: 업무진행에서의 과정과 결과 모두 중요\n"
    "- 각자의 위치에서 실질적인 성과와 결과물을 낼 수 있도록 해야함\n"
    "\n"
    "3. 조직문화\n"
    "- 업무 능률 향상을 위한 생성형 AI 활용\n"
    "- 구성원 각자 책임감을 가지고 근무\n"
    "- 6월1일, 2명 인원 합류 외 계속해서 필요 인원 충원 예정\n"
    f"{_DIRECTIVES_MARKER}\n"
    "1. 금주 예정 사항\n"
    "- 부서별, 개인별로 업무 및 부서 발전을 위한 고민 필요\n"
    "- 보다 경쟁력 있는 회사로의 발전을 위한 부서별, 개인별 필요한 요소 등에 대한 대표님 면담\n\n"
    + _CONTEXT_BLOCK
)


# 이 길이 이하의 전사문은 청킹 없이 한 번에 요약한다(map 생략). max_model_len(8192)
# 안에서 전사문 + 프롬프트(약 1500자/1000토큰) + 출력(목표 1500~2000자) 이 모두
# 들어가는 안전 한도(한국어 ~1.5자/토큰): 8192 - 1000(sys) - 1500(out) - 128(safety)
# ≈ 5560 토큰 ≈ 약 8300자.
#
# 6000 → 8000 상향(2026-05-20):
# 이전 한도 6000 자에서 19.6분 회의 전사 6,267자가 살짝 초과해 굳이 map-reduce 에
# 진입했고, map 3청크가 모두 빈 응답(1자)을 내며 폴백이 raw 청크를 reduce 에 그대로
# 넘겨 정답 회의록의 80% 가 손실됐다. 한도를 8000자로 올리면 ~25분 회의(8000자
# 분량)까지 single-call 한 번에 reduce 가 전체를 보고 정리한다.
# (값은 모듈 상단 _compute_char_budgets 가 settings.max_model_len 기반으로 산출 —
#  max_model_len=8192 면 여기 설명한 8000 과 동일.)
# map 출력이 이 길이 미만이면 실패로 보고 폴백 프롬프트로 한 번 더 시도한다.
_MAP_MIN_CHARS = 15


async def _map_chunk(index: int, chunk: str) -> str:
    """청크 → 메모. 1차(엄격) 실패 시 2차(완화)로 재시도, 그래도 실패면 원문 그대로.

    빈 응답으로 끝나는 청크가 누적되면 reduce 단계가 사실상 빈 입력으로 회의 전체를
    환각하는 사고가 난다(2026-05-20 관측). 2단 폴백으로 적어도 청크의 키워드 나열은
    보장.
    """
    note = await _collect(_MAP_SYSTEM, chunk, temperature=_MAP_TEMPERATURE)
    if len(note) >= _MAP_MIN_CHARS:
        return note
    logger.warning(
        "[summarize] map chunk %d 1차 출력 부족(%d자) — 완화 프롬프트 재시도",
        index + 1, len(note),
    )
    # 2차: 완화 프롬프트. '키워드 나열' 만 요구해 모델이 자기검열로 EOS 가는 경로 회피.
    # 약간 더 높은 temperature 로 다양성 부여.
    note = await _collect(_MAP_SYSTEM_FALLBACK, chunk, temperature=0.5)
    if len(note) >= _MAP_MIN_CHARS:
        return note
    logger.warning(
        "[summarize] map chunk %d 2차도 부족(%d자) — 원문 사용",
        index + 1, len(note),
    )
    return chunk


async def summarize_transcript(transcript: str) -> MeetingSummary:
    """전사문을 회의록 2개 섹션으로 요약.

    Raises:
        ValueError: transcript 가 비어 있는 경우.
        RuntimeError: vLLM 호출 실패(stream_chat 가 던지는 예외 전파).
    """
    text = (transcript or "").strip()
    if not text:
        raise ValueError("전사문이 비어 있습니다.")

    if len(text) <= _SINGLE_CALL_MAX_CHARS:
        # 짧은~중간 길이(약 25분 회의까지) — 전사문 전체를 한 번에 요약.
        # map 단계는 청크가 불분명할 때 빈 출력을 내고 reduce 가 그 빈 입력으로
        # 회의를 통째로 지어내는 위험이 있다. 한도 안에 들어가면 원문을 그대로
        # reduce 에 넘기는 편이 안전하고 입력에도 충실하다.
        logger.warning("[summarize] single-call (chars=%d)", len(text))
        notes = text
    else:
        # 긴 전사문(약 25분 초과) — map-reduce.
        chunks = split_transcript(text)
        logger.warning(
            "[summarize] map-reduce (chars=%d chunks=%d)", len(text), len(chunks)
        )
        notes_parts: list[str] = []
        for i, chunk in enumerate(chunks):
            notes_parts.append(await _map_chunk(i, chunk))
        notes = "\n\n".join(notes_parts)
        # reduce 입력이 단일 호출 한도를 초과하면 단순 truncate 대신 second-stage
        # reduce: notes 를 다시 청킹해 청크별 메모를 더 압축한 뒤 합친다. 회의 후반부가
        # 통째로 잘려나가는 사고(2026-05-20: 6267자 → notes 6271자 → 6000자에서 truncate)
        # 를 방지한다.
        if len(notes) > _SINGLE_CALL_MAX_CHARS:
            logger.warning(
                "[summarize] notes 한도 초과(%d > %d) — second-stage reduce",
                len(notes), _SINGLE_CALL_MAX_CHARS,
            )
            sub_chunks = split_transcript(notes, max_chars=_CHUNK_CHARS)
            compressed: list[str] = []
            for i, sub in enumerate(sub_chunks):
                compressed.append(await _map_chunk(i, sub))
            notes = "\n\n".join(compressed)
            # 두 번 압축했는데도 한도를 넘으면(매우 긴 회의) 마지막 한도 직전까지만.
            # 이 경우는 비정상이고 사용자가 회의를 더 짧게 나누어야 함.
            if len(notes) > _SINGLE_CALL_MAX_CHARS:
                logger.warning(
                    "[summarize] 2단 압축 후에도 초과(%d) — head 우선 truncate",
                    len(notes),
                )
                notes = notes[:_SINGLE_CALL_MAX_CHARS]

    # reduce — 전사문(또는 메모)을 최종 2개 섹션으로.
    final = await _collect(_REDUCE_SYSTEM, notes, temperature=_REDUCE_TEMPERATURE)
    logger.warning("[summarize] reduce → %d chars", len(final))

    # ── 출력 sanity 검증 + 1회 재시도 ─────────────────────────────────
    # LLM 이 (a) 두 머리표를 모두 빠뜨려 빈 회의록을 내거나, (b) 프롬프트 자리표시
    # 문구('주제', '세부 내용', '지시 제목')를 그대로 흘리거나, (c) 회의 분량 대비
    # 비정상적으로 짧은(<200자) 출력을 내면, 재현성 좋은 패턴이라 1회 재시도로
    # 회복 가능한 경우가 많다. 폴백 시 약간 더 높은 temperature(0.5) 로 동일 입력을
    # 다시 요약하고, 그 결과가 더 충실하면 채택한다.
    if _needs_retry(final, len(text)):
        logger.warning(
            "[summarize] reduce 품질 미달(%d자, decl=%.2f) — 재시도",
            len(final), _declarative_ratio(final),
        )
        retry = await _collect(
            _REDUCE_SYSTEM + _RETRY_REMINDER,
            notes,
            temperature=0.5,
        )
        # 재시도 채택 조건:
        # (1) leak 이 없고
        # (2) 줄글 비율(평서형 종결)이 1차보다 낮거나, 양쪽 다 임계치 이하면서 더 길면 채택.
        # 줄글 톤 회피가 핵심이라 길이보다 declarative_ratio 를 우선한다.
        retry_leak = _has_placeholder_leak(retry)
        final_decl = _declarative_ratio(final)
        retry_decl = _declarative_ratio(retry)
        better_style = (
            not retry_leak
            and (retry_decl < final_decl
                 or (retry_decl <= _MAX_DECLARATIVE_RATIO and len(retry) > len(final)))
        )
        if better_style:
            logger.warning(
                "[summarize] 재시도 채택 (1차 %d자 decl=%.2f → 2차 %d자 decl=%.2f)",
                len(final), final_decl, len(retry), retry_decl,
            )
            final = retry
        else:
            logger.warning(
                "[summarize] 재시도 채택 안 함 (1차 %d자 decl=%.2f / 2차 %d자 decl=%.2f / leak=%s)",
                len(final), final_decl, len(retry), retry_decl, retry_leak,
            )

    return parse_sections(final)


# 프롬프트 스켈레톤이 그대로 새어 나오면 안 되는 자리표시 문구. 회의 본문에 이런 단어가
# '제목 한 줄' 형태로 등장하면 LLM 이 양식 더미를 그대로 출력한 신호로 본다.
_PLACEHOLDER_LEAKS = (
    "주제",
    "세부 내용",
    "세부내용",
    "지시 제목",
    "지시제목",
    "세부 지시",
)


def _has_placeholder_leak(text: str) -> bool:
    """프롬프트 자리표시 문구가 줄 첫머리·번호 항목 제목으로 들어갔는지 검사."""
    for line in text.splitlines():
        stripped = re.sub(r"^[\s\d.)\-*•]+", "", line).strip()
        if not stripped:
            continue
        # 제목으로 등장한 자리표시(콜론/끝맺음 무관) — 본문 내 단어 자체 등장은 통과.
        for ph in _PLACEHOLDER_LEAKS:
            if stripped == ph or stripped.startswith(f"{ph}:") or stripped.startswith(f"{ph}："):
                return True
    return False


def _declarative_ratio(text: str) -> float:
    """불릿 줄 중 평서형 종결('~다.', '~한다.', '~된다.' 등)로 끝나는 비율.

    정답 회의록은 명사형 종결("필요함", "충원 예정", "활용") 위주라 줄글식 평서형 종결은
    거의 0%. 자동 요약이 줄글로 흐르는지 감지하는 핵심 지표.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        # 불릿/번호 마커 제거
        s = re.sub(r"^[\s\d.)\-*•]+", "", s).strip()
        # 머리표·헤드라인은 제외
        if not s or s in (_MAIN_MARKER, _DIRECTIVES_MARKER):
            continue
        # 너무 짧으면(제목 줄) 제외
        if len(s) < 8:
            continue
        lines.append(s)
    if not lines:
        return 0.0
    # 평서형 종결 패턴: 마침표가 있든 없든 '다/한다/된다/했다/이다/합니다/입니다' 로 끝나는 줄
    pat = re.compile(r"(다|한다|된다|했다|이다|필요하다|합니다|입니다)\.?$")
    declarative = sum(1 for s in lines if pat.search(s))
    return declarative / len(lines)


# 평서형 종결 허용 한도. 30% 초과면 줄글로 흐른 것으로 보고 재시도.
_MAX_DECLARATIVE_RATIO = 0.30


def _needs_retry(final: str, transcript_chars: int) -> bool:
    """reduce 결과가 재시도가 필요한 상태인지 판정."""
    if not final or len(final) < 200:
        # 전사문이 짧으면(<500자) 짧은 결과도 정상일 수 있다 — 재시도 자제.
        return transcript_chars > 500
    if _MAIN_MARKER not in final and _DIRECTIVES_MARKER not in final:
        # 두 머리표가 모두 없으면 parse_sections 가 전체를 main 으로 떠넘긴다 — 재시도.
        return True
    if _has_placeholder_leak(final):
        return True
    ratio = _declarative_ratio(final)
    if ratio > _MAX_DECLARATIVE_RATIO:
        logger.warning(
            "[summarize] declarative_ratio=%.2f > %.2f — 줄글 종결 과다, 재시도",
            ratio, _MAX_DECLARATIVE_RATIO,
        )
        return True
    return False


_RETRY_REMINDER = (
    "\n\n[재시도 알림 — 직전 출력이 양식·문체 요구사항을 충족하지 못했습니다.]\n"
    "반드시 다음을 지키세요:\n"
    f"- 정확히 두 머리표 '{_MAIN_MARKER}' 와 '{_DIRECTIVES_MARKER}' 를 사용합니다.\n"
    "- 줄글로 풀어 쓰지 않습니다. 평서형 종결('~다', '~한다', '~된다', '~필요하다', "
    "'~합니다') 을 절대 사용하지 않습니다. 명사형 종결('~필요', '~예정', '~활용', "
    "'~중요', '~검토', '~확보', '~함', '~해야함') 또는 콜론 구조('X: Y') 로 짧게 "
    "끊어 적습니다. 한 불릿은 30~80자 안에서 끝냅니다.\n"
    "- 회의에 등장한 구체 사실(날짜·인원수·부서명·일정) 을 추상 일반론으로 대체하지 "
    "않습니다. 구체값이 있으면 그대로 적습니다.\n"
    "- 자리표시 문구('주제', '세부 내용', '지시 제목' 같은 더미 단어) 를 그대로 "
    "출력하지 않습니다. 항상 전사문에서 추론한 실제 회의 내용으로 채웁니다.\n"
    "- 회의 주요 내용은 최소 3개 이상의 번호 항목으로, 각 항목 아래 2~4줄의 세부 "
    "불릿을 둡니다. 지시사항도 '금주 예정 사항' 처럼 묶음 제목 1~3개 아래 세부 "
    "불릿을 두는 2단 구조로 정리합니다(평면 나열 금지).\n"
    "- 카테고리 제목(번호 항목) 사이에 빈 줄을 한 줄 둡니다.\n"
    "- 두 머리표 외 다른 머리말·메타 발언·markdown 강조 기호(**, ##, ` 등) 금지.\n"
)


__all__ = [
    "MeetingSummary",
    "split_transcript",
    "parse_sections",
    "summarize_transcript",
]
