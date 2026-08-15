from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, require_login, verify_password, hash_password
from app.db import get_session
from app.law_catalog import (
    FREE_TIER_LAW_LIMIT,
    grouped_toggleable_laws,
    selected_law_ids,
    set_enabled_laws,
)
from app.models import (
    OCCUPATIONS,
    USER_ROLES,
    USER_ROLE_LABELS,
    Incident,
    IncidentAttachment,
    IncidentComment,
    IncidentEvent,
    User,
)
from app.routers.regions import sido_list, sigungu_list
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
            "sido_list": sido_list(session),
            "sigungu_list": sigungu_list(session),
            "occupations": OCCUPATIONS,
            "user_roles": USER_ROLES,
            "user_role_labels": USER_ROLE_LABELS,
        },
    )


@router.post("/settings", response_class=HTMLResponse)
def update_settings(
    request: Request,
    display_name: str = Form(...),
    role: str = Form(...),
    occupation: str = Form(""),
    contact: str = Form(""),
    sido_code: str = Form(""),
    sigungu_code: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    profile = session.get(User, user.id)
    sidos = sido_list(session)
    sigungus = sigungu_list(session)

    # 검증에 실패해도 입력값이 사라지지 않도록, 방금 제출한 값으로 화면을 다시 그린다.
    submitted = {
        "username": profile.username,
        "role": role,
        "role_label": USER_ROLE_LABELS.get(role, role),
        "display_name": display_name,
        "occupation": occupation,
        "contact": contact,
        "sido_code": sido_code or None,
        "sigungu_code": sigungu_code or None,
    }

    def render(message: str, is_error: bool):
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active": "settings",
                "profile": submitted if is_error else profile,
                "sido_list": sidos,
                "sigungu_list": sigungus,
                "occupations": OCCUPATIONS,
                "user_roles": USER_ROLES,
                "user_role_labels": USER_ROLE_LABELS,
                "error": message if is_error else None,
                "success": message if not is_error else None,
            },
            status_code=400 if is_error else 200,
        )

    if not display_name.strip():
        return render("이름을 입력해 주세요.", True)
    if role not in USER_ROLES:
        return render("올바르지 않은 역할입니다.", True)
    if not contact.strip():
        return render("연락처를 입력해 주세요.", True)
    if occupation and occupation not in dict(OCCUPATIONS):
        return render("올바르지 않은 직종입니다.", True)

    if new_password or new_password_confirm or current_password:
        if not verify_password(current_password, profile.password_hash):
            return render("현재 비밀번호가 올바르지 않습니다.", True)
        if len(new_password) < 4:
            return render("새 비밀번호는 4자 이상이어야 합니다.", True)
        if new_password != new_password_confirm:
            return render("새 비밀번호가 일치하지 않습니다.", True)
        profile.password_hash = hash_password(new_password)

    profile.display_name = display_name.strip()
    profile.role = role
    profile.occupation = occupation or None
    profile.contact = contact.strip()
    profile.sido_code = sido_code or None
    profile.sigungu_code = sigungu_code or None
    session.commit()

    return render("변경사항이 저장되었습니다.", False)


