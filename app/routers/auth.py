from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, SESSION_TTL, create_session, hash_password, revoke_session, verify_password
from app.db import get_session
from app.models import User
from app.routers.regions import sido_list, sigungu_list
from app.templating import templates

router = APIRouter()


def _redirect_with_session(token: str, destination: str = "/dashboard") -> RedirectResponse:
    response = RedirectResponse(url=destination, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


MANAGER_ONLY_PREFIXES = ("/dashboard", "/review")


def _home_for(user: User) -> str:
    """신청자는 사건 모니터링에 접근할 수 없으므로 검토 결과 화면을 홈으로 쓴다."""
    return "/dashboard" if user.is_manager else "/results"


def _safe_next(next_path: str, user: User) -> str:
    """로그인 후 이동할 경로를 정한다.

    - 오픈 리다이렉트 방지: 같은 사이트의 절대 경로만 허용
    - 권한이 없는 경로면 403 화면 대신 각자의 홈으로 보낸다
    """
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return _home_for(user)
    if not user.is_manager and next_path.startswith(MANAGER_ONLY_PREFIXES):
        return _home_for(user)
    return next_path


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    current = getattr(request.state, "user", None)
    if current:
        return RedirectResponse(url=_home_for(current), status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "hide_nav": True, "next": next}
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    session: Session = Depends(get_session),
):
    user = session.query(User).filter(User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "hide_nav": True,
                "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
                "username": username,
                "next": next,
            },
            status_code=401,
        )
    return _redirect_with_session(create_session(session, user), _safe_next(next, user))


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, session: Session = Depends(get_session)):
    current = getattr(request.state, "user", None)
    if current:
        return RedirectResponse(url=_home_for(current), status_code=303)
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "hide_nav": True,
            "sido_list": sido_list(session),
            "sigungu_list": sigungu_list(session),
        },
    )


@router.post("/signup")
def signup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(...),
    role: str = Form("requester"),
    rank: str = Form(""),
    contact: str = Form(""),
    sido_code: str = Form(""),
    sigungu_code: str = Form(""),
    session: Session = Depends(get_session),
):
    username = username.strip()

    def fail(message: str):
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "hide_nav": True,
                "sido_list": sido_list(session),
                "sigungu_list": sigungu_list(session),
                "error": message,
                "username": username,
                "display_name": display_name,
                "rank": rank,
                "contact": contact,
            },
            status_code=400,
        )

    if len(username) < 3:
        return fail("아이디는 3자 이상이어야 합니다.")
    if len(password) < 4:
        return fail("비밀번호는 4자 이상이어야 합니다.")
    if password != password_confirm:
        return fail("비밀번호가 일치하지 않습니다.")
    if role not in ("requester", "manager"):
        return fail("잘못된 역할입니다.")
    if not sido_code:
        return fail("활동 지역(시·도)을 선택해 주세요.")
    if not sigungu_code:
        return fail("활동 지역(시·군·구)을 선택해 주세요.")
    if not contact.strip():
        return fail("연락처를 입력해 주세요.")
    if session.query(User).filter(User.username == username).first():
        return fail("이미 사용 중인 아이디입니다.")

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip() or username,
        role=role,
        rank=rank.strip() or None,
        contact=contact.strip(),
        sido_code=sido_code,
        sigungu_code=sigungu_code,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return _redirect_with_session(create_session(session, user), _home_for(user))


@router.post("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    revoke_session(session, request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
