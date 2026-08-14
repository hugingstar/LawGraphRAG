"""행정구역 조회 API.

시군구가 250개뿐이라 페이지마다 서버에 되묻지 않고 한 번에 내려보낸 뒤 클라이언트에서
시도 선택에 따라 걸러 쓴다(app/static/regions.js). 2단 드롭다운이 회원가입/설정/사건작성
세 화면에 반복되므로 목록 조회를 여기 한 곳으로 모은다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Region

router = APIRouter()


def sido_list(session: Session) -> list[Region]:
    return list(
        session.scalars(select(Region).where(Region.level == "sido").order_by(Region.code)).all()
    )


def sigungu_list(session: Session) -> list[Region]:
    return list(
        session.scalars(select(Region).where(Region.level == "sigungu").order_by(Region.code)).all()
    )


@router.get("/api/regions")
def get_regions(session: Session = Depends(get_session)):
    return {
        "sido": [{"code": r.code, "name": r.name} for r in sido_list(session)],
        "sigungu": [
            {"code": r.code, "name": r.name, "parent_code": r.parent_code}
            for r in sigungu_list(session)
        ],
    }
