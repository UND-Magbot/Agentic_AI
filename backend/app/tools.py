import html
import json
import logging
import re
from typing import Any

from . import hiworks_mailer, web_search
from .mail_account_service import get_default_mail_account

# uvicorn 기본 WARNING 레벨에 노출되도록 warning 으로 통일.
_log = logging.getLogger("tools.dispatch")


SEARCH_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "사용자가 '오늘/지금/최신/실시간' 같은 시점성 단어 또는 시세/환율/날씨/뉴스 등 변동성 "
            "데이터를 명시 요청하거나 '검색해줘/찾아줘' 라고 직접 지시할 때만 호출. "
            "일반 상식/개념/요약/작성 요청에는 호출 금지 — 모르면 '모릅니다' 로 답."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색어. 한국어 또는 영어. 명사구 위주로 간결하게.",
                },
            },
            "required": ["query"],
        },
    },
}


# 사칙(UND_사칙_2026): 연차 사용 보고 메일 — 제목 양식 + 필수 참조 인원.
# chunk #165/#202 본문 그대로 반영. 다른 회사 양식과 혼동하지 않도록 사칙의 정확한 텍스트 사용.
_LEAVE_EMAIL_PARAMS = {
    "date": {"type": "string", "description": "시작일 YYYY-MM-DD."},
    "duration_days": {"type": "number", "description": "일수, 기본 1."},
    "start_time": {"type": "string", "description": "HH:MM, 기본 09:00."},
    "end_time": {"type": "string", "description": "HH:MM, 기본 18:00."},
    "reason": {"type": "string", "description": "사유, 기본 개인사유."},
    "english_name": {"type": "string", "description": "발신자 영어이름."},
    "korean_name": {"type": "string", "description": "발신자 한글이름."},
    "report_kind": {
        "type": "string",
        "enum": ["사용예정", "사용일"],
        "description": "미리/사후. 기본 사용예정.",
    },
}


COMPOSE_LEAVE_EMAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "compose_leave_email",
        "description": (
            "**연차/휴가 메일 작성·양식 요청 시 반드시 이 도구를 호출한다.** "
            "사용자가 '연차 메일 작성', '휴가 메일 양식', '연차 보고 메일 만들어줘' 같이 "
            "메일 작성 의도를 보이면 사칙 chunk 본문을 직접 풀어쓰지 말고 이 도구로 위임. "
            "회사 표준 양식(send_mail_v1)과 시그니처가 자동 부착된 초안을 반환. "
            "빠진 필드는 기본값(사유=개인사유, 09:00~18:00) 또는 사용자에게 되묻기. "
            "이후 발송 요청이 오면 send_leave_email 에 **여기서 사용한 동일한 인자를 그대로** 재전달."
        ),
        "parameters": {
            "type": "object",
            "properties": _LEAVE_EMAIL_PARAMS,
            "required": ["date"],
        },
    },
}


SEND_LEAVE_EMAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "send_leave_email",
        "description": (
            "compose_leave_email 과 **동일한 인자**(date, duration_days, reason, ...)를 "
            "다시 넘기면 서버가 회사 표준 양식 + 시그니처로 본문을 재생성해 Hiworks SMTP 로 "
            "실제 발송한다. 사용자가 '발송/보내/전송/쏴' 등 발송 의도를 보이면 **즉시 이 도구를 "
            "호출**한다. **금지사항**: 자연어로 '메일을 발송했습니다', '보냈습니다', "
            "'전송 완료' 같은 종결 문장을 출력하는 것은 거짓말이며 절대 금지 — 발송은 오직 이 "
            "도구 호출로만 발생한다. 도구 호출 전에는 발송 완료를 단정하지 말 것. **본문 텍스트를 "
            "직접 작성·요약·전달하지 말 것 — 양식은 서버에서 채운다.** to/cc 비우면 .env 기본값"
            "(HIWORKS_DEFAULT_TO / HIWORKS_DEFAULT_CC_ALL)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                **_LEAVE_EMAIL_PARAMS,
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "받는 사람 이메일. 비우면 HIWORKS_DEFAULT_TO.",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "참조 이메일. 비우면 HIWORKS_DEFAULT_CC_ALL.",
                },
            },
            "required": ["date"],
        },
    },
}


# Expense 보고 양식 작성 — 자체 호스팅 vLLM 명분에 부합(금액/지출 = 민감 데이터).
# 메일 발송은 1차 범위 외 — 사용자 요청(2026-05-14)으로 send_expense_email 은 의도적으로
# 추가하지 않는다. 본 도구는 xlsx 생성 + 다운로드 URL 반환까지만 수행.
_EXPENSE_CATEGORIES = (
    "접대비",
    "복리후생비",
    "여비교통비",
    "소모품비",
    "차량유지비",
    "지급수수료",
    "도서인쇄비",
    "해당없음",
)

