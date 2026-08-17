"""Neo4j 그래프를 3d-force-graph 가 먹는 {nodes, links} 로 변환한다.

전체를 한 번에 내보내지 않는다. Article 은 법령 하나당 수백 개라 조금만 쌓여도
WebGL 이 버티지 못한다. 처음엔 Domain/Law 만 그리고, 노드를 클릭했을 때 그
아래를 붙여 넣는 드릴다운 구조다.
"""

from __future__ import annotations

import math
from typing import Any

from ops import palette
from ops.checks import get_neo4j_driver
from ops.config import ops_settings as cfg


def _node(node_id: str, label: str, name: str, sub: str | None = None,
          val: float = 1.0, meta: dict[str, Any] | None = None,
          law_name: str | None = None) -> dict[str, Any]:
    """노드 하나를 만든다.

    색은 화면에서 고를 수 있는 기준마다 하나씩 미리 계산해 둔다. 프론트가 기준을
    바꿀 때 서버를 다시 부르지 않아도 되고, 범례와 노드가 같은 정의를 쓰게 된다.
    `heat`(인용도)는 전체를 봐야 정해지므로 overview 가 나중에 채운다.
    """
    group = palette.group_of(law_name if law_name is not None else (name if label == "Law" else None))
    return {
        "id": node_id,
        "label": label,
        "name": name or "(이름 없음)",
        "sub": sub,
        "val": val,
        "group": group,
        "group_name": palette.GROUP_NAMES[group],
        "heat": None,
        "colors": {
            "category": palette.GROUP_COLORS[group],
            "label": palette.LABEL_COLORS.get(label, palette.LABEL_COLORS["Unknown"]),
        },
        "meta": meta or {},
    }


def _article_name(record: dict[str, Any]) -> str:
    label = record.get("article_label")
    if label:
        return label
    no = record.get("article_no")
    sub = record.get("article_no_sub")
    return f"제{no}조" + (f"의{sub}" if sub else "")


_OVERVIEW = """
MATCH (l:Law)
OPTIONAL MATCH (d:Domain)-[:CONTAINS]->(l)
RETURN elementId(l)                          AS law_node_id,
       l.law_id                              AS law_id,
       l.law_name                            AS law_name,
       elementId(d)                          AS domain_node_id,
       d.code                                AS domain_code,
       d.name                                AS domain_name,
       count { (l)-[:HAS_ARTICLE]->(:Article) } AS article_count
ORDER BY article_count DESC
LIMIT $limit
"""

_ORPHAN_DOMAINS = """
MATCH (d:Domain)
WHERE NOT (d)-[:CONTAINS]->(:Law)
RETURN elementId(d) AS domain_node_id, d.code AS domain_code, d.name AS domain_name
LIMIT 100
"""

# 법령끼리 얼마나 서로를 인용하는지 집계한다.
#
# 원래는 Domain-[:CONTAINS]->Law 만으로 개요를 그렸는데, 실제 그래프에선
# 5380개 법령 중 Domain 에 붙은 게 1개뿐이라 화면이 연결 없는 점 구름이 된다.
# 조문 단위 REFERENCES 를 법령 단위로 말아 올리면 "어느 법이 어느 법을 얼마나
# 인용하는가"라는, 매크로 뷰에서 실제로 의미 있는 구조가 나온다.
_CROSS_LAW_REFERENCES = """
MATCH (a:Article)-[:REFERENCES]->(t:Article)
WHERE a.law_id <> t.law_id
RETURN a.law_id AS src_law, t.law_id AS dst_law, count(*) AS weight
ORDER BY weight DESC
LIMIT $limit
"""


