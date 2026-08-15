"""데모용 기준 데이터(사건 유형) 시딩. 이미 존재하면 아무 것도 하지 않는다.

행정구역(regions)은 지도 경계 파일에서 따로 시드한다(app/regions_seed.py).
"""

from sqlalchemy.orm import Session

from app.law_category import LAW_CATEGORIES, classify_law
from app.models import IncidentCategory, Law, LawCategory

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
