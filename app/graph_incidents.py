"""사건과 지역을 Neo4j 그래프에 편입한다.

법령 그래프(Law/Article/Entity)만으로는 "어떤 조문이 어떤 조문과 이어지는가"까지만 알 수 있다.
여기에 실제 사건과 발생 지역을 노드로 붙이면 "이 지역에서 자주 걸리는 조문", "이 조문이 많이
적용된 지역" 같은 질문에 답할 수 있다 — Postgres 집계로는 지역·조문을 잇는 조인이 여러 단계라
표현하기 번거로운 반면, 그래프에서는 한 번의 순회로 끝난다.

그래프 스키마:
    (:Region {code, name})-[:HAS_INCIDENT]->(:Incident {id, category})-[:CITES]->(:Article)

**장애 격리**: 이 모듈의 실패는 절대 호출부로 전파되지 않는다. 사건 접수는 그래프 동기화보다
훨씬 중요하므로, Neo4j가 죽어 있어도 신고는 정상적으로 저장되어야 한다
(app/graph_retrieval.py의 방침과 동일).
"""

import logging

from app.graph_db import graph_session
from app.models import Incident

logger = logging.getLogger(__name__)

_SYNC_QUERY = """
MERGE (r:Region {code: $region_code})
  SET r.name = $region_name, r.parent_code = $parent_code
MERGE (i:Incident {id: $incident_id})
  SET i.category = $category, i.status = $status, i.created_at = $created_at
MERGE (r)-[:HAS_INCIDENT]->(i)
"""

# 인용 조문을 다시 이을 때는 기존 CITES를 먼저 지운다.
# 관리자가 검토 화면에서 조문을 제거할 수 있어, 지우지 않으면 삭제된 인용이 그래프에 남는다.
_CLEAR_CITES = """
MATCH (i:Incident {id: $incident_id})-[c:CITES]->()
DELETE c
"""

# 조문을 Law 노드를 거쳐 찾는다. Article에 law_name을 복사해 두긴 하지만, 그 속성은
# graph_ingest가 나중에 추가한 것이라 먼저 적재된 조문 노드에는 없다. HAS_ARTICLE 관계는
# 처음부터 항상 있으므로 이쪽이 안전하다.
_LINK_CITATION = """
MATCH (i:Incident {id: $incident_id})
MATCH (:Law {law_name: $law_name})-[:HAS_ARTICLE]->
      (a:Article {article_no: $article_no, article_no_sub: $article_no_sub})
MERGE (i)-[:CITES]->(a)
"""

_TOP_ARTICLES_BY_REGION = """
MATCH (r:Region)-[:HAS_INCIDENT]->(:Incident)-[:CITES]->(a:Article)<-[:HAS_ARTICLE]-(l:Law)
WHERE r.code = $region_code OR r.parent_code = $region_code
RETURN l.law_name AS law_name, a.article_label AS article_label, a.title AS title,
       count(*) AS hits
ORDER BY hits DESC
LIMIT $limit
"""


def sync_incident(incident: Incident) -> None:
    """사건 1건과 그 인용 조문을 그래프에 반영한다. 실패해도 예외를 던지지 않는다."""
    if not incident.sigungu_code:
        return

    try:
        with graph_session() as gs:
            gs.run(
                _SYNC_QUERY,
                region_code=incident.sigungu_code,
                region_name=incident.sigungu.name if incident.sigungu else "",
                parent_code=incident.sido_code,
                incident_id=incident.id,
                category=incident.category.name if incident.category else None,
                status=incident.status,
                created_at=incident.created_at.isoformat() if incident.created_at else None,
            )

            gs.run(_CLEAR_CITES, incident_id=incident.id)
            for citation in incident.citations or []:
                article_no, article_no_sub = _parse_article_label(citation.get("article_label", ""))
                if article_no is None:
                    continue
                gs.run(
                    _LINK_CITATION,
                    incident_id=incident.id,
                    law_name=citation.get("law_name"),
                    article_no=article_no,
                    article_no_sub=article_no_sub,
                )
    except Exception:  # noqa: BLE001 - 사건 접수가 그래프 동기화 실패로 무너지면 안 된다
        logger.warning("사건 %s의 그래프 동기화 실패", incident.id, exc_info=True)


def top_articles_for_region(region_code: str, limit: int = 5) -> list[dict]:
    """해당 지역(시도 코드면 하위 시군구 포함)에서 가장 많이 인용된 조문."""
    try:
        with graph_session() as gs:
            return gs.run(_TOP_ARTICLES_BY_REGION, region_code=region_code, limit=limit).data()
    except Exception:  # noqa: BLE001 - 통계 위젯 하나 때문에 대시보드가 죽으면 안 된다
        logger.warning("지역 %s의 빈출 조문 조회 실패", region_code, exc_info=True)
        return []


def _parse_article_label(label: str) -> tuple[int | None, int]:
    """'제38조의2' -> (38, 2). citations에는 조번호가 라벨 문자열로만 남아 있다."""
    import re

    match = re.match(r"제(\d+)조(?:의(\d+))?", label or "")
    if not match:
        return None, 0
    return int(match.group(1)), int(match.group(2) or 0)
