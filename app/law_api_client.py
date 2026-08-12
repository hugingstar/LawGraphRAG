"""법제처 국가법령정보 공동활용 Open API 클라이언트.

OC 키는 https://open.law.go.kr 에서 발급받아 LAW_OC_KEY 환경변수로 설정한다.
API 스펙: https://open.law.go.kr/LSO/openApi/guideList.do
"""

import httpx

from app.config import settings

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"


class LawApiError(RuntimeError):
    pass


def _require_oc_key() -> str:
    if not settings.law_oc_key:
        raise LawApiError(
            "LAW_OC_KEY가 설정되어 있지 않습니다. https://open.law.go.kr 에서 OC 키를 발급받아 "
            ".env의 LAW_OC_KEY에 설정하세요."
        )
    return settings.law_oc_key


def search_law(query: str, *, display: int = 20) -> list[dict]:
    """법령명으로 검색해 후보 목록(법령ID/MST 포함)을 반환한다."""
    oc = _require_oc_key()
    params = {
        "OC": oc,
        "target": "law",
        "type": "XML",
        "query": query,
        "display": display,
    }
    resp = httpx.get(SEARCH_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    return _parse_search_response(resp.text)


def get_law_detail_xml(law_id: str | None = None, mst: str | None = None) -> str:
    """법령ID 또는 MST(법령일련번호)로 조문 전문 XML을 조회한다."""
    oc = _require_oc_key()
    if not law_id and not mst:
        raise ValueError("law_id 또는 mst 중 하나는 필요합니다.")

    params = {"OC": oc, "target": "law", "type": "XML"}
    if mst:
        params["MST"] = mst
    else:
        params["ID"] = law_id

    resp = httpx.get(DETAIL_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.text


def _parse_search_response(xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    results = []
    for law_el in root.findall(".//law"):
        results.append(
            {
                "law_id": _text(law_el, "법령ID"),
                "mst": _text(law_el, "법령일련번호"),
                "law_name": _text(law_el, "법령명한글"),
                "law_type": _text(law_el, "법령구분명"),
                "promulgation_date": _text(law_el, "공포일자"),
                "effective_date": _text(law_el, "시행일자"),
            }
        )
    return results


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None
