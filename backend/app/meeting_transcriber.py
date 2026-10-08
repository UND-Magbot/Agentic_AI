"""회의 녹음 음성 → 텍스트 변환 (STT) — faster-whisper 로컬 추론.

사내 회의 녹음은 보안등급상(req.md §3.3 S/C 등급) 외부 STT API 로 전송할 수 없으므로
로컬 CPU 추론으로 처리한다. 모델은 첫 사용 시 1회 로드 후 프로세스 수명 동안 캐시한다.

m4a/mp3/wav/webm 등 PyAV(번들 ffmpeg) 가 디코드 가능한 컨테이너를 지원한다.
"""
from __future__ import annotations

import io
import logging
import os
import threading
from dataclasses import dataclass

logger = logging.getLogger("meeting_transcriber")

# 모델 크기 — medium: 한국어 인식 품질이 small 대비 한 단계 위. 한국어 다음절 단어/
# 사내 고유명사 인식 정확도 향상. CPU 추론 시 약 2~3배 느려지지만, 회의 1회 분량은
# 백그라운드 처리이므로 허용 가능. 더 정확한 결과가 필요한 환경(GPU 가능)에서는
# .env 의 WHISPER_MODEL=large-v3 로 운영 중 교체 가능. 호출부 시그니처 무영향.
_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium")
_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
# int8 — CPU 추론에서 메모리/속도 이점. 정확도 손실은 회의 요약 용도에서 무시 가능.
_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

# initial_prompt — Whisper 의 prompt 토큰은 ~224 한도이고 동시에 'condition_on_
# previous_text' 와 결합되면 회의 도입부 무음/잡음 구간에서 prompt 본문이 회의 내용으로
# 환각 출력되는 사고가 관측되었다(2026-05-20: "한국어 표준어로 회의를 진행하였습니다"
# 가 D5 첫 줄로 나옴 — 이는 _build_stt_prompt() 의 꼬리 "한국어 표준어로 받아쓰기"
# 가 그대로 받아쓰여진 것). 도메인 어휘는 요약 LLM 단계의 명칭 정규화 사전으로 충분히
# 처리되므로, STT 의 initial_prompt 는 환각 위험을 줄이기 위해 기본값 None.
# 환경변수로 명시할 때만 사용(예: 회의 직전 정확히 알려진 키워드 한 줄).
_INITIAL_PROMPT = os.environ.get("WHISPER_INITIAL_PROMPT") or None

# beam_size — faster-whisper 기본값 5. 한국어 회의에서 8 까지 올려도 동일 파일 STT 결과가
# 시도마다 6267 → 5493 → 1382 자로 흔들리는 비결정성이 관측되어, 기본값 5 로 복원.
# 비결정성의 주범은 beam 크기보다 temperature fallback 과 condition_on_previous_text.
_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))

# VAD(음성 활동 감지) — 무음/잡음 구간을 미리 잘라내면 도입부 환각("오늘의 주인공은
# 누구일까요?" 등)과 마지막 무음 환각이 큰 폭으로 줄어든다. faster-whisper 1.2.x 의
# 번들 Silero VAD 가 일부 회의 녹음에서 0개 세그먼트를 반환하는 사고가 있어 기본 off.
# WHISPER_VAD=1 로 명시 활성화하면 보수적 파라미터(음성 패딩 400ms·최소 무음 500ms)로
# 동작 — 잘림 위험을 최소화하면서 환각만 줄인다.
_VAD_ENABLED = os.environ.get("WHISPER_VAD", "0") not in ("0", "false", "False", "")

# 오디오 전처리 — 16kHz 모노 변환 + 라우드니스 정규화. 회의실 거리 마이크 / 휴대폰
# 녹음에서 입력 레벨이 낮으면 Whisper 가 무음으로 오판해 도입부를 통째로 건너뛰거나
# 환각을 만든다. PyAV(번들 ffmpeg) 로 디코드해 RMS 기준 -23 LUFS 근처로 끌어올리면
# small/medium 모델 인식률이 체감으로 개선된다. WHISPER_PREPROCESS=0 으로 비활성 가능.
_PREPROCESS_ENABLED = os.environ.get("WHISPER_PREPROCESS", "1") not in ("0", "false", "False")

_model = None
_lock = threading.Lock()


