"""법령 수집 CLI.

사용법 (OC 키 발급 및 .env 설정 후):
    python -m app.ingest             # 증분 동기화: 개정되었거나 새로 생긴 법령만 갱신,
                                      #   현행 목록에서 빠진(폐지된) 법령은 조문을 비운다
    python -m app.ingest --all       # 변경 여부와 무관하게 현행 법령 전체를 강제로 재수집
    python -m app.ingest 민법 형법    # 특정 법령명만 즉시 수집(관리자 수동 보정용)

법제처 Open API로 법령을 검색 -> 상세 조회 -> DB upsert -> 조문 청킹 -> 임베딩 저장한다.

재실행에 안전하다. 조문 원문의 해시를 저장해 두고 내용이 그대로면 청킹·임베딩을 건너뛰므로,
법령을 반복 수집해도 바뀐 조문만 다시 계산한다. 기본 동작(--all 없이 인자 없이 실행)은 한 걸음
더 나아가 법령 단위에서도 MST(법령일련번호)가 그대로면 상세 조회 자체를 건너뛴다 — 수천 건
규모에서는 "바뀐 것만" 처리해야 API 호출과 임베딩 비용이 실제 개정 건수에 비례하게 된다.
"""

import argparse
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
from app.law_category import classify_law
from app.models import Article, ArticleChunk, Law, LawCategory
from app.parser import parse_law_detail


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _category_id(session: Session, code: str) -> int | None:
    category = session.scalar(select(LawCategory).where(LawCategory.code == code))
    if category is None:
        category = session.scalar(select(LawCategory).where(LawCategory.code == "etc"))
    return category.id if category else None


def upsert_law(session: Session, meta: dict) -> Law:
    law = session.scalar(select(Law).where(Law.law_id == meta["law_id"]))
    if law is None:
        law = Law(law_id=meta["law_id"])
        session.add(law)

    law.law_name = meta["law_name"]
    law.law_type = meta.get("law_type")
    law.department = meta.get("department")
    law.mst = meta.get("mst")
    law.repealed_at = None  # 다시 수집됐다는 건 현행 목록에 있다는 뜻이니 폐지 표시를 해제한다
    law.category_id = _category_id(session, classify_law(meta["law_name"], meta.get("department")))
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


def _ingest_articles(session: Session, law: Law, articles: list[dict], label: str) -> None:
    if not articles:
        raise LawApiError(f"조문을 하나도 파싱하지 못했습니다: {label}")

    changed: list[Article] = []
    for article_data in tqdm(articles, desc=f"{label} 조문 확인", unit="조문"):
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
    print(f"[ingest] 완료: {label} (조문 {len(articles)}개)")


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

    ingest_law_by_mst(session, exact["mst"], exact["law_id"], exact["law_name"], exact.get("department"))


def ingest_law_by_mst(
    session: Session, mst: str, law_id_hint: str | None, name_hint: str, department: str | None = None
) -> None:
    """법령명 검색 없이 MST(법령일련번호)로 바로 상세를 조회해 수집한다.

    list_all_current_laws()로 전체 법령을 순회할 때는 목록 조회 시점에 이미 MST를 알고
    있으므로, 법령명 검색 단계(ingest_law가 하는 '정확히 일치하는 후보 찾기')를 건너뛴다.
    """
    print(f"[ingest] 상세 조회: {name_hint} (MST={mst})")
    detail = parse_law_detail(get_law_detail_xml(mst=mst))
    detail.setdefault("law_id", None)
    if not detail.get("law_id"):
        detail["law_id"] = law_id_hint
    if not detail.get("law_name"):
        detail["law_name"] = name_hint
    detail["department"] = department
    detail["mst"] = mst

    law = upsert_law(session, detail)
    _ingest_articles(session, law, detail.get("articles", []), name_hint)


def list_all_current_laws(display: int = 100) -> list[dict]:
    """법제처 API의 현행 법령 전체를 페이지네이션으로 순회해 나열한다(수천 건 규모).

    query=""로 검색하면 전체 법령이 대상이 된다. 마지막 페이지는 display보다 적게 오므로
    그 시점에 멈춘다.
    """
    all_laws: list[dict] = []
    page = 1
    while True:
        results = search_law("", display=display, page=page)
        if not results:
            break
        all_laws.extend(r for r in results if r.get("current_yn") in (None, "현행"))
        if len(results) < display:
            break
        page += 1
    return all_laws


def _repeal_missing_laws(session: Session, current_law_ids: set[str]) -> int:
    """현행 목록에서 더 이상 보이지 않는 법령을 폐지 처리한다.

    법령 행 자체(이력)는 남기되 조문/청크는 지운다 — 검색 결과에 실효된 법이 섞이지 않게
    하면서도, DB를 통째로 지우지 않아 나중에 그 법이 참조됐던 이력을 추적할 수 있다.
    """
    stale = session.scalars(
        select(Law).where(Law.repealed_at.is_(None), Law.law_id.notin_(current_law_ids))
    ).all()
    for law in stale:
        session.query(Article).filter(Article.law_id == law.id).delete()
        law.repealed_at = datetime.datetime.now(datetime.timezone.utc)
    if stale:
        session.commit()
    return len(stale)


