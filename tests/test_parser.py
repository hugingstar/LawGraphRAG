"""법제처 조문 XML 파싱 — 특히 삭제된 조문을 걸러내는지 검증한다.

삭제 조문("제51조 삭제 <2018.9.18>")은 법적 내용이 없는데도 법제처 XML에 계속 실려 온다.
이런 껍데기가 수천 건 쌓이면 임베딩 공간에서 거의 똑같은 벡터 뭉치를 이루고, 그 뭉치가
어떤 질의에서도 일정 거리(실측 0.158)에 자리잡아 후보 상위를 점거한다.
실제로 이 필터를 넣기 전에는 "폭행죄 형법 조문" 질의의 최근접 40개가 전부 삭제 조문이었다.
"""

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from app.parser import parse_articles


def _xml(*units: str) -> ET.Element:
    return ET.fromstring(f"<법령>{''.join(units)}</법령>")


def _unit(no: int, title: str | None, content: str, sub: int = 0, kind: str = "조문") -> str:
    # 개정일자 표기의 "<...>"가 그대로 들어가면 XML이 깨지므로 이스케이프한다.
    title_tag = f"<조문제목>{escape(title)}</조문제목>" if title else ""
    return (
        "<조문단위>"
        f"<조문번호>{no}</조문번호>"
        f"<조문가지번호>{sub}</조문가지번호>"
        f"<조문여부>{kind}</조문여부>"
        f"{title_tag}"
        f"<조문내용>{escape(content)}</조문내용>"
        "</조문단위>"
    )


def test_keeps_normal_article():
    articles = parse_articles(_xml(_unit(260, "폭행, 존속폭행", "제260조(폭행, 존속폭행) ①사람의 신체에 대하여 폭행을 가한 자는")))
    assert len(articles) == 1
    assert articles[0]["article_no"] == 260
    assert articles[0]["title"] == "폭행, 존속폭행"


def test_drops_deleted_article():
    articles = parse_articles(_xml(_unit(51, None, "제51조 삭제 <2018.9.18>")))
    assert articles == []


def test_drops_deleted_article_with_sub_number():
    articles = parse_articles(_xml(_unit(32, None, "제32조의6 삭제 <2018.9.18>", sub=6)))
    assert articles == []


def test_keeps_article_that_merely_mentions_deletion():
    """본문 안에 '삭제'가 들어 있을 뿐인 조문까지 지우면 안 된다."""
    content = "제3조(집단적 폭행 등) ① 삭제 <2016.1.6> ③ 이 법을 위반한 자는 처벌한다"
    articles = parse_articles(_xml(_unit(3, "집단적 폭행 등", content)))
    assert len(articles) == 1


def test_drops_deleted_but_keeps_siblings():
    articles = parse_articles(
        _xml(
            _unit(50, "권한의 위임", "제50조(권한의 위임) 이 법에 따른 권한은 위임할 수 있다"),
            _unit(51, None, "제51조 삭제 <2026.3.24>"),
            _unit(52, "벌칙", "제52조(벌칙) 위반한 자는 처벌한다"),
        )
    )
    assert [a["article_no"] for a in articles] == [50, 52]
