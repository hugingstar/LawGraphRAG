"""검색 가능한 법령 이름 조회.

Advisor(`/`)·나의 요청(`/request`)·전사 사건 모니터링(`/dashboard`) 세 화면이 모두
"지금 이용 가능한 법 리스트" 카드를 보여주므로 조회 로직을 한 곳에 모은다.
"""

from sqlalchemy.orm import Session

from app.models import Law


def available_law_names(session: Session) -> list[str]:
    """TARGET_LAWS(app/ingest.py)의 목표 목록이 아니라, 실제로 수집이 끝나 검색 가능한
    법령만 돌려준다 — 목표만 적어두면 아직 못 넣은 법이 '이용 가능'으로 보인다."""
    return [name for (name,) in session.query(Law.law_name).order_by(Law.law_name).all()]
