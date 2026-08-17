"""벡터 검색으로 찾은 조문을 시드로, Neo4j 그래프에서 관련 조문을 확장한다.

REFERENCES 관계(명시적 조문 인용)와, 같은 엔티티(의무주체/적용대상/처벌 등)를 공유하는 조문을
1~2-hop 이내에서 찾아 후보에 추가한다. Neo4j 조회가 실패해도(다운/타임아웃) 예외를 삼키고 빈
리스트를 반환한다 — 그래프 확장 없이도 벡터 검색 결과만으로 항상 정상 동작해야 한다.
"""

import logging
import time

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.graph_db import graph_session
from app.models import Article, Law

logger = logging.getLogger(__name__)

# 그래프가 비어 있는지 확인한 결과를 캐시하는 시간. 적재(app.graph_ingest)는 앱 밖에서
# 따로 돌리므로, 비어 있다는 판정을 영구히 붙들면 적재 후 재시작해야 그래프가 살아난다.
_EMPTY_RECHECK_SECONDS = 300

_graph_populated: bool | None = None
_last_checked_at: float = 0.0

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


def _graph_has_articles() -> bool:
    """그래프에 Article 노드가 하나라도 있는지 확인한다(결과는 잠시 캐시한다).

    graph_ingest를 한 번도 돌리지 않았으면 그래프가 통째로 비어 있는데, 그 상태로도
    확장 쿼리는 검색할 때마다 Neo4j를 왕복하며 매번 빈 결과를 받는다. 미리 걸러서
    쓸데없는 왕복을 없앤다. 비어 있다는 판정만 만료시키므로, 적재를 마치면 몇 분 안에
    앱 재시작 없이 그래프 확장이 다시 살아난다."""
    global _graph_populated, _last_checked_at

    if _graph_populated:
        return True  # 한 번 채워진 그래프가 도로 비는 일은 없다고 본다
    if _graph_populated is False and time.monotonic() - _last_checked_at < _EMPTY_RECHECK_SECONDS:
        return False

    try:
        with graph_session() as gs:
            populated = gs.run("MATCH (a:Article) RETURN a LIMIT 1").peek() is not None
    except Exception:
        logger.warning("Neo4j 상태 확인 실패, 그래프 확장을 건너뜁니다.", exc_info=True)
        populated = False

    if not populated and _graph_populated is None:
        # 처음 확인했을 때만 알린다 — 매 검색마다 찍으면 로그가 가득 찬다.
        logger.info(
            "Neo4j에 조문 그래프가 없어 그래프 확장을 건너뜁니다. "
            "`python -m app.graph_ingest --law <법령명>`으로 적재하면 자동으로 다시 사용합니다."
        )

    _graph_populated = populated
    _last_checked_at = time.monotonic()
    return populated


def graph_expand(session: Session, seed_articles: list[Article], limit: int = 10) -> list[Article]:
    """seed_articles와 그래프상으로 관련된 다른 조문을 반환한다(seed 자신은 제외)."""
    if not seed_articles or not _graph_has_articles():
        return []

    seeds = [
        {"law_id": a.law.law_id, "article_no": a.article_no, "article_no_sub": a.article_no_sub}
        for a in seed_articles
    ]

    try:
        with graph_session() as gs:
            records = gs.run(_EXPAND_QUERY, seeds=seeds, limit=limit).data()
    except Exception:
        logger.warning("Neo4j 그래프 확장 실패, 벡터 결과만 사용합니다.", exc_info=True)
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
