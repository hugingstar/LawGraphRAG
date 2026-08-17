"""사고 진술문을 쟁점 추출 -> 벡터·그래프 검색 -> Gemini 판단을 거쳐
원문 위치 기준 조문 인용(citation) 목록으로 변환한다.

검색 질의는 두 갈래로 만든다.
- **쟁점 단위**(app.issues): 진술문 전체를 먼저 읽어 분야별 쟁점을 뽑고, 쟁점마다
  법률 용어로 다시 쓴 질의를 던진다. 여러 문장에 흩어진 사실이 결합해 생기는 쟁점
  (예: "폭행" + "가해자가 배우자" -> 가정폭력)은 이 갈래로만 잡힌다.
- **청크 단위**(기존): 문장 슬라이딩 윈도우. 쟁점 추출이 놓친 국소적인 서술을 받친다.

두 갈래는 분야별로 각자 top_k를 배정받으므로, 형사 조문이 후보를 독식해 민사·노동
조문이 후보에조차 못 드는 일이 없다.
"""

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.citations import (
    DOMAIN_LABELS,
    AnalysisUnit,
    Citation,
    IssueAnalysis,
    chunk_units,
    citation_spans,
    clean_law_name,
    issue_units,
    merge_citations,
)
from app.config import settings
from app.issues import extract_issues
from app.law_links import article_url
from app.retrieval import HybridLawOwlyRetriever

logger = logging.getLogger(__name__)

# 진술문 청크 하나당 LLM에 넘길 후보 조문 수. 법령이 수천 건 켜져 있으면 맞는 조문이
# 상위 6개 밖으로 밀려나 "적용되는 조문 없음"이 되는 일이 잦아 넉넉하게 잡는다.
DEFAULT_TOP_K = 12

# 쟁점 단위 질의는 이미 분야가 좁혀져 있어 후보를 이만큼 넓게 볼 필요가 없다.
# 쟁점 개수만큼 호출이 늘어나므로 프롬프트 길이를 아끼는 편이 낫다.
ISSUE_TOP_K = 8


class AnalysisFailed(RuntimeError):
    """모든 청크에서 LLM 호출이 실패해 아무것도 판단하지 못한 상태.

    이걸 빈 인용 목록으로 돌려주면 화면에는 "적용되는 조문을 찾지 못했습니다"가 떠서
    '적용될 조문이 없다'는 판단 결과와 구분이 안 된다. 무료 티어 분당 요청 한도(429)처럼
    다시 시도하면 되는 실패는 결과가 아니라 오류로 알려야 한다."""


class _CitationItem(BaseModel):
    law_name: str
    article_no: int
    article_no_sub: int = Field(default=0)
    quote: str
    reason: str = ""

    @field_validator("article_no_sub", "reason", mode="before")
    @classmethod
    def _null_to_default(cls, value, info):
        """LLM이 보낸 null을 기본값으로 바꾼다.

        Gemini의 구조화 출력은 스키마에 있는 속성을 빼지 않고 값이 없으면 null로 채워
        보낸다("제38조"처럼 가지번호가 없으면 article_no_sub=null). Field(default=...)는
        키가 아예 없을 때만 적용되므로 null은 그대로 검증 실패가 되고, 그 예외가
        _call_llm 밖으로 나가면 청크 하나 때문에 분석 전체가 죽는다."""
        if value is None:
            return cls.model_fields[info.field_name].get_default()
        return value


class _CitationsResponse(BaseModel):
    citations: list[_CitationItem]


_PROMPT = PromptTemplate.from_template(
    "다음은 법적 검토가 필요한 사실관계입니다.\n\n"
    "{context_block}"
    "[검토 대상]\n"
    "{chunk_text_value}\n\n"
    "[후보 조문 목록]\n"
    "{candidates_prompt}\n\n"
    "위 후보 조문 중, 이 사실관계에 실제로 의미 있게 적용되는 조문만 보고하세요.\n"
    "분야를 가리지 마세요 — 하나의 사실관계가 민사·형사·노동·가족 등 여러 분야에 동시에 "
    "걸리는 것이 정상입니다.\n"
    "당사자 사이의 관계(가족·혼인·고용·계약)는 그 자체로 위법이 아니어도 다른 사실과 결합해 "
    "적용 조문과 처벌·구제 방법을 바꿉니다. 반드시 함께 고려하세요.\n"
    "관련 없는 후보는 생략하세요. quote는 반드시 [검토 대상] 원문에서 그대로 발췌해야 하며, "
    "그 조문을 적용하게 만든 문장을 발췌하세요(관계를 서술한 문장이 근거라면 그 문장을 쓰세요)."
)