def overview(limit: int | None = None) -> dict[str, Any]:
    """드릴다운의 출발점. Law 를 조문 수 순으로 싣고, Domain 소속과
    법령 간 상호인용을 간선으로 얹는다."""
    limit = limit or cfg.graph_max_nodes
    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    law_node_id_by_law_id: dict[str, str] = {}
    truncated = False

    with get_neo4j_driver().session() as session:
        rows = session.run(_OVERVIEW, limit=limit).data()
        truncated = len(rows) >= limit

        for row in rows:
            law_node_id = row["law_node_id"]
            law_node_id_by_law_id[row["law_id"]] = law_node_id
            article_count = row["article_count"] or 0
            nodes[law_node_id] = _node(
                law_node_id,
                "Law",
                row["law_name"] or row["law_id"],
                sub=f"조문 {article_count}개",
                # 조문 수를 그대로 반지름에 쓰면 큰 법령이 화면을 잡아먹는다.
                val=1.0 + min(article_count, 500) ** 0.5,
                meta={"law_id": row["law_id"], "article_count": article_count},
            )

            domain_node_id = row["domain_node_id"]
            if domain_node_id:
                if domain_node_id not in nodes:
                    nodes[domain_node_id] = _node(
                        domain_node_id,
                        "Domain",
                        row["domain_name"] or row["domain_code"],
                        sub="분야",
                        val=6.0,
                        meta={"code": row["domain_code"]},
                        law_name=row["domain_name"],
                    )
                links.append(
                    {"source": domain_node_id, "target": law_node_id, "type": "CONTAINS"}
                )

        for row in session.run(_ORPHAN_DOMAINS).data():
            node_id = row["domain_node_id"]
            nodes.setdefault(
                node_id,
                _node(
                    node_id,
                    "Domain",
                    row["domain_name"] or row["domain_code"],
                    sub="분야 (연결된 법령 없음)",
                    val=6.0,
                    meta={"code": row["domain_code"]},
                    law_name=row["domain_name"],
                ),
            )

        # 상한을 넉넉히 잡되, 양끝이 모두 실린 법령 쌍만 남긴다. 화면에 없는
        # 법령으로 가는 간선을 넣으면 프론트에서 버려지고 계산만 낭비된다.
        for row in session.run(_CROSS_LAW_REFERENCES, limit=limit * 3).data():
            source = law_node_id_by_law_id.get(row["src_law"])
            target = law_node_id_by_law_id.get(row["dst_law"])
            if not source or not target:
                continue
            links.append(
                {
                    "source": source,
                    "target": target,
                    "type": "CITES",
                    "weight": row["weight"],
                }
            )

    _apply_citation_heat(nodes, links)

    return {
        "nodes": list(nodes.values()),
        "links": links,
        "truncated": truncated,
        "palette": palette.palette_payload(),
    }


def _apply_citation_heat(nodes: dict[str, dict[str, Any]],
                         links: list[dict[str, Any]]) -> None:
    """법령이 인용망에 얼마나 얽혀 있는지를 0..1 로 눌러 노드에 심는다.

    받은 인용만 세면(= 권위) 1500개 중 1027개가 0 이라 램프의 맨 아래 칸에 몰려
    화면이 다시 단색이 된다. 주고받은 것을 함께 세면 그 수가 395개로 줄고 중간층이
    드러난다. 여기서 색이 답하는 질문은 "누가 권위인가"가 아니라 "어디가 인용망의
    중심인가"다.

    정규화는 로그로 한다. 선형으로 하면 1163회짜리 하나가 램프를 독점한다.
    """
    degree: dict[str, int] = {}
    for link in links:
        if link["type"] != "CITES":
            continue
        weight = link.get("weight") or 1
        degree[link["source"]] = degree.get(link["source"], 0) + weight
        degree[link["target"]] = degree.get(link["target"], 0) + weight

    if not degree:
        return
    ceiling = math.log1p(max(degree.values()))
    if ceiling <= 0:
        return

    for node_id, node in nodes.items():
        weight = degree.get(node_id)
        # 0회와 '집계 없음'은 다르다. 아무와도 얽히지 않은 법령도 램프의 맨 아래
        # 칸을 받아야 화면에서 사라지지 않는다.
        node["heat"] = math.log1p(weight) / ceiling if weight else 0.0


_LABEL_OF = "MATCH (n) WHERE elementId(n) = $node_id RETURN labels(n) AS labels"

_LAW_ARTICLES = """
MATCH (l:Law)-[:HAS_ARTICLE]->(a:Article)
WHERE elementId(l) = $node_id
RETURN elementId(a)   AS id,
       a.article_no     AS article_no,
       a.article_no_sub AS article_no_sub,
       a.article_label  AS article_label,
       a.title          AS title,
       a.law_id         AS law_id,
       a.law_name       AS law_name
ORDER BY a.article_no, a.article_no_sub
LIMIT $limit
"""

_ARTICLE_REFERENCES = """
MATCH (a:Article)-[:REFERENCES]->(t:Article)
WHERE elementId(a) IN $ids
RETURN elementId(a)   AS src,
       elementId(t)   AS dst,
       t.article_no     AS article_no,
       t.article_no_sub AS article_no_sub,
       t.article_label  AS article_label,
       t.title          AS title,
       t.law_id         AS law_id,
       t.law_name       AS law_name
LIMIT $limit
"""

