from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import require_login, verify_password, hash_password
from app.db import get_session
from app.models import Department, Site, User
from app.templating import templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    profile = session.get(User, user.id)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "active": "settings",
            "profile": profile,
            "departments": session.query(Department).order_by(Department.name).all(),
            "sites": session.query(Site).order_by(Site.name).all(),
        },
    )


@router.post("/settings", response_class=HTMLResponse)
def update_settings(
    request: Request,
    display_name: str = Form(...),
    rank: str = Form(""),
    contact: str = Form(""),
    department_id: str = Form(""),
    site_id: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    profile = session.get(User, user.id)
    departments = session.query(Department).order_by(Department.name).all()
    sites = session.query(Site).order_by(Site.name).all()

    # 검증에 실패해도 입력값이 사라지지 않도록, 방금 제출한 값으로 화면을 다시 그린다.
    submitted = {
        "username": profile.username,
        "role_label": profile.role_label,
        "display_name": display_name,
        "rank": rank,
        "contact": contact,
        "department_id": int(department_id) if department_id else None,
        "site_id": int(site_id) if site_id else None,
    }

    def render(message: str, is_error: bool):
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active": "settings",
                "profile": submitted if is_error else profile,
                "departments": departments,
                "sites": sites,
                "error": message if is_error else None,
                "success": message if not is_error else None,
            },
            status_code=400 if is_error else 200,
        )

    if not display_name.strip():
        return render("이름을 입력해 주세요.", True)
    if not contact.strip():
        return render("연락처를 입력해 주세요.", True)

    if new_password or new_password_confirm or current_password:
        if not verify_password(current_password, profile.password_hash):
            return render("현재 비밀번호가 올바르지 않습니다.", True)
        if len(new_password) < 4:
            return render("새 비밀번호는 4자 이상이어야 합니다.", True)
        if new_password != new_password_confirm:
            return render("새 비밀번호가 일치하지 않습니다.", True)
        profile.password_hash = hash_password(new_password)

    profile.display_name = display_name.strip()
    profile.rank = rank.strip() or None
    profile.contact = contact.strip()
    profile.department_id = int(department_id) if department_id else None
    profile.site_id = int(site_id) if site_id else None
    session.commit()

    return render("변경사항이 저장되었습니다.", False)
