"""사고 진술문을 청킹 -> 하이브리드 검색 -> Gemini 판단을 거쳐
원문 위치 기준 조문 인용(citation) 목록으로 변환한다.
"""

from dataclasses import dataclass

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.chunking import chunk_text
from app.config import settings
from app.law_links import article_url
from app.retrieval import HybridSafetyLawRetriever


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
    citations.sort(key=lambda c: (c.law_name, c.article_no, c.article_no_sub, c.start))
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


def annotate_text(session: Session, text: str, *, top_k: int = 6) -> list[Citation]:
    chunks = chunk_text(text, window_size=4, overlap=1)
    citations: list[Citation] = []
    
    retriever = HybridSafetyLawRetriever(session=session, top_k=top_k)
    
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0
    )
    structured_llm = llm.with_structured_output(_CitationsResponse)
    
    prompt_template = PromptTemplate.from_template(
        "다음은 산업안전보건 사고 상황을 서술한 글의 일부입니다.\n\n"
        "[텍스트 구간]\n"
        "{chunk_text_value}\n\n"
        "[후보 조문 목록]\n"
        "{candidates_prompt}\n\n"
        "위 후보 조문 중, 텍스트 구간의 내용에 실제로 의미 있게 적용되는 조문만 보고하세요.\n"
        "관련 없는 후보는 생략하세요. quote는 반드시 [텍스트 구간] 원문에서 그대로 발췌해야 합니다."
    )
    
    chain = prompt_template | structured_llm

    for chunk in chunks:
        docs = retriever.invoke(chunk.text)
        if not docs:
            continue
            
        lines = []
        for d in docs:
            lines.append(
                f"- {d.metadata['law_name']} {d.metadata['article_label']} "
                f"({d.metadata['title'] or ''}): {d.page_content}"
            )
        candidates_str = "\n".join(lines)
        
        parsed = chain.invoke({
            "chunk_text_value": chunk.text,
            "candidates_prompt": candidates_str
        })
        
        if not parsed:
            continue
            
        raw_citations = [item.model_dump() for item in parsed.citations]
        by_key = {(d.metadata["article_no"], d.metadata["article_no_sub"], d.metadata["law_name"]): d for d in docs}

        for raw in raw_citations:
            key = (raw["article_no"], raw.get("article_no_sub", 0), raw["law_name"])
            match = by_key.get(key)
            offsets = _resolve_offset(chunk.char_start, chunk.text, raw["quote"])
            if offsets is None:
                continue
            start, end = offsets
            citations.append(
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

    return _merge_citations(citations)