def _build_chain():
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    return _PROMPT | llm.with_structured_output(_CitationsResponse)


def _retrieve_units(
    session: Session, text: str, user_id: int, top_k: int
) -> tuple[IssueAnalysis, list[tuple[AnalysisUnit, list]]]:
    """쟁점·청크 단위를 만들고, 단위별 후보 조문을 순차 조회한다.

    검색은 순차로 돈다(하나의 SQLAlchemy 세션을 여러 스레드가 동시에 쓰면 안 된다).
    검색 대상 법은 user_id로 좁힌다(설정 > 법 활성화에서 이 사용자가 고른 법만). 분야로
    한 번 더 좁히는 단계는 없다 — app.retrieval._vector_candidates 참고.
    """
    analysis = extract_issues(text)
    units = issue_units(text, analysis) + chunk_units(text)

    issue_retriever = HybridLawOwlyRetriever(session=session, user_id=user_id, top_k=ISSUE_TOP_K)
    chunk_retriever = HybridLawOwlyRetriever(session=session, user_id=user_id, top_k=top_k)

    unit_data = []
    for unit in units:
        retriever = issue_retriever if unit.domain else chunk_retriever
        docs = retriever.invoke(unit.query)
        if docs:
            unit_data.append((unit, docs))

    # 결과가 비었을 때 어디가 비었는지(후보가 없었는지, LLM이 없다고 했는지) 로그로
    # 구분할 수 있어야 한다 — 화면에는 두 경우가 똑같이 "조문 없음"으로 보인다.
    logger.info(
        "조문 후보 조회: 단위=%d개(쟁점 %d) 중 후보있음=%d개",
        len(units), len(analysis.issues), len(unit_data),
    )
    return analysis, unit_data


def _call_llm(chain, item):
    """분석 단위 하나에 대해 LLM을 호출한다. 실패하면 parsed=None으로 돌려준다.

    LLM이 스키마에 안 맞는 값을 돌려주는 일은 드물지만 일어나는데, 그 예외를 그대로
    올려보내면 단위 하나 때문에 진술문 전체 분석이 실패한다. 나머지 단위의 결과라도
    보여주는 편이 낫고, 원인은 로그로 남긴다(_citations_from_result가 None을 걸러낸다)."""
    unit, docs = item
    lines = [
        f"- {d.metadata['law_name']} {d.metadata['article_label']} "
        f"({d.metadata['title'] or ''}): {d.page_content}"
        for d in docs
    ]
    try:
        parsed = chain.invoke(
            {
                "context_block": unit.context,
                "chunk_text_value": unit.prompt_text,
                "candidates_prompt": "\n".join(lines),
            }
        )
    except Exception:
        logger.exception("조문 인용 추출 실패 — 이 단위는 건너뛴다 (%s)", unit.issue_label or unit.query[:30])
        parsed = None
    return unit, docs, parsed


