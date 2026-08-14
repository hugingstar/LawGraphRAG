from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from app.auth import require_manager
from app.db import get_session
from app.models import Incident, IncidentCategory, Region, User
from app.routers.regions import sido_list, sigungu_list
from app.serializers import incident_to_dict
from app.templating import templates

router = APIRouter()


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    sido_code: str | None = None,
    sigungu_code: str | None = None,
    category_id: int | None = None,
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "sido_list": sido_list(session),
            "sigungu_list": sigungu_list(session),
            "categories": session.query(IncidentCategory).order_by(IncidentCategory.id).all(),
            "initial_sido_code": sido_code or "",
            "initial_sigungu_code": sigungu_code or "",
            "initial_category_id": category_id or "",
            "active": "review",
            "wide": True,
        },
    )


@router.get("/api/review/incidents")
def review_incidents(
    sido_code: str | None = None,
    sigungu_code: str | None = None,
    category_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    # 필터를 지정하지 않으면 전국 모든 지역·유형을 최신순으로 보여준다.
    query = session.query(Incident)
    if sido_code:
        query = query.filter(Incident.sido_code == sido_code)
    if sigungu_code:
        query = query.filter(Incident.sigungu_code == sigungu_code)
    if category_id:
        query = query.filter(Incident.category_id == category_id)
    if status:
        query = query.filter(Incident.status == status)

    keyword = (q or "").strip()
    if keyword:
        # 신고 본문과 지역/유형/작성자까지 아우르는 통합 검색.
        # 시도와 시군구가 같은 regions 테이블을 두 번 참조하므로 별칭으로 구분한다.
        like = f"%{keyword}%"
        sido = aliased(Region)
        sigungu = aliased(Region)
        query = (
            query.outerjoin(sido, Incident.sido_code == sido.code)
            .outerjoin(sigungu, Incident.sigungu_code == sigungu.code)
            .outerjoin(IncidentCategory, Incident.category_id == IncidentCategory.id)
        )
        query = query.filter(
            or_(
                Incident.statement.ilike(like),
                Incident.location.ilike(like),
                Incident.reporter_name.ilike(like),
                Incident.reviewer_note.ilike(like),
                sido.name.ilike(like),
                sigungu.name.ilike(like),
                IncidentCategory.name.ilike(like),
            )
        )

    incidents = query.order_by(Incident.created_at.desc()).all()
    return [incident_to_dict(i) for i in incidents]
