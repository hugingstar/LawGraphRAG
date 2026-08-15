"""검색 가능한 법령 이름 조회.

Advisor(`/`)·나의 요청(`/request`)·전국 사건 모니터링(`/dashboard`) 세 화면이 모두
"지금 이용 가능한 법 리스트" 카드를 보여주므로 조회 로직을 한 곳에 모은다.
"""

from sqlalchemy.orm import Session

from app.models import Law


def available_law_names(session: Session) -> list[str]:
    """실제로 수집이 끝나 검색 가능한 법령만 돌려준다. 폐지되어 조문이 비워진 법령
    (Law.repealed_at)은 목록에서 제외한다."""
    return [
        name
        for (name,) in session.query(Law.law_name)
        .filter(Law.repealed_at.is_(None))
        .order_by(Law.law_name)
        .all()
    ]
