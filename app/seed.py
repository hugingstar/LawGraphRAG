"""데모용 기준 데이터(사건 유형/테스트 계정) 시딩. 이미 존재하면 아무 것도 하지 않는다.

행정구역(regions)은 지도 경계 파일에서 따로 시드한다(app/regions_seed.py).
"""

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.law_category import LAW_CATEGORIES, classify_law
from app.models import IncidentCategory, Law, LawCategory, User
from app.regions_seed import _SIDO_MERGES

# 전국 단위로 다루는 사건 유형. code는 URL 쿼리/필터에 쓰이므로 영문 슬러그로 둔다.
INCIDENT_CATEGORIES = [
    ("industrial", "산업재해"),
    ("traffic", "교통사고"),
    ("fire", "화재·폭발"),
    ("construction", "건설사고"),
    ("environment", "환경오염"),
    ("medical", "의료사고"),
    ("consumer", "소비자피해"),
    ("labor", "노동분쟁"),
    ("etc", "기타"),
]


# 테스트용 기본 계정. 지역은 서로 다른 시도에 흩어놓아 지도 색칠이 눈에 보이게 한다.
TEST_ACCOUNTS = [
    {
        "username": "user01",
        "password": "1111",
        "display_name": "김신청",
        "role": "requester",
        "occupation": "production",
        "contact": "010-2345-6789",
        "sigungu_code": "37011",  # 경상북도 포항시남구
    },
    {
        # user01과 같은 시도(경북), 다른 시군구
        "username": "user02",
        "password": "1111",
        "display_name": "이물류",
        "role": "requester",
        "occupation": "transport",
        "contact": "010-2222-3333",
        "sigungu_code": "37050",  # 경상북도 구미시
    },
    {
        # 다른 시도
        "username": "user03",
        "password": "1111",
        "display_name": "최검사",
        "role": "requester",
        "occupation": "production",
        "contact": "010-3333-4444",
        "sigungu_code": "36060",  # 전라남도 광양시
    },
    {
        "username": "user04",
        "password": "1111",
        "display_name": "정설비",
        "role": "requester",
        "occupation": "production",
        "contact": "010-4444-5555",
        "sigungu_code": "11010",  # 서울특별시 종로구
    },
    {
        "username": "manager01",
        "password": "1111",
        "display_name": "박안전",
        "role": "manager",
        "occupation": "professional",
        "contact": "010-9876-5432",
        "sigungu_code": "11010",  # 서울특별시 종로구
    },
    {
        "username": "manager02",
        "password": "1111",
        "display_name": "한관리",
        "role": "manager",
        "occupation": "professional",
        "contact": "010-5555-6666",
        "sigungu_code": "29010",  # 세종특별자치시
    },
]


def seed_reference_data(session: Session) -> None:
    if session.query(IncidentCategory).count() == 0:
        session.add_all(IncidentCategory(code=code, name=name) for code, name in INCIDENT_CATEGORIES)
    session.commit()

    existing_codes = {c.code for c in session.query(LawCategory).all()}
    missing = [(code, name) for code, name in LAW_CATEGORIES if code not in existing_codes]
    if missing:
        session.add_all(LawCategory(code=code, name=name) for code, name in missing)
    session.commit()
    _backfill_law_categories(session)

    for account in TEST_ACCOUNTS:
        user = session.query(User).filter(User.username == account["username"]).first()

        if user is None:
            user = User(
                username=account["username"],
                password_hash=hash_password(account["password"]),
                display_name=account["display_name"],
                role=account["role"],
            )
            session.add(user)

        # 이미 있는 계정이라도 프로필이 비어 있으면 채워준다(컬럼이 나중에 추가되었기 때문).
        user.occupation = user.occupation or account["occupation"]
        user.contact = user.contact or account["contact"]
        user.sigungu_code = user.sigungu_code or account["sigungu_code"]
        sido_prefix = account["sigungu_code"][:2]
        user.sido_code = user.sido_code or _SIDO_MERGES.get(sido_prefix, sido_prefix)

    session.commit()


def _backfill_law_categories(session: Session) -> None:
    """이미 수집된 법령의 분야를 매 기동 시 재계산한다(재수집 없이).

    classify_law() 규칙이 계속 다듬어지므로(app/law_category.py), NULL인 것만 채우면 규칙이
    바뀌어도 기존 법령은 옛 분류에 갇힌다. 순수 규칙 함수라 재계산 비용이 작으니 매번 다시
    계산해 항상 최신 규칙을 반영한다."""
    laws = session.query(Law).all()
    if not laws:
        return

    category_id_by_code = {c.code: c.id for c in session.query(LawCategory).all()}
    for law in laws:
        code = classify_law(law.law_name, law.department)
        law.category_id = category_id_by_code.get(code, category_id_by_code.get("etc"))
    session.commit()