COMPOSE_EXPENSE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "compose_expense_report",
        "description": (
            "월간 expense(외부 지출 비용) 보고 양식 생성. "
            "사용자가 '익스펜스/expense/지출/경비/비용/정산 + 작성/만들/써/해줘/부탁/처리/정리' "
            "조합으로 요청하면 본 도구를 호출(자연어로 양식 흉내 금지). "
            "회사 표준 xlsx 양식을 만들고 영수증은 시트3 슬롯(최대 16장)에 입력 순서대로 임베드.\n\n"
            "라인 매핑: 사용자가 한 줄에 적은 '계정과목, 날짜, 사유, 금액, 공급자' 5필드를 "
            "한 line 객체로 1:1 매핑. 줄 수 = lines 길이. 누락·순서변경·필드 섞기 금지.\n\n"
            "필수:\n"
            "1) 사용자 메시지의 모든 지출 라인을 빠짐없이 lines 에. 명시적으로 'X' 라 한 분류만 제외.\n"
            "2) system prompt 의 '## 현재 요청 컨텍스트' 에 표시된 첨부 ID 전부를 "
            "receipt_attachment_ids 에 표시 순서 그대로(누락·재정렬 금지). 0개면 [].\n"
            "3) 월/일만 적힌 날짜는 system prompt '현재 날짜' 의 연도로 YYYY-MM-DD 정규화. "
            "요일 글자(월/화/...)는 제거.\n"
            "4) category 는 enum 값만 — 교통→여비교통비, 식사/회식→복리후생비, 접대→접대비, "
            "사무용품→소모품비, 주유→차량유지비, 수수료→지급수수료, 도서/인쇄→도서인쇄비, 기타→해당없음.\n"
            "5) amount 는 숫자(정수)만 — '29,500원'→29500, '₩30000'→30000. 0/음수 금지.\n"
            "6) vendor, purpose 비어 있으면 안 됨.\n\n"
            "메일 발송 도구 없음 — 다운로드 URL 만 안내."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "author": {
                    "type": "string",
                    "description": "작성자 한글 이름. system prompt 의 '현재 사용자' 값 우선.",
                },
                "year": {
                    "type": "integer",
                    "description": "system prompt 의 '현재 날짜' 연도 사용.",
                },
                "month": {
                    "type": "integer",
                    "description": "보고 월 1~12. lines 의 date 가 속한 월.",
                },
                "lines": {
                    "type": "array",
                    "description": "사용자 메시지의 줄 순서 그대로. 누락/순서변경 금지.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "enum": ["법인카드", "개인카드"],
                                "description": "사용자 명시 카드 종류.",
                            },
                            "category": {
                                "type": "string",
                                "enum": list(_EXPENSE_CATEGORIES),
                                "description": "계정과목 enum.",
                            },
                            "date": {
                                "type": "string",
                                "description": "YYYY-MM-DD. 요일 글자 제거.",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "지출 사유 (원문 그대로).",
                            },
                            "amount": {
                                "type": "number",
                                "description": "원, 정수. 콤마/단위 제거.",
                            },
                            "vendor": {
                                "type": "string",
                                "description": "공급자 상호 (원문 그대로).",
                            },
                            "user": {
                                "type": "string",
                                "description": "법인카드 시 사용자명, 개인카드면 빈 문자열.",
                            },
                        },
                        "required": [
                            "source", "category", "date", "purpose", "amount", "vendor",
                        ],
                    },
                },
                "receipt_attachment_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "첨부 ID 전부를 표시 순서 그대로. 0개면 []."
                    ),
                },
            },
            "required": ["author", "year", "month", "lines"],
        },
    },
}


# 일일자금수지 비교·검증 — 거래내역 + 일일자금수지(자금실적) xlsx 첨부를 받아 당일잔액·
# 자금일계표를 외과적 기입하고 계획↔실적을 검증한 뒤 채워진 파일 + 검증 요약을 반환.
# 자금계획은 일일자금수지 파일에 '자금계획_원화/외화' 시트로 내장 → 별도 첨부 불필요.
# main.py 의 결정론 fast-path(_detect_fund_reconcile_intent)가 1차로 처리하며, 본 도구는
# fast-path 가 못 잡은 표현을 LLM 이 인식해 호출하는 폴백(tools 지원 백엔드에서 동작).
RECONCILE_FUND_DAILY_TOOL = {
    "type": "function",
    "function": {
        "name": "reconcile_fund_daily",
        "description": (
            "일일자금수지(자금실적) 작성·검증 요청 시 호출. 사용자가 은행 '거래내역' 엑셀과 "
            "'일일자금수지'(당일잔액·증감액이 비어 있는 자금실적) 엑셀을 첨부하고 "
            "'자금계획/자금실적 비교·검증', '일일자금수지 채워줘/작성', '당일잔액 맞춰줘' 같이 "
            "요청하면 본 도구로 위임(자연어로 표 흉내 금지).\n\n"
            "동작: 거래내역으로 계좌별 당일잔액을 산출해 일일자금수지 공란에 외과적 기입하고, "
            "내장 자금계획 시트와 당일 실적을 통화별(원화/외화)로 대조·검증한 뒤 채워진 파일과 "
            "검증 결과를 다운로드로 제공한다. 원본은 수정하지 않는다.\n\n"
            "필수: system prompt '## 현재 요청 컨텍스트' 에 표시된 첨부 ID 전부를 "
            "attachment_ids 에 표시 순서 그대로 넣는다(엑셀 2개 — 거래내역·일일자금수지). "
            "date 는 사용자가 'YYYY-MM-DD' 처럼 연도까지 명시했을 때만 채우고, 아니면 비워 "
            "서버가 거래내역·공란 시트로 자동 판별하게 둔다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "첨부된 엑셀 파일 ID 전부(거래내역 + 일일자금수지). "
                        "system prompt 컨텍스트의 첨부 ID 를 순서 그대로. 최소 2개."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": "대상 일자 YYYY-MM-DD. 연도까지 명시됐을 때만. 아니면 생략.",
                },
            },
            "required": ["attachment_ids"],
        },
    },
}