def sync_all_laws(session: Session) -> tuple[int, int, int, list[tuple[str, str]]]:
    """법제처 현행 목록과 우리 DB를 비교해 바뀐 것만 처리한다.

    - law_id가 새로 생겼거나, MST(법령일련번호)가 저장된 값과 다르면(=개정됨) 상세를
      다시 받아온다.
    - MST가 그대로면 상세 조회조차 하지 않고 건너뛴다 — API 호출과 임베딩 비용을
      "실제로 바뀐 법령 수"에 비례하게 만드는 핵심.
    - 현행 목록에서 아예 빠진(폐지된) 법령은 조문을 비운다.

    반환값: (처리 건수, 건너뛴 건수, 폐지 처리 건수, 실패 목록)
    """
    print("[ingest] 법제처 현행 법령 전체 목록을 조회합니다...")
    current = [e for e in list_all_current_laws() if e.get("law_id")]
    current_by_id = {e["law_id"]: e for e in current}

    existing_by_id = {law.law_id: law for law in session.scalars(select(Law)).all()}

    to_process = [
        entry
        for law_id, entry in current_by_id.items()
        if (existing := existing_by_id.get(law_id)) is None
        or existing.mst != entry.get("mst")
        or existing.repealed_at is not None
    ]
    skipped = len(current_by_id) - len(to_process)
    print(f"[ingest] 전체 {len(current_by_id)}건 중 변경분 {len(to_process)}건만 처리 (동일 {skipped}건은 건너뜀)")

    failures: list[tuple[str, str]] = []
    for entry in tqdm(to_process, desc="법령 동기화", unit="법령"):
        try:
            ingest_law_by_mst(session, entry["mst"], entry["law_id"], entry["law_name"], entry.get("department"))
        except Exception as exc:  # noqa: BLE001 - 법령 하나 실패로 전체를 멈추지 않는다
            session.rollback()
            failures.append((entry["law_name"], str(exc)))
            print(f"[ingest][오류] {entry['law_name']}: {exc}", file=sys.stderr)

    repealed_count = _repeal_missing_laws(session, set(current_by_id))
    if repealed_count:
        print(f"[ingest] 현행 목록에서 빠진 법령 {repealed_count}건을 폐지 처리했습니다(조문 삭제, 이력은 보존)")

    return len(to_process), skipped, repealed_count, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="법령 수집 CLI")
    parser.add_argument("--all", action="store_true", help="변경 여부와 무관하게 현행 법령 전체를 강제로 재수집")
    parser.add_argument("law_names", nargs="*", help="수집할 법령명(생략 시: --all이면 전체, 아니면 증분 동기화)")
    args = parser.parse_args()

    session = SessionLocal()
    failures: list[tuple[str, str]] = []
    try:
        if args.law_names:
            targets = args.law_names
            for law_name in targets:
                # 법령 하나가 실패해도 나머지는 계속 수집한다.
                try:
                    ingest_law(session, law_name)
                except Exception as exc:  # noqa: BLE001 - 개별 법령 실패를 배치 전체와 격리한다
                    session.rollback()
                    failures.append((law_name, str(exc)))
                    print(f"[ingest][오류] {law_name}: {exc}", file=sys.stderr)
            print(f"\n[ingest] 총 {len(targets)}개 중 {len(targets) - len(failures)}개 성공")
        elif args.all:
            all_laws = list_all_current_laws()
            print(f"[ingest] 총 {len(all_laws)}건 강제 재수집 시작")
            for entry in tqdm(all_laws, desc="전체 법령 수집", unit="법령"):
                try:
                    ingest_law_by_mst(
                        session, entry["mst"], entry["law_id"], entry["law_name"], entry.get("department")
                    )
                except Exception as exc:  # noqa: BLE001 - 법령 하나 실패로 전체를 멈추지 않는다
                    session.rollback()
                    failures.append((entry["law_name"], str(exc)))
                    print(f"[ingest][오류] {entry['law_name']}: {exc}", file=sys.stderr)
            _repeal_missing_laws(session, {e["law_id"] for e in all_laws if e.get("law_id")})
            print(f"\n[ingest] 총 {len(all_laws)}개 중 {len(all_laws) - len(failures)}개 성공")
        else:
            processed, skipped, repealed, sync_failures = sync_all_laws(session)
            failures.extend(sync_failures)
            print(f"\n[ingest] 동기화 완료: 처리 {processed}건 / 건너뜀 {skipped}건 / 폐지 {repealed}건")
    finally:
        session.close()

    if failures:
        print(f"[ingest] 실패 {len(failures)}건:", file=sys.stderr)
        for name, message in failures:
            print(f"  - {name}: {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