# 조문 하나를 클릭했을 때는 들어오는 인용도 붙인다. "이 조문을 누가 인용하는가"가
# 감사·검토에서 가장 자주 필요한 질문이다. 법령 단위 펼치기에서는 쓰지 않는다 —
# 조문 수백 개의 역인용을 한꺼번에 끌어오면 화면이 감당하지 못한다.
_ARTICLE_INCOMING = """
MATCH (s:Article)-[:REFERENCES]->(a:Article)
WHERE elementId(a) IN $ids
RETURN elementId(s)   AS src,
       elementId(a)   AS dst,
       s.article_no     AS article_no,
       s.article_no_sub AS article_no_sub,
       s.article_label  AS article_label,
       s.title          AS title,
       s.law_id         AS law_id,
       s.law_name       AS law_name
LIMIT $limit
"""

_ARTICLE_LAW = """
MATCH (l:Law)-[:HAS_ARTICLE]->(a:Article)
WHERE elementId(a) = $node_id
RETURN elementId(l) AS id,
       l.law_id     AS law_id,
       l.law_name   AS law_name,
       count { (l)-[:HAS_ARTICLE]->(:Article) } AS article_count
"""

_ARTICLE_ENTITIES = """
MATCH (a:Article)-[r]->(e:Entity)
WHERE elementId(a) IN $ids
RETURN elementId(a) AS src,
       elementId(e) AS dst,
       type(r)      AS rel,
       e.name       AS name,
       e.type       AS etype
LIMIT $limit
"""

_ENTITY_ARTICLES = """
MATCH (a:Article)-[r]->(e:Entity)
WHERE elementId(e) = $node_id
RETURN elementId(a)   AS src,
       type(r)          AS rel,
       a.article_no     AS article_no,
       a.article_no_sub AS article_no_sub,
       a.article_label  AS article_label,
       a.title          AS title,
       a.law_id         AS law_id,
       a.law_name       AS law_name
LIMIT $limit
"""

_DOMAIN_LAWS = """
MATCH (d:Domain)-[:CONTAINS]->(l:Law)
WHERE elementId(d) = $node_id
RETURN elementId(l) AS id,
       l.law_id     AS law_id,
       l.law_name   AS law_name,
       count { (l)-[:HAS_ARTICLE]->(:Article) } AS article_count
LIMIT $limit
"""


def expand(node_id: str, limit: int | None = None) -> dict[str, Any]:
    """노드 하나의 이웃을 돌려준다. 프론트가 기존 그래프에 병합한다."""
    limit = limit or 300
    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    with get_neo4j_driver().session() as session:
        record = session.run(_LABEL_OF, node_id=node_id).single()
        if record is None:
            return {"nodes": [], "links": [], "expanded": None}
        labels = set(record["labels"])

        if "Domain" in labels:
            for row in session.run(_DOMAIN_LAWS, node_id=node_id, limit=limit).data():
                count = row["article_count"] or 0
                nodes[row["id"]] = _node(
                    row["id"], "Law", row["law_name"] or row["law_id"],
                    sub=f"조문 {count}개",
                    val=1.0 + min(count, 500) ** 0.5,
                    meta={"law_id": row["law_id"], "article_count": count},
                )
                links.append({"source": node_id, "target": row["id"], "type": "CONTAINS"})
            return _result(nodes, links, "Domain")

        if "Law" in labels:
            article_rows = session.run(_LAW_ARTICLES, node_id=node_id, limit=limit).data()
            for row in article_rows:
                nodes[row["id"]] = _node(
                    row["id"], "Article", _article_name(row),
                    sub=row.get("title"),
                    val=1.5,
                    meta={"law_id": row["law_id"], "law_name": row["law_name"],
                          "title": row.get("title")},
                    law_name=row["law_name"],
                )
                links.append({"source": node_id, "target": row["id"], "type": "HAS_ARTICLE"})

            article_ids = [row["id"] for row in article_rows]
            if article_ids:
                _attach_article_edges(session, article_ids, nodes, links, limit)
            return _result(nodes, links, "Law")

        if "Entity" in labels:
            for row in session.run(_ENTITY_ARTICLES, node_id=node_id, limit=limit).data():
                nodes[row["src"]] = _node(
                    row["src"], "Article", _article_name(row),
                    sub=row.get("law_name") or row.get("title"),
                    val=1.5,
                    meta={"law_id": row["law_id"], "law_name": row["law_name"]},
                    law_name=row["law_name"],
                )
                links.append({"source": row["src"], "target": node_id, "type": row["rel"]})
            return _result(nodes, links, "Entity")

        if "Article" in labels:
            # 소속 법령을 같이 얹는다. 조문만 덩그러니 떠 있으면 어느 법인지 알 수 없다.
            law = session.run(_ARTICLE_LAW, node_id=node_id).single()
            if law:
                count = law["article_count"] or 0
                nodes[law["id"]] = _node(
                    law["id"], "Law", law["law_name"] or law["law_id"],
                    sub=f"조문 {count}개",
                    val=1.0 + min(count, 500) ** 0.5,
                    meta={"law_id": law["law_id"], "article_count": count},
                )
                links.append({"source": law["id"], "target": node_id, "type": "HAS_ARTICLE"})

            _attach_article_edges(session, [node_id], nodes, links, limit, incoming=True)
            return _result(nodes, links, "Article")

    return {"nodes": [], "links": [], "expanded": None}


