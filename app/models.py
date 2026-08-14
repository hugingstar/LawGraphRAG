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
#   in_review            담당자 검토중
#   supplement_requested 내용이 부족해 신고자에게 보완 요청
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

# requester: 사건을 신고하고 심층 검토를 요청하는 신청자
# manager: 심층 검토를 수행하는 담당자
USER_ROLES = ("requester", "manager")

USER_ROLE_LABELS = {"requester": "신청자", "manager": "검토 담당자"}

# 일반 시민 대상 서비스라 회사 직급이 아니라 직종(occupation) 대분류에서 고른다.
OCCUPATIONS = (
    ("office", "사무직"),
    ("service", "서비스직"),
    ("sales", "판매직"),
    ("production", "생산·제조직"),
    ("construction", "건설·기능직"),
    ("transport", "운전·운송직"),
    ("agriculture", "농림축산어업"),
    ("professional", "전문직 (의료·법률·교육 등)"),
    ("public_official", "공무원"),
    ("self_employed", "자영업"),
    ("student", "학생"),
    ("homemaker", "주부"),
    ("unemployed", "무직"),
    ("etc", "기타"),
)

OCCUPATION_LABELS = dict(OCCUPATIONS)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="requester")
    occupation: Mapped[str | None] = mapped_column(String)  # 직종 (OCCUPATIONS 코드)
    contact: Mapped[str | None] = mapped_column(String)  # 연락처
    # 사용자의 활동 지역. 사건의 '발생 지역'과는 별개이며(사건은 폼에서 직접 입력),
    # 신규 사건 작성 시 지역 셀렉트의 기본값으로만 쓰인다.
    sido_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"))
    sigungu_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sido: Mapped["Region | None"] = relationship(foreign_keys=[sido_code])
    sigungu: Mapped["Region | None"] = relationship(foreign_keys=[sigungu_code])

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @property
    def role_label(self) -> str:
        return USER_ROLE_LABELS.get(self.role, self.role)

    @property
    def occupation_label(self) -> str:
        """구버전 자유 텍스트 직급이 남아 있는 계정도 깨지지 않도록, 알려진 코드가 아니면
        저장된 값을 그대로 보여준다."""
        if not self.occupation:
            return ""
        return OCCUPATION_LABELS.get(self.occupation, self.occupation)


class UserSession(Base):
    """서버 측 세션. 쿠키에는 추측 불가능한 토큰만 담기므로 쿠키 서명 라이브러리가 필요 없고,
    로그아웃은 해당 행을 삭제하는 것으로 즉시 무효화된다."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship()


class Region(Base):
    """행정구역(시도/시군구)을 자기참조 한 테이블로 담는다.

    `code`는 통계청(KOSTAT) 행정구역 코드로, 시도는 2자리("11"=서울특별시),
    시군구는 5자리("11010"=종로구)이며 앞 2자리가 곧 소속 시도 코드다.
    지도 경계 파일(app/static/geo/*.topo.json)의 feature code와 같은 값을 쓰므로
    DB 집계 결과를 지도 도형에 바로 매칭할 수 있다(app/regions_seed.py 참고).
    """

    __tablename__ = "regions"

    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # "종로구"
    full_name: Mapped[str] = mapped_column(String, nullable=False)  # "서울특별시 종로구"
    level: Mapped[str] = mapped_column(String, nullable=False)  # 'sido' | 'sigungu'
    parent_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"))

    children: Mapped[list["Region"]] = relationship(
        back_populates="parent", remote_side="Region.parent_code"
    )
    parent: Mapped["Region | None"] = relationship(
        back_populates="children", remote_side="Region.code"
    )


class IncidentCategory(Base):
    """사건 유형(산업재해/교통사고/화재 등). 지역과 함께 대시보드 교차집계의 축이 된다."""

    __tablename__ = "incident_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    incidents: Mapped[list["Incident"]] = relationship(back_populates="category")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 사건이 '발생한' 지역. 신고자 소속(User.sido_code)과 다를 수 있으므로 사건마다 따로 받는다.
    sido_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"))
    sigungu_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("incident_categories.id"))

    # 구조화된 신고 항목. statement는 이 항목들을 합쳐 만든 분석 대상 원문이며,
    # citations의 char offset이 statement를 기준으로 하므로 저장 후 변경하지 않는다.
    reporter_name: Mapped[str | None] = mapped_column(String)  # 작성자 이름
    reporter_occupation: Mapped[str | None] = mapped_column(String)  # 작성자 직종 (레이블 텍스트 스냅샷)
    reporter_contact: Mapped[str | None] = mapped_column(String)  # 작성자 연락처
    reporter_info: Mapped[str | None] = mapped_column(Text)  # (구버전) 통합 인적사항
    occurred_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))  # 사고일시
    location: Mapped[str | None] = mapped_column(Text)  # 사고장소
    background: Mapped[str | None] = mapped_column(Text)  # 경위
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

    sido: Mapped["Region | None"] = relationship(foreign_keys=[sido_code])
    sigungu: Mapped["Region | None"] = relationship(foreign_keys=[sigungu_code])
    category: Mapped["IncidentCategory | None"] = relationship(back_populates="incidents")
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
        parts = [p for p in (self.reporter_name, self.reporter_occupation, self.reporter_contact) if p]
        return " / ".join(parts) if parts else (self.reporter_info or "")

    @property
    def region_label(self) -> str:
        """'서울특별시 종로구'처럼 사람이 읽는 지역 표기. 시군구가 없으면 시도만 반환한다."""
        if self.sigungu:
            return self.sigungu.full_name
        if self.sido:
            return self.sido.full_name
        return ""


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
    # full_text의 해시. 재수집 때 내용이 그대로면 청킹·임베딩을 통째로 건너뛴다.
    content_hash: Mapped[str | None] = mapped_column(String)
    # 그래프 추출(Gemini 호출)을 끝낸 시각. 값이 있으면 graph_ingest가 건너뛴다.
    # 조문 하나당 LLM 1회라 이 표시가 없으면 재실행마다 전량 재호출된다.
    graph_synced_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
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
