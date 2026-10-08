"""회의 녹음 STT 검증 — 실제 m4a 파일을 faster-whisper 로 전사.

backend/app/meeting_transcriber.py 의 transcribe() 를 실제 녹음 파일로 호출하여
전사 결과를 docs/archive/_transcript.txt 에 저장한다. 후속 요약 설계의 입력 길이 측정용.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.meeting_transcriber import transcribe  # noqa: E402

AUDIO = ROOT / "알파시티1로31길.m4a"


def main() -> int:
    if not AUDIO.exists():
        print(f"FAIL: 녹음 파일 없음 — {AUDIO}")
        return 1

    audio_bytes = AUDIO.read_bytes()
    print(f"오디오: {AUDIO.name} ({len(audio_bytes) / 1024 / 1024:.2f} MB)")
    print("전사 시작 (모델 로드 + 추론)...")

    t0 = time.time()
    result = transcribe(audio_bytes, language="ko")
    elapsed = time.time() - t0

    out = ROOT / "docs" / "archive" / "_transcript.txt"
    out.write_text(result.text, encoding="utf-8")

    print(f"완료: {elapsed:.1f}초 소요")
    print(f"  오디오 길이: {result.duration_sec:.1f}초 ({result.duration_sec / 60:.1f}분)")
    print(f"  감지 언어: {result.language}")
    print(f"  전사 문자 수: {len(result.text)}")
    print(f"  저장: {out}")
    print("--- 앞 600자 ---")
    print(result.text[:600])

    if len(result.text) < 50:
        print("FAIL: 전사 결과가 비정상적으로 짧음")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
