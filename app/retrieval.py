"""하이브리드 검색: pgvector 코사인 유사도 + pg_trgm 키워드 유사도 + Neo4j 그래프 확장.

벡터와 trigram 결과를 Reciprocal Rank Fusion(RRF)으로 합쳐 순위를 매긴 뒤(RRF는 점수 스케일이
다른 두 검색 방식을 정규화 없이 안정적으로 합칠 수 있어 하이브리드 검색에서 널리 쓰인다),
상위 조문을 시드로 그래프 확장(graph_expand)을 추가로 수행해 명시적으로 인용되거나 같은
개념(의무주체/처벌 등)을 공유하는 조문을 보강한다. 그래프 확장은 항상 벡터+trigram 결과보다
낮은 우선순위로 덧붙여지며, Neo4j가 죽어 있어도 기존 결과는 그대로 반환된다.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.embeddings import embed_queries
from app.graph_retrieval import graph_expand
from app.models import Article, ArticleChunk, Law

RRF_K = 60
GRAPH_SEED_COUNT = 3
GRAPH_EXTRA_LIMIT = 4


@dataclass
class RetrievedArticle:
    article: Article
    law: Law
    chunk_text: str
    score: float


def _vector_candidates(session: Session, query_vector: list[float], limit: int) -> list[int]:
    rows = session.execute(
        select(ArticleChunk.id)
        .order_by(ArticleChunk.embedding.cosine_distance(query_vector))
        .limit(limit)
    ).all()
    return [r[0] for r in rows]


def _trigram_candidates(session: Session, query_text: str, limit: int) -> list[int]:
    rows = session.execute(
        select(ArticleChunk.id)
        .order_by(func.similarity(ArticleChunk.chunk_text, query_text).desc())
        .limit(limit)
    ).all()
    return [r[0] for r in rows]


def hybrid_search(session: Session, query_text: str, top_k: int = 8, candidate_pool: int = 30) -> list[RetrievedArticle]:
    query_vector = embed_queries([query_text])[0]

    vector_ids = _vector_candidates(session, query_vector, candidate_pool)
    trigram_ids = _trigram_candidates(session, query_text, candidate_pool)

    rrf_scores: dict[int, float] = {}
    for rank, chunk_id in enumerate(vector_ids):
        rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, chunk_id in enumerate(trigram_ids):
        rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    if not rrf_scores:
        return []

    chunk_ids = list(rrf_scores.keys())
    chunks = session.execute(
        select(ArticleChunk).where(ArticleChunk.id.in_(chunk_ids))
    ).scalars().all()

    # 조문(article) 단위로 최고 점수 청크만 남긴다.
    best_by_article: dict[int, tuple[ArticleChunk, float]] = {}
    for chunk in chunks:
        score = rrf_scores[chunk.id]
        current = best_by_article.get(chunk.article_id)
        if current is None or score > current[1]:
            best_by_article[chunk.article_id] = (chunk, score)

    ranked = sorted(best_by_article.values(), key=lambda pair: pair[1], reverse=True)[:top_k]

    results = []
    for chunk, score in ranked:
        article = chunk.article
        results.append(
            RetrievedArticle(article=article, law=article.law, chunk_text=chunk.chunk_text, score=score)
        )

    results.extend(_graph_expanded_results(session, results))
    return results


def _graph_expanded_results(session: Session, seed_results: list[RetrievedArticle]) -> list[RetrievedArticle]:
    """기존 벡터+trigram 결과보다 항상 낮은 점수로, 그래프상 관련 조문을 덧붙인다."""
    if not seed_results:
        return []

    seed_articles = [r.article for r in seed_results[:GRAPH_SEED_COUNT]]
    related_articles = graph_expand(session, seed_articles, limit=GRAPH_EXTRA_LIMIT)

    seen_article_ids = {r.article.id for r in seed_results}
    lowest_score = min(r.score for r in seed_results)

    extra = []
    for article in related_articles:
        if article.id in seen_article_ids:
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
    top_k: int = 8
    candidate_pool: int = 30
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        results = hybrid_search(self.session, query, self.top_k, self.candidate_pool)
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
                }
            )
            docs.append(doc)
        return docs

