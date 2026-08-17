"""쟁점·인용·분석 단위의 데이터 모델과 원문 오프셋 계산.

LLM도 DB도 건드리지 않는 순수 로직만 모아 둔다. 인용이 원문 어디에 붙는지(=화면
하이라이트 위치)와 어떤 인용을 하나로 합칠지는 이 서비스에서 가장 틀리기 쉬운 부분인데,
app.issues·app.annotate에 그대로 두면 langchain·torch를 임포트해야 해서 API 키 없이는
테스트조차 돌릴 수 없다(tests/test_issues.py).

- app.issues: 사실관계 -> 쟁점(LLM 1회)
- app.annotate: 쟁점/청크별 후보 조문 -> 적용 조문 판단(LLM n회)
- 이 모듈: 두 단계가 주고받는 자료구조와 오프셋 계산
"""

import logging
import re
from dataclasses import dataclass, field

from app.chunking import chunk_text, locate_quote
from app.law_category import LAW_CATEGORIES

logger = logging.getLogger(__name__)

# 쟁점 분야는 법령 분야(law_categories)와 같은 코드를 쓴다. 화면 배지 라벨을 따로
# 관리하지 않아도 되고, 조문의 분야와 쟁점의 분야를 같은 축으로 묶을 수 있다.
DOMAIN_LABELS: dict[str, str] = dict(LAW_CATEGORIES)


# 쟁점 추출 프롬프트가 반드시 하나씩 훑는 관점(app/issues.py의 ①~④).
# 화면에서 이 네 줄은 결과가 없어도 자리를 지킨다 — 모델이 관점 하나를 통째로 빠뜨리면
# "미검토"로 드러나야 하기 때문이다. 빠뜨린 것과 검토 후 불성립은 전혀 다른 정보다.
# 프롬프트의 ⑤(그 밖의 행정·절차)는 분야가 열려 있어 고정 줄로 두지 않는다.
REVIEW_CHECKLIST: list[str] = ["criminal", "civil", "labor", "family_inheritance"]


def normalize_domain(code: str) -> str:
    """LLM이 보낸 분야 코드를 정식 코드로 맞춘다. 못 맞추면 'etc'.

    모델은 코드 목록을 프롬프트로 받고도 짧은 쪽을 골라 보낸다(실측: 'family_inheritance'
    대신 'family'). 그대로 두면 화면에서 "가족·상속" 묶음이 "기타"로 흩어져 다각도 검토가
    흐려진다. 스키마 enum으로 못박는 방법은 Gemini가 쟁점을 아예 못 내놓게 만들어 못 쓴다.

    정확히 일치하는 코드가 먼저고, 없으면 그 코드로 시작하는 정식 코드가 **하나뿐일 때만**
    받아들인다('civil'은 그 자체로 정식 코드이므로 'civil_procedure'로 끌려가지 않는다)."""
    code = (code or "").strip().lower()
    if code in DOMAIN_LABELS:
        return code
    matches = [c for c in DOMAIN_LABELS if c.startswith(code)] if code else []
    return matches[0] if len(matches) == 1 else "etc"


@dataclass
class Issue:
    """사실관계에서 뽑아낸 법적 쟁점 하나."""

    domain: str
    domain_label: str
    label: str
    query: str  # 조문 검색에 쓸, 법률 용어로 다시 쓴 질의
    spans: list[tuple[int, int]]  # 이 쟁점의 근거가 된 원문 구간


@dataclass
class DismissedDomain:
    """검토했으나 쟁점이 성립하지 않는다고 판단한 관점.

    이게 없으면 화면에서 "그 관점을 보고 아니라고 판단한 것"과 "모델이 그냥 빠뜨린 것"이
    똑같이 빈칸으로 보인다. 사용자에게는 후자가 훨씬 위험한 정보다."""

    domain: str
    domain_label: str
    reason: str


@dataclass
class IssueAnalysis:
    facts: str
    issues: list[Issue]
    dismissed: list[DismissedDomain] = field(default_factory=list)


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
    # 이 인용이 어느 분야 쟁점에서 나왔는지. 화면에서 민사/형사/노동/가족으로 묶어
    # 보여주는 축이라, 같은 조문이라도 분야가 다르면 합치지 않는다.
    domain: str | None = None
    domain_label: str | None = None
    issue_label: str | None = None


@dataclass
class AnalysisUnit:
    """LLM에 한 번 물어볼 단위(쟁점 하나 또는 청크 하나)."""

    query: str  # 벡터 검색에 넣을 질의
    prompt_text: str  # LLM에게 보여줄 텍스트
    search_spans: list[tuple[int, int]]  # quote를 먼저 뒤질 원문 구간
    # quote를 원문에서 못 찾았을 때 대신 쓸 앵커. 비어 있으면 그 인용은 버린다.
    # 쟁점 단위에서만 채운다 — 청크 단위의 앵커는 청크 전체라 하이라이트가 너무 넓어진다.
    fallback_spans: list[tuple[int, int]] = field(default_factory=list)
    domain: str | None = None
    domain_label: str | None = None
    issue_label: str | None = None
    context: str = ""  # 사실관계 요약 등 프롬프트에 덧붙일 맥락


def issue_to_dict(issue: Issue) -> dict:
    return {
        "domain": issue.domain,
        "domain_label": issue.domain_label,
        "label": issue.label,
        "query": issue.query,
    }


_LAW_NAME_TAIL_RE = re.compile(r"\s*제\d+조.*$")