TOOLS = [
    SEARCH_WEB_TOOL,
    COMPOSE_LEAVE_EMAIL_TOOL,
    SEND_LEAVE_EMAIL_TOOL,
    COMPOSE_EXPENSE_REPORT_TOOL,
    RECONCILE_FUND_DAILY_TOOL,
]


# ── compose_leave_email 헬퍼 ───────────────────────────────────────────────

# YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD 모두 허용. 모델이 자주 '/' 구분자로 넘김.
_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")


def _format_dates(date_str: str) -> tuple[str, str]:
    """'2026-02-04' / '2026/02/04' / '2026.2.4' → ('2026/02/04', '260204')."""
    m = _DATE_RE.match(date_str.strip())
    if not m:
        return date_str, ""
    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    return f"{y}/{mo}/{d}", f"{y[2:]}{mo}{d}"


async def _build_leave_email(args: dict[str, Any]) -> dict[str, Any]:
    """compose/send 공통: 인자 + mail_accounts row 로 subject/body/cc_display/account 생성.

    LLM 이 본문을 직접 들고다니지 않게 하려고 별도 함수로 분리. send_leave_email 도
    이 함수를 다시 호출해 본문을 재구성한 뒤 발송한다. 시그니처·기본 수신자는 DB
    `mail_accounts` 의 active+default(hiworks) row 에서 가져온다. row 가 없으면
    settings fallback (mail_account_service 내부 처리).
    """
    account = await get_default_mail_account("hiworks")

    date_raw = (args.get("date") or "").strip()
    duration = args.get("duration_days") or 1
    start_time = (args.get("start_time") or "09:00").strip()
    end_time = (args.get("end_time") or "18:00").strip()
    reason = (args.get("reason") or "개인사유").strip()
    eng_name = (args.get("english_name") or "{영어이름}").strip()
    kor_name = (args.get("korean_name") or "{한글이름}").strip()
    report_kind = (args.get("report_kind") or "사용예정").strip()
    if report_kind not in ("사용예정", "사용일"):
        report_kind = "사용예정"

    display_date, yymmdd = _format_dates(date_raw)
    yymmdd_for_title = yymmdd or "YYMMDD"

    # 기간 표시 — float 정수면 정수형으로.
    try:
        dur_num = float(duration)
        dur_str = str(int(dur_num)) if dur_num.is_integer() else str(dur_num)
    except (TypeError, ValueError):
        dur_str = str(duration)

    subject = f"연차 사용 보고_{yymmdd_for_title}({report_kind})_{kor_name}"

    # CC 표시 — 전체 이메일 명단은 토큰 낭비라 요약 텍스트. 발송 시 실제 명단은 account 컬럼 사용.
    cc_all = list(account.get("default_cc_all") or [])
    cc_normal = list(account.get("default_cc") or [])
    if cc_all:
        cc_display = f"대표님 제외 전 직원 ({len(cc_all)}명)"
    elif cc_normal:
        cc_display = f"필수 참조 ({len(cc_normal)}명)"
    else:
        cc_display = "대표님, 최혁수 상무(Daniel), 배성철 차장(Jacob), 이채진 주임(Vivian)"

    # send_mail_v1.jpg 양식 그대로. 본문 끝에 mail_accounts.signature 자동 부착.
    signature = (account.get("signature") or "").rstrip()
    body_parts = [
        "Dear All,",
        "",
        f"안녕하십니까, {eng_name}입니다.",
        "하기와 같이 휴가를 사용하려 합니다.",
        "",
        "",
        f"일시: {display_date} (기간: {dur_str}일 [{start_time}~ {end_time}])",
        f"사유: {reason}",
        "",
        "필요한 사항이 있으시면 연락 부탁 드립니다.",
        "",
        "감사합니다.",
    ]
    if signature:
        body_parts.extend(["", signature])
    body = "\n".join(body_parts) + "\n"

    html_body = _build_leave_email_html(
        eng_name=eng_name,
        display_date=display_date,
        dur_str=dur_str,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
        signature=signature,
    )

    return {
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "cc_display": cc_display,
        "account": account,
    }


