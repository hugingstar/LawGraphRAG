"""검색: pgvector 코사인 유사도(HNSW) → cross-encoder 재순위 → Neo4j 그래프 확장.

벡터 검색으로 후보를 넓게 뽑고(조문 단위로 추린 뒤 상위 RERANK_POOL개), cross-encoder가
질의와 조문을 같이 읽어 다시 줄 세운 다음, 상위 조문을 시드로 그래프 확장(graph_expand)을
수행해 명시적으로 인용되거나 같은 개념(의무주체/처벌 등)을 공유하는 조문을 보강한다.
그래프 확장은 항상 벡터·재순위 결과보다 낮은 우선순위로 덧붙여지며, Neo4j가 죽어 있거나
재순위 모델을 못 써도 벡터 결과는 그대로 나간다.

키워드(pg_trgm) 검색은 뺐다: `ORDER BY similarity(...) LIMIT n`은 GIN 인덱스로 풀 수 없어
활성화된 법의 청크를 매번 풀스캔했고(229만 청크 기준 청크당 2.5~6.3초), 법령을 수천 건 켜면
이 한 갈래가 분석 시간을 통째로 지배했다. 같은 조건에서 벡터 단독은 0.5초 수준이다.
"""

from dataclasses import dataclass

from sqlalchemy import select, text as sqltext
from sqlalchemy.orm import Session

from app.embeddings import embed_queries
from app.graph_retrieval import graph_expand
from app.law_catalog import selected_law_ids
from app.rerank import RERANK_POOL, rerank_order
from app.models import Article, ArticleChunk, Law, UserLawSelection

# 그래프 확장은 이제 벡터와 함께 두 갈래 중 하나이므로 시드와 확장 폭을 조금 넓게 잡는다.
GRAPH_SEED_COUNT = 5
GRAPH_EXTRA_LIMIT = 8

# 벡터가 물어오는 후보 청크 수. 조문 하나가 여러 청크로 쪼개져 있어서 후보 30개는 서로 다른
# 조문 12개 남짓밖에 안 된다 — 법령이 수천 건 켜져 있으면 정작 맞는 조문이 그 안에 못 들어와
# "적용되는 조문 없음"이 되어버린다. 개수를 늘려도 지연이 거의 그대로라(측정: 30개·150개·300개
# 모두 0.53~0.64초) 넉넉히 잡는다. 150개 = 서로 다른 조문 56개.
DEFAULT_CANDIDATE_POOL = 150


@dataclass
class RetrievedArticle:
    article: Article
    law: Law
    chunk_text: str
    score: float


