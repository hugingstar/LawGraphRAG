from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_session
from app.law_catalog import inject_available_laws
from app.models import User
from app.mypage_stats import build_mypage_stats
from app.templating import templates

router = APIRouter()


@router.get("/mypage", response_class=HTMLResponse)
def mypage(
    request: Request,
    user: User = Depends(require_login),
    _laws: None = Depends(inject_available_laws),
):
    return templates.TemplateResponse(
        "mypage.html", {"request": request, "active": "mypage", "wide": True}
    )


@router.get("/api/mypage/stats")
def mypage_stats(
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """항상 로그인한 본인의 통계만 낸다 — 남의 활동 품질을 들여다보는 화면이 아니다."""
    return build_mypage_stats(session, user.id)
