import json
import logging

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.annotate import annotate_text, annotate_text_stream
from app.auth import SESSION_COOKIE, require_login, user_for_token
from app.citations import citation_to_dict, dismissed_to_dict, issue_to_dict
from app.db import SessionLocal, engine, get_session
from app.law_catalog import inject_available_laws
from app.migrate import finalize_migration, run_migrations
from app.models import Base, User
from app.regions_seed import seed_regions
from app.seed import seed_reference_data
from app.templating import templates
from app.routers import auth, dashboard, incidents, mypage, regions, results, review, settings


# uvicorn은 자기 로거만 설정해서 app.* 로거는 핸들러 없이 남는다 — 그러면 조문 분석이
# 왜 빈손으로 끝났는지(후보 없음/LLM 실패/인용문 불일치) 컨테이너 로그에 안 남는다.
# 루트는 WARNING으로 둬서 서드파티(httpx 등) 로그가 쏟아지지 않게 하고, 우리 모듈만 INFO.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")
logging.getLogger("app").setLevel(logging.INFO)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="Law Owly")
app.mount("/static", NoCacheStaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(incidents.router)
app.include_router(mypage.router)
app.include_router(regions.router)
app.include_router(results.router)
app.include_router(review.router)
app.include_router(settings.router)


@app.middleware("http")
async def attach_current_user(request: Request, call_next):
    """세션 쿠키로 사용자를 찾아 request.state.user에 실어둔다(없으면 None).
    템플릿과 의존성이 모두 이 값을 본다."""
    request.state.user = None
    request.state.profile = {}
    request.state.available_laws = []
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = SessionLocal()
        try:
            user = user_for_token(session, token)
            if user:
                # 세션이 닫힌 뒤에도 템플릿에서 쓸 수 있도록 관계 값을 미리 평문으로 뽑아둔다.
                request.state.profile = {
                    "occupation": user.occupation_label,
                    "contact": user.contact,
                    "sido": user.sido.name if user.sido else None,
                    "sigungu": user.sigungu.name if user.sigungu else None,
                    "region": user.sigungu.full_name if user.sigungu else None,
                }
                session.expunge(user)
                request.state.user = user
        finally:
            session.close()
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def auth_exception_handler(request: Request, exc: StarletteHTTPException):
    """페이지 요청에서 인증/권한 오류가 나면 로그인 화면으로 보낸다(API는 JSON 유지)."""
    if exc.status_code == 401 and not request.url.path.startswith("/api"):
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
    if exc.status_code == 403 and not request.url.path.startswith("/api"):
        return templates.TemplateResponse(
            "forbidden.html", {"request": request, "message": exc.detail}, status_code=403
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/healthz")
def healthz():
    """컨테이너 헬스체크 전용. 인증을 타지 않아야 하고(로그인 리다이렉트가 끼면
    healthcheck 가 무의미해진다) DB 도 건드리지 않는다 — 프로세스가 살아 있는지만 본다."""
    return {"status": "ok"}


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    # 1단계 마이그레이션(스키마) -> 지역 시드 -> 2단계(사업장->지역 이관 및 구 스키마 제거).
    # 이관이 regions를 참조하므로 시드가 그 사이에 끼어야 한다(app/migrate.py 참고).
    run_migrations(engine)
    session = SessionLocal()
    try:
        seed_regions(session)
        seed_reference_data(session)
    finally:
        session.close()
    finalize_migration(engine)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    user: User = Depends(require_login),
    _laws: None = Depends(inject_available_laws),
):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "active": "lookup",
            "wide": True,
        },
    )


@app.post("/analyze")
def analyze(
    text: str = Form(...),
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
):
    citations = annotate_text(session, text, user.id)
    return {
        "text": text,
        "citations": [citation_to_dict(c) for c in citations],
    }


@app.post("/analyze/stream")
def analyze_stream(text: str = Form(...), user: User = Depends(require_login)):
    """조문 인용이 발견되는 대로 한 줄씩(NDJSON) 내보낸다.

    StreamingResponse는 라우트 핸들러가 반환한 뒤 실제 스트리밍이 이뤄지므로,
    Depends(get_session)을 쓰면 제너레이터가 돌기 전에 세션이 먼저 닫혀버릴 수 있다.
    그래서 세션을 이 함수 안에서 직접 열고 제너레이터의 finally에서 닫는다.
    """

    user_id = user.id

    def generate():
        session = SessionLocal()
        try:
            for kind, payload in annotate_text_stream(session, text, user_id):
                if kind == "issues":
                    event = {
                        "type": "issues",
                        "facts": payload.facts,
                        "issues": [issue_to_dict(i) for i in payload.issues],
                        "dismissed": [dismissed_to_dict(d) for d in payload.dismissed],
                    }
                elif kind == "citation":
                    event = {"type": "citation", "citation": citation_to_dict(payload)}
                else:  # "done"
                    event = {"type": "done", "citations": [citation_to_dict(c) for c in payload]}
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001 - 스트림 도중 실패도 클라이언트에 알려야 한다
            yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"
        finally:
            session.close()

    return StreamingResponse(generate(), media_type="application/x-ndjson")


