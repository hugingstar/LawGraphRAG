"""검색 가능한 법령 이름 조회 및 사용자별 법 활성화.

Advisor(`/`)·나의 요청(`/request`)·전국 사건 모니터링(`/dashboard`) 세 화면이 모두
"지금 이용 가능한 법 리스트" 카드를 보여주므로 조회 로직을 한 곳에 모은다.

어떤 법을 검색에 쓸지는 계정마다 다르게 고를 수 있다(app.models.UserLawSelection) —
로그아웃 후 다시 로그인해도 그 사람이 마지막으로 고른 조합이 그대로 남는다.
"""

from sqlalchemy.orm import Session

from app.law_category import LAW_CATEGORIES
from app.models import Law, LawCategory, UserLawSelection

# 무료 티어에서 한 계정이 동시에 활성화할 수 있는 법의 최대 개수. 유료 티어가 생기기
# 전까지는 모든 계정에 적용되는 단일 한도다(app.routers.settings.update_settings_laws에서
# 강제한다).
FREE_TIER_LAW_LIMIT = 10


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
    """화면에 그려졌던 law.id(known_law_ids) 범위 안에서만 이 사용자의 선택을 갱신한다.

    known_law_ids로 범위를 제한하는 이유: 화면을 그린 뒤 저장하는 사이에 수집 파이프라인이
    새 법령을 추가했을 수 있는데, 그 법령은 애초에 체크박스로 제출되지 않으므로 "제출 안 됨 =
    끄기"로 취급하면 사용자가 건드리지도 않은 법이 꺼져 버린다."""
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