def _build_leave_email_html(
    *,
    eng_name: str,
    display_date: str,
    dur_str: str,
    start_time: str,
    end_time: str,
    reason: str,
    signature: str,
) -> str:
    """send_mail_v1.jpg 양식의 HTML 버전. 일시/사유/시그니처 첫 줄 볼드.

    inline CSS 만 사용 — Gmail/Outlook/Hiworks 메일뷰 호환성. 변수는 모두 html.escape
    로 이스케이프하여 사용자 입력에서 태그 주입 사고 차단.
    """
    e = html.escape

    # 시그니처는 plain text 줄들. 첫 줄을 강조하고 줄바꿈은 <br> 로.
    sig_html = ""
    if signature:
        sig_lines = signature.splitlines()
        if sig_lines:
            first = f"<strong>{e(sig_lines[0])}</strong>"
            rest = [e(l) for l in sig_lines[1:]]
            sig_html_lines = [first, *rest]
            sig_html = (
                "<hr style=\"border:none;border-top:1px solid #d0d0d0;margin:24px 0 12px;\">"
                f"<p style=\"margin:0;font-size:13px;color:#444;line-height:1.6;\">"
                f"{'<br>'.join(sig_html_lines)}"
                "</p>"
            )

    schedule_line = (
        f"일시: {e(display_date)} "
        f"(기간: {e(dur_str)}일 [{e(start_time)}~ {e(end_time)}])"
    )
    reason_line = f"사유: {e(reason)}"

    return (
        "<!DOCTYPE html>"
        "<html><body style=\"margin:0;padding:16px;"
        "font-family:'Apple SD Gothic Neo','맑은 고딕','Malgun Gothic',sans-serif;"
        "font-size:14px;line-height:1.7;color:#222;\">"
        "<p style=\"margin:0 0 12px;\">Dear All,</p>"
        f"<p style=\"margin:0 0 12px;\">안녕하십니까, {e(eng_name)}입니다.<br>"
        "하기와 같이 휴가를 사용하려 합니다.</p>"
        f"<p style=\"margin:18px 0;\"><strong>{schedule_line}</strong><br>"
        f"<strong>{reason_line}</strong></p>"
        "<p style=\"margin:0 0 12px;\">필요한 사항이 있으시면 연락 부탁 드립니다.</p>"
        "<p style=\"margin:0;\">감사합니다.</p>"
        f"{sig_html}"
        "</body></html>"
    )


async def _compose_leave_email(args: dict[str, Any]) -> str:
    """사칙 + send_mail_v1 양식을 채워 발송 직전 메일 초안을 반환."""
    built = await _build_leave_email(args)
    subject = built["subject"]
    body = built["body"]
    cc_display = built["cc_display"]

    composed = (
        "=== 연차 사용 보고 메일 초안 ===\n\n"
        f"[제목] {subject}\n\n"
        f"[필수 참조 (CC)] {cc_display}\n\n"
        f"[본문]\n{body}"
    )

    # LLM 에 돌려주는 JSON — 모델이 사용자에게 이 문자열을 그대로 출력하도록.
    return json.dumps(
        {
            "ok": True,
            "subject": subject,
            "cc": cc_display,
            "body": body.rstrip(),
            "composed": composed,
            "instruction_to_assistant": (
                "다음 composed 필드를 **그대로 복사해** 사용자에게 출력하라. "
                "[제목], [필수 참조 (CC)], [본문] 세 헤더와 그 아래 내용 전부를 빠짐없이 포함. "
                "본문만 발췌하거나 시그니처를 잘라내지 말 것. 추가 설명·인사말·코드블록 없이 "
                "composed 텍스트 그대로 전달. "
                "사용자가 이후 '발송/보내' 라고 하면 send_leave_email 에 이 도구로 넘긴 인자를 "
                "그대로 다시 전달할 것 (subject/body 는 도구 인자에서 제외, 서버가 재생성)."
            ),
        },
        ensure_ascii=False,
    )


# ── compose_expense_report 정규화/검증 헬퍼 ───────────────────────────────────
# LLM 이 사람의 자유로운 표현(콤마 포함 금액, 요일 글자 포함 날짜, 통화기호 등)을 그대로
# 흘려보내는 사고를 잡아내고 자동 정정 가능한 것만 보정한다. 본질적으로 사용자 의도를
# 추측해야 하는 보정(예: 누락된 line 추가)은 하지 않고 LLM 에게 재호출 요청을 돌려준다.

# 사용자가 적는 흔한 금액 표기 → 숫자 추출용. ₩, $, 원, 콤마, 공백 제거.
_AMOUNT_CLEAN_RE = re.compile(r"[,\s ]")
_AMOUNT_KEEP_RE = re.compile(r"[\d.\-]")
# YYYY-MM-DD 정확 매칭(요일 글자 등 추가 토큰은 거부 — LLM 이 깨끗하게 넘기도록 강제).
_DATE_STRICT_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")

