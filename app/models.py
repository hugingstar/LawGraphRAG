import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


# 사건 처리 상태
#   review_requested     검토 요청 접수
#   in_review            안전부서 검토중
#   supplement_requested 내용이 부족해 요청자에게 보완 요청
#   completed            검토 완료
INCIDENT_STATUSES = (
    "review_requested",
    "in_review",
    "supplement_requested",
    "supplement_completed",
    "completed",
)

INCIDENT_STATUS_LABELS = {
    "review_requested": "검토 요청",
    "in_review": "검토중",
    "supplement_requested": "보완 요청",
    "supplement_completed": "보완 완료",
    "completed": "검토 완료",
}

# 사건 스레드 메시지 종류
COMMENT_KINDS = ("comment", "supplement_request", "supplement_reply", "follow_up", "conclusion")

COMMENT_KIND_LABELS = {
    "comment": "코멘트",
    "supplement_request": "보완 요청",
    "supplement_reply": "보완 내용",
    "follow_up": "추가 문의",
    "conclusion": "최종 검토 결과",
}

# requester: 각 부서에서 심층 검토를 요청하는 신청자
# manager: 안전부서에서 심층 검토를 수행하는 관리자
USER_ROLES = ("requester", "manager")

USER_ROLE_LABELS = {"requester": "신청자", "manager": "안전부서 관리자"}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="requester")
    rank: Mapped[str | None] = mapped_column(String)  # 직급
    contact: Mapped[str | None] = mapped_column(String)  # 연락처
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    department: Mapped["Department | None"] = relationship()
    site: Mapped["Site | None"] = relationship()

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @property
    def role_label(self) -> str:
        return USER_ROLE_LABELS.get(self.role, self.role)


class UserSession(Base):
    """서버 측 세션. 쿠키에는 추측 불가능한 토큰만 담기므로 쿠키 서명 라이브러리가 필요 없고,
    로그아웃은 해당 행을 삭제하는 것으로 즉시 무효화된다."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship()


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    incidents: Mapped[list["Incident"]] = relationship(back_populates="department")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    incidents: Mapped[list["Incident"]] = relationship(back_populates="site")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)

    # 구조화된 신고 항목. statement는 이 항목들을 합쳐 만든 분석 대상 원문이며,
    # citations의 char offset이 statement를 기준으로 하므로 저장 후 변경하지 않는다.
    reporter_name: Mapped[str | None] = mapped_column(String)  # 작성자 이름
    reporter_rank: Mapped[str | None] = mapped_column(String)  # 작성자 직급
    reporter_contact: Mapped[str | None] = mapped_column(String)  # 작성자 연락처
    reporter_info: Mapped[str | None] = mapped_column(Text)  # (구버전) 통합 인적사항
    occurred_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))  # 사고일시
    location: Mapped[str | None] = mapped_column(Text)  # 사고장소
    background: Mapped[str | None] = mapped_column(Text)  # 사고경위
    situation: Mapped[str | None] = mapped_column(Text)  # 당시상황
    action_taken: Mapped[str | None] = mapped_column(Text)  # 조치내용
    damage: Mapped[str | None] = mapped_column(Text)  # 피해상황

    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="review_requested")
    citations: Mapped[list | None] = mapped_column(JSON)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # 최초로 "검토 시작"을 누른 관리자. 그 뒤로는 이 사람만 코멘트/보완요청/최종결과를 남길 수 있다.
    assigned_manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    department: Mapped["Department"] = relationship(back_populates="incidents")
    site: Mapped["Site"] = relationship(back_populates="incidents")
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_user_id])
    assigned_manager: Mapped["User | None"] = relationship(foreign_keys=[assigned_manager_id])
    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentEvent.created_at"
    )
    comments: Mapped[list["IncidentComment"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentComment.created_at"
    )
    attachments: Mapped[list["IncidentAttachment"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentAttachment.created_at"
    )

    @property
    def reporter_summary(self) -> str:
        parts = [p for p in (self.reporter_name, self.reporter_rank, self.reporter_contact) if p]
        return " / ".join(parts) if parts else (self.reporter_info or "")


class IncidentEvent(Base):
    """사건의 상태 변경 이력(감사 추적). incidents.status는 '현재 상태'를 빠르게 조회하기 위한
    비정규화 값이고, 실제 검토 이력(누가 언제 무엇으로 바꿨는지)의 근거는 이 테이블에 누적된다."""

    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="events")
    actor: Mapped["User | None"] = relationship()


class IncidentComment(Base):
    """요청자와 안전부서가 주고받는 스레드. 보완 요청/보완 내용/추가 문의/최종 결과가 모두
    여기에 시간순으로 누적되며, 수정·삭제 없이 append-only로 남는다."""

    __tablename__ = "incident_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String, nullable=False, default="comment")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="comments")
    author: Mapped["User | None"] = relationship()


class IncidentAttachment(Base):
    """사건에 첨부된 증빙 파일(PDF, DOCX 등). 별도 파일 스토리지 없이 파일 내용을
    DB에 직접 저장한다 — 이 앱 규모에서는 S3 등 외부 스토리지를 두는 것보다 단순하다."""

    __tablename__ = "incident_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="attachments")
    uploaded_by: Mapped["User | None"] = relationship()


class Law(Base):
    __tablename__ = "laws"

    id: Mapped[int] = mapped_column(primary_key=True)
    law_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    law_name: Mapped[str] = mapped_column(String, nullable=False)
    law_type: Mapped[str | None] = mapped_column(String)
    promulgation_date: Mapped[datetime.date | None] = mapped_column(Date)
    effective_date: Mapped[datetime.date | None] = mapped_column(Date)
    last_synced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    articles: Mapped[list["Article"]] = relationship(back_populates="law", cascade="all, delete-orphan")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("law_id", "article_no", "article_no_sub"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    law_id: Mapped[int] = mapped_column(ForeignKey("laws.id", ondelete="CASCADE"), nullable=False)
    article_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_no_sub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[datetime.date | None] = mapped_column(Date)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    law: Mapped["Law"] = relationship(back_populates="articles")
    chunks: Mapped[list["ArticleChunk"]] = relationship(back_populates="article", cascade="all, delete-orphan")

    @property
    def article_label(self) -> str:
        if self.article_no_sub:
            return f"제{self.article_no}조의{self.article_no_sub}"
        return f"제{self.article_no}조"


class ArticleChunk(Base):
    __tablename__ = "article_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    article: Mapped["Article"] = relationship(back_populates="chunks")
