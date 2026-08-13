from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_session
from app.models import Incident, User
from app.serializers import incident_to_dict
from app.templating import templates

router = APIRouter()


@router.get("/results", response_class=HTMLResponse)
def results_page(request: Request, user: User = Depends(require_login)):
    return templates.TemplateResponse(
        "results.html", {"request": request, "active": "results", "wide": True}
    )


@router.get("/api/results/my-incidents")
def my_incidents(
    status: str | None = None,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """요청자는 본인이 올린 요청만, 관리자는 본인이 올린 요청을 조회한다(관리자 검토 화면은 /review)."""
    query = session.query(Incident).filter(Incident.created_by_user_id == user.id)
    if status:
        query = query.filter(Incident.status == status)
    incidents = query.order_by(Incident.created_at.desc()).all()
    return [incident_to_dict(i) for i in incidents]
