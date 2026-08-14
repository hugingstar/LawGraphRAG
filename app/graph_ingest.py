"""조문 그래프 구축 CLI.

Postgres(laws/articles, app/ingest.py로 이미 채워짐)를 읽어 조문 간 참조 관계와 핵심 엔티티를
LLM으로 추출한 뒤 Neo4j에 적재한다. 기존 벡터 임베딩 파이프라인과는 분리된 별도 명령이므로,
여러 번 실행해도 pgvector 데이터에는 영향을 주지 않는다.

**이어달리기**: 조문 하나당 Gemini 1회를 호출하는데 무료 티어는 분당 15회·일일 500회다.
그래서 처리한 조문에 `graph_synced_at`을 찍어두고 다음 실행에서 건너뛴다. 쿼터가 떨어져
중간에 멈춰도 다시 실행하면 남은 조문부터 이어서 진행한다.

사용법:
    python -m app.graph_ingest              # 남은 조문 전체
    python -m app.graph_ingest --limit 400  # 오늘 쿼터만큼만
"""

import argparse
import datetime
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tqdm import tqdm

from app.db import SessionLocal
from app.graph_db import ensure_constraints, graph_session
from app.graph_extract import (
    ENTITY_RELATIONS,
    ArticleGraphData,
    build_graph_chain,
    extract_article_graph,
)
from app.models import Article, Law


def _merge_article_node(law: Law, article: Article) -> None:
    with graph_session() as gs:
        gs.run(
            "MERGE (l:Law {law_id: $law_id}) SET l.law_name = $law_name",
            law_id=law.law_id, law_name=law.law_name,
        )
        gs.run(
            """
            MATCH (l:Law {law_id: $law_id})
            MERGE (a:Article {law_id: $law_id, article_no: $article_no, article_no_sub: $article_no_sub})
            SET a.title = $title, a.article_label = $article_label, a.law_name = $law_name
            MERGE (l)-[:HAS_ARTICLE]->(a)
            """,
            law_id=law.law_id,
            law_name=law.law_name,
            article_no=article.article_no,
            article_no_sub=article.article_no_sub,
            title=article.title,
            article_label=article.article_label,
        )


def _merge_graph_data(
    law: Law, article: Article, data: ArticleGraphData, law_id_by_name: dict[str, str]
) -> None:
    with graph_session() as gs:
        for ref in data.references:
            # 인용에 법령명이 붙어 있으면(예: "형법 제30조") 그 법령의 조문으로 연결한다.
            # 예전에는 무조건 자기 law_id로 연결해서, 타 법령 인용이 자기 조문으로 잘못
            # 이어지고 존재하지도 않는 조문 노드를 만들어 그래프를 오염시켰다.
            target_law_id = law.law_id
            if ref.law_name:
                resolved = law_id_by_name.get(ref.law_name.strip())
                if resolved is None:
                    continue  # 아직 수집하지 않은 법령이면 유령 노드를 만들지 않고 건너뛴다
                target_law_id = resolved

            if (
                target_law_id == law.law_id
                and ref.article_no == article.article_no
                and ref.article_no_sub == article.article_no_sub
            ):
                continue  # 자기 참조는 만들지 않는다

            gs.run(
                """
                MATCH (a:Article {law_id: $law_id, article_no: $article_no, article_no_sub: $article_no_sub})
                MERGE (t:Article {law_id: $target_law_id, article_no: $ref_no, article_no_sub: $ref_sub})
                MERGE (a)-[:REFERENCES]->(t)
                """,
                law_id=law.law_id,
                article_no=article.article_no,
                article_no_sub=article.article_no_sub,
                target_law_id=target_law_id,
                ref_no=ref.article_no,
                ref_sub=ref.article_no_sub,
            )

        for ent in data.entities:
            if ent.relation not in ENTITY_RELATIONS:
                continue
            # 관계 타입은 Cypher에서 파라미터 바인딩이 불가능하므로 화이트리스트(ENTITY_RELATIONS)
            # 검증을 통과한 값만 f-string으로 삽입한다.
            gs.run(
                f"""
                MATCH (a:Article {{law_id: $law_id, article_no: $article_no, article_no_sub: $article_no_sub}})
                MERGE (e:Entity {{name: $name, type: $etype}})
                MERGE (a)-[:{ent.relation}]->(e)
                """,
                law_id=law.law_id,
                article_no=article.article_no,
                article_no_sub=article.article_no_sub,
                name=ent.entity_name,
                etype=ent.entity_type,
            )


def graph_ingest_all(session: Session, limit: int | None = None) -> None:
    laws = session.scalars(select(Law)).all()
    if not laws:
        print(
            "[graph_ingest][경고] laws 테이블이 비어 있습니다. 먼저 python -m app.ingest 를 실행하세요.",
            file=sys.stderr,
        )
        return

    ensure_constraints()

    # 법령명 -> law_id. 조문이 타 법령을 인용할 때 올바른 노드로 잇기 위해 쓴다.
    law_id_by_name = {law.law_name: law.law_id for law in laws}

    pending = session.scalars(
        select(Article).where(Article.graph_synced_at.is_(None)).order_by(Article.id)
    ).all()
    if limit:
        pending = pending[:limit]

    if not pending:
        print("[graph_ingest] 처리할 조문이 없습니다. 모두 동기화되어 있습니다.")
        return

    total_articles = session.scalar(select(func.count()).select_from(Article)) or 0
    print(f"[graph_ingest] 미처리 조문 {len(pending)}개 처리 시작 (전체 {total_articles}개)")

    chain = build_graph_chain()
    law_by_id = {law.id: law for law in laws}
    failures = 0

    # 무료 티어 쿼터(분당 15회)에 맞춰 직렬로 처리한다. 동시 요청을 늘리면 429 재시도만
    # 늘어 오히려 처리량이 떨어진다.
    for article in tqdm(pending, desc="그래프 추출", unit="조문"):
        law = law_by_id.get(article.law_id)
        if law is None:
            continue
        try:
            _merge_article_node(law, article)
            data = extract_article_graph(chain, law.law_name, article.article_label, article.full_text)
            _merge_graph_data(law, article, data, law_id_by_name)
        except Exception as exc:  # noqa: BLE001 - 조문 하나의 실패로 배치를 끝내지 않는다
            failures += 1
            print(f"\n[graph_ingest][오류] {law.law_name} {article.article_label}: {exc}", file=sys.stderr)
            # 쿼터 소진이면 계속 시도해봐야 전부 실패하므로 여기서 멈춘다.
            # 이미 처리한 조문은 표시가 남아 있어 다음 실행이 그 뒤부터 이어간다.
            if "429" in str(exc) or "quota" in str(exc).lower():
                print("[graph_ingest] API 쿼터 소진으로 중단합니다. 나중에 다시 실행하면 이어서 진행됩니다.", file=sys.stderr)
                break
            continue

        article.graph_synced_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()  # 조문 하나가 끝날 때마다 표시를 확정해 중단에 대비한다

    done = session.scalar(
        select(func.count()).select_from(Article).where(Article.graph_synced_at.isnot(None))
    ) or 0
    print(f"\n[graph_ingest] 동기화 완료 {done}/{total_articles}개 조문 (이번 실행 실패 {failures}건)")


def main() -> None:
    parser = argparse.ArgumentParser(description="조문 그래프 구축")
    parser.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 최대 조문 수")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        graph_ingest_all(session, limit=args.limit)
    finally:
        session.close()


if __name__ == "__main__":
    main()
