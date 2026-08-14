"""인증/세션 처리.

비밀번호는 stdlib pbkdf2-hmac-sha256(솔트 포함)으로 해싱하고, 세션은 DB에 저장한다.
쿠키에는 추측 불가능한 랜덤 토큰만 담기므로 별도 서명 라이브러리가 필요 없고,
로그아웃 시 해당 세션 행을 지우는 것으로 즉시 무효화된다.
"""

import datetime
import hashlib
import hmac
import os
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User, UserSession

SESSION_COOKIE = "sla_session"
SESSION_TTL = datetime.timedelta(days=7)
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_session(session: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + SESSION_TTL
    session.add(UserSession(token=token, user_id=user.id, expires_at=expires_at))
    session.commit()
    return token


def revoke_session(session: Session, token: str | None) -> None:
    if not token:
        return
    session.query(UserSession).filter(UserSession.token == token).delete()
    session.commit()


def user_for_token(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    record = session.get(UserSession, token)
    if not record:
        return None
    if record.expires_at < datetime.datetime.now(datetime.timezone.utc):
        session.delete(record)
        session.commit()
        return None
    return record.user


def current_user(request: Request) -> User | None:
    """미들웨어가 request.state에 실어둔 사용자."""
    return getattr(request.state, "user", None)


def require_login(request: Request) -> User:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


def require_manager(request: Request) -> User:
    user = require_login(request)
    if not user.is_manager:
        raise HTTPException(status_code=403, detail="검토 담당자만 접근할 수 있습니다.")
    return user


def get_auth_session(session: Session = Depends(get_session)) -> Session:
    return session