def _attach_article_edges(session, article_ids: list[str], nodes: dict[str, dict[str, Any]],
                          links: list[dict[str, Any]], limit: int,
                          incoming: bool = False) -> None:
    """조문 집합에서 뻗어 나가는 REFERENCES 와 Entity 관계를 붙인다."""
    edge_limit = limit * 3

    def article_node(row: dict[str, Any], node_id: str) -> None:
        nodes.setdefault(
            node_id,
            _node(
                node_id, "Article", _article_name(row),
                sub=row.get("law_name") or row.get("title"),
                val=1.5,
                meta={"law_id": row["law_id"], "law_name": row["law_name"],
                      "title": row.get("title")},
                law_name=row["law_name"],
            ),
        )

    for row in session.run(_ARTICLE_REFERENCES, ids=article_ids, limit=edge_limit).data():
        article_node(row, row["dst"])
        links.append({"source": row["src"], "target": row["dst"], "type": "REFERENCES"})

    if incoming:
        for row in session.run(_ARTICLE_INCOMING, ids=article_ids, limit=edge_limit).data():
            article_node(row, row["src"])
            links.append({"source": row["src"], "target": row["dst"], "type": "REFERENCES"})

    for row in session.run(_ARTICLE_ENTITIES, ids=article_ids, limit=edge_limit).data():
        nodes.setdefault(
            row["dst"],
            _node(
                row["dst"], "Entity", row["name"],
                sub=row["etype"],
                val=2.0,
                meta={"type": row["etype"]},
            ),
        )
        links.append({"source": row["src"], "target": row["dst"], "type": row["rel"]})


def _result(nodes, links, expanded) -> dict[str, Any]:
    # 펼치기로 들어온 노드는 인용도를 매기지 않는다. 조각 안에서 센 인용 횟수는
    # 전체 순위와 뜻이 달라서, 같은 램프에 얹으면 색이 거짓말을 한다.
    return {"nodes": list(nodes.values()), "links": links, "expanded": expanded}


_SEARCH = """
MATCH (l:Law)
WHERE toLower(l.law_name) CONTAINS toLower($q) OR l.law_id CONTAINS $q
RETURN elementId(l) AS id, l.law_id AS law_id, l.law_name AS law_name,
       count { (l)-[:HAS_ARTICLE]->(:Article) } AS article_count
LIMIT 30
"""


def search_laws(query: str) -> list[dict[str, Any]]:
    """검색 결과마다 바로 심을 수 있는 노드를 함께 돌려준다.

    개요 상한에 걸려 화면에 없던 법령이 검색으로 걸릴 수 있다. 그때 프론트가 노드를
    직접 만들면 색 계산이 그쪽으로 새므로, 서버가 만든 것을 그대로 쓰게 한다.
    """
    if not query.strip():
        return []
    with get_neo4j_driver().session() as session:
        rows = session.run(_SEARCH, q=query.strip()).data()

    for row in rows:
        count = row["article_count"] or 0
        row["node"] = _node(
            row["id"], "Law", row["law_name"] or row["law_id"],
            sub=f"조문 {count}개",
            val=1.0 + min(count, 500) ** 0.5,
            meta={"law_id": row["law_id"], "article_count": count},
        )
    return rows