def _vector_candidates(
    session: Session, query_vector: list[float], limit: int, user_id: int
) -> list[int]:
    """이 사용자가 설정 > 법 활성화에서 켜 둔 법의 청크 중 질의와 가장 가까운 것들.

    분야(LawCategory) 필터는 걸지 않는다. 분야 조인을 붙이면 플래너가 HNSW 인덱스를
    포기하고 해당 분야 청크를 전부 훑어(측정: 178,636행 정렬 900ms) 인덱스 스캔(41ms)보다
    22배 느려지는 데다, 규칙 분류가 'etc'로 남긴 법령 3,387건(전체의 60%)이 후보에서
    통째로 빠진다. 결과가 0건일 때만 걸리는 기존 폴백은 '결과가 나쁜' 경우를 못 살렸다."""
    # HNSW 스캔은 ef_search개(기본 40)를 훑고 끝내므로, 활성화 법 필터에 걸러지고 나면
    # LIMIT을 못 채운다 — 실측으로 limit=150을 걸어도 10행만 돌아왔다. iterative_scan을
    # 켜면 LIMIT이 찰 때까지 인덱스를 이어서 훑는다(pgvector 0.8+). relaxed_order는
    # 순서가 아주 조금 흐트러질 수 있지만, 어차피 조문 단위로 다시 추리고 LLM이 판단한다.
    session.execute(sqltext("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    session.execute(sqltext("SET LOCAL hnsw.ef_search = 100"))
    stmt = (
        select(ArticleChunk.id)
        .join(Article, ArticleChunk.article_id == Article.id)
        .join(Law, Article.law_id == Law.id)
        .join(UserLawSelection, UserLawSelection.law_id == Law.id)
        .where(UserLawSelection.user_id == user_id)
        .order_by(ArticleChunk.embedding.cosine_distance(query_vector))
        .limit(limit)
    )
    return [r[0] for r in session.execute(stmt).all()]


def hybrid_search(
    session: Session,
    query_text: str,
    user_id: int,
    top_k: int = 8,
    candidate_pool: int = DEFAULT_CANDIDATE_POOL,
) -> list[RetrievedArticle]:
    query_vector = embed_queries([query_text])[0]

    vector_ids = _vector_candidates(session, query_vector, candidate_pool, user_id)

    # 순위가 앞설수록 높은 점수. 그래프 확장 결과가 이 점수 아래에 붙는다.
    scores = {chunk_id: 1.0 / (rank + 1) for rank, chunk_id in enumerate(vector_ids)}

    # 켜 둔 법이 하나도 없으면 여기서 빈손이 되는 게 맞다 — 사용자가 아직 검색 대상 법을
    # 고르지 않았다는 뜻이다.
    if not scores:
        return []

    chunks = session.execute(
        select(ArticleChunk).where(ArticleChunk.id.in_(list(scores.keys())))
    ).scalars().all()

    # 조문(article) 단위로 최고 점수 청크만 남긴다.
    best_by_article: dict[int, tuple[ArticleChunk, float]] = {}
    for chunk in chunks:
        score = scores[chunk.id]
        current = best_by_article.get(chunk.article_id)
        if current is None or score > current[1]:
            best_by_article[chunk.article_id] = (chunk, score)

    # 벡터 순서로 넉넉히 남긴 뒤 cross-encoder가 다시 줄 세운다(실패하면 벡터 순서 유지).
    ranked = sorted(best_by_article.values(), key=lambda pair: pair[1], reverse=True)[:RERANK_POOL]
    order = rerank_order(query_text, [chunk.chunk_text for chunk, _ in ranked])
    if order is not None:
        ranked = [ranked[i] for i in order]
    ranked = ranked[:top_k]

    results = []
    # 재순위 뒤에는 벡터 점수가 순서와 안 맞으므로, 최종 순위를 그대로 점수로 쓴다
    # (_graph_expanded_results가 이 점수 아래에 그래프 결과를 붙인다).
    for rank, (chunk, _) in enumerate(ranked):
        article = chunk.article
        results.append(
            RetrievedArticle(
                article=article,
                law=article.law,
                chunk_text=chunk.chunk_text,
                score=1.0 / (rank + 1),
            )
        )

    results.extend(_graph_expanded_results(session, results, user_id))
    return results


def _graph_expanded_results(
    session: Session, seed_results: list[RetrievedArticle], user_id: int
) -> list[RetrievedArticle]:
    """벡터 결과보다 항상 낮은 점수로, 그래프상 관련 조문을 덧붙인다.

    그래프 확장은 Neo4j 전체를 대상으로 도니, 이 사용자가 설정 > 법 활성화에서 켜지 않은
    법의 조문까지 딸려 올 수 있다. 그런 조문은 애초에 검색 대상이 아니어야 하므로 여기서
    한 번 더 걸러낸다."""
    if not seed_results:
        return []

    seed_articles = [r.article for r in seed_results[:GRAPH_SEED_COUNT]]
    related_articles = graph_expand(session, seed_articles, limit=GRAPH_EXTRA_LIMIT)
    enabled_law_ids = selected_law_ids(session, user_id)

    seen_article_ids = {r.article.id for r in seed_results}
    lowest_score = min(r.score for r in seed_results)

    extra = []
    for article in related_articles:
        if article.id in seen_article_ids or article.law_id not in enabled_law_ids:
            continue
        seen_article_ids.add(article.id)
        chunk_text = article.chunks[0].chunk_text if article.chunks else article.full_text[:500]
        extra.append(
            RetrievedArticle(
                article=article,
                law=article.law,
                chunk_text=chunk_text,
                score=lowest_score / 2,
            )
        )
    return extra


from typing import Any
from pydantic import ConfigDict, Field
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document

class HybridLawOwlyRetriever(BaseRetriever):
    """LangChain BaseRetriever 구현체로, 내부적으로 기존 hybrid_search를 호출합니다."""
    session: Any = Field(exclude=True)
    user_id: int
    top_k: int = 8
    candidate_pool: int = DEFAULT_CANDIDATE_POOL

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        results = hybrid_search(
            self.session, query, self.user_id, self.top_k, self.candidate_pool
        )
        docs = []
        for r in results:
            doc = Document(
                page_content=r.chunk_text,
                metadata={
                    "law_name": r.law.law_name,
                    "article_no": r.article.article_no,
                    "article_no_sub": r.article.article_no_sub,
                    "title": r.article.title,
                    "score": r.score,
                    "article_label": r.article.article_label,
                    # 쟁점 단위가 아닌 청크 단위 인용에 분야 배지를 붙이는 근거
                    # (app.annotate._citations_from_result). 검색을 좁히는 데는 쓰지 않는다.
                    "category_code": r.law.category.code if r.law.category else None,
                }
            )
            docs.append(doc)
        return docs