# 같은 문장 패턴이 3회 이상 연속 반복되면 1회로 줄이는 후처리 — Whisper repetition
# hallucination 안전망. 정상 회의 대화에서 같은 문장(8자+) 을 3번 연속 그대로 말하는 일은
# 사실상 없으므로 false positive 위험이 매우 낮다. Whisper 옵션들(temperature fallback /
# compression_ratio_threshold / hallucination_silence_threshold / no_repeat_ngram_size /
# repetition_penalty) 을 모두 적용했어도 가끔 슬립 스루 하는 케이스의 마지막 방어선.
import re as _re

# (1) 문장 단위 반복: 같은 8~80자 sentence 가 3회 이상 연속 → 1회로.
_SENT_REPETITION_RE = _re.compile(
    r"(?P<sent>[^?!.\n]{8,80}[?!.])(?:\s*(?P=sent)){2,}"
)
# (2) 문장 부호 없는 짧은 어구 반복(예: "안녕하세요 안녕하세요 안녕하세요"): 같은 5~40자
#     공백/시퀀스가 3회 이상 → 1회로. 회의에 같은 짧은 어구를 5회 이상 연속 말하지 않는다.
_PHRASE_REPETITION_RE = _re.compile(
    r"(?P<ph>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ]{4,40}?)(?:\s+(?P=ph)){4,}"
)
# (3) 짧은 토큰(1~7자) + 마침표/쉼표 반복(예: "충분히. 충분히. 충분히…" 13회).
#     temperature fallback + repetition_penalty 가 못 잡고 통과한 Whisper repetition
#     hallucination 의 한 패턴. 회의에서 같은 1~7자 단어를 마침표/쉼표로 끊어 3회 이상
#     연속 말하는 일은 사실상 없다(2026-05-21 attachment id=49 관측: "충분히." ×15,
#     "위원님," ×10).
_SHORT_TOKEN_REPETITION_RE = _re.compile(
    r"(?P<tok>[가-힣A-Za-z0-9]{1,7})(?P<sep>[.,!?])(?:\s*(?P=tok)(?P=sep)){2,}"
)
# (4) 단일 글자 과도 반복(예: "네네네네네네네네…" 14회). 정상 한국어에서 같은 글자가
#     6회 이상 연속되는 경우는 없다("ㅋㅋㅋ" 같은 자모 단독은 회의록 입력에 부적합).
_CHAR_REPETITION_RE = _re.compile(r"([가-힣A-Za-z])\1{5,}")
# (5) 숫자 나열 환각(예: "1, 2, 3, 4, 5, 6, 7, 8."). Whisper 가 무음 구간에 시퀀스 토큰을
#     쏟아내는 패턴. 4개 이상의 연속 정수가 쉼표/공백으로 나열되면 환각으로 본다.
_NUMBER_SEQUENCE_RE = _re.compile(r"(?:\b\d{1,2}\b[\s,.]*){4,}")
# (6) 무대지시 브래킷(예: "[(라멘을 풀고 있음)] [(엄청크)]"). YouTube 자막 학습 흔적으로,
#     사내 회의 녹취에는 절대 등장하지 않는 패턴.
_STAGE_DIRECTION_RE = _re.compile(r"\[\([^)]{1,60}\)\]")

# 알려진 한국어 STT 환각 phrases — YouTube 자막/방송 데이터로 학습된 Whisper 가 회의
# 도입부 무음/저음질 구간에 끼워 넣는 흔한 패턴들. 정상 회의에서 등장할 가능성이 매우
# 낮은 정확한 문구만 등록(false positive 회피). 대소문자/문장부호 변형 허용.
# 단일 발화로 1회만 통과한 케이스(repetition regex 가 못 잡음) 까지 모두 제거 대상.
_KNOWN_HALLUCINATION_PATTERNS = [
    # YouTube/방송 도입부 클리셰
    r"오늘의\s*주인공은\s*누구(?:일까요|입니까)\s*[?!.]?",
    r"오늘의\s*주인공은\s*저\s*요\s*[?!.]?",
    r"저요[,\s]*저요[,\s]*저요[,\s]*?(?:저요[,\s]*?){0,5}",
    # 방송/유튜브 마무리 클리셰
    r"구독(?:과|\s*하기)?\s*(?:좋아요|알림설정)\s*(?:부탁드립니다|해\s*주세요|꼭\s*눌러주세요)\s*[?!.]?",
    r"시청해\s*주셔서\s*감사합니다\s*[?!.]?",
    r"다음\s*(?:영상|시간)에(?:서)?\s*(?:만나요|뵙겠습니다)\s*[?!.]?",
    # 인사 클리셰 단독 반복 (회의에선 한 번 정도는 정상 — 연속만 잡음)
    r"(?:안녕하세요\s*여러분\s*[?!.]?\s*){2,}",
]
_HALLUCINATION_RES = [_re.compile(p) for p in _KNOWN_HALLUCINATION_PATTERNS]


