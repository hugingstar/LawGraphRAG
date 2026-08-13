"""데모용 기준 데이터(부서/사업장/테스트 계정) 시딩. 이미 존재하면 아무 것도 하지 않는다."""

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Department, Site, User

DEPARTMENTS = [
    "양극재1생산부",
    "양극재2생산부",
    "음극재생산부",
    "전구체생산부",
    "리튬제련부",
    "설비보전부",
    "품질보증부",
    "물류부",
    "연구소",
    "안전환경부",
]

SITES = ["포항사업장", "광양사업장", "구미사업장", "세종사업장"]


# 테스트용 기본 계정
TEST_ACCOUNTS = [
    {
        "username": "user01",
        "password": "1111",
        "display_name": "김신청",
        "role": "requester",
        "rank": "검사반장",
        "contact": "010-2345-6789",
        "department": "품질보증부",
        "site": "포항사업장",
    },
    {
        # user01과 같은 사업장, 다른 부서
        "username": "user02",
        "password": "1111",
        "display_name": "이물류",
        "role": "requester",
        "rank": "물류담당",
        "contact": "010-2222-3333",
        "department": "물류부",
        "site": "포항사업장",
    },
    {
        # user01과 다른 사업장, 같은 부서
        "username": "user03",
        "password": "1111",
        "display_name": "최검사",
        "role": "requester",
        "rank": "품질검사원",
        "contact": "010-3333-4444",
        "department": "품질보증부",
        "site": "광양사업장",
    },
    {
        # user01과 다른 사업장, 다른 부서
        "username": "user04",
        "password": "1111",
        "display_name": "정설비",
        "role": "requester",
        "rank": "설비담당",
        "contact": "010-4444-5555",
        "department": "설비보전부",
        "site": "구미사업장",
    },
    {
        "username": "manager01",
        "password": "1111",
        "display_name": "박안전",
        "role": "manager",
        "rank": "안전보건팀장",
        "contact": "010-9876-5432",
        "department": "안전환경부",
        "site": "포항사업장",
    },
    {
        "username": "manager02",
        "password": "1111",
        "display_name": "한관리",
        "role": "manager",
        "rank": "안전관리자",
        "contact": "010-5555-6666",
        "department": "안전환경부",
        "site": "세종사업장",
    },
]


def seed_reference_data(session: Session) -> None:
    if session.query(Department).count() == 0:
        session.add_all(Department(name=name) for name in DEPARTMENTS)
    if session.query(Site).count() == 0:
        session.add_all(Site(name=name) for name in SITES)
    session.commit()

    for account in TEST_ACCOUNTS:
        department = session.query(Department).filter(Department.name == account["department"]).first()
        site = session.query(Site).filter(Site.name == account["site"]).first()
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
        user.rank = user.rank or account["rank"]
        user.contact = user.contact or account["contact"]
        user.department_id = user.department_id or (department.id if department else None)
        user.site_id = user.site_id or (site.id if site else None)

    session.commit()
