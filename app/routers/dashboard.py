import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.auth import require_manager
from app.db import get_session
from app.models import INCIDENT_STATUS_LABELS, Department, Incident, Site, User
from app.templating import templates

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, user: User = Depends(require_manager)):
    today = datetime.date.today()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "wide": True,
            "default_start": (today - datetime.timedelta(days=30)).isoformat(),
            "default_end": today.isoformat(),
        },
    )


def _date_bounds(start: str | None, end: str | None):
    """YYYY-MM-DD 문자열을 [시작 00:00, 끝 다음날 00:00) 범위로 바꾼다."""
    start_dt = end_dt = None
    if start:
        try:
            start_dt = datetime.datetime.fromisoformat(start).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            start_dt = None
    if end:
        try:
            end_date = datetime.date.fromisoformat(end)
            end_dt = datetime.datetime.combine(
                end_date + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.timezone.utc
            )
        except ValueError:
            end_dt = None
    return start_dt, end_dt


@router.get("/api/dashboard/stats")
def dashboard_stats(
    start: str | None = None,
    end: str | None = None,
    user: User = Depends(require_manager),
    session: Session = Depends(get_session),
):
    start_dt, end_dt = _date_bounds(start, end)

    def scoped(query):
        if start_dt:
            query = query.filter(Incident.created_at >= start_dt)
        if end_dt:
            query = query.filter(Incident.created_at < end_dt)
        return query

    def join_conditions(fk_column):
        """기간 조건을 WHERE가 아닌 ON 절에 넣는다.
        WHERE에 넣으면 outer join이 inner join처럼 동작해서 '해당 기간에 사건이 0건인
        사업장/부서'가 결과에서 통째로 사라진다."""
        conditions = [fk_column]
        if start_dt:
            conditions.append(Incident.created_at >= start_dt)
        if end_dt:
            conditions.append(Incident.created_at < end_dt)
        return and_(*conditions)

    total = scoped(session.query(Incident)).count()

    status_rows = scoped(
        session.query(Incident.status, func.count(Incident.id)).group_by(Incident.status)
    ).all()
    status_counts = {status: 0 for status in INCIDENT_STATUS_LABELS}
    for status, count in status_rows:
        status_counts[status] = count

    site_rows = (
        session.query(Site.id, Site.name, func.count(Incident.id))
        .outerjoin(Incident, join_conditions(Incident.site_id == Site.id))
        .group_by(Site.id, Site.name)
        .order_by(Site.id)
        .all()
    )

    dept_rows = (
        session.query(Department.id, Department.name, func.count(Incident.id))
        .outerjoin(Incident, join_conditions(Incident.department_id == Department.id))
        .group_by(Department.id, Department.name)
        .order_by(Department.id)
        .all()
    )

    # 사업장 x 부서 교차 집계 (히트맵용). 값이 0인 조합은 굳이 행으로 만들지 않고
    # 프런트에서 by_site/by_department를 축으로 빈칸을 0으로 채운다.
    matrix_rows = scoped(
        session.query(Incident.site_id, Incident.department_id, func.count(Incident.id))
        .group_by(Incident.site_id, Incident.department_id)
    ).all()

    return {
        "total": total,
        "status_counts": status_counts,
        "status_labels": INCIDENT_STATUS_LABELS,
        "by_site": [{"id": sid, "name": name, "count": count} for sid, name, count in site_rows],
        "by_department": [{"id": did, "name": name, "count": count} for did, name, count in dept_rows],
        "matrix": [
            {"site_id": sid, "department_id": did, "count": count} for sid, did, count in matrix_rows
        ],
    }