def _strip_repetition_hallucination(text: str) -> str:
    """반복 환각 패턴 + 알려진 환각 phrase 를 제거.

    예) '오늘의 주인공은 누구일까요? 오늘의 주인공은 누구일까요? …' (×13) → 제거.
    회의 본문엔 영향 없음(정상 한국어 대화 패턴이 false positive 될 가능성 매우 낮음).
    """
    if not text:
        return text
    cleaned = _SENT_REPETITION_RE.sub(lambda m: m.group("sent"), text)
    cleaned = _PHRASE_REPETITION_RE.sub(lambda m: m.group("ph"), cleaned)
    # 짧은 토큰 + 구두점 반복("충분히. 충분히. ...") → 토큰 한 번만 남긴다.
    cleaned = _SHORT_TOKEN_REPETITION_RE.sub(
        lambda m: f"{m.group('tok')}{m.group('sep')}", cleaned
    )
    # 단일 글자 과도 반복("네네네네…") → 1글자만 남긴다. 정상 한국어가 false positive
    # 될 가능성 없음(같은 글자 6회+ 연속은 사람이 발화하지 않는다).
    cleaned = _CHAR_REPETITION_RE.sub(r"\1", cleaned)
    # 숫자 나열 환각("1, 2, 3, 4, 5, 6, 7, 8") → 통째로 제거. 회의 본문의 정상 숫자
    # 인용(예: "5월 20일" / "100만 원")은 4개 이상 연속이 아니라 단발이라 안전.
    cleaned = _NUMBER_SEQUENCE_RE.sub(" ", cleaned)
    # 무대지시 브래킷("[(라멘을 풀고 있음)]") 은 통째로 제거. 회의 녹취에 본질적으로
    # 등장할 수 없는 표기.
    cleaned = _STAGE_DIRECTION_RE.sub(" ", cleaned)
    # 알려진 환각 phrase — repetition 후 1회만 남은 케이스까지 통째로 strip.
    # YouTube 자막 사전학습 흔적이 회의 도입부에 끼어드는 사고 차단.
    for rx in _HALLUCINATION_RES:
        cleaned = rx.sub(" ", cleaned)
    # 연속 공백 정리.
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


@dataclass
class TranscriptResult:
    """STT 결과. text 는 전체 전사문, duration 은 오디오 길이(초)."""

    text: str
    duration_sec: float
    language: str


def _get_model():
    """WhisperModel 싱글턴. 첫 호출 시 모델 로드(필요 시 HuggingFace 에서 1회 다운로드)."""
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            logger.warning(
                "[stt] loading whisper model=%s device=%s compute=%s",
                _MODEL_SIZE, _DEVICE, _COMPUTE_TYPE,
            )
            _model = WhisperModel(
                _MODEL_SIZE, device=_DEVICE, compute_type=_COMPUTE_TYPE
            )
    return _model


# Whisper 가 기대하는 입력 형식: 16kHz mono float32 numpy 배열.
_TARGET_SR = 16_000
# 정규화 목표 RMS — 약 -20 dBFS. 너무 높이면 클리핑/잡음 증폭, 너무 낮으면 무음 오판.
# 회의 녹음(휴대폰/회의실 마이크)의 입력 레벨이 일관되게 낮은 케이스에서 가장 효과적이다.
_TARGET_RMS = 0.1
# 정규화 게인 한도 — 잡음/공실 톤만 있는 구간을 과도하게 증폭해 환각을 유발하지 않도록
# 최대 12dB(약 4배)로 제한한다.
_MAX_GAIN = 4.0


