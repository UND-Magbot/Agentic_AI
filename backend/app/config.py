import os
from pathlib import Path

from dotenv import load_dotenv

# 루트 .env (c:\UND_AI_Agent\.env) 를 명시적으로 로드.
# uvicorn 은 .env 를 자동 로드하지 않으므로 여기서 처리.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ROOT_ENV)


def _build_database_url() -> str:
    """DATABASE_URL 우선, 없으면 POSTGRES_* 조합으로 생성. 드라이버는 asyncpg 강제."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        user = os.environ.get("POSTGRES_USER", "und")
        pw = os.environ.get("POSTGRES_PASSWORD", "und")
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5432")
        db = os.environ.get("POSTGRES_DB", "agent_ai")
        url = f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}"
    # SQLAlchemy 비동기 사용을 위해 드라이버를 asyncpg 로 표준화.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Settings:
    # --- LLM (Ollama / Gemma3) ---
    # 전 스택 Ollama 이전 완료. vLLM/Qwen 경로는 파기(백업: _backup_qwen_*).
    # Ollama(UND-AI GPU 서버) — 외부 호출 시 OLLAMA_HOST=0.0.0.0 으로 띄운 서버 주소.
    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
    # 모든 Ollama 호출의 컨텍스트 크기 — 하나로 통일한다. 요청마다 다르면 Ollama 가 모델을 다시 올려
    # 약 3초씩 지연된다(2026-09-28 실측: 채팅 4K ↔ 제안서 16K 전환마다 재적재).
    # 2026-09-29 서버 동시 처리 8칸 증설 후: 32K 는 GPU 91%(일부 CPU, 1인 속도 절반), 24K 는 100%.
    ollama_num_ctx: int = int(os.environ.get("OLLAMA_NUM_CTX", "24576"))
    # 제안서 기능 전용 모델 — 속도보다 품질 우선이라 공용 모델과 분리한다.
    # 2026-09-23 비교: gemma4:26b(MoE)가 수치 날조 0건·복붙률 4%로 가장 좋았다.
    proposal_model: str = os.environ.get("PROPOSAL_MODEL", "gemma4:26b-a4b-it-qat")
    proposal_think: bool = os.environ.get("PROPOSAL_THINK", "1").lower() not in ("0", "false", "no")
    # 회사 제품 추천만 다른 사내 모델로(비우면 proposal_model). 제안서 쪽 설정과 따로 바꿀 수 있게.
    recommend_model: str = os.environ.get("RECOMMEND_MODEL", "")
    # draw.io 데스크톱 실행 파일(개념도 PNG 내보내기). 비우면 PATH·기본 설치 경로에서 찾는다.
    drawio_exe: str = os.environ.get("DRAWIO_EXE", "")
    port: int = int(os.environ.get("PORT", "8000"))
    cors_origins: list[str] = [
        o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
    ]

    # --- Database ---
    database_url: str = _build_database_url()
    db_pool_size: int = int(os.environ.get("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
    db_echo: bool = os.environ.get("DB_ECHO", "false").lower() == "true"

    # --- 내부 서비스 토큰 (BFF → Agent 호출 보호) ---
    # req.md §5: Agent Runtime 은 BFF 가 발급한 단명/공유 서비스 토큰으로만 호출 가능.
    # /v1/generate · /v1/title 같은 무인증 LLM/도구 경로를 외부 직접 접근으로부터 보호한다.
    # 비어 있으면(개발 편의) 가드를 건너뛴다 — 설정되면 X-Internal-Token 헤더 일치를 강제.
    internal_service_token: str = os.environ.get("INTERNAL_SERVICE_TOKEN", "")

    # --- Auth / JWT ---
    jwt_secret: str = os.environ.get("JWT_SECRET", "replace-with-256bit-secret")
    jwt_algorithm: str = os.environ.get("JWT_ALGORITHM", "HS256")
    # JWT_EXPIRES_IN 은 "30m" / "1h" / "7d" 형식. 분 단위 정수도 허용.
    jwt_expires_in: str = os.environ.get("JWT_EXPIRES_IN", "30m")
    # 응답 최대 토큰. 기본 2048 — 한국어 답변 2~4 단락 + 표/예시 까지 잘리지 않는 수준.
    # 더 짧게/길게 쓰려면 .env 의 MAX_TOKENS 로 조정.
    max_tokens: int = int(os.environ.get("MAX_TOKENS", "2048"))
    # 모델 컨텍스트 길이(입력+출력 합 상한). Gemma3 는 8192 지원. .env 의 MAX_MODEL_LEN 으로 조정.
    max_model_len: int = int(os.environ.get("MAX_MODEL_LEN", "8192"))
    # Tavily 외부 검색. 비어있으면 search_web 도구가 안내 메시지를 반환한다.
    tavily_api_key: str = os.environ.get("TAVILY_API_KEY", "")

    # --- Codex 브리지 (docker-codeserver/codex-bridge) ---
    # gemma 가 못 하는 웹 검색·이미지 생성·심층 분석을 Codex(ChatGPT 계정)에 위임한다.
    # URL/토큰이 비어 있으면 Codex 위임 fast-path 전체가 비활성.
    codex_bridge_url: str = os.environ.get("CODEX_BRIDGE_URL", "").rstrip("/")
    codex_bridge_token: str = os.environ.get("CODEX_BRIDGE_TOKEN", "")
    # search_web 제공자: codex | tavily.
    search_provider: str = os.environ.get("SEARCH_PROVIDER", "tavily").strip().lower()
    # 사내 자료(RAG 발췌)를 외부 Codex(OpenAI)로 보내도 되는지. 사내 데이터 반출이므로 명시적 opt-in.
    codex_allow_internal_data: bool = os.environ.get(
        "CODEX_ALLOW_INTERNAL_DATA", "false"
    ).lower() in ("1", "true", "yes")
    # --- MinIO (객체 저장) ---
    # docker-compose 의 minio 서비스를 default endpoint 로. 외부 배포 시 .env 로 override.
    minio_endpoint: str = os.environ.get("MINIO_ENDPOINT", "minio:9000")
    minio_access_key: str = os.environ.get("MINIO_ROOT_USER", "cortex")
    minio_secret_key: str = os.environ.get("MINIO_ROOT_PASSWORD", "change-me-min-8")
    minio_secure: bool = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    minio_bucket: str = os.environ.get("MINIO_BUCKET", "und-cortex-attachments")
    # 첨부 업로드 한도. 75MB — 1시간 내외 회의 녹음(m4a @128kbps mono ≈ 60MB) 수용.
    # 멀티모달 이미지/PDF/문서 공통. .env 의 ATTACHMENT_MAX_BYTES 로 override 가능.
    attachment_max_bytes: int = int(os.environ.get("ATTACHMENT_MAX_BYTES", str(75 * 1024 * 1024)))

    # --- Hiworks SMTP 메일 발송 ---
    # smtps.hiworks.com:465 (SSL). 인증은 사용자별 계정 ID + 비밀번호(또는 앱비번).
    # 다수 사용자 운영 시에는 DB 에 사용자별 자격증명 암호화 저장으로 확장. 1차는 단일 발신자.
    hiworks_smtp_host: str = os.environ.get("HIWORKS_SMTP_HOST", "smtps.hiworks.com")
    hiworks_smtp_port: int = int(os.environ.get("HIWORKS_SMTP_PORT", "465"))
    hiworks_smtp_use_ssl: bool = os.environ.get("HIWORKS_SMTP_USE_SSL", "true").lower() == "true"
    hiworks_smtp_username: str = os.environ.get("HIWORKS_SMTP_USERNAME", "")
    hiworks_smtp_password: str = os.environ.get("HIWORKS_SMTP_PASSWORD", "")
    # 발신자 이름(Display Name). 비어있으면 username 만 사용.
    hiworks_from_name: str = os.environ.get("HIWORKS_FROM_NAME", "")
    # 메일 본문 끝에 자동 부착되는 시그니처. .env 에는 한 줄에 \n 구분으로 작성.
    # 예: ㈜유엔디 프로 배재병\nPhone ...\nFax ...\n본사 ...\n공장 ...
    # 비어 있으면 시그니처 미부착.
    hiworks_signature: str = os.environ.get("HIWORKS_SIGNATURE", "").replace("\\n", "\n")
    # 사칙 일반 메일 송부 규정 (#161/#198) — 대표님/관련 담당자/sales@ 같은 일반 CC.
    # 일반 업무 메일에 사용. 콤마 구분 (예: a@x,b@x,c@x).
    hiworks_default_cc: list[str] = [
        e.strip() for e in os.environ.get("HIWORKS_DEFAULT_CC", "").split(",") if e.strip()
    ]
    # 사칙 연차 사용 보고 (#165/#202) — 전 직원 1주 전 공유. send_leave_email 기본 CC.
    # 콤마 구분.
    hiworks_default_cc_all: list[str] = [
        e.strip() for e in os.environ.get("HIWORKS_DEFAULT_CC_ALL", "").split(",") if e.strip()
    ]
    # 기본 TO. 테스트 단계에서는 본인 계정 한 개. 운영 시 전 직원 메일링리스트.
    hiworks_default_to: list[str] = [
        e.strip() for e in os.environ.get("HIWORKS_DEFAULT_TO", "").split(",") if e.strip()
    ]


settings = Settings()


def validate_security_settings() -> None:
    """기동 시 보안 필수 설정 검증.

    약한/기본 JWT 시크릿으로는 부팅을 거부한다. (req.md §5 — 비밀정보는 강한 값이어야 하며
    공개 플레이스홀더로 운영 금지. 약한 키는 superadmin 토큰 위조로 이어진다.)
    기존 호출부 시그니처는 변경하지 않으며, 신규 함수로만 추가한다.
    """
    s = settings.jwt_secret or ""
    placeholders = {"replace-with-256bit-secret", "change-me", "secret", ""}
    if s in placeholders:
        raise RuntimeError(
            "JWT_SECRET 이 기본 플레이스홀더입니다. "
            ".env 에 32바이트 이상의 강한 랜덤 시크릿을 설정한 뒤 다시 기동하세요. "
            "(예: python -c \"import secrets; print(secrets.token_urlsafe(48))\")"
        )
    if len(s.encode("utf-8")) < 32:
        raise RuntimeError(
            f"JWT_SECRET 이 너무 짧습니다(={len(s.encode('utf-8'))} bytes). "
            "RFC 7518 권장에 따라 32바이트 이상으로 설정하세요."
        )
