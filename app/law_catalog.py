"""검색 가능한 법령 이름 조회 및 사용자별 법 활성화.

로그인한 모든 화면 상단에 "활성화 된 법" 바(_layout.html)가 뜨므로 조회 로직을 한 곳에
모은다. 화면을 실제로 그리는 라우트만 inject_available_laws를 Depends로 붙인다 —
미들웨어에서 매 요청(정적 파일·API 호출 포함)마다 조회하면 페이지 이동이 눈에 띄게
느려진다.

어떤 법을 검색에 쓸지는 계정마다 다르게 고를 수 있다(app.models.UserLawSelection) —
로그아웃 후 다시 로그인해도 그 사람이 마지막으로 고른 조합이 그대로 남는다.
"""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_session
from app.law_category import LAW_CATEGORIES
from app.models import Law, LawCategory, User, UserLawSelection


def available_law_names(session: Session, user_id: int) -> list[str]:
    """이 사용자가 검색 대상으로 켜 둔 법령만 돌려준다. 폐지되어 조문이 비워진 법령
    (Law.repealed_at)은 계정 설정과 무관하게 항상 제외한다."""
    return [
        name
        for (name,) in session.query(Law.law_name)
        .join(UserLawSelection, UserLawSelection.law_id == Law.id)
        .filter(UserLawSelection.user_id == user_id, Law.repealed_at.is_(None))
        .order_by(Law.law_name)
        .all()
    ]


def inject_available_laws(
    request: Request,
    user: User = Depends(require_login),
    session: Session = Depends(get_session),
) -> None:
    """페이지 라우터가 Depends(inject_available_laws)로 붙이면 request.state.available_laws가
    채워져 _layout.html 상단의 "활성화 된 법" 바에 쓰인다."""
    request.state.available_laws = available_law_names(session, user.id)


def grouped_toggleable_laws(session: Session) -> list[tuple[str, list[Law]]]:
    """설정 > 법 활성화 화면에 표시할, 폐지되지 않은 전체 법령을 분야별로 묶어서 돌려준다.

    처음 보는 사람이 수천 건을 한 줄로 쭉 훑기는 어려우므로, app.law_category의 분류
    규칙(법령명 접미사/소관부처 기반)이 매긴 분야로 묶고 작은 제목을 붙인다. 순서는
    app.law_category.LAW_CATEGORIES 표시 순(분야 코드 사전 정의 순)을 따르고, 어디에도
    안 걸린 "기타"가 늘 마지막에 오게 한다."""
    category_codes = {c.id: c.code for c in session.query(LawCategory).all()}
    laws = (
        session.query(Law)
        .filter(Law.repealed_at.is_(None))
        .order_by(Law.law_name)
        .all()
    )
    laws_by_code: dict[str, list[Law]] = {}
    for law in laws:
        code = category_codes.get(law.category_id, "etc")
        laws_by_code.setdefault(code, []).append(law)

    return [
        (name, laws_by_code[code])
        for code, name in LAW_CATEGORIES
        if laws_by_code.get(code)
    ]


def toggleable_law_ids(session: Session) -> set[int]:
    """설정 화면에 체크박스로 그려지는 전체 법령 id — set_enabled_laws의 known_law_ids로 쓴다.

    체크박스 하나마다 hidden input을 같이 보내는 방식은 법령이 수천 건이라 폼 필드 수
    제한에 걸리므로, 저장 시점에 서버가 같은 범위를 DB에서 다시 계산한다
    (grouped_toggleable_laws와 같은 조건: 폐지되지 않은 전체 법령)."""
    return {
        law_id
        for (law_id,) in session.query(Law.id).filter(Law.repealed_at.is_(None)).all()
    }


def selected_law_ids(session: Session, user_id: int) -> set[int]:
    """이 사용자가 현재 켜 둔 law.id 집합."""
    return {
        law_id
        for (law_id,) in session.query(UserLawSelection.law_id)
        .filter(UserLawSelection.user_id == user_id)
        .all()
    }


def set_enabled_laws(
    session: Session, user_id: int, known_law_ids: set[int], enabled_law_ids: set[int]
) -> None:
    """고를 수 있었던 law.id(known_law_ids) 범위 안에서만 이 사용자의 선택을 갱신한다.

    known_law_ids로 범위를 제한하는 이유: 체크박스로 그려지지 않는 법(폐지되어 목록에서
    빠진 법)은 애초에 제출될 수 없으므로, "제출 안 됨 = 끄기"로 취급하면 사용자가 건드리지도
    않은 법이 꺼져 버린다. 호출부는 toggleable_law_ids()로 그 범위를 구한다."""
    if not known_law_ids:
        return
    enabled_law_ids = enabled_law_ids & known_law_ids
    session.query(UserLawSelection).filter(
        UserLawSelection.user_id == user_id, UserLawSelection.law_id.in_(known_law_ids)
    ).delete(synchronize_session=False)
    session.bulk_save_objects(
        [UserLawSelection(user_id=user_id, law_id=law_id) for law_id in enabled_law_ids]
    )
    session.commit()
