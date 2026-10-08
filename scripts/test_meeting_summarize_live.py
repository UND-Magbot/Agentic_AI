"""회의 요약 라이브 검증 — 실제 전사문을 vLLM 으로 요약.

이미 만들어 둔 전사문(docs/archive/_transcript.txt)을 입력으로 meeting_summarizer 를
실제 vLLM 에 태운다. STT 를 다시 돌리지 않아 요약 프롬프트만 빠르게 반복 검증할 수 있다.

전제: vLLM 가동 중 (localhost:8001).
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 호스트에서 직접 vLLM 에 접속 — .env 의 host.docker.internal 대신 localhost.
os.environ["VLLM_BASE_URL"] = "http://localhost:8001"
sys.path.insert(0, str(ROOT / "backend"))

from app.meeting_summarizer import summarize_transcript  # noqa: E402

TRANSCRIPT = ROOT / "docs" / "archive" / "_transcript.txt"

# 프롬프트 스켈레톤이 그대로 새어 나오면 안 되는 자리표시 문구.
_PLACEHOLDER_LEAKS = ["세부 내용", "지시 제목", "세부 지시"]


def main() -> int:
    if not TRANSCRIPT.exists():
        print(f"FAIL: 전사문 없음 — {TRANSCRIPT} (먼저 test_meeting_transcribe.py 실행)")
        return 1

    transcript = TRANSCRIPT.read_text(encoding="utf-8")
    print(f"전사문: {len(transcript)}자")
    print("요약 중 (vLLM map-reduce)...")

    summary = asyncio.run(summarize_transcript(transcript))

    print("\n--- 회의 주요 내용 ---")
    print(summary.main_content)
    print("\n--- 지시사항 ---")
    print(summary.directives)
    print()

    fails: list[str] = []
    combined = summary.main_content + "\n" + summary.directives

    leaks = [p for p in _PLACEHOLDER_LEAKS if p in combined]
    if leaks:
        fails.append(f"프롬프트 자리표시 문구 누출: {leaks}")

    if len(summary.main_content) < 30:
        fails.append(f"회의 주요 내용이 너무 짧음 ({len(summary.main_content)}자)")
    if "요약 생성에 실패" in summary.main_content:
        fails.append("회의 주요 내용 파싱 실패 (마커 누락)")
    if not summary.directives.strip():
        fails.append("지시사항이 비었음")

    if fails:
        print(f"FAIL ({len(fails)}건):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS - 요약 정상 (자리표시 문구 누출 없음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
