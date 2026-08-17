"""조문 인용(REFERENCES) 관계를 LLM 없이 정규식으로 추출해 Neo4j에 적재한다.

`app.graph_ingest`는 조문 하나당 Gemini를 1회 부르기 때문에 무료 티어로는 20만 조문을
다 처리할 수 없다(실측 적재율 16/204,862). 그런데 조문 간 인용은 "제38조", "「산업안전보건법」
제5조"처럼 표기가 정형화되어 있어 규칙만으로 뽑을 수 있다. 엔티티 관계(DEFINES/APPLIES_TO 등)는
문장의 의미를 읽어야 해서 여전히 LLM이 필요하므로, 이 모듈은 REFERENCES만 채운다.

`graph_synced_at`은 건드리지 않는다 — 그 표시는 "LLM으로 엔티티까지 뽑았다"는 뜻이라,
여기서 찍으면 나중에 graph_ingest가 그 조문을 건너뛰어 엔티티가 영영 안 생긴다. 이 적재는
MERGE라서 여러 번 돌려도 안전하다.

사용법:
    python -m app.graph_references                    # 전체 법령
    python -m app.graph_references --law "민법"        # 이 법령의 조문만
"""

import argparse
import re

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

from app.db import SessionLocal
from app.graph_db import ensure_constraints, graph_session
from app.models import Article, Law

# 「법령명」 제12조의3 — 다른 법령을 가리키는 인용
_CROSS_LAW_RE = re.compile(r"「([^」\n]{2,60})」\s*제(\d+)조(?:의(\d+))?")
# 제12조의3 — 법령명이 안 붙어 있으면 같은 법령 안의 조문으로 본다
_SAME_LAW_RE = re.compile(r"제(\d+)조(?:의(\d+))?")

BATCH_SIZE = 2000


def extract_references(text: str) -> set[tuple[str | None, int, int]]:
    """조문 원문에서 (법령명 또는 None, 조번호, 가지번호) 집합을 뽑는다.

    법령명이 붙은 인용을 먼저 잡고 그 구간을 제외한 뒤 나머지 "제N조"를 같은 법령 인용으로
    본다 — 그렇게 하지 않으면 「산업안전보건법」 제5조가 자기 법의 제5조로도 잡힌다."""
    refs: set[tuple[str | None, int, int]] = set()
    consumed: list[tuple[int, int]] = []

    for m in _CROSS_LAW_RE.finditer(text):
        refs.add((m.group(1).strip(), int(m.group(2)), int(m.group(3) or 0)))
        consumed.append(m.span())

    for m in _SAME_LAW_RE.finditer(text):
        if any(start <= m.start() < end for start, end in consumed):
            continue
        refs.add((None, int(m.group(1)), int(m.group(2) or 0)))

    return refs


def _write_edges(edges: list[dict]) -> None:
    """(source, target) 쌍을 한 트랜잭션에서 MERGE한다.

    노드는 Postgres에 실제로 있는 조문만 넘어오므로(ingest_references에서 걸러진다)
    유령 노드가 생기지 않는다."""
    if not edges:
        return
    with graph_session() as gs:
        gs.run(
            """
            UNWIND $edges AS e
            MERGE (l:Law {law_id: e.law_id}) SET l.law_name = e.law_name
            MERGE (a:Article {law_id: e.law_id, article_no: e.article_no,
                              article_no_sub: e.article_no_sub})
              SET a.law_name = e.law_name, a.article_label = e.article_label, a.title = e.title
            MERGE (l)-[:HAS_ARTICLE]->(a)
            MERGE (tl:Law {law_id: e.t_law_id}) SET tl.law_name = e.t_law_name
            MERGE (t:Article {law_id: e.t_law_id, article_no: e.t_article_no,
                              article_no_sub: e.t_article_no_sub})
              SET t.law_name = e.t_law_name, t.article_label = e.t_article_label, t.title = e.t_title
            MERGE (tl)-[:HAS_ARTICLE]->(t)
            MERGE (a)-[:REFERENCES]->(t)
            """,
            edges=edges,
        )


def ingest_references(session: Session, law_names: list[str] | None = None) -> None:
    laws = session.scalars(select(Law)).all()
    if not laws:
        print("[graph_references] laws 테이블이 비어 있습니다. 먼저 python -m app.ingest 를 실행하세요.")
        return

    ensure_constraints()

    law_by_pk = {law.id: law for law in laws}
    law_pk_by_name = {law.law_name: law.id for law in laws}

    stmt = select(Article)
    if law_names:
        target_pks = [law_pk_by_name[n] for n in law_names if n in law_pk_by_name]
        unknown = set(law_names) - set(law_pk_by_name)
        if unknown:
            print(f"[graph_references][경고] 찾지 못한 법령: {', '.join(sorted(unknown))}")
        if not target_pks:
            return
        stmt = stmt.where(Article.law_id.in_(target_pks))

    articles = session.scalars(stmt.order_by(Article.id)).all()
    if not articles:
        print("[graph_references] 대상 조문이 없습니다.")
        return

    # 인용 대상이 실제로 존재하는 조문일 때만 관계를 만든다(없는 조를 가리키는 오탈자·
    # 개정 전 표기가 유령 노드로 남지 않게 한다).
    index: dict[tuple[int, int, int], Article] = {
        (a.law_id, a.article_no, a.article_no_sub): a for a in articles
    }
    if law_names:  # 대상 법령만 읽었어도 인용 대상은 다른 법령일 수 있다
        for a in session.scalars(select(Article)).all():
            index.setdefault((a.law_id, a.article_no, a.article_no_sub), a)

    edges: list[dict] = []
    written = 0
    skipped_unknown = 0

    def node_fields(article: Article, prefix: str) -> dict:
        law = law_by_pk[article.law_id]
        return {
            f"{prefix}law_id": law.law_id,
            f"{prefix}law_name": law.law_name,
            f"{prefix}article_no": article.article_no,
            f"{prefix}article_no_sub": article.article_no_sub,
            f"{prefix}article_label": article.article_label,
            f"{prefix}title": article.title,
        }

    for article in tqdm(articles, desc="인용 추출", unit="조문"):
        for ref_law_name, ref_no, ref_sub in extract_references(article.full_text or ""):
            target_law_pk = article.law_id
            if ref_law_name is not None:
                target_law_pk = law_pk_by_name.get(ref_law_name)
                if target_law_pk is None:
                    skipped_unknown += 1  # 아직 수집하지 않은 법령
                    continue

            target = index.get((target_law_pk, ref_no, ref_sub))
            if target is None or target.id == article.id:
                if target is None:
                    skipped_unknown += 1
                continue

            edges.append({**node_fields(article, ""), **node_fields(target, "t_")})
            if len(edges) >= BATCH_SIZE:
                _write_edges(edges)
                written += len(edges)
                edges = []

    _write_edges(edges)
    written += len(edges)
    print(f"[graph_references] REFERENCES {written}건 적재 (대상 없어 건너뜀 {skipped_unknown}건)")


def main() -> None:
    parser = argparse.ArgumentParser(description="조문 인용 관계만 정규식으로 그래프 적재(LLM 미사용)")
    parser.add_argument(
        "--law",
        action="append",
        dest="law_names",
        metavar="법령명",
        help="이 법령의 조문만 처리한다(여러 번 지정 가능). 생략하면 전체 법령이 대상이다.",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        ingest_references(session, law_names=args.law_names)
    finally:
        session.close()


if __name__ == "__main__":
    main()
