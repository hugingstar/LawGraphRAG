"""법령 수집 CLI.

사용법 (OC 키 발급 및 .env 설정 후):
    python -m app.ingest                 # TARGET_LAWS 전체
    python -m app.ingest 민법 형법        # 특정 법령만

법제처 Open API로 법령을 검색 -> 상세 조회 -> DB upsert -> 조문 청킹 -> 임베딩 저장한다.

재실행에 안전하다. 조문 원문의 해시를 저장해 두고 내용이 그대로면 청킹·임베딩을 건너뛰므로,
수십 개 법령을 반복 수집해도 바뀐 조문만 다시 계산한다.
"""

import datetime
import hashlib
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

from app.chunking import chunk_text
from app.db import SessionLocal
from app.embeddings import embed_passages
from app.law_api_client import LawApiError, get_law_detail_xml, search_law
from app.models import Article, ArticleChunk, Law
from app.parser import parse_law_detail

TARGET_LAWS = [
    # --- 산업안전 ---
    "산업안전보건기준에 관한 규칙",
    "산업안전보건법",
    "산업안전보건법 시행령",
    "산업안전보건법 시행규칙",
    "중대재해 처벌 등에 관한 법률",
    # --- 기본 6법 ---
    "대한민국헌법",
    "민법",
    "형법",
    "상법",
    "민사소송법",
    "형사소송법",
    # --- 노동 ---
    "근로기준법",
    "노동조합 및 노동관계조정법",
    # --- 교통 ---
    "도로교통법",
    "교통사고처리 특례법",
    "자동차손해배상 보장법",
    # --- 화재·건설·시설 ---
    "소방기본법",
    "건축법",
    "건설산업기본법",
    "시설물의 안전 및 유지관리에 관한 특별법",
    # --- 환경·재난 ---
    "환경정책기본법",
    "화학물질관리법",
    "재난 및 안전관리 기본법",
    # --- 배상·소비자 ---
    "국가배상법",
    "제조물 책임법",
]


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def upsert_article(session: Session, law: Law, article_data: dict) -> tuple[Article, bool]:
    """조문을 upsert하고 (조문, 재임베딩이 필요한지)를 돌려준다.

    내용이 바뀌지 않았다면 기존 청크를 그대로 두고 False를 반환한다 — 이 판단이 없으면
    법령 하나를 다시 수집할 때마다 수천 개 청크를 지우고 전량 재임베딩하게 된다.
    """
    article = session.scalar(
        select(Article).where(
            Article.law_id == law.id,
            Article.article_no == article_data["article_no"],
            Article.article_no_sub == article_data["article_no_sub"],
        )
    )
    new_hash = _hash_text(article_data["full_text"])

    if article is None:
        article = Article(
            law_id=law.id,
            article_no=article_data["article_no"],
            article_no_sub=article_data["article_no_sub"],
        )
        session.add(article)
    elif article.content_hash == new_hash:
        return article, False

    article.title = article_data.get("title")
    article.full_text = article_data["full_text"]
    article.effective_date = _parse_date(article_data.get("effective_date"))
    article.content_hash = new_hash
    # 내용이 바뀌었으니 그래프에서 뽑아둔 관계도 더 이상 유효하지 않다.
    article.graph_synced_at = None
    session.flush()

    session.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).delete()
    return article, True


def embed_and_store_chunks(session: Session, articles: list[Article]) -> int:
    """여러 조문의 청크를 한 번에 임베딩한다.

    조문마다 embed_passages()를 부르면 배치 크기 설정이 무의미해지고 GPU 왕복만 늘어난다.
    법령 단위로 모아 한 번에 넘겨야 배치가 실제로 채워진다.
    """
    pending: list[tuple[Article, object]] = []
    for article in articles:
        for chunk in chunk_text(article.full_text, window_size=3, overlap=1):
            pending.append((article, chunk))

    if not pending:
        return 0

    vectors = embed_passages([c.text for _, c in pending])
    for (article, chunk), vector in zip(pending, vectors):
        session.add(
            ArticleChunk(
                article_id=article.id,
                chunk_text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
            )
        )
    return len(pending)


def ingest_law(session: Session, law_name: str) -> None:
    print(f"[ingest] 검색 중: {law_name}")
    candidates = search_law(law_name)
    if not candidates:
        raise LawApiError(f"검색 결과 없음: {law_name}")

    # 정확히 일치하는 법령만 받아들인다. 예전에는 후보 첫 번째를 무조건 채택했는데,
    # "민법"처럼 짧은 이름은 유사 법령이 수십 개라 엉뚱한 법을 수집할 수 있었다.
    exact = next((c for c in candidates if c["law_name"] == law_name), None)
    if exact is None:
        near = ", ".join(c["law_name"] for c in candidates[:5])
        raise LawApiError(f"'{law_name}'과 정확히 일치하는 법령이 없습니다. 유사 후보: {near}")

    print(f"[ingest] 상세 조회: {exact['law_name']} (MST={exact['mst']})")
    detail = parse_law_detail(get_law_detail_xml(mst=exact["mst"]))
    detail.setdefault("law_id", None)
    if not detail.get("law_id"):
        detail["law_id"] = exact["law_id"]
    if not detail.get("law_name"):
        detail["law_name"] = exact["law_name"]

    law = upsert_law(session, detail)

    articles = detail.get("articles", [])
    if not articles:
        raise LawApiError(f"조문을 하나도 파싱하지 못했습니다: {law_name}")

    changed: list[Article] = []
    for article_data in tqdm(articles, desc=f"{law_name} 조문 확인", unit="조문"):
        article, needs_embedding = upsert_article(session, law, article_data)
        if needs_embedding:
            changed.append(article)

    skipped = len(articles) - len(changed)
    if changed:
        print(f"[ingest] 임베딩 {len(changed)}개 조문 (변경 없음 {skipped}개는 건너뜀)")
        chunk_count = embed_and_store_chunks(session, changed)
        print(f"[ingest] 청크 {chunk_count}개 저장")
    else:
        print(f"[ingest] 변경된 조문이 없습니다 ({skipped}개 모두 건너뜀)")

    session.commit()
    print(f"[ingest] 완료: {law_name} (조문 {len(articles)}개)")


def main() -> None:
    targets = sys.argv[1:] or TARGET_LAWS
    session = SessionLocal()
    failures: list[tuple[str, str]] = []
    try:
        for law_name in targets:
            # 법령 하나가 실패해도 나머지는 계속 수집한다. 25개를 돌리는데 한 건의
            # API 오류로 전체가 중단되면 재실행 비용이 너무 크다.
            try:
                ingest_law(session, law_name)
            except Exception as exc:  # noqa: BLE001 - 개별 법령 실패를 배치 전체와 격리한다
                session.rollback()
                failures.append((law_name, str(exc)))
                print(f"[ingest][오류] {law_name}: {exc}", file=sys.stderr)
    finally:
        session.close()

    print(f"\n[ingest] 총 {len(targets)}개 중 {len(targets) - len(failures)}개 성공")
    if failures:
        print(f"[ingest] 실패 {len(failures)}건:", file=sys.stderr)
        for law_name, message in failures:
            print(f"  - {law_name}: {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
