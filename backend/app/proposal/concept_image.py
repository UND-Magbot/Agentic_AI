"""제안서 적용 컨셉 이미지 — 완성 본문(현황·구성·단계)으로 그림 지시를 만들어 외부 이미지 생성(Codex 브리지)에 맡긴다.

사용자 결정(2026-09-29): gemma 는 이미지를 못 만들고 우회 결과물 품질이 낮아, 이미지만 먼저 외부로 돌려 품질을 본다.
  - 그림 지시는 공정 설명만 담는다. 고객사명·지역·제품명은 codex_client._post 의 external_gateway 가
    이미지 모드(일반어)로 가린 뒤 보내고, 가린 본문을 external_calls 에 기록한다.
  - 그림 속 한글·숫자는 틀리기 쉬우므로 글자 없는 그림을 요청하고, 설명은 슬라이드에 코드가 적는다.
  - 실패·미설정이면 이미지 없이 진행한다(제안서 본문은 그대로 완성).
"""
from __future__ import annotations

import logging

from .. import codex_client
from .body import ProposalBody

_log = logging.getLogger("proposal.concept_image")

PROMPT_MAX = 2000        # codex-bridge LIMITS.prompt
_GUIDE = """제조 현장 로봇 자동화 제안서에 넣을 '적용 컨셉 이미지' 1장을 그려라.
- 16:9 가로, 사실적인 3D 렌더 스타일, 밝고 깨끗한 공장 내부, 제안서 표지급 완성도.
- 아래 공정·구성이 한눈에 보이게: 로봇(들), 말단 툴·그리퍼, 비전 카메라, 컨베이어·지그 등 주변 설비, 제품 흐름.
- 이미지 안에 글자·숫자·로고·회사명·워터마크를 넣지 않는다(설명은 슬라이드에 따로 적는다).
- 안전 펜스·작업자 동선은 현실적으로. 과장된 SF 표현 금지.

공정·구성:
"""


def build_prompt(body: ProposalBody) -> str:
    """본문에서 공정 현황·시스템 구성·단계 요지를 뽑아 그림 지시를 만든다(글자 수 한도 안)."""
    parts = []
    by_id = {s.id: s for s in body.sections}
    if body.title:
        parts.append(f"주제: {body.title}")
    for sid, label in (("situation", "현재 공정"), ("system", "제안 구성"), ("solution", "단계")):
        s = by_id.get(sid)
        if not s or s.placeholder:
            continue
        lines = [f"{c.heading}: " + "; ".join(c.bullets[:3]) for c in s.cards]
        parts.append(f"[{label}] " + " / ".join(lines))
    text = "\n".join(parts)
    room = PROMPT_MAX - len(_GUIDE) - 20
    return _GUIDE + (text if len(text) <= room else text[:room] + " …")


async def generate(body: ProposalBody, *, user_id: int | None = None) -> tuple[bytes | None, str]:
    """(PNG bytes 또는 None, 사용자에게 보일 한 줄 안내). 실패해도 예외를 올리지 않는다."""
    if not codex_client.is_configured():
        return None, "외부 이미지 생성이 설정되지 않아 컨셉 이미지 없이 만들었습니다."
    try:
        image, _mime, _desc = await codex_client.generate_image(build_prompt(body), user_id=user_id)
        return image, "적용 컨셉 이미지를 외부 AI 이미지 생성으로 만들어 디자인판에 넣었습니다(고객사명 등은 가린 채 전송)."
    except codex_client.CodexError as e:
        _log.warning("[proposal] 컨셉 이미지 생성 실패 → 생략: %s", e)
        return None, f"컨셉 이미지를 만들지 못해 생략했습니다({e})."
