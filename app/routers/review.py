from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import require_manager
from app.db import get_session
from app.models import Department, Incident, Site, User
from app.serializers import incident_to_dict
from app.templating import templates

router = APIRouter()


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    site_id: int | None = None,
    department_id: int | None = None,
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    sites = session.query(Site).order_by(Site.name).all()
    departments = session.query(Department).order_by(Department.name).all()
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "sites": sites,
            "departments": departments,
            "initial_site_id": site_id or "",
            "initial_department_id": department_id or "",
            "active": "review",
            "wide": True,
        },
    )


@router.get("/api/review/incidents")
def review_incidents(
    site_id: int | None = None,
    department_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    # 필터를 지정하지 않으면 전체 사업장·전체 부서를 최신순으로 보여준다.
    query = session.query(Incident)
    if site_id:
        query = query.filter(Incident.site_id == site_id)
    if department_id:
        query = query.filter(Incident.department_id == department_id)
    if status:
        query = query.filter(Incident.status == status)

    keyword = (q or "").strip()
    if keyword:
        # 신고 본문과 사업장/부서/작성자까지 아우르는 통합 검색
        like = f"%{keyword}%"
        query = query.outerjoin(Site, Incident.site_id == Site.id).outerjoin(
            Department, Incident.department_id == Department.id
        )
        query = query.filter(
            or_(
                Incident.statement.ilike(like),
                Incident.location.ilike(like),
                Incident.reporter_name.ilike(like),
                Incident.reviewer_note.ilike(like),
                Site.name.ilike(like),
                Department.name.ilike(like),
            )
        )

    incidents = query.order_by(Incident.created_at.desc()).all()
    return [incident_to_dict(i) for i in incidents]
