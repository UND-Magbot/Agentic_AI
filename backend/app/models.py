"""
ORM 모델 — 02_users.sql 로 이미 생성된 스키마에 매핑.

PostgreSQL 측 ENUM 타입 (user_role, user_domain) 은 init SQL 에서 만들었으므로
SQLAlchemy 가 다시 생성하지 않도록 create_type=False / native_enum=True 로 둔다.
"""
from datetime import datetime
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    domain_admin = "domain_admin"
    member = "member"
    viewer = "viewer"


class UserDomain(str, enum.Enum):
    all = "all"
    finance = "finance"
    sales = "sales"
    design = "design"
    develop = "develop"


# DB 측 ENUM 을 그대로 사용 (init SQL 에서 생성됨).
_user_role_enum = SAEnum(
    UserRole,
    name="user_role",
    native_enum=True,
    create_type=False,
    values_callable=lambda x: [m.value for m in x],
)
_user_domain_enum = SAEnum(
    UserDomain,
    name="user_domain",
    native_enum=True,
    create_type=False,
    values_callable=lambda x: [m.value for m in x],
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    role: Mapped[UserRole] = mapped_column(_user_role_enum, nullable=False, default=UserRole.member)
    domain: Mapped[UserDomain] = mapped_column(_user_domain_enum, nullable=False, default=UserDomain.all)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # N:M (User ↔ Permission). secondary 테이블을 명시적으로 잡는다.
    permissions: Mapped[list["Permission"]] = relationship(
        "Permission",
        secondary="user_permissions",
        primaryjoin="User.id == foreign(UserPermission.user_id)",
        secondaryjoin="Permission.id == foreign(UserPermission.permission_id)",
        viewonly=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_domain", "domain"),
        Index("ix_users_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role.value}>"


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    domain: Mapped[UserDomain] = mapped_column(_user_domain_enum, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_permissions_domain", "domain"),)

    def __repr__(self) -> str:
        return f"<Permission code={self.code!r} domain={self.domain.value}>"


class UserPermission(Base):
    __tablename__ = "user_permissions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversation(Base):
    """대화 한 건. messages 와 1:N. 03_conversations.sql 로 테이블이 미리 생성됨."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[UserDomain] = mapped_column(
        _user_domain_enum, nullable=False, default=UserDomain.all
    )
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index("ix_conversations_domain", "domain"),
        Index("ix_conversations_user_starred", "user_id", "starred"),
    )


class Attachment(Base):
    """업로드된 첨부 파일 메타데이터.

    실제 객체는 MinIO 의 `bucket/key` 에 저장되고, 이 행은 그 포인터 + 원본 메타.
    1차 구현에서는 conversation/message 와의 연결은 nullable — 사용자가 입력창에 첨부했지만
    아직 발화하지 않은 상태도 허용. 메시지 전송 시점에 conversation_id/message_id 를 채운다.
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="CASCADE")
    )
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_attachments_user_created", "user_id", "created_at"),
        Index("ix_attachments_conversation", "conversation_id"),
        Index("ix_attachments_message", "message_id"),
    )


class Message(Base):
    """대화 안의 단일 메시지. role: user/assistant/system."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[UserDomain | None] = mapped_column(_user_domain_enum)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages")

    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)


class Project(Base):
    """프로젝트(대화 그룹). Claude.ai 의 'Projects' 와 동일한 컨셉.

    - system_prompt 는 그룹 전체에 적용되는 Instructions.
    - domain 은 권장 도메인(NULL=자동) — 카드 색 스트립과 새 대화 기본값에 활용.
    - 대화 ↔ 프로젝트 연결은 N:M 매핑 테이블(project_conversations)로 관리하지만
      현재 정책은 한 대화당 0~1개 프로젝트.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[UserDomain | None] = mapped_column(_user_domain_enum)
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_projects_user_updated", "user_id", "updated_at"),
        Index("ix_projects_user_starred", "user_id", "starred"),
    )


class ProjectConversation(Base):
    """프로젝트 ↔ 대화 N:M 매핑."""

    __tablename__ = "project_conversations"

    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_project_conversations_project", "project_id", "added_at"),
        Index("ix_project_conversations_conversation", "conversation_id"),
    )


class MailAccount(Base):
    """메일 발신 계정 / SMTP / 시그니처 / 기본 수신자.

    provider 컬럼으로 hiworks / naver / gmail 등 사이트 구분. user_id 는 NULL 허용 —
    1차는 단일 발신자 운영, 추후 사용자별 row 로 확장. (provider, username) 으로 UPSERT.
    """

    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255))
    signature: Mapped[str | None] = mapped_column(Text)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_to: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    default_cc: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    default_cc_all: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("provider", "username", name="uq_mail_accounts_provider_username"),
        Index("ix_mail_accounts_user_provider", "user_id", "provider"),
        Index("ix_mail_accounts_provider", "provider"),
    )


class Vendor(Base):
    """수금 거래처 마스터 — 재무팀 수금확인거래처 명(canonical)과 집행일 규칙.

    거래처별로 비영업일/말일 집행 시 방향(전진/후진)과 회수유형을 영구 저장한다.
    계획표 자동기입 시 이 canonical_name 으로 치환하고 direction 으로 날짜를 보정한다.
    name_norm 은 정규화(㈜·주식회사·공백 제거) 키로 UNIQUE — 중복/매칭 기준.
    """

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    currency: Mapped[str | None] = mapped_column(String(8))            # 원화 / 외화
    collection_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="미정"              # 말일/고정일/어음/미정
    )
    collection_day: Mapped[int | None] = mapped_column(Integer)        # 고정일형 회수일
    direction: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="전진"               # 후진/전진/미정
    )
    direction_source: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    aliases: Mapped[list["VendorAlias"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )


class VendorAlias(Base):
    """거래처 별칭 — 계획표 옛 표기 → canonical 치환용.

    개명/약칭(예: 구 상호 → 새 상호)을 매핑한다. alias_norm 은 전역 UNIQUE 라
    치환 조회가 모호하지 않다.
    """

    __tablename__ = "vendor_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    vendor: Mapped["Vendor"] = relationship(back_populates="aliases")

    __table_args__ = (Index("ix_vendor_aliases_vendor", "vendor_id"),)
