from typing import Literal

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class GenerateRequest(BaseModel):
    systemPrompt: str
    messages: list[Message]
    classification: Literal["internal", "public"] = "internal"
    # 옵션: 재생성/다양성 요청 시 sampling override. None 이면 settings 기본값.
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    # BFF 가 인증한 요청 사용자 ID. 도구가 만든 파일(예: Codex 생성 이미지)의 소유자로 쓴다.
    user_id: int | None = None


class TitleRequest(BaseModel):
    """대화 제목 자동 생성 요청. 첫 user 메시지 + (옵션)첫 assistant 응답."""
    messages: list[Message]