# category enum (도구 description 과 동기). 사용자 표현에서 enum 으로 mapping.
_CATEGORY_ALIASES: dict[str, str] = {
    # 교통 관련
    "교통비": "여비교통비",
    "택시비": "여비교통비",
    "택시": "여비교통비",
    "지하철": "여비교통비",
    "버스": "여비교통비",
    "항공료": "여비교통비",
    "철도": "여비교통비",
    "ktx": "여비교통비",
    "srt": "여비교통비",
    "유류비": "차량유지비",
    "주유": "차량유지비",
    "주유비": "차량유지비",
    "세차": "차량유지비",
    # 식사 관련
    "점심": "복리후생비",
    "저녁": "복리후생비",
    "회식": "복리후생비",
    "식사": "복리후생비",
    "간식": "복리후생비",
    "다과": "복리후생비",
    # 접대
    "접대": "접대비",
    "거래처 식사": "접대비",
    "거래처식사": "접대비",
    # 소모품
    "사무용품": "소모품비",
    "문구": "소모품비",
    "비품": "소모품비",
    # 도서/인쇄
    "책": "도서인쇄비",
    "도서": "도서인쇄비",
    "인쇄": "도서인쇄비",
    "복사": "도서인쇄비",
    # 수수료
    "은행수수료": "지급수수료",
    "수수료": "지급수수료",
    # 기타/잡비
    "잡비": "해당없음",
    "기타": "해당없음",
}


def _normalize_amount(raw: Any) -> tuple[float | None, str | None]:
    """LLM 이 넘긴 amount 를 float 으로 정규화.

    반환: (정수화 가능 float, 오류 메시지 or None).
    """
    if raw is None:
        return None, "amount 가 비어 있음"
    if isinstance(raw, (int, float)):
        if raw <= 0:
            return None, f"amount 가 0 또는 음수({raw})"
        return float(raw), None
    s = str(raw).strip()
    if not s:
        return None, "amount 가 빈 문자열"
    cleaned = _AMOUNT_CLEAN_RE.sub("", s)
    # 숫자/소수점/마이너스 만 추출 — '29500원', '₩29500', '29,500' 모두 흡수.
    only_num = "".join(ch for ch in cleaned if _AMOUNT_KEEP_RE.match(ch))
    if not only_num:
        return None, f"amount={s!r} 에서 숫자를 찾을 수 없음"
    try:
        v = float(only_num)
    except ValueError:
        return None, f"amount={s!r} 가 숫자로 변환 안 됨"
    if v <= 0:
        return None, f"amount={v} 가 0 또는 음수"
    return v, None


def _normalize_date(raw: Any) -> tuple[str | None, str | None]:
    """LLM 이 넘긴 date 를 YYYY-MM-DD 로 정규화.

    허용: '2026-02-09', '2026/2/9', '2026.02.09', '2026-02-09 월' (요일 글자 stripped).
    거부: '2/9', '2월 9일', 'yesterday' — LLM 이 컨텍스트의 연도로 채워 다시 호출하도록.
    """
    if raw is None:
        return None, "date 가 비어 있음"
    s = str(raw).strip()
    if not s:
        return None, "date 가 빈 문자열"
    # 요일 글자(월~일/Mon~Sun)와 한국어 월/일 접미사 제거 → 'YYYY-MM-DD' 만 남기기 시도.
    s_clean = re.sub(r"[월화수목금토일\s]+$", "", s)
    s_clean = re.sub(r"[년월일]", "-", s_clean).strip("-")
    s_clean = s_clean.replace("/", "-").replace(".", "-")
    parts = s_clean.split("-")
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        return None, (
            f"date={s!r} 는 'YYYY-MM-DD' 형식이 아닙니다. "
            "system prompt 의 현재 날짜 연도로 채워 다시 호출하세요."
        )
    y, mo, d = (int(p) for p in parts)
    if y < 1900 or y > 2100:
        return None, f"date={s!r} 연도({y}) 범위가 비정상"
    if not (1 <= mo <= 12) or not (1 <= d <= 31):
        return None, f"date={s!r} 월/일 범위 비정상"
    return f"{y:04d}-{mo:02d}-{d:02d}", None


def _normalize_category(raw: Any) -> tuple[str | None, str | None]:
    """category 를 enum 값으로 매핑. enum 에 없으면 alias 시도 → 그래도 없으면 거부."""
    if raw is None:
        return None, "category 가 비어 있음"
    s = str(raw).strip()
    if not s:
        return None, "category 가 빈 문자열"
    if s in _EXPENSE_CATEGORIES:
        return s, None
    key = s.lower().replace(" ", "")
    for alias, target in _CATEGORY_ALIASES.items():
        if alias.lower().replace(" ", "") in key:
            return target, None
    return None, (
        f"category={s!r} 가 허용된 값({list(_EXPENSE_CATEGORIES)}) 에 없습니다. "
        f"교통→여비교통비, 식사→복리후생비 같이 매핑하세요."
    )


def _normalize_source(raw: Any) -> tuple[str | None, str | None]:
    s = (str(raw or "")).strip()
    if s in ("법인카드", "개인카드"):
        return s, None
    low = s.lower()
    if any(k in low for k in ("corp", "company", "법인")):
        return "법인카드", None
    if any(k in low for k in ("personal", "private", "개인", "private")):
        return "개인카드", None
    return None, f"source={raw!r} 가 '법인카드' 또는 '개인카드' 가 아닙니다."