def _decode_and_normalize(audio_bytes: bytes) -> "tuple[object, float] | None":
    """PyAV 로 오디오 → 16kHz mono float32 numpy + 라우드니스 정규화.

    실패하거나 numpy/PyAV 가 없으면 None 을 반환해 호출자가 원래 바이트 경로로 폴백한다.
    회의 녹음의 RMS 가 낮을수록 효과가 크다(저음량 회의실 마이크 → +6~12 dB 증폭).
    """
    try:
        import av  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None

    try:
        container = av.open(io.BytesIO(audio_bytes))
    except Exception as exc:
        logger.warning("[stt] preprocess: av.open 실패 — %s (원본 바이트 사용)", exc)
        return None

    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            logger.warning("[stt] preprocess: 오디오 스트림 없음 — 원본 바이트 사용")
            return None

        # PyAV AudioResampler 로 16kHz / mono / fltp(float32 planar) 통일.
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=_TARGET_SR)
        chunks: list = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                arr = resampled.to_ndarray()  # shape: (1, N)
                chunks.append(arr.reshape(-1).astype(np.float32, copy=False))
        # 마지막 flush.
        for resampled in resampler.resample(None):
            arr = resampled.to_ndarray()
            chunks.append(arr.reshape(-1).astype(np.float32, copy=False))

        if not chunks:
            return None
        audio = np.concatenate(chunks)
        if audio.size == 0:
            return None

        # 라우드니스 정규화 — RMS 기준. 무음 구간이 많으면 RMS 가 0 에 가까우므로 게인을
        # 제한해 잡음 증폭을 방지.
        rms = float(np.sqrt(np.mean(audio * audio)))
        if rms > 1e-5:
            gain = min(_TARGET_RMS / rms, _MAX_GAIN)
            if gain > 1.05:  # 5% 미만 차이면 굳이 건드리지 않는다(원본 보존).
                audio = audio * gain
                # 클리핑 방지 — 피크가 0.99 를 넘으면 그 비율로 축소.
                peak = float(np.max(np.abs(audio)))
                if peak > 0.99:
                    audio = audio * (0.99 / peak)
                logger.warning(
                    "[stt] preprocess: gain x%.2f (rms %.4f → %.4f)",
                    gain, rms, float(np.sqrt(np.mean(audio * audio))),
                )
        duration = float(audio.shape[0]) / _TARGET_SR
        return audio, duration
    except Exception as exc:
        logger.warning("[stt] preprocess 실패 — %s (원본 바이트 사용)", exc)
        return None
    finally:
        container.close()


