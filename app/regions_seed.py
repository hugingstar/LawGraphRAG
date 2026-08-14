"""행정구역(regions) 시드를 지도 경계 파일에서 직접 생성한다.

지역 목록을 코드에 따로 하드코딩하지 않고 지도와 같은 파일에서 읽는 이유는, DB의 지역 코드와
지도 도형의 feature code가 어긋나면 색칠할 지역을 못 찾아 지도가 조용히 비어 보이기 때문이다.
같은 파일을 단일 진실 공급원으로 삼으면 그런 불일치가 원천적으로 생기지 않는다.

경계 데이터 출처: 통계청(KOSTAT) 2018 행정구역 경계, KOGL(공공누리) 라이선스.

**2026-07-01 광주·전남 행정통합**: 원본 경계 파일은 2018년 스냅샷이라 광주광역시(24)·
전라남도(36)가 통합되어 "전남광주통합특별시"가 된 사실을 반영하지 못한다. 시군구 경계선
자체는 바뀌지 않았으므로(구·시·군 모양은 그대로, 소속 시도만 바뀜) 도형을 다시 그리는 대신
아래 `_SIDO_MERGES`로 두 시도를 하나의 코드 아래 묶는다. 통계청이 신규 시도코드를 공식
부여하면(이 글 작성 시점 기준 미확인) 그 코드로 교체할 것 — 지금은 광주의 기존 코드(24)를
잠정적으로 재사용한다.
"""

import json
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Region

_GEO_DIR = Path(__file__).parent / "static" / "geo"

# (파일명, TopoJSON object 키, level)
_SOURCES = [
    ("sido.topo.json", "skorea_provinces_2018_geo", "sido"),
    ("sigungu.topo.json", "skorea_municipalities_2018_geo", "sigungu"),
]

# 광주·전남 통합(2026-07-01): 옛 전라남도(36) 코드를 광주의 옛 코드(24)로 흡수한다.
# 시군구 코드 앞 2자리로 소속 시도를 찾는 로직(_parent_code)이 이 표를 거치도록 해서,
# 옛 36xxx 시군구도 자동으로 통합 시도 밑에 들어간다.
_SIDO_MERGES = {"36": "24"}
_MERGED_SIDO_NAMES = {"24": "전남광주통합특별시"}


def _parent_code(sido_prefix: str) -> str:
    return _SIDO_MERGES.get(sido_prefix, sido_prefix)


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
                if code in _SIDO_MERGES:
                    # 통합으로 사라진 옛 시도(전라남도)는 별도 행을 만들지 않는다 —
                    # 그 시도에 속했던 시군구는 아래에서 통합 코드로 편입된다.
                    continue
                name = _MERGED_SIDO_NAMES.get(code, name)
                sido_names[code] = name
                parent_code = None
                full_name = name
            else:
                # 시군구 코드 앞 2자리가 소속 시도 코드다("11010" -> "11"). 광주/전남처럼
                # 통합된 시도는 _parent_code가 통합 코드로 바꿔준다.
                parent_code = _parent_code(code[:2])
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
    _sweep_merged_sido(session)


def _sweep_merged_sido(session: Session) -> None:
    """옛 시도(전라남도=36)가 이번 실행 전에 이미 DB에 심어져 있었다면 정리한다.

    위 upsert 루프는 병합으로 사라진 시도를 더 이상 건드리지 않으므로, 예전에 심어진
    행이 그대로 남아 통합 시도(24)와 중복 표시된다. FK로 그 코드를 참조하는 사건/사용자를
    먼저 통합 코드로 옮긴 뒤에야 옛 행을 지울 수 있다. 이미 정리된 상태라면(옛 행이 없으면)
    조용히 아무 것도 하지 않는다 — 매 기동마다 실행돼도 안전하다.
    """
    for old_code, new_code in _SIDO_MERGES.items():
        if session.get(Region, old_code) is None:
            continue
        session.execute(
            text("UPDATE incidents SET sido_code = :new WHERE sido_code = :old"),
            {"new": new_code, "old": old_code},
        )
        session.execute(
            text("UPDATE users SET sido_code = :new WHERE sido_code = :old"),
            {"new": new_code, "old": old_code},
        )
        session.commit()
        session.execute(text("DELETE FROM regions WHERE code = :old"), {"old": old_code})
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
