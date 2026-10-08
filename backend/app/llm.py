"""LLM facade.

전 스택이 Ollama(Gemma3)로 이전되어 vLLM/Qwen 경로는 파기했다(백업: _backup_qwen_*).
호출부는 `from .llm import stream_chat, stream_chat_with_tools` 만 사용하므로, 추후 다른
provider 로 교체할 때도 이 파일만 바꾸면 된다.
"""
from __future__ import annotations

from .ollama_client import stream_chat, stream_chat_with_tools

__all__ = ["stream_chat", "stream_chat_with_tools"]
