"""조문 그래프 구축 CLI.

Postgres(laws/articles, app/ingest.py로 이미 채워짐)를 읽어 조문 간 참조 관계와 핵심 엔티티를
LLM으로 추출한 뒤 Neo4j에 적재한다. 기존 벡터 임베딩 파이프라인(app/ingest.py)과는 완전히 분리된
별도 파이프라인이므로, 여러 번 실행해도 기존 pgvector 데이터에는 영향을 주지 않는다.

사용법:
    python -m app.graph_ingest
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

from app.db import SessionLocal
from app.graph_db import graph_session
from app.graph_extract import ENTITY_RELATIONS, ArticleGraphData, build_graph_chain, extract_article_graph
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
            SET a.title = $title, a.article_label = $article_label
            MERGE (l)-[:HAS_ARTICLE]->(a)
            """,
            law_id=law.law_id,
            article_no=article.article_no,
            article_no_sub=article.article_no_sub,
            title=article.title,
            article_label=article.article_label,
        )


def _merge_graph_data(law: Law, article: Article, data: ArticleGraphData) -> None:
    with graph_session() as gs:
        for ref in data.references:
            if ref.article_no == article.article_no and ref.article_no_sub == article.article_no_sub:
                continue  # 자기 참조는 만들지 않는다
            gs.run(
                """
                MATCH (a:Article {law_id: $law_id, article_no: $article_no, article_no_sub: $article_no_sub})
                MERGE (t:Article {law_id: $law_id, article_no: $ref_no, article_no_sub: $ref_sub})
                MERGE (a)-[:REFERENCES]->(t)
                """,
                law_id=law.law_id,
                article_no=article.article_no,
                article_no_sub=article.article_no_sub,
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


def _process_article(chain, law: Law, article: Article) -> tuple[Law, Article, ArticleGraphData]:
    data = extract_article_graph(chain, law.law_name, article.article_label, article.full_text)
    return law, article, data


def graph_ingest_all(session: Session) -> None:
    laws = session.scalars(select(Law)).all()
    if not laws:
        print(
            "[graph_ingest][경고] laws 테이블이 비어 있습니다. 먼저 python -m app.ingest 를 실행하세요.",
            file=sys.stderr,
        )
        return

    chain = build_graph_chain()

    for law in laws:
        articles = list(law.articles)
        print(f"[graph_ingest] {law.law_name}: 노드 생성 중 ({len(articles)}개 조문)")
        for article in articles:
            _merge_article_node(law, article)

        print(f"[graph_ingest] {law.law_name}: 엔티티/관계 추출 중")
        # Gemini 무료 티어 쿼터(분당 15회)에 맞춰 동시성을 낮게 유지한다.
        # 높은 동시성은 429 재시도만 늘려 오히려 처리량이 떨어진다.
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [executor.submit(_process_article, chain, law, article) for article in articles]
            for future in tqdm(
                as_completed(futures), total=len(futures), desc=f"{law.law_name} 추출중", unit="조문"
            ):
                law_, article_, data = future.result()
                _merge_graph_data(law_, article_, data)

        print(f"[graph_ingest] 완료: {law.law_name}")


def main() -> None:
    session = SessionLocal()
    try:
        graph_ingest_all(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
