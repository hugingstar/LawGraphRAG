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


def _collect_text(el) -> str:
    """조문단위 하위의 모든 텍스트(항/호/목 포함)를 순서대로 이어붙인다."""
    parts = []
    for t in el.itertext():
        t = t.strip()
        if t:
            parts.append(t)
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
    articles = []
    for unit in root.findall(".//조문단위"):
        article_no_raw = _text(unit, "조문번호")
        if not article_no_raw or not article_no_raw.isdigit():
            continue  # 조문 삭제/편장절 표시 등 실제 조문이 아닌 항목은 건너뜀

        title_raw = _text(unit, "조문제목")
        content = _text(unit, "조문내용") or ""
        full_text = _collect_text(unit)
        if not full_text:
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
