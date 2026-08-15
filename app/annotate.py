"""사고 진술문을 청킹 -> 하이브리드 검색 -> Gemini 판단을 거쳐
원문 위치 기준 조문 인용(citation) 목록으로 변환한다.
"""

from dataclasses import dataclass
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.chunking import chunk_text
from app.config import settings
from app.law_category import classify_query_domains
from app.law_links import article_url
from app.retrieval import HybridLawOwlyRetriever


class _CitationItem(BaseModel):
    law_name: str
    article_no: int
    article_no_sub: int = Field(default=0)
    quote: str
    reason: str


class _CitationsResponse(BaseModel):
    citations: list[_CitationItem]


@dataclass
class Citation:
    law_name: str
    article_no: int
    article_no_sub: int
    title: str | None
    start: int
    end: int
    reason: str
    url: str


def _resolve_offset(chunk_start: int, chunk_str: str, quote: str) -> tuple[int, int] | None:
    idx = chunk_str.find(quote)
    if idx == -1:
        return None
    return chunk_start + idx, chunk_start + idx + len(quote)


def _merge_citations(citations: list[Citation]) -> list[Citation]:
    """같은 조문에 대한 겹치거나 인접한 인용을 하나로 합친다."""
    citations = sorted(citations, key=lambda c: (c.law_name, c.article_no, c.article_no_sub, c.start))
    merged: list[Citation] = []
    for c in citations:
        if merged:
            prev = merged[-1]
            same_article = (
                prev.law_name == c.law_name
                and prev.article_no == c.article_no
                and prev.article_no_sub == c.article_no_sub
            )
            if same_article and c.start <= prev.end:
                prev.end = max(prev.end, c.end)
                continue
        merged.append(c)
    merged.sort(key=lambda c: c.start)
    return merged


def citation_to_dict(c: Citation) -> dict:
    return {
        "law_name": c.law_name,
        "article_label": f"제{c.article_no}조" + (f"의{c.article_no_sub}" if c.article_no_sub else ""),
        "title": c.title,
        "start": c.start,
        "end": c.end,
        "reason": c.reason,
        "url": c.url,
    }


_PROMPT = PromptTemplate.from_template(
    "다음은 산업안전보건 사고 상황을 서술한 글의 일부입니다.\n\n"
    "[텍스트 구간]\n"
    "{chunk_text_value}\n\n"
    "[후보 조문 목록]\n"
    "{candidates_prompt}\n\n"
    "위 후보 조문 중, 텍스트 구간의 내용에 실제로 의미 있게 적용되는 조문만 보고하세요.\n"
    "관련 없는 후보는 생략하세요. quote는 반드시 [텍스트 구간] 원문에서 그대로 발췌해야 합니다."
)


def _build_chain():
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    return _PROMPT | llm.with_structured_output(_CitationsResponse)


def _retrieve_chunk_data(session: Session, text: str, top_k: int):
    """문장을 청킹하고, 청크별 후보 조문을 순차 조회한다(세션 동시 사용을 피하기 위해 순차 처리).

    분야 분류는 전체 진술문 기준으로 한 번만 수행해 청크마다 LLM을 다시 부르지 않는다.
    분류에 실패하거나 애매하면 domain_codes가 None이 되어 기존과 동일하게 전체 검색으로 동작한다.
    """
    domain_codes = classify_query_domains(text)
    chunks = chunk_text(text, window_size=4, overlap=1)
    retriever = HybridLawOwlyRetriever(session=session, top_k=top_k, domain_codes=domain_codes)

    chunk_data = []
    for chunk in chunks:
        docs = retriever.invoke(chunk.text)
        if docs:
            chunk_data.append((chunk, docs))
    return chunk_data


def _call_llm(chain, item):
    chunk, docs = item
    lines = [
        f"- {d.metadata['law_name']} {d.metadata['article_label']} "
        f"({d.metadata['title'] or ''}): {d.page_content}"
        for d in docs
    ]
    parsed = chain.invoke({"chunk_text_value": chunk.text, "candidates_prompt": "\n".join(lines)})
    return chunk, docs, parsed


def _citations_from_result(chunk, docs, parsed) -> list[Citation]:
    if not parsed:
        return []

    by_key = {(d.metadata["article_no"], d.metadata["article_no_sub"], d.metadata["law_name"]): d for d in docs}
    result: list[Citation] = []
    for item in parsed.citations:
        raw = item.model_dump()
        key = (raw["article_no"], raw.get("article_no_sub", 0), raw["law_name"])
        match = by_key.get(key)
        offsets = _resolve_offset(chunk.char_start, chunk.text, raw["quote"])
        if offsets is None:
            continue
        start, end = offsets
        result.append(
            Citation(
                law_name=raw["law_name"],
                article_no=raw["article_no"],
                article_no_sub=raw.get("article_no_sub", 0),
                title=match.metadata["title"] if match else None,
                start=start,
                end=end,
                reason=raw.get("reason", ""),
                url=article_url(raw["law_name"], raw["article_no"], raw.get("article_no_sub", 0)),
            )
        )
    return result


def annotate_text(session: Session, text: str, *, top_k: int = 6) -> list[Citation]:
    chunk_data = _retrieve_chunk_data(session, text, top_k)
    chain = _build_chain()

    citations: list[Citation] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for chunk, docs, parsed in executor.map(lambda item: _call_llm(chain, item), chunk_data):
            citations.extend(_citations_from_result(chunk, docs, parsed))

    return _merge_citations(citations)


def annotate_text_stream(session: Session, text: str, *, top_k: int = 6) -> Iterator[tuple[str, object]]:
    """조문 인용을 찾는 대로 하나씩 내보낸다(실시간 표시용).

    청크별 LLM 호출은 병렬로 실행하되, `as_completed`로 먼저 끝난 것부터 바로 내보낸다
    (제출 순서를 기다리는 `executor.map`과 달리 느린 청크 하나 때문에 전체가 막히지 않는다).
    각 청크 결과는 ("citation", Citation) 이벤트로, 전체가 끝나면 조문별로 겹치는 구간을
    합친 최종 목록을 ("done", list[Citation])으로 내보낸다.
    """
    chunk_data = _retrieve_chunk_data(session, text, top_k)
    chain = _build_chain()

    all_citations: list[Citation] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_call_llm, chain, item) for item in chunk_data]
        for future in as_completed(futures):
            chunk, docs, parsed = future.result()
            for citation in _citations_from_result(chunk, docs, parsed):
                all_citations.append(citation)
                yield ("citation", citation)

    yield ("done", _merge_citations(all_citations))
