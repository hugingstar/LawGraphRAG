"""벡터/키워드 검색으로 찾은 조문을 시드로, Neo4j 그래프에서 관련 조문을 확장한다.

REFERENCES 관계(명시적 조문 인용)와, 같은 엔티티(의무주체/적용대상/처벌 등)를 공유하는 조문을
1~2-hop 이내에서 찾아 후보에 추가한다. Neo4j 조회가 실패해도(다운/타임아웃) 예외를 삼키고 빈
리스트를 반환한다 — 그래프 확장 없이도 기존 벡터+trigram 검색 결과만으로 항상 정상 동작해야 한다.
"""

import logging

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.graph_db import graph_session
from app.models import Article, Law

logger = logging.getLogger(__name__)

_EXPAND_QUERY = """
UNWIND $seeds AS s
MATCH (seed:Article {law_id: s.law_id, article_no: s.article_no, article_no_sub: s.article_no_sub})
MATCH (seed)-[:REFERENCES|DEFINES|APPLIES_TO|PENALIZED_BY|REQUIRES*1..2]-(related:Article)
WHERE NOT (related.law_id = seed.law_id AND related.article_no = seed.article_no
           AND related.article_no_sub = seed.article_no_sub)
RETURN DISTINCT related.law_id AS law_id, related.article_no AS article_no,
       related.article_no_sub AS article_no_sub
LIMIT $limit
"""


def graph_expand(session: Session, seed_articles: list[Article], limit: int = 10) -> list[Article]:
    """seed_articles와 그래프상으로 관련된 다른 조문을 반환한다(seed 자신은 제외)."""
    if not seed_articles:
        return []

    seeds = [
        {"law_id": a.law.law_id, "article_no": a.article_no, "article_no_sub": a.article_no_sub}
        for a in seed_articles
    ]

    try:
        with graph_session() as gs:
            records = gs.run(_EXPAND_QUERY, seeds=seeds, limit=limit).data()
    except Exception:
        logger.warning("Neo4j 그래프 확장 실패, 벡터/트라이그램 결과만 사용합니다.", exc_info=True)
        return []

    if not records:
        return []

    law_ids = {r["law_id"] for r in records}
    laws = session.scalars(select(Law).where(Law.law_id.in_(law_ids))).all()
    law_pk_by_law_id = {law.law_id: law.id for law in laws}

    keys = [
        (law_pk_by_law_id[r["law_id"]], r["article_no"], r["article_no_sub"])
        for r in records
        if r["law_id"] in law_pk_by_law_id
    ]
    if not keys:
        return []

    return session.scalars(
        select(Article).where(tuple_(Article.law_id, Article.article_no, Article.article_no_sub).in_(keys))
    ).all()