@router.get("/settings/laws", response_class=HTMLResponse)
def settings_laws_page(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    law_groups = grouped_toggleable_laws(session)
    checked_ids = selected_law_ids(session, user.id)
    return templates.TemplateResponse(
        "settings_laws.html",
        {
            "request": request,
            "active": "settings",
            "wide": True,
            "law_groups": law_groups,
            "law_limit": FREE_TIER_LAW_LIMIT,
            "checked_ids": checked_ids,
            "enabled_count": len(checked_ids),
        },
    )


@router.post("/settings/laws", response_class=HTMLResponse)
def update_settings_laws(
    request: Request,
    law_ids: list[int] = Form(default=[]),
    known_law_ids: list[int] = Form(default=[]),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    # 클라이언트에서 체크박스를 5개 초과로 못 누르게 막아 두지만, 폼은 누구든 조작해서
    # 보낼 수 있으므로 서버에서도 반드시 다시 검증한다.
    if len(law_ids) > FREE_TIER_LAW_LIMIT:
        law_groups = grouped_toggleable_laws(session)
        checked_ids = set(law_ids)
        return templates.TemplateResponse(
            "settings_laws.html",
            {
                "request": request,
                "active": "settings",
                "wide": True,
                "law_groups": law_groups,
                "law_limit": FREE_TIER_LAW_LIMIT,
                # 저장은 실패했지만, 사용자가 방금 고르던 선택 그대로 보여줘서 몇 개만 해제하고
                # 바로 다시 제출할 수 있게 한다(저장된 이전 상태로 되돌리면 다시 처음부터 골라야 함).
                "checked_ids": checked_ids,
                "enabled_count": len(checked_ids),
                "error": f"무료 티어는 최대 {FREE_TIER_LAW_LIMIT}개까지만 활성화할 수 있습니다.",
            },
            status_code=400,
        )

    set_enabled_laws(session, user.id, set(known_law_ids), set(law_ids))
    law_groups = grouped_toggleable_laws(session)
    checked_ids = selected_law_ids(session, user.id)
    return templates.TemplateResponse(
        "settings_laws.html",
        {
            "request": request,
            "active": "settings",
            "wide": True,
            "law_groups": law_groups,
            "law_limit": FREE_TIER_LAW_LIMIT,
            "checked_ids": checked_ids,
            "enabled_count": len(checked_ids),
            "success": "저장되었습니다.",
        },
    )


@router.get("/api/settings/laws/fragment", response_class=HTMLResponse)
def settings_laws_fragment(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    law_groups = grouped_toggleable_laws(session)
    checked_ids = selected_law_ids(session, user.id)
    return templates.TemplateResponse(
        "_settings_laws_form.html",
        {
            "request": request,
            "law_groups": law_groups,
            "law_limit": FREE_TIER_LAW_LIMIT,
            "checked_ids": checked_ids,
            "enabled_count": len(checked_ids),
        },
    )


@router.post("/api/settings/laws/fragment")
def update_settings_laws_fragment(
    request: Request,
    law_ids: list[int] = Form(default=[]),
    known_law_ids: list[int] = Form(default=[]),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    if len(law_ids) > FREE_TIER_LAW_LIMIT:
        return JSONResponse(
            {"success": False, "error": f"무료 티어는 최대 {FREE_TIER_LAW_LIMIT}개까지만 활성화할 수 있습니다."},
            status_code=400,
        )

    set_enabled_laws(session, user.id, set(known_law_ids), set(law_ids))
    
    from app.law_catalog import available_law_names
    active_names = available_law_names(session, user.id)
    return {"success": True, "active_names": active_names}


@router.post("/settings/delete-account")
def delete_account(
    request: Request,
    confirm_username: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """계정을 실제로 삭제한다. 되돌릴 수 없으므로 아이디·비밀번호를 다시 입력받아
    확인한다 — 로그인된 상태라 세션만으로도 삭제할 수 있지만, 그러면 다른 사람이 잠깐
    자리를 비운 계정을 만졌을 때 실수로 탈퇴시키는 사고를 막을 수 없다."""
    profile = session.get(User, user.id)

    if confirm_username.strip() != profile.username:
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active": "settings",
                "profile": profile,
                "sido_list": sido_list(session),
                "sigungu_list": sigungu_list(session),
                "occupations": OCCUPATIONS,
                "user_roles": USER_ROLES,
                "user_role_labels": USER_ROLE_LABELS,
                "delete_error": "아이디가 일치하지 않습니다.",
            },
            status_code=400,
        )
    if not verify_password(confirm_password, profile.password_hash):
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active": "settings",
                "profile": profile,
                "sido_list": sido_list(session),
                "sigungu_list": sigungu_list(session),
                "occupations": OCCUPATIONS,
                "user_roles": USER_ROLES,
                "user_role_labels": USER_ROLE_LABELS,
                "delete_error": "비밀번호가 올바르지 않습니다.",
            },
            status_code=400,
        )

    # 이 사람이 작성/처리한 사건과 감사 이력은 지우지 않는다 — incident_events는 append-only
    # 감사 추적이라 계정 탈퇴로 사라지면 안 된다. 대신 작성자 연결만 끊는다(컬럼이 nullable).
    for model, column in (
        (Incident, Incident.created_by_user_id),
        (Incident, Incident.assigned_manager_id),
        (IncidentEvent, IncidentEvent.actor_user_id),
        (IncidentComment, IncidentComment.author_user_id),
        (IncidentAttachment, IncidentAttachment.uploaded_by_user_id),
    ):
        session.query(model).filter(column == profile.id).update(
            {column.key: None}, synchronize_session=False
        )

    session.delete(profile)  # user_sessions는 ON DELETE CASCADE로 함께 삭제된다
    session.commit()

    response = RedirectResponse(url="/login?deleted=1", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
