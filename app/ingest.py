"""법령 수집 CLI.

사용법 (OC 키 발급 및 .env 설정 후):
    python -m app.ingest

Data/DATA_SOURCE_URL.md 에 정의된 법령명들을 법제처 Open API로 검색 -> 상세 조회 ->
DB upsert -> 조문 청킹 -> 임베딩 저장까지 수행한다.
"""

import datetime
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chunking import chunk_text
from app.db import SessionLocal
from app.embeddings import embed_passages
from app.law_api_client import LawApiError, get_law_detail_xml, search_law
from app.models import Article, ArticleChunk, Law
from app.parser import parse_law_detail

TARGET_LAWS = [
    "산업안전보건기준에 관한 규칙",
    "산업안전보건법",
    "산업안전보건법 시행령",
    "산업안전보건법 시행규칙",
]


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def upsert_law(session: Session, meta: dict) -> Law:
    law = session.scalar(select(Law).where(Law.law_id == meta["law_id"]))
    if law is None:
        law = Law(law_id=meta["law_id"])
        session.add(law)

    law.law_name = meta["law_name"]
    law.law_type = meta.get("law_type")
    law.promulgation_date = _parse_date(meta.get("promulgation_date"))
    law.effective_date = _parse_date(meta.get("effective_date"))
    law.last_synced_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return law


def upsert_article(session: Session, law: Law, article_data: dict) -> Article:
    article = session.scalar(
        select(Article).where(
            Article.law_id == law.id,
            Article.article_no == article_data["article_no"],
            Article.article_no_sub == article_data["article_no_sub"],
        )
    )
    if article is None:
        article = Article(
            law_id=law.id,
            article_no=article_data["article_no"],
            article_no_sub=article_data["article_no_sub"],
        )
        session.add(article)

    article.title = article_data.get("title")
    article.full_text = article_data["full_text"]
    article.effective_date = _parse_date(article_data.get("effective_date"))
    session.flush()

    # 조문이 갱신됐을 수 있으니 기존 청크는 지우고 다시 생성
    session.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).delete()
    return article


def embed_and_store_chunks(session: Session, article: Article) -> None:
    chunks = chunk_text(article.full_text, window_size=3, overlap=1)
    if not chunks:
        return
    vectors = embed_passages([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        session.add(
            ArticleChunk(
                article_id=article.id,
                chunk_text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
            )
        )


def ingest_law(session: Session, law_name: str) -> None:
    print(f"[ingest] 검색 중: {law_name}")
    candidates = search_law(law_name)
    if not candidates:
        print(f"[ingest][경고] 검색 결과 없음: {law_name}", file=sys.stderr)
        return

    exact = next((c for c in candidates if c["law_name"] == law_name), candidates[0])
    print(f"[ingest] 상세 조회: {exact['law_name']} (MST={exact['mst']})")
    xml_text = get_law_detail_xml(mst=exact["mst"])
    detail = parse_law_detail(xml_text)
    if not detail.get("law_id"):
        detail["law_id"] = exact["law_id"]
    if not detail.get("law_name"):
        detail["law_name"] = exact["law_name"]

    law = upsert_law(session, detail)

    articles = detail.get("articles", [])
    print(f"[ingest] 조문 {len(articles)}개 처리 중...")
    for article_data in articles:
        article = upsert_article(session, law, article_data)
        embed_and_store_chunks(session, article)

    session.commit()
    print(f"[ingest] 완료: {law_name} ({len(articles)}개 조문)")


def main() -> None:
    session = SessionLocal()
    try:
        for law_name in TARGET_LAWS:
            try:
                ingest_law(session, law_name)
            except LawApiError as e:
                print(f"[ingest][오류] {e}", file=sys.stderr)
                sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
