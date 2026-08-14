import datetime
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.annotate import annotate_text, citation_to_dict
from app.auth import require_login, require_manager
from app.db import get_session
from app.graph_incidents import sync_incident
from app.law_catalog import available_law_names
from app.models import (
    COMMENT_KIND_LABELS,
    COMMENT_KINDS,
    INCIDENT_STATUSES,
    Incident,
    IncidentAttachment,
    IncidentCategory,
    IncidentComment,
    IncidentEvent,
    Region,
    User,
)
from app.routers.regions import sido_list, sigungu_list
from app.serializers import incident_to_dict
from app.templating import templates

router = APIRouter()

# 첨부파일 제약: 문서/이미지 위주로, 실행 파일류는 허용하지 않는다.
ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png",
}
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024  # 20MB
MAX_ATTACHMENTS_PER_REQUEST = 5


@router.get("/request", response_class=HTMLResponse)
def request_page(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    # 이름/직종/연락처는 프로필에서 가져와 보여주기만 한다. 반면 '사건 발생 지역'은
    # 신고자 소속과 다를 수 있으므로 프로필에서 파생하지 않고 폼에서 직접 받는다
    # (프로필 지역은 셀렉트의 기본 선택값으로만 쓴다).
    # (미들웨어가 넘겨준 user는 detached라 관계 접근을 위해 세션에서 다시 읽는다)
    profile = session.get(User, user.id)
    return templates.TemplateResponse(
        "request.html",
        {
            "request": request,
            "active": "request",
            "profile": {
                "name": profile.display_name,
                "occupation": profile.occupation_label,
                "contact": profile.contact,
                "region": profile.sigungu.full_name if profile.sigungu else None,
            },
            "default_sido_code": profile.sido_code,
            "default_sigungu_code": profile.sigungu_code,
            "sido_list": sido_list(session),
            "sigungu_list": sigungu_list(session),
            "categories": session.query(IncidentCategory).order_by(IncidentCategory.id).all(),
            "available_laws": available_law_names(session),
            "wide": True,
        },
    )


def _parse_occurred_at(raw: str) -> datetime.datetime | None:
    """<input type="datetime-local">이 보내는 'YYYY-MM-DDTHH:MM' 문자열을 파싱한다."""
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def _compose_statement(
    *,
    reporter_summary: str,
    occurred_at: datetime.datetime | None,
    location: str,
    background: str,
    situation: str,
    action_taken: str,
    damage: str,
) -> str:
    """구조화된 항목을 하나의 분석 대상 원문으로 합친다.

    citations의 char offset이 이 문자열을 기준으로 잡히므로, 저장 이후에는 절대 재구성하지 않고
    DB의 statement 값을 그대로 화면에 표시한다.

    발생 지역·사건 유형은 일부러 넣지 않는다. 이 문자열이 곧 조문 검색의 질의문이라,
    "제주특별자치도" 같은 지역명이 섞이면 그 지명이 우연히 등장하는 무관한 조문이
    상위로 올라온다(실제로 제주 교통사고가 산업안전보건법 시행령의 제주 관련 조문에
    매칭된 사례가 있었다). 지역·유형은 구조화 컬럼으로 따로 저장되고 화면에도 별도 항목으로
    표시되므로 원문에 중복해 넣을 이유가 없다.
    """
    sections = [
        ("작성자 인적사항", reporter_summary),
        ("사고일시", occurred_at.strftime("%Y-%m-%d %H:%M") if occurred_at else ""),
        ("사고장소", location),
        ("경위", background),
        ("당시상황", situation),
        ("조치내용", action_taken),
        ("피해상황", damage),
    ]
    return "\n\n".join(f"{label}: {value.strip()}" for label, value in sections if value and value.strip())


def _resolve_region_and_category(
    session: Session, sido_code: str, sigungu_code: str, category_id: str
) -> tuple[Region, IncidentCategory | None]:
    """폼으로 받은 지역/유형 코드를 실제 레코드로 바꾼다.

    지역은 폼 입력이라 클라이언트가 임의 값을 보낼 수 있으므로, 실재하는 시군구인지와
    선택된 시도에 실제로 속하는지를 모두 확인한다(코드 앞 2자리 = 소속 시도).
    """
    region = session.get(Region, sigungu_code) if sigungu_code else None
    if region is None or region.level != "sigungu":
        raise HTTPException(status_code=400, detail="사건 발생 지역(시·군·구)을 선택해 주세요.")
    if region.parent_code != sido_code:
        raise HTTPException(status_code=400, detail="선택한 시·도와 시·군·구가 일치하지 않습니다.")

    category = None
    if category_id:
        category = session.get(IncidentCategory, int(category_id))
        if category is None:
            raise HTTPException(status_code=400, detail="알 수 없는 사건 유형입니다.")
    return region, category


def _validate_attachments(files: list[UploadFile]) -> list[UploadFile]:
    files = [f for f in files if f and f.filename]
    if len(files) > MAX_ATTACHMENTS_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"첨부파일은 최대 {MAX_ATTACHMENTS_PER_REQUEST}개까지 가능합니다.")
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_ATTACHMENT_EXTENSIONS))
            raise HTTPException(status_code=400, detail=f"'{f.filename}'은(는) 허용되지 않는 파일 형식입니다. 허용 형식: {allowed}")
    return files