def transcribe(audio_bytes: bytes, *, language: str | None = "ko") -> TranscriptResult:
    """오디오 바이트를 텍스트로 변환.

    Args:
        audio_bytes: m4a/mp3/wav 등 인코딩된 오디오 파일 바이트.
        language: 강제 언어 코드. None 이면 자동 감지. 사내 회의는 기본 'ko'.

    Returns:
        TranscriptResult(text, duration_sec, language).

    Raises:
        ValueError: audio_bytes 가 비어 있는 경우.
        RuntimeError: 오디오 디코딩/추론 실패.
    """
    if not audio_bytes:
        raise ValueError("빈 오디오 데이터입니다.")

    model = _get_model()
    try:
        # vad_filter 는 기본 비활성화. faster-whisper 1.2.1 의 번들 Silero VAD 가 실제
        # 음성이 있는 회의 녹음에서도 0개 세그먼트를 반환하는 문제가 관측되어(전사 결과
        # 공백), VAD 없이 전체 오디오를 처리한다. 회의 녹음은 대부분 발화 구간이라 무방.
        # 정확도 향상을 위해 WHISPER_VAD=1 로 명시 활성화 가능(보수적 파라미터).
        #
        # 옵션 조정 이력(2026-05-20):
        #  1차) 정확도 명목으로 긴 initial_prompt + condition_on_previous_text=True +
        #       temperature 전체 fallback [0.0~1.0] + 엄격 임계값을 켰더니, prompt 본문이
        #       회의 첫 줄에 환각 누출되고 같은 파일에서 STT 출력이 6267→5493→1382 자로
        #       흔들리며 비결정적이 됨.
        #  2차) 전부 빼고 temperature=0.0 단일로 갔더니 결정성·prompt leak 은 해결됐으나
        #       회의 도입부 무음 구간에서 "오늘의 주인공은 누구일까요?" 가 100+ 회 반복되는
        #       repetition hallucination 이 그대로 남음 (Whisper 의 잘 알려진 이슈 —
        #       temperature fallback / compression_ratio_threshold 가 정확히 이걸 잡아준다).
        #  3차) 균형: 반복 환각은 차단하되 fallback 의 비결정성 영향은 최소화.
        #
        # 최종 조합(2026-05-21):
        # - initial_prompt=None (필요 시 .env 짧게) — prompt leak 차단.
        # - condition_on_previous_text=False — snowball(세그먼트 환각 전파) 차단.
        # - temperature=[0.0, 0.2, 0.4] — 반복 환각 시 단계적 fallback (0.6+ 는 생략,
        #   정상 세그먼트 폐기 위험).
        # - compression_ratio_threshold=2.4 — 같은 토큰 반복 비율 높으면 fallback 트리거.
        # - hallucination_silence_threshold=2.0 — 회의 도입부 무음(2초+) 구간에서 만들어진
        #   환각("오늘의 주인공은 누구일까요?" 13회 반복 같은 사고)을 직접 폐기.
        #   3차 시도에서 위 두 옵션만으로는 이 패턴이 통과되어 추가 방어.
        # - no_repeat_ngram_size=3 — 같은 3-gram("오늘의 주인공은" 등) 반복 생성 차단.
        # - repetition_penalty=1.15 — 같은 토큰 재선택 logit 패널티. 너무 높이면 정상
        #   조사·종결어미('-요','-입니다')가 자연 반복돼야 하는 한국어를 망쳐 1.1~1.2 권장.
        # - log_prob_threshold / no_speech_threshold: faster-whisper 기본값.
        # - without_timestamps=True — 토큰 예산을 본문 인식에 집중. 회의록 용도에선
        #   세그먼트별 타임스탬프가 필요 없고, 토큰 절약이 환각 감소에 미세하게 기여.
        kwargs: dict = {
            "language": language,
            "beam_size": _BEAM_SIZE,
            "vad_filter": _VAD_ENABLED,
            "temperature": [0.0, 0.2, 0.4],
            "condition_on_previous_text": False,
            "compression_ratio_threshold": 2.4,
            "hallucination_silence_threshold": 2.0,
            "no_repeat_ngram_size": 3,
            "repetition_penalty": 1.15,
            "without_timestamps": True,
        }
        if _VAD_ENABLED:
            # 회의 발화 보존 우선 — 너무 공격적인 VAD 는 첫 음절을 깎는다.
            kwargs["vad_parameters"] = {
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 400,
            }
        if _INITIAL_PROMPT:
            kwargs["initial_prompt"] = _INITIAL_PROMPT

        # 오디오 전처리(16kHz mono + 라우드니스 정규화) — 가능하면 numpy 배열 경로로
        # 모델에 직접 넘긴다. 실패하거나 비활성화면 인코딩된 바이트 경로 폴백.
        audio_input: object
        if _PREPROCESS_ENABLED:
            pre = _decode_and_normalize(audio_bytes)
            if pre is not None:
                audio_input, _dur = pre
                logger.warning(
                    "[stt] preprocess ok — duration=%.1fs (16kHz mono normalized)", _dur
                )
            else:
                audio_input = io.BytesIO(audio_bytes)
        else:
            audio_input = io.BytesIO(audio_bytes)

        segments, info = model.transcribe(audio_input, **kwargs)
        # segments 는 지연 평가 제너레이터 — 여기서 iterate 하며 실제 추론이 수행된다.
        parts = [seg.text.strip() for seg in segments]
    except Exception as e:  # PyAV 디코드 실패 / CTranslate2 추론 오류 등.
        raise RuntimeError(f"음성 인식 실패: {e}") from e

    text = " ".join(p for p in parts if p)
    cleaned = _strip_repetition_hallucination(text)
    if cleaned != text:
        logger.warning(
            "[stt] post-clean: removed %d chars of repetition", len(text) - len(cleaned),
        )
        text = cleaned
    logger.warning(
        "[stt] done — duration=%.1fs language=%s chars=%d",
        info.duration, info.language, len(text),
    )
    return TranscriptResult(
        text=text,
        duration_sec=float(info.duration),
        language=info.language,
    )


__all__ = ["TranscriptResult", "transcribe"]
