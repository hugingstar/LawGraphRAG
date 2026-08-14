import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.auth import require_manager
from app.db import get_session
from app.models import INCIDENT_STATUS_LABELS, Incident, IncidentCategory, Region, User
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
        지역'이 결과에서 통째로 사라지고, 지도에서 그 지역이 아예 안 그려진다."""
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

    # 시도/시군구별 집계. 지도가 '사건 0건인 지역'도 회색으로 그려야 하므로 outerjoin으로
    # 모든 지역을 남긴다(기간 조건은 위 join_conditions로 ON 절에 들어간다).
    sido_rows = (
        session.query(Region.code, Region.name, func.count(Incident.id))
        .outerjoin(Incident, join_conditions(Incident.sido_code == Region.code))
        .filter(Region.level == "sido")
        .group_by(Region.code, Region.name)
        .order_by(Region.code)
        .all()
    )

    sigungu_rows = (
        session.query(Region.code, Region.name, Region.parent_code, func.count(Incident.id))
        .outerjoin(Incident, join_conditions(Incident.sigungu_code == Region.code))
        .filter(Region.level == "sigungu")
        .group_by(Region.code, Region.name, Region.parent_code)
        .order_by(Region.code)
        .all()
    )

    category_rows = (
        session.query(IncidentCategory.id, IncidentCategory.code, IncidentCategory.name, func.count(Incident.id))
        .outerjoin(Incident, join_conditions(Incident.category_id == IncidentCategory.id))
        .group_by(IncidentCategory.id, IncidentCategory.code, IncidentCategory.name)
        .order_by(IncidentCategory.id)
        .all()
    )

    # 시도 x 사건유형 교차 집계. 값이 0인 조합은 행으로 만들지 않고 프런트에서 0으로 채운다.
    matrix_rows = scoped(
        session.query(Incident.sido_code, Incident.category_id, func.count(Incident.id))
        .group_by(Incident.sido_code, Incident.category_id)
    ).all()

    return {
        "total": total,
        "status_counts": status_counts,
        "status_labels": INCIDENT_STATUS_LABELS,
        "by_sido": [{"code": code, "name": name, "count": count} for code, name, count in sido_rows],
        "by_sigungu": [
            {"code": code, "name": name, "parent_code": parent, "count": count}
            for code, name, parent, count in sigungu_rows
        ],
        "by_category": [
            {"id": cid, "code": code, "name": name, "count": count}
            for cid, code, name, count in category_rows
        ],
        "matrix": [
            {"sido_code": sido, "category_id": cid, "count": count}
            for sido, cid, count in matrix_rows
        ],
    }
