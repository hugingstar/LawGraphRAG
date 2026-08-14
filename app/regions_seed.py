"""행정구역(regions) 시드를 지도 경계 파일에서 직접 생성한다.

지역 목록을 코드에 따로 하드코딩하지 않고 지도와 같은 파일에서 읽는 이유는, DB의 지역 코드와
지도 도형의 feature code가 어긋나면 색칠할 지역을 못 찾아 지도가 조용히 비어 보이기 때문이다.
같은 파일을 단일 진실 공급원으로 삼으면 그런 불일치가 원천적으로 생기지 않는다.

경계 데이터 출처: 통계청(KOSTAT) 2018 행정구역 경계, KOGL(공공누리) 라이선스.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Region

_GEO_DIR = Path(__file__).parent / "static" / "geo"

# (파일명, TopoJSON object 키, level)
_SOURCES = [
    ("sido.topo.json", "skorea_provinces_2018_geo", "sido"),
    ("sigungu.topo.json", "skorea_municipalities_2018_geo", "sigungu"),
]


def _read_features(filename: str, object_key: str) -> list[dict]:
    path = _GEO_DIR / filename
    with path.open(encoding="utf-8") as f:
        topo = json.load(f)
    return [g["properties"] for g in topo["objects"][object_key]["geometries"]]


def seed_regions(session: Session) -> None:
    """시도 -> 시군구 순으로 upsert한다(시군구의 parent_code FK가 시도를 참조하므로 순서가 중요)."""
    sido_names: dict[str, str] = {}

    for filename, object_key, level in _SOURCES:
        for props in _read_features(filename, object_key):
            code = props["code"]
            name = props["name"]

            if level == "sido":
                sido_names[code] = name
                parent_code = None
                full_name = name
            else:
                # 시군구 코드 앞 2자리가 곧 소속 시도 코드다("11010" -> "11").
                parent_code = code[:2]
                full_name = f"{sido_names.get(parent_code, '')} {name}".strip()

            region = session.get(Region, code)
            if region is None:
                region = Region(code=code)
                session.add(region)

            region.name = name
            region.full_name = full_name
            region.level = level
            region.parent_code = parent_code

        session.flush()

    session.commit()


def region_count(session: Session) -> dict[str, int]:
    """검증용. level별 지역 수를 반환한다."""
    rows = session.execute(
        select(Region.level, Region.code)
    ).all()
    counts: dict[str, int] = {}
    for level, _ in rows:
        counts[level] = counts.get(level, 0) + 1
    return counts