@router.post("/api/incidents")
def create_incident(
    sido_code: str = Form(...),
    sigungu_code: str = Form(...),
    category_id: str = Form(""),
    occurred_at: str = Form(""),
    location: str = Form(""),
    background: str = Form(...),
    situation: str = Form(""),
    action_taken: str = Form(""),
    damage: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    # 작성자 정보(이름/직종/연락처)는 여전히 프로필에서 가져온다 — 클라이언트 값을 신뢰하면
    # 타인을 사칭한 접수가 가능해지기 때문이다. 반면 '사건 발생 지역'은 신고자 소속과 무관하게
    # 어디서든 신고할 수 있어야 하므로 폼 값을 쓰되, 아래에서 실재하는 지역인지 검증한다.
    profile = session.get(User, user.id)
    if not background.strip():
        raise HTTPException(status_code=400, detail="경위는 반드시 입력해야 합니다.")

    region, category = _resolve_region_and_category(session, sido_code, sigungu_code, category_id)

    files = _validate_attachments(files)
    file_payloads: list[tuple[str, str, bytes]] = []
    for f in files:
        contents = f.file.read()
        if len(contents) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(status_code=400, detail=f"'{f.filename}' 파일이 20MB를 초과합니다.")
        file_payloads.append((f.filename, f.content_type or "application/octet-stream", contents))

    reporter_name = profile.display_name
    reporter_occupation = profile.occupation_label
    reporter_contact = profile.contact or ""

    occurred = _parse_occurred_at(occurred_at)
    reporter_summary = " / ".join(p for p in (reporter_name, reporter_occupation, reporter_contact) if p)
    statement = _compose_statement(
        reporter_summary=reporter_summary,
        occurred_at=occurred,
        location=location,
        background=background,
        situation=situation,
        action_taken=action_taken,
        damage=damage,
    )

    # 자동 조문 분석은 외부 LLM에 의존하므로 실패할 수 있다(할당량 초과, 일시 장애 등).
    # 그렇더라도 접수된 사고 신고 자체는 절대 유실되면 안 되므로, 분석 실패 시에는
    # 인용 없이 저장하고 안전부서가 수동으로 검토하도록 이력에 남긴다.
    analysis_failed = False
    try:
        citations = annotate_text(session, statement)
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 신고 유실보다 낫다
        session.rollback()
        citations = []
        analysis_failed = True
        print(f"[incident] 자동 조문 분석 실패, 인용 없이 접수합니다: {exc}", flush=True)

    incident = Incident(
        sido_code=region.parent_code,
        sigungu_code=region.code,
        category_id=category.id if category else None,
        reporter_name=reporter_name or None,
        reporter_occupation=reporter_occupation or None,
        reporter_contact=reporter_contact or None,
        occurred_at=occurred,
        location=location.strip() or None,
        background=background.strip(),
        situation=situation.strip() or None,
        action_taken=action_taken.strip() or None,
        damage=damage.strip() or None,
        statement=statement,
        status="review_requested",
        citations=[citation_to_dict(c) for c in citations],
        created_by_user_id=user.id,
    )
    incident.events.append(
        IncidentEvent(
            status="review_requested",
            note="심층 검토 요청 접수 (자동 조문 분석 실패 — 담당자 수동 검토 필요)"
            if analysis_failed
            else "심층 검토 요청 접수",
            actor_user_id=user.id,
        )
    )
    for filename, content_type, contents in file_payloads:
        incident.attachments.append(
            IncidentAttachment(
                filename=filename,
                content_type=content_type,
                size_bytes=len(contents),
                data=contents,
                uploaded_by_user_id=user.id,
            )
        )
    session.add(incident)
    session.commit()
    session.refresh(incident)
    sync_incident(incident)  # 실패해도 접수는 이미 확정되어 있다(graph_incidents 참고)
    return {**incident_to_dict(incident), "analysis_failed": analysis_failed}


def _load_incident(session: Session, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="사건을 찾을 수 없습니다.")
    return incident


def _assert_can_view(incident: Incident, user: User) -> None:
    """관리자는 전부 볼 수 있고, 신청자는 자신이 올린 요청만 볼 수 있다."""
    if user.is_manager:
        return
    if incident.created_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="본인이 요청한 건만 조회할 수 있습니다.")