def clean_law_name(name: str) -> str:
    """LLM이 보낸 법령명에서 뒤에 붙은 조번호·조문제목을 떼어낸다.

    후보 목록에 없는 조문을 인용하면 DB의 정식 법령명을 못 붙이고 LLM 표기를 그대로 쓰는데,
    모델이 "민법 제836조의2 (이혼의 절차)"처럼 조번호까지 넣어 보내는 일이 있다. 그대로 두면
    화면에 "민법 제836조의2 (이혼의 절차) 제836조"로 찍히고 법제처 링크도 깨진다."""
    return _LAW_NAME_TAIL_RE.sub("", name).strip() or name.strip()


def dismissed_to_dict(d: DismissedDomain) -> dict:
    return {"domain": d.domain, "domain_label": d.domain_label, "reason": d.reason}


def citation_to_dict(c: Citation) -> dict:
    return {
        "law_name": c.law_name,
        "article_label": f"제{c.article_no}조" + (f"의{c.article_no_sub}" if c.article_no_sub else ""),
        "title": c.title,
        "start": c.start,
        "end": c.end,
        "reason": c.reason,
        "url": c.url,
        "domain": c.domain,
        "domain_label": c.domain_label,
        "issue_label": c.issue_label,
    }


def resolve_spans(text: str, quotes: list[str]) -> list[tuple[int, int]]:
    """근거 문구들을 원문 위치로 바꾼다(겹치는 구간은 합친다).

    이 구간이 곧 화면 하이라이트의 앵커가 된다. 관계 사실을 서술한 문장이 여기 들어와야
    "두 번째 문장에는 아무 표시도 안 뜨는" 문제가 풀린다."""
    spans: list[tuple[int, int]] = []
    for quote in quotes:
        span = locate_quote(text, quote)
        if span is None:
            logger.info("쟁점 근거 문구를 원문에서 못 찾았다: %r", quote[:40])
            continue
        spans.append(span)

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def issue_units(text: str, analysis: IssueAnalysis) -> list[AnalysisUnit]:
    """쟁점마다 분석 단위를 만든다.

    LLM에 보여줄 텍스트는 그 쟁점의 근거 구간만 이어붙인다(근거가 없으면 원문 전체).
    근거 구간이 곧 하이라이트 앵커이므로, 관계를 서술한 문장이 여기 들어오면 그 문장에도
    마커가 붙는다."""
    units = []
    for issue in analysis.issues:
        spans = issue.spans
        context = f"[사실관계 요약]\n{analysis.facts}\n\n" if analysis.facts else ""
        context += f"[검토 쟁점] {issue.domain_label} — {issue.label}\n\n"
        units.append(
            AnalysisUnit(
                query=issue.query,
                prompt_text="\n".join(text[s:e] for s, e in spans) if spans else text,
                search_spans=spans or [(0, len(text))],
                fallback_spans=spans,
                domain=issue.domain,
                domain_label=issue.domain_label,
                issue_label=issue.label,
                context=context,
            )
        )
    return units


def chunk_units(text: str) -> list[AnalysisUnit]:
    """문장 슬라이딩 윈도우 단위. 쟁점 추출이 놓친 국소적인 서술을 받친다."""
    return [
        AnalysisUnit(
            query=chunk.text,
            prompt_text=chunk.text,
            search_spans=[(chunk.char_start, chunk.char_end)],
        )
        for chunk in chunk_text(text, window_size=4, overlap=1)
    ]


def citation_spans(text: str, unit: AnalysisUnit, quote: str) -> list[tuple[int, int]]:
    """인용문을 원문 위치로 바꾼다. 못 찾으면 쟁점의 근거 구간을 앵커로 쓴다.

    예전에는 못 찾으면 인용을 그냥 버렸는데, 그러면 관계 사실처럼 "발췌할 위법행위 문구"가
    없는 근거에서 나온 조문이 화면에서 통째로 사라진다. 쟁점 단위에는 근거 구간이 있으니
    그쪽에 앵커를 건다(근거가 여러 문장이면 각각에 마커가 붙는다)."""
    for span in unit.search_spans:
        found = locate_quote(text, quote, within=span)
        if found is not None:
            return [found]
    if unit.fallback_spans:
        logger.info("인용문을 원문에서 못 찾아 쟁점 근거 구간에 앵커한다: %r", quote[:40])
        return list(unit.fallback_spans)
    logger.info("인용문을 원문에서 못 찾아 건너뛴다: %r", quote[:60])
    return []


def merge_citations(citations: list[Citation]) -> list[Citation]:
    """같은 분야·같은 조문에 대한 겹치거나 인접한 인용을 하나로 합친다.

    분야가 다르면 합치지 않는다 — 같은 조문이라도 형사 쟁점에서 나온 것과 민사 쟁점에서
    나온 것은 사용자에게 다른 정보다."""
    citations = sorted(
        citations,
        key=lambda c: (c.domain or "", c.law_name, c.article_no, c.article_no_sub, c.start),
    )
    merged: list[Citation] = []
    for c in citations:
        if merged:
            prev = merged[-1]
            same_article = (
                prev.domain == c.domain
                and prev.law_name == c.law_name
                and prev.article_no == c.article_no
                and prev.article_no_sub == c.article_no_sub
            )
            if same_article and c.start <= prev.end:
                prev.end = max(prev.end, c.end)
                continue
        merged.append(c)
    merged.sort(key=lambda c: c.start)
    return merged
