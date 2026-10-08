"""제안서 판단 두뇌 선택 — 사내 gemma(proposal_llm) 또는 GPT(Codex 브리지 /v1/ask).

사용자 결정(2026-09-29): 질의응답 이후 '만드는' 단계(공정 컨셉 제안·비교안·수정 반영·견적 행 초안)는
선택 없이 GPT 가 한다(workflow.MAKER). 자료 읽기(원문 추출)는 원문 전체가 나가야 해서 gemma 로 둔다.
  - GPT 로는 추출이 끝난 문항 값 요약만 보낸다. codex_client._post 의 external_gateway 가 고객사명·지역·인명을
    가명으로 바꾸고 금액을 지운 뒤 보내며, 돌아온 답의 가명을 되돌리고 전송을 external_calls 에 남긴다.
  - GPT 가 실패하면 gemma 로 대신하고, 호출부가 그 사실을 기록에 남긴다.
  - 결과 검증(근거 없는 수치 제거 등)은 두뇌와 무관하게 같은 코드가 한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import codex_client, proposal_llm

_log = logging.getLogger("proposal_project.brain")

BRAINS = ("gemma", "gpt")
BRAIN_LABEL = {"gemma": "사내 AI", "gpt": "AI"}
QUESTION_MAX = 4000      # 브리지 LIMITS.question
CONTEXT_MAX = 60000      # 브리지 LIMITS.context

_JSON_TAIL = "\n\n출력은 설명·마크다운 없이 JSON 객체 하나만 쓴다. 사내 자료의 문항 값만 근거로 삼는다."


@dataclass
class BrainResult:
    raw: str
    used: str                 # 실제로 답한 두뇌
    fallback_reason: str = ""


async def ask_json(system: str, data: str, *, brain: str, user_id: int | None,
                   num_predict: int = 4000, temperature: float = 0.3, chat=None) -> BrainResult:
    """system(규칙·출력 형식) + data(문항 값) → JSON 문자열. gpt 실패 시 gemma 로 대신한다."""
    chat = chat or proposal_llm.chat
    if brain == "gpt":
        question = (system + _JSON_TAIL)[:QUESTION_MAX]
        if not codex_client.is_configured():
            reason = "외부 AI 연결 미설정"
        else:
            try:
                raw = await codex_client.ask(question, data[:CONTEXT_MAX], user_id=user_id)
                return BrainResult(raw, "gpt")
            except codex_client.CodexError as e:
                reason = str(e)
                _log.warning("GPT 호출 실패 → gemma 로 대신: %s", reason)
        raw = await chat([{"role": "system", "content": system}, {"role": "user", "content": data}],
                         fmt="json", num_predict=num_predict, temperature=temperature)
        return BrainResult(raw, "gemma", reason)
    raw = await chat([{"role": "system", "content": system}, {"role": "user", "content": data}],
                     fmt="json", num_predict=num_predict, temperature=temperature)
    return BrainResult(raw, "gemma")