@router.get("/api/incidents/{incident_id}")
def get_incident(
    incident_id: int,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    incident = _load_incident(session, incident_id)
    _assert_can_view(incident, user)
    return incident_to_dict(incident)


@router.post("/api/incidents/{incident_id}/edit")
def edit_incident(
    incident_id: int,
    occurred_at: str = Form(""),
    location: str = Form(""),
    background: str = Form(...),
    situation: str = Form(""),
    action_taken: str = Form(""),
    damage: str = Form(""),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """요청자가 처음 작성했던 틀(9개 항목)을 유지한 채 신고 내용을 직접 보완한다.

    검토가 이미 끝난(completed) 건은 확정된 결과라 수정할 수 없다. 담당자가 남긴 코멘트가
    formal한 "보완 요청"이 아니라 단순 코멘트여도(예: "사고일자 미기입") 요청자가 바로
    내용을 고칠 수 있어야 하므로, 상태를 가리지 않고 completed 이전이면 항상 허용한다.
    """
    incident = _load_incident(session, incident_id)
    if incident.created_by_user_id != user.id:
        raise HTTPException(status_code=403, detail="본인이 작성한 요청만 수정할 수 있습니다.")
    if incident.status == "completed":
        raise HTTPException(status_code=400, detail="검토가 완료된 요청은 수정할 수 없습니다.")
    if not background.strip():
        raise HTTPException(status_code=400, detail="경위는 반드시 입력해야 합니다.")

    occurred = _parse_occurred_at(occurred_at)
    statement = _compose_statement(
        reporter_summary=incident.reporter_summary,
        occurred_at=occurred,
        location=location,
        background=background,
        situation=situation,
        action_taken=action_taken,
        damage=damage,
    )

    analysis_failed = False
    try:
        citations = annotate_text(session, statement)
    except Exception as exc:  # noqa: BLE001 - 분석 실패해도 수정 내용은 반드시 저장한다
        session.rollback()
        citations = []
        analysis_failed = True
        print(f"[incident] 수정 후 재분석 실패, 기존 인용 없이 저장합니다: {exc}", flush=True)

    incident.occurred_at = occurred
    incident.location = location.strip() or None
    incident.background = background.strip()
    incident.situation = situation.strip() or None
    incident.action_taken = action_taken.strip() or None
    incident.damage = damage.strip() or None
    incident.statement = statement
    if not analysis_failed:
        incident.citations = [citation_to_dict(c) for c in citations]

    session.add(
        IncidentEvent(
            incident_id=incident.id,
            status=incident.status,
            note="요청자가 신고 내용을 수정했습니다.",
            actor_user_id=user.id,
        )
    )
    session.commit()
    session.refresh(incident)
    sync_incident(incident)  # 재분석으로 인용이 바뀌었을 수 있다
    return {**incident_to_dict(incident), "analysis_failed": analysis_failed}


def _content_disposition(filename: str, inline: bool) -> str:
    """한글 등 비ASCII 파일명도 깨지지 않도록 RFC 5987 형식으로 인코딩한다.

    filename*(RFC 5987)를 읽는 최신 브라우저는 항상 원래 이름을 그대로 쓰고, 이걸
    모르는 구형 클라이언트만 filename(ASCII) 폴백을 본다 — 그마저 한글이 통째로
    사라지면 확장자만 남은 이상한 이름이 되므로 "download.pdf"처럼 채워준다.
    """
    kind = "inline" if inline else "attachment"
    name, ext = os.path.splitext(filename)
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip()
    ascii_ext = ext.encode("ascii", "ignore").decode("ascii").strip()
    ascii_fallback = f"{ascii_name or 'download'}{ascii_ext}"
    return f"{kind}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/api/incidents/{incident_id}/attachments/{attachment_id}")
def download_attachment(
    incident_id: int,
    attachment_id: int,
    disposition: str = "attachment",
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    incident = _load_incident(session, incident_id)
    _assert_can_view(incident, user)

    attachment = session.get(IncidentAttachment, attachment_id)
    if not attachment or attachment.incident_id != incident_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")

    inline = disposition == "inline"
    return Response(
        content=attachment.data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": _content_disposition(attachment.filename, inline)},
    )


@router.post("/api/incidents/{incident_id}/comments")
def add_comment(
    incident_id: int,
    body: str = Form(...),
    kind: str = Form("comment"),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """스레드에 메시지를 남긴다. 종류에 따라 사건 상태도 함께 전이시킨다.

    - 관리자: comment(코멘트), supplement_request(보완 요청 -> 상태 supplement_requested),
              conclusion(최종 결과 -> 상태 completed) — 이 사건의 담당자(assigned_manager)만 가능
    - 신청자: supplement_reply(보완 내용 -> 상태 supplement_completed, 파일 첨부 가능), follow_up(추가 문의)
    """
    if kind not in COMMENT_KINDS:
        raise HTTPException(status_code=400, detail="잘못된 메시지 종류입니다.")
    if not body.strip():
        raise HTTPException(status_code=400, detail="내용을 입력해야 합니다.")

    incident = _load_incident(session, incident_id)
    _assert_can_view(incident, user)

    manager_only = {"comment", "supplement_request", "conclusion"}
    requester_only = {"supplement_reply", "follow_up"}
    if kind in manager_only and not user.is_manager:
        raise HTTPException(status_code=403, detail="검토 담당자만 사용할 수 있습니다.")
    if kind in requester_only and user.is_manager:
        raise HTTPException(status_code=403, detail="요청자만 사용할 수 있습니다.")
    if kind in manager_only and incident.assigned_manager_id and incident.assigned_manager_id != user.id:
        raise HTTPException(
            status_code=403,
            detail=f"이 사건은 {incident.assigned_manager.display_name}님이 검토를 시작한 담당 건입니다. 담당자만 코멘트를 남길 수 있습니다.",
        )

    files = _validate_attachments(files)

    session.add(
        IncidentComment(incident_id=incident.id, author_user_id=user.id, kind=kind, body=body.strip())
    )
    for f in files:
        contents = f.file.read()
        if len(contents) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(status_code=400, detail=f"'{f.filename}' 파일이 20MB를 초과합니다.")
        incident.attachments.append(
            IncidentAttachment(
                filename=f.filename,
                content_type=f.content_type or "application/octet-stream",
                size_bytes=len(contents),
                data=contents,
                uploaded_by_user_id=user.id,
            )
        )

    # 메시지 종류에 따른 상태 전이
    next_status = {
        "supplement_request": "supplement_requested",
        "supplement_reply": "supplement_completed",
        "conclusion": "completed",
    }.get(kind)
    if kind == "follow_up" and incident.status == "completed":
        next_status = "in_review"  # 완료된 건에 추가 문의가 오면 다시 검토중으로 되돌린다

    if next_status and next_status != incident.status:
        incident.status = next_status
        if kind == "conclusion":
            incident.reviewer_note = body.strip()
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                status=next_status,
                note=f"{COMMENT_KIND_LABELS.get(kind, kind)}: {body.strip()[:120]}",
                actor_user_id=user.id,
            )
        )

    session.commit()
    session.refresh(incident)
    return incident_to_dict(incident)


@router.post("/api/incidents/{incident_id}/status")
def update_incident_status(
    incident_id: int,
    status: str = Form(...),
    reviewer_note: str = Form(""),
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    if status not in INCIDENT_STATUSES:
        raise HTTPException(status_code=400, detail="잘못된 상태값입니다.")
    incident = session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="사건을 찾을 수 없습니다.")

    if incident.assigned_manager_id is None:
        # "검토 시작"을 처음 누른 사람이 이 사건의 담당자로 고정된다.
        incident.assigned_manager_id = user.id
    elif incident.assigned_manager_id != user.id:
        raise HTTPException(
            status_code=403,
            detail=f"이 사건은 {incident.assigned_manager.display_name}님이 검토를 시작한 담당 건입니다. 담당자만 상태를 변경할 수 있습니다.",
        )

    incident.status = status
    if reviewer_note:
        incident.reviewer_note = reviewer_note
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            status=status,
            note=reviewer_note or None,
            actor_user_id=user.id,
        )
    )
    session.commit()
    session.refresh(incident)
    return incident_to_dict(incident)


class CitationIn(BaseModel):
    law_name: str
    article_label: str
    title: str | None = None
    start: int
    end: int
    reason: str = ""
    url: str


@router.post("/api/incidents/{incident_id}/citations")
def replace_citations(
    incident_id: int,
    citations: list[CitationIn],
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    """관리자가 분석 결과 화면에서 마커를 추가/제거한 뒤 그 결과를 통째로 저장한다.

    부분 patch가 아니라 항상 전체 목록으로 교체한다 — 클라이언트가 이미 최종 상태를
    들고 있으므로 서버에서 병합 로직을 따로 둘 필요가 없다.
    """
    incident = _load_incident(session, incident_id)
    incident.citations = [c.model_dump() for c in citations]
    session.commit()
    session.refresh(incident)
    sync_incident(incident)
    return incident_to_dict(incident)
