"""법제처 Open API 조문 상세 XML 파서.

법령마다 세부 태그 구성이 조금씩 다를 수 있어(시행령/시행규칙/규칙 등),
알려진 태그가 없으면 건너뛰는 방식으로 관대하게 파싱한다.
"""

import re
import xml.etree.ElementTree as ET


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


# 조문단위 아래에서 실제 본문이 담긴 태그. 나머지(조문번호·조문여부·조문시행일자·
# 조문변경여부·조문이동이전/이후)는 메타데이터이고, 항번호·호번호·목번호는 같은 기호가
# 항내용/호내용/목내용 앞에 이미 들어 있어 중복이다.
_CONTENT_TAGS = ("조문내용", "항내용", "호내용", "목내용")

# "제51조 삭제 <2018.9.18>"처럼 삭제된 조문. 법제처 XML은 삭제된 조를 이 한 줄짜리
# 껍데기로 계속 내려보낸다.
_DELETED_ARTICLE_RE = re.compile(r"^제\d+조(?:의\d+)?\s*삭제")


def _collect_text(el) -> str:
    """조문 본문만(조문내용 + 항/호/목 내용) 문서 순서대로 이어붙인다.

    예전에는 조문단위 전체에 itertext()를 걸어 메타데이터까지 본문에 섞였다. 그 결과
    제42조의 원문이 "42 / 조문 / 추락의 방지 / 20250901 / N / 제42조(추락의 방지) / ① / ..."로
    시작했고, 이 앞부분이 그대로 청킹·임베딩되어 검색 후보를 갉아먹었다."""
    parts = []
    for node in el.iter():
        if node.tag not in _CONTENT_TAGS:
            continue
        text = "".join(node.itertext()).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def parse_law_meta(root: ET.Element) -> dict:
    basic = root.find(".//기본정보")
    if basic is None:
        basic = root
    return {
        "law_id": _text(basic, "법령ID") or _text(root, "법령ID"),
        "law_name": _text(basic, "법령명_한글") or _text(basic, "법령명한글") or _text(root, "법령명"),
        "law_type": _text(basic, "법종구분") or _text(basic, "법령구분명"),
        "promulgation_date": _text(basic, "공포일자"),
        "effective_date": _text(basic, "시행일자"),
    }


def parse_articles(root: ET.Element) -> list[dict]:
    units = root.findall(".//조문단위")

    # 편장절 제목("제6장 추락 또는 붕괴에 의한 위험 방지")도 조문단위로 오는데, 조문번호는
    # 바로 뒤 조문의 번호를 그대로 달고 있어 번호만 봐서는 걸러지지 않는다. 조문여부가
    # '조문'이 아니면 대개 이런 제목이라 걸러낸다.
    #
    # 다만 "노동절 제정에 관한 법률"처럼 조문 형식 없이 본문 전체가 한 덩어리인 아주 짧은
    # 법은 그 유일한 조문단위조차 조문여부='전문'로 온다 — 그 경우 필터를 걸면 조문이
    # 하나도 안 남는다. 이 법에 '조문'으로 표시된 단위가 하나도 없으면(=장절 구성 자체가
    # 없는 법) 필터를 걸지 않는다.
    has_real_articles = any(_text(u, "조문여부") == "조문" for u in units)

    articles = []
    for unit in units:
        article_no_raw = _text(unit, "조문번호")
        if not article_no_raw or not article_no_raw.isdigit():
            continue  # 조문 삭제 등 실제 조문이 아닌 항목은 건너뜀

        if has_real_articles and _text(unit, "조문여부") != "조문":
            continue

        title_raw = _text(unit, "조문제목")
        content = _text(unit, "조문내용") or ""
        full_text = _collect_text(unit)
        if not full_text:
            continue

        # 삭제된 조문은 저장하지 않는다. 본문이 "제51조 삭제 <2018.9.18>" 한 줄뿐이라
        # 법적 내용이 없는데, 이런 껍데기가 5천 건 넘게 쌓이면 임베딩 공간에서 서로
        # 거의 똑같은 벡터 뭉치를 이룬다. 그 뭉치가 어떤 질의에서도 0.16 언저리에
        # 자리잡아, 정확히 맞는 조문이 그보다 멀면(키워드 나열형 질의에서 자주 그렇다)
        # 후보 상위를 통째로 점거해 버린다 — 실측으로 "폭행죄 형법 조문 반의사불벌죄"의
        # 최근접 40개가 전부 삭제 조문이었다.
        if _DELETED_ARTICLE_RE.match(full_text):
            continue

        articles.append(
            {
                "article_no": int(article_no_raw),
                "article_no_sub": int(_text(unit, "조문가지번호") or 0),
                "title": title_raw,
                "full_text": full_text or content,
                "effective_date": _text(unit, "조문시행일자"),
            }
        )
    return articles


def parse_law_detail(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    meta = parse_law_meta(root)
    articles = parse_articles(root)
    return {**meta, "articles": articles}


def split_article_no(label: str) -> tuple[int, int]:
    """'제38조의2' 같은 라벨을 (38, 2)로 변환한다."""
    m = re.match(r"제(\d+)조(?:의(\d+))?", label)
    if not m:
        raise ValueError(f"조 번호 형식을 인식할 수 없습니다: {label}")
    return int(m.group(1)), int(m.group(2) or 0)