def _normalize_lines(raw_lines: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """모든 line 에 대해 정규화를 시도하고, 실패한 항목은 errors 에 인덱스 + 사유로 적재."""
    if not isinstance(raw_lines, list):
        return [], ["lines 가 배열이 아닙니다."]
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, item in enumerate(raw_lines):
        if not isinstance(item, dict):
            errors.append(f"line[{i}] 가 객체가 아님: {item!r}")
            continue
        line_errs: list[str] = []

        source, e = _normalize_source(item.get("source"))
        if e: line_errs.append(e)
        category, e = _normalize_category(item.get("category"))
        if e: line_errs.append(e)
        date, e = _normalize_date(item.get("date"))
        if e: line_errs.append(e)
        amount, e = _normalize_amount(item.get("amount"))
        if e: line_errs.append(e)

        purpose = (item.get("purpose") or "").strip()
        if not purpose:
            line_errs.append("purpose(지출사유) 가 비어 있음")
        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            line_errs.append("vendor(공급자 상호) 가 비어 있음")
        user = (item.get("user") or "").strip()

        if line_errs:
            errors.append(f"line[{i}]: " + " | ".join(line_errs))
            continue

        # 법인카드인데 user(사용자) 비어 있으면 author 폴백은 호출자에서 결정.
        normalized.append({
            "source": source,
            "category": category,
            "date": date,
            "purpose": purpose,
            "amount": amount,
            "vendor": vendor,
            "user": user,
        })
    return normalized, errors


def _validate_year_month_against_lines(
    year: int, month: int, lines: list[dict[str, Any]]
) -> list[str]:
    """보고 연/월 vs 라인 날짜 들의 정합성. 모든 라인이 같은 연/월일 필요는 없지만
    심하게 어긋나면 LLM 의 month/year 추론이 잘못된 신호 — warning 으로만.
    """
    msgs: list[str] = []
    yms = {(int(l["date"][:4]), int(l["date"][5:7])) for l in lines}
    matching = sum(1 for y, m in yms if y == year and m == month)
    if not yms:
        return msgs
    if matching == 0:
        msgs.append(
            f"⚠ 보고 연월 {year}-{month:02d} 가 어느 라인의 날짜와도 일치하지 않습니다. "
            f"라인 날짜의 연/월: {sorted(yms)}. month/year 인자를 다시 확인하세요."
        )
    return msgs


def _error_payload(errors: list[str], retry_hint: str) -> str:
    """LLM 이 재호출 시 무엇을 고쳐야 할지 명확히 보이도록 구조화된 에러 응답."""
    short_message = "⚠ Expense 양식 생성 실패 (인자 검증 실패).\n" + "\n".join(
        f"  - {e}" for e in errors
    )
    return json.dumps(
        {
            "ok": False,
            "retryable": True,
            "errors": errors,
            "error": " | ".join(errors)[:500],
            "short_message": short_message,
            "instruction_to_assistant": (
                f"인자 검증 실패. 위 errors 를 한 줄씩 읽고 잘못된 필드만 보정해 "
                f"compose_expense_report 를 **즉시 한 번 더** 호출하라. {retry_hint} "
                "사용자에게 실패 사실을 노출하기 전에 자동 보정 1회 시도. "
                "재호출 후에도 실패하면 그때 short_message 를 그대로 사용자에게 보여라."
            ),
        },
        ensure_ascii=False,
    )


async def _compose_expense_report(args: dict[str, Any]) -> str:
    """expense 양식 생성 → MinIO 저장 → Attachment row 생성 후 메타 반환.

    LLM 에 돌려주는 JSON 에 short_message 를 포함해 vllm_client 가 그대로 사용자에게
    출력하도록 한다(leave_email 와 동일한 short-circuit 패턴). 메일 발송은 1차 범위 외라
    별도 발송 도구가 없음을 짧게 부언.

    검증/정규화 흐름:
    1) author/year/month — 기본 형식 검증
    2) lines — 항목별 정규화 (source/category/date/amount/purpose/vendor)
       실패 시 errors 배열을 채워 LLM 에 재호출 요청 (retryable=true)
    3) attachment IDs — int 변환 + 최대 16장 검사
    4) builder 호출 → 성공 시 short_message + 다운로드 URL 반환
    """
    # 지연 import — tools 모듈 로딩 시 expense_service → models → SQLAlchemy 가 따라오는데
    # main.py 의 import 사이클(database 초기화 전) 을 피하려고 함수 진입 시점에만 가져온다.
    from .expense_service import compose_expense_report as _do_compose

    errors: list[str] = []

    author = (args.get("author") or "").strip()
    if not author:
        errors.append("author(작성자) 가 비어 있음. system prompt 의 '현재 사용자' 값을 사용하세요.")

    try:
        year = int(args.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        month = int(args.get("month") or 0)
    except (TypeError, ValueError):
        month = 0
    if year < 1900 or year > 2100:
        errors.append(
            f"year={args.get('year')!r} 가 잘못됨. system prompt 의 '현재 날짜' 연도를 사용하세요."
        )
    if not (1 <= month <= 12):
        errors.append(f"month={args.get('month')!r} 는 1~12 정수여야 함.")

    raw_lines = args.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        errors.append("lines(지출 라인) 이 비었거나 배열이 아님. 한 줄이라도 있어야 도구 호출.")
        return _error_payload(
            errors,
            retry_hint="라인이 없으면 도구를 호출하지 말 것.",
        )

    normalized_lines, line_errors = _normalize_lines(raw_lines)
    errors.extend(line_errors)

    if errors:
        return _error_payload(
            errors,
            retry_hint=(
                "사용자 메시지를 다시 읽고 각 줄을 1:1 line 객체로 매핑. 날짜는 YYYY-MM-DD, "
                "금액은 숫자만, category 는 enum 값만 넣을 것."
            ),
        )

    # 라인 수가 슬롯 한도(16) 또는 데이터 시트 한도(48) 초과면 즉시 거부.
    personal_count = sum(1 for l in normalized_lines if l["source"] == "개인카드")
    corp_count = len(normalized_lines) - personal_count
    if personal_count > 16:
        return _error_payload(
            [f"개인카드 라인 {personal_count}건 — 영수증 슬롯 한도 16 초과."],
            retry_hint="개인카드 라인을 16건 이하로 분할 요청하세요.",
        )
    if corp_count > 48 or personal_count > 48:
        return _error_payload(
            [f"데이터 시트 한도(48행) 초과: 법인={corp_count} 개인={personal_count}."],
            retry_hint="한 번에 48건 이하로 분할 요청하세요.",
        )

    receipt_ids_raw = args.get("receipt_attachment_ids") or []
    receipt_ids: list[int] = []
    for r in receipt_ids_raw:
        try:
            receipt_ids.append(int(r))
        except (TypeError, ValueError):
            _log.warning("[dispatch] 영수증 ID 변환 실패: %r — skip", r)
    if len(receipt_ids) > 16:
        return _error_payload(
            [f"영수증 ID {len(receipt_ids)}장 — 슬롯 한도 16 초과."],
            retry_hint="영수증을 16장 이하로 줄여 다시 호출하세요.",
        )

    # 정합성 경고 (실패 아님 — builder 는 진행).
    consistency_warnings = _validate_year_month_against_lines(
        year, month, normalized_lines
    )

    # 법인카드 user 가 비어 있으면 author 로 채워 builder 에 넘김.
    for l in normalized_lines:
        if l["source"] == "법인카드" and not l["user"]:
            l["user"] = author

    try:
        result = await _do_compose(
            author=author,
            year=year,
            month=month,
            lines=normalized_lines,
            receipt_attachment_ids=receipt_ids,
        )
    except Exception as e:
        _log.warning("[dispatch] compose_expense_report 실패: %s", e)
        return _error_payload(
            [f"expense 양식 생성 실패(서버 예외): {e}"],
            retry_hint="동일 인자로 재시도하거나 사용자에게 일시적 오류 안내.",
        )

    # 누락 감지 — 개인카드 라인 수 vs 영수증 ID 수 불일치 → 사칙 위반 경고(soft warning).
    runtime_warnings: list[str] = []
    if personal_count != len(receipt_ids):
        runtime_warnings.append(
            f"⚠ 개인카드 라인 {personal_count}건 vs 영수증 {len(receipt_ids)}장 불일치 — "
            "사칙상 개인카드 사용분은 영수증 필수. 누락 시 빈 슬롯으로 출력됨."
        )
    runtime_warnings.extend(consistency_warnings)

    # 최소·정돈된 출력: 한 줄 안내 + 다운로드 카드(markdown link 형태).
    # 프론트가 markdown link 를 감지해 다운로드 아이콘+파일명 카드 UI 로 렌더.
    # 라인 수/영수증 수/메일 안내 등 부가 정보는 사용자 요청으로 모두 제거.
    # 검증 경고(불일치 등)는 양식 위에 살짝만 노출(품질 신호 유지).
    msg_parts = ["✓ Expense 양식이 생성되었습니다."]
    if runtime_warnings:
        msg_parts.extend(runtime_warnings)
    msg_parts.append(f"[{result.filename}]({result.download_url})")
    short_message = "\n\n".join(msg_parts)
    return json.dumps(
        {
            "ok": True,
            "short_message": short_message,
            "attachment_id": result.attachment_id,
            "filename": result.filename,
            "download_url": result.download_url,
            "size_bytes": result.size_bytes,
            "lines_count": result.lines_count,
            "receipts_count": result.receipts_count,
            "warnings": runtime_warnings,
            "instruction_to_assistant": (
                "위 short_message 한 단락만 그대로 출력. 라인 목록 재인용·임의 종결문 금지. "
                "메일 발송이 가능하다고 답하지 말 것 — 본 도구는 발송하지 않는다."
            ),
        },
        ensure_ascii=False,
    )


async def _reconcile_fund_daily(args: dict[str, Any]) -> str:
    """거래내역 + 일일자금수지 첨부 → 당일잔액 기입 + 계획↔실적 검증.

    데이터 처리는 LLM 이 못 하는 결정론 작업이므로 reconcile_service 에 위임한다.
    LLM 에 돌려주는 JSON 의 short_message 를 그대로 사용자에게 출력하도록 지시한다
    (expense/leave 와 동일 short-circuit 패턴). 다운로드 링크 2개(채워진 파일·검증결과)
    포함. 지연 import — reconcile_service → database/SQLAlchemy 의 import 사이클 회피.
    """
    from .finance import reconcile_service as _rs

    raw_ids = args.get("attachment_ids") or []
    ids: list[int] = []
    for r in raw_ids:
        try:
            ids.append(int(r))
        except (TypeError, ValueError):
            _log.warning("[dispatch] reconcile 첨부 ID 변환 실패: %r — skip", r)
    if len(ids) < 2:
        return json.dumps(
            {
                "ok": False,
                "short_message": (
                    "엑셀 2개(은행 거래내역 + 일일자금수지)를 입력창에 함께 첨부한 뒤 "
                    "다시 요청해 주세요."
                ),
                "instruction_to_assistant": "위 short_message 한 단락만 그대로 출력.",
            },
            ensure_ascii=False,
        )

    requested_date = (args.get("date") or "").strip() or None

    try:
        result = await _rs.reconcile_fund_daily(
            attachment_ids=ids, requested_date=requested_date
        )
    except Exception as e:
        _log.warning("[dispatch] reconcile_fund_daily 실패: %s", e)
        return json.dumps(
            {
                "ok": False,
                "short_message": f"⚠ 일일자금수지 비교·검증 실패: {e}",
                "instruction_to_assistant": (
                    "위 short_message 한 단락만 그대로 출력. 추가 설명·재시도 안내 금지."
                ),
            },
            ensure_ascii=False,
        )

    short_message = (
        f"{result.summary}\n\n"
        f"[{result.filename}]({result.download_url})\n\n"
        f"[검증결과_{result.verify_filename}]({result.verify_download_url})"
    )
    return json.dumps(
        {
            "ok": result.ok,
            "short_message": short_message,
            "date": result.date,
            "filename": result.filename,
            "download_url": result.download_url,
            "verify_download_url": result.verify_download_url,
            "written": result.written,
            "instruction_to_assistant": (
                "위 short_message 를 그대로 출력. 요약 수치를 재계산·재인용하지 말고, "
                "다운로드 링크 2개(채워진 파일·검증결과)를 빠뜨리지 말 것. "
                "추가 인사말·설명 없이 short_message 만 전달."
            ),
        },
        ensure_ascii=False,
    )


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """tool_call을 실제 동작으로 매핑. LLM에 돌려줄 문자열을 반환."""
    _log.warning("[dispatch] name=%s args=%s", name, arguments)
    if name == "compose_expense_report":
        return await _compose_expense_report(arguments)
    if name == "reconcile_fund_daily":
        return await _reconcile_fund_daily(arguments)
    if name == "search_web":
        query = arguments.get("query", "").strip()
        if not query:
            return json.dumps({"error": "query 누락"}, ensure_ascii=False)
        result = await web_search.search(query)
        return json.dumps(result, ensure_ascii=False)
    if name == "compose_leave_email":
        return await _compose_leave_email(arguments)
    if name == "send_leave_email":
        if not (arguments.get("date") or "").strip():
            _log.warning("[dispatch] send_leave_email aborted: empty date")
            return json.dumps(
                {"ok": False, "error": "date(시작일) 인자가 비어 있습니다."},
                ensure_ascii=False,
            )
        # LLM 이 본문을 줄여 넘기는 사고를 차단 — compose 와 같은 인자로 서버가 다시 빌드.
        built = await _build_leave_email(arguments)
        subject = built["subject"]
        body = built["body"]
        account = built["account"]
        to = arguments.get("to") or list(account.get("default_to") or [])
        # 연차 보고는 사칙 (#202: "전 직원 1주 전 공유") 상 CC=전 직원이 맞다.
        # 호출자 명시 > mail_accounts.default_cc_all (연차 전용) > mail_accounts.default_cc (일반 fallback).
        cc = (
            arguments.get("cc")
            or list(account.get("default_cc_all") or [])
            or list(account.get("default_cc") or [])
        )
        if not to:
            return json.dumps(
                {
                    "ok": False,
                    "error": "수신자(to) 가 지정되지 않았고 mail_accounts.default_to 도 비어 있습니다. "
                             "사용자에게 받는 사람 이메일을 물어보세요.",
                },
                ensure_ascii=False,
            )
        result = await hiworks_mailer.send_mail(
            subject=subject, body=body, html_body=built.get("html_body"),
            to=to, cc=cc, account=account,
        )
        cc_summary = f"전 직원 ({len(cc)}명)" if len(cc) > 5 else ", ".join(cc)
        to_summary = ", ".join(to)
        if result.ok:
            short_msg = f"✓ 발송이 완료되었습니다.\n받는 사람: {to_summary}\n참조: {cc_summary}"
        else:
            short_msg = f"⚠ 발송 실패: {result.detail}"
        return json.dumps(
            {
                "ok": result.ok,
                "short_message": short_msg,
                "to": to,
                "cc": cc,
                "instruction_to_assistant": (
                    "위 short_message 한 단락만 그대로 출력. "
                    "메일 제목·본문·CC 명단을 다시 인용하지 말 것. 추가 설명·인사말 금지."
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
