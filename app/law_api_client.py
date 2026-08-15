"""법제처 국가법령정보 공동활용 Open API 클라이언트.

OC 키는 https://open.law.go.kr 에서 발급받아 LAW_OC_KEY 환경변수로 설정한다.
API 스펙: https://open.law.go.kr/LSO/openApi/guideList.do
"""

import time

import httpx

from app.config import settings

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0

# 수십 개 법령을 연달아 수집하므로 커넥션을 재사용한다(매 호출 httpx.get은 TCP/TLS 핸드셰이크를
# 매번 새로 한다). 모듈 수준에 하나만 두고 프로세스 수명 동안 함께 쓴다.
_client = httpx.Client(timeout=30.0, headers={"User-Agent": "LawOwly/1.0"})


class LawApiError(RuntimeError):
    pass


def _get_with_retry(url: str, params: dict) -> httpx.Response:
    """일시적 오류(타임아웃/5xx/429)는 지수 백오프로 재시도한다.

    법제처 API는 대량 조회 중 간헐적으로 실패하는데, 재시도가 없으면 법령 하나가 통째로
    유실된다. 4xx(429 제외)는 요청 자체가 잘못된 것이라 재시도하지 않는다.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = _client.get(url, params=params)
            if resp.status_code < 400:
                return resp
            if resp.status_code != 429 and resp.status_code < 500:
                raise LawApiError(f"법제처 API 오류 {resp.status_code}: {resp.text[:200]}")
            last_error = LawApiError(f"법제처 API 오류 {resp.status_code}")
        except httpx.RequestError as exc:
            last_error = exc

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

    raise LawApiError(f"법제처 API 호출이 {MAX_RETRIES}회 모두 실패했습니다: {last_error}")


def _require_oc_key() -> str:
    if not settings.law_oc_key:
        raise LawApiError(
            "LAW_OC_KEY가 설정되어 있지 않습니다. https://open.law.go.kr 에서 OC 키를 발급받아 "
            ".env의 LAW_OC_KEY에 설정하세요."
        )
    return settings.law_oc_key


def search_law(query: str, *, display: int = 100, page: int = 1) -> list[dict]:
    """법령명으로 검색해 후보 목록(법령ID/MST 포함)을 반환한다.

    display를 넉넉히 두는 이유는, 수집 측에서 '법령명 정확 일치'만 채택하기 때문이다.
    "민법"처럼 부분 일치하는 법령이 많은 이름은 목록이 잘리면 정작 원하는 법이 후보에서
    빠져 수집이 실패한다.

    query=""(빈 문자열)이면 전체 법령을 대상으로 하므로, page와 함께 쓰면 전체 목록을
    페이지네이션으로 순회할 수 있다(app.ingest.list_all_current_laws 참고).
    """
    params = {
        "OC": _require_oc_key(),
        "target": "law",
        "type": "XML",
        "query": query,
        "display": display,
        "page": page,
    }
    return _parse_search_response(_get_with_retry(SEARCH_URL, params).text)


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

    return _get_with_retry(DETAIL_URL, params).text


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
                "department": _text(law_el, "소관부처명"),
                "current_yn": _text(law_el, "현행연혁코드"),
                "promulgation_date": _text(law_el, "공포일자"),
                "effective_date": _text(law_el, "시행일자"),
            }
        )
    return results


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None