def _citations_from_result(text: str, unit: AnalysisUnit, docs, parsed) -> list[Citation]:
    if not parsed:
        return []

    by_key = {(d.metadata["article_no"], d.metadata["article_no_sub"], d.metadata["law_name"]): d for d in docs}
    # LLM이 법령명을 그대로 안 돌려주는 일이 있어(예: "산업안전보건기준에 관한 규칙 제44조
    # (안전대의 부착설비 등)"처럼 조번호·제목까지 붙여서) 법령명 없이 조번호만으로도 찾아본다.
    # 후보에서 찾아야 조문 제목과 국가법령정보센터 링크를 제대로 붙일 수 있다.
    by_article_no: dict[tuple[int, int], object] = {}
    for d in docs:
        by_article_no.setdefault((d.metadata["article_no"], d.metadata["article_no_sub"]), d)

    result: list[Citation] = []
    for item in parsed.citations:
        raw = item.model_dump()
        article_no_sub = raw.get("article_no_sub", 0)
        match = by_key.get((raw["article_no"], article_no_sub, raw["law_name"]))
        if match is None:
            match = by_article_no.get((raw["article_no"], article_no_sub))
            if match is None:
                logger.info(
                    "후보에 없는 조문을 인용했다 — 그대로 채택한다: %s 제%s조",
                    raw["law_name"], raw["article_no"],
                )

        spans = citation_spans(text, unit, raw["quote"])
        if not spans:
            continue
        # 링크·제목은 DB에 있는 정식 법령명 기준으로 만든다(LLM이 보낸 표기는 신뢰하지 않는다).
        # 후보에 없어 DB 이름을 못 쓸 때는 최소한 조번호 꼬리라도 떼고 쓴다.
        law_name = match.metadata["law_name"] if match else clean_law_name(raw["law_name"])
        # 쟁점 단위면 그 쟁점의 분야를, 청크 단위면 조문이 속한 법령의 분야를 붙인다.
        domain = unit.domain or (match.metadata.get("category_code") if match else None)
        domain_label = unit.domain_label or (DOMAIN_LABELS.get(domain) if domain else None)
        for start, end in spans:
            result.append(
                Citation(
                    law_name=law_name,
                    article_no=raw["article_no"],
                    article_no_sub=article_no_sub,
                    title=match.metadata["title"] if match else None,
                    start=start,
                    end=end,
                    reason=raw.get("reason", ""),
                    url=article_url(law_name, raw["article_no"], article_no_sub),
                    domain=domain,
                    domain_label=domain_label,
                    issue_label=unit.issue_label,
                )
            )
    return result


def _fail_if_all_llm_calls_failed(total: int, failed: int) -> None:
    if total and failed == total:
        raise AnalysisFailed(
            "조문 분석에 실패했습니다. LLM 호출이 모두 실패했습니다"
            "(무료 티어 분당 요청 한도 초과일 수 있습니다). 잠시 후 다시 시도해 주세요."
        )


def annotate_text(session: Session, text: str, user_id: int, *, top_k: int = DEFAULT_TOP_K) -> list[Citation]:
    _analysis, unit_data = _retrieve_units(session, text, user_id, top_k)
    chain = _build_chain()

    citations: list[Citation] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        for unit, docs, parsed in executor.map(lambda item: _call_llm(chain, item), unit_data):
            if parsed is None:
                failed += 1
                continue
            citations.extend(_citations_from_result(text, unit, docs, parsed))

    _fail_if_all_llm_calls_failed(len(unit_data), failed)
    logger.info("조문 분석 완료: 단위=%d개 인용=%d건", len(unit_data), len(citations))
    return merge_citations(citations)


def annotate_text_stream(
    session: Session, text: str, user_id: int, *, top_k: int = DEFAULT_TOP_K
) -> Iterator[tuple[str, object]]:
    """조문 인용을 찾는 대로 하나씩 내보낸다(실시간 표시용).

    단위별 LLM 호출은 병렬로 실행하되, `as_completed`로 먼저 끝난 것부터 바로 내보낸다
    (제출 순서를 기다리는 `executor.map`과 달리 느린 단위 하나 때문에 전체가 막히지 않는다).
    맨 먼저 ("issues", IssueAnalysis)로 어떤 분야를 검토하는지 알리고, 각 결과는
    ("citation", Citation) 이벤트로, 전체가 끝나면 분야·조문별로 겹치는 구간을 합친 최종
    목록을 ("done", list[Citation])으로 내보낸다.

    쟁점 목록을 먼저 보내는 건 화면 때문만이 아니다 — 어떤 쟁점에서 조문이 하나도 안
    나왔는지 사용자가 알 수 있어야 "검토했지만 해당 없음"과 "아예 검토 안 함"이 구분된다.
    """
    analysis, unit_data = _retrieve_units(session, text, user_id, top_k)
    yield ("issues", analysis)

    chain = _build_chain()

    all_citations: list[Citation] = []
    failed = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_call_llm, chain, item) for item in unit_data]
        for future in as_completed(futures):
            unit, docs, parsed = future.result()
            if parsed is None:
                failed += 1
                continue
            for citation in _citations_from_result(text, unit, docs, parsed):
                all_citations.append(citation)
                yield ("citation", citation)

    # 호출부(app.main.analyze_stream)가 이 예외를 error 이벤트로 바꿔 화면에 알린다.
    _fail_if_all_llm_calls_failed(len(unit_data), failed)
    logger.info("조문 분석 완료(스트림): 단위=%d개 인용=%d건", len(unit_data), len(all_citations))
    yield ("done", merge_citations(all_citations))
