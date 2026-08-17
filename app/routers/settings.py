import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, require_login, verify_password, hash_password
from app.db import get_session
from app.law_catalog import (
    available_law_names,
    grouped_toggleable_laws,
    inject_available_laws,
    selected_law_ids,
    set_enabled_laws,
    toggleable_law_ids,
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
logger = logging.getLogger(__name__)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
    _laws: None = Depends(inject_available_laws),
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
    _laws: None = Depends(inject_available_laws),
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


def law_form_context(session: Session, user_id: int) -> dict:
    """법 활성화 폼(_settings_laws_form.html)이 필요로 하는 값. 페이지와 팝업이 같은 폼을
    쓰므로 한 곳에서 만든다. law_total은 "N / 전체개수개 선택됨" 표시에 쓴다 — 활성화
    개수 자체에는 제한이 없다(전체 선택 가능)."""
    law_groups = grouped_toggleable_laws(session)
    checked_ids = selected_law_ids(session, user_id)
    return {
        "law_groups": law_groups,
        "law_total": sum(len(laws) for _, laws in law_groups),
        "checked_ids": checked_ids,
        "enabled_count": len(checked_ids),
    }


@router.get("/settings/laws", response_class=HTMLResponse)
def settings_laws_page(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
    _laws: None = Depends(inject_available_laws),
):
    return templates.TemplateResponse(
        "settings_laws.html",
        {
            "request": request,
            "active": "settings",
            "wide": True,
            **law_form_context(session, user.id),
        },
    )


@router.post("/settings/laws", response_class=HTMLResponse)
def update_settings_laws(
    request: Request,
    law_ids: list[int] = Form(default=[]),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    set_enabled_laws(session, user.id, toggleable_law_ids(session), set(law_ids))
    # inject_available_laws Depends는 요청 본문 처리 전에 도니 방금 저장한 선택이
    # 반영 안 된 값을 캐싱한다 — 저장 뒤 직접 다시 조회해서 상단 바에 최신 목록이 뜨게 한다.
    request.state.available_laws = available_law_names(session, user.id)
    return templates.TemplateResponse(
        "settings_laws.html",
        {
            "request": request,
            "active": "settings",
            "wide": True,
            **law_form_context(session, user.id),
            "success": "저장되었습니다.",
        },
    )


@router.get("/api/settings/laws/fragment", response_class=HTMLResponse)
def settings_laws_fragment(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "_settings_laws_form.html",
        {
            "request": request,
            **law_form_context(session, user.id),
            # 팝업 안에서만 "닫기" 버튼을 보여준다 — 설정 페이지에서는 닫을 대상이 없다.
            "in_modal": True,
        },
    )


@router.post("/api/settings/laws/fragment")
async def update_settings_laws_fragment(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    """팝업에서 AJAX로 호출되는 저장 엔드포인트.

    체크된 법령만 폼으로 받고, 저장 범위(known_law_ids)는 서버가 DB에서 다시 계산한다.
    체크박스마다 hidden input을 함께 보내면 법령이 수천 건이라 Starlette의 폼 필드 수
    제한(기본 1000)에 걸린다.
    """
    try:
        form = await request.form()
        law_ids = {int(v) for v in form.getlist("law_ids")}
    except Exception:
        logger.warning("법 활성화 폼 파싱 실패 (user_id=%s)", user.id, exc_info=True)
        return JSONResponse(
            {"success": False, "error": "전송된 데이터를 처리할 수 없습니다."},
            status_code=400,
        )

    try:
        set_enabled_laws(session, user.id, toggleable_law_ids(session), law_ids)
        return {"success": True, "active_names": available_law_names(session, user.id)}
    except Exception:
        # 예외 문구를 그대로 돌려주면 내부 구조가 새어 나가므로 로그로만 남긴다.
        logger.exception("법 활성화 저장 실패 (user_id=%s)", user.id)
        return JSONResponse(
            {"success": False, "error": "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."},
            status_code=500,
        )


@router.post("/settings/delete-account")
def delete_account(
    request: Request,
    confirm_username: str = Form(...),
    confirm_password: str = Form(...),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
    _laws: None = Depends(inject_available_laws),
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
