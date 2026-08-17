"""사실관계에서 법적 쟁점을 뽑아내는 단계(검색 이전).

문장 단위 청킹 + 벡터 검색만으로는 **여러 문장에 흩어진 사실이 결합해 생기는 쟁점**을
잡지 못한다. "대표가 직원을 폭행했다. 대표는 직원의 남편이다."에서 두 번째 문장은 그
자체로 위법행위 서술이 아니라 관계 사실이라, 단독으로 벡터 검색을 걸면 가족관계등록법
같은 무관한 조문만 올라온다. 그런데 첫 문장과 결합하면 가정폭력·근로기준법상 사용자의
폭행 금지·재판상 이혼사유라는 **별개의 쟁점**이 생긴다.

그래서 검색 전에 LLM을 1회 호출해 (1) 당사자 관계까지 반영한 사실관계 요약과
(2) 분야별 검색 질의를 만들어 둔다. 질의는 원문 표현이 아니라 법률 용어로 다시 쓴
문장이라, 원문에 "폭행"밖에 없어도 "가정구성원 사이의 폭력범죄" 조문을 찾아올 수 있다.

이 단계가 실패해도(쿼터 초과 등) 예외를 삼키고 빈 결과를 돌려준다 — 호출부는 기존
청크 단위 검색만으로 정상 동작한다(fail-open. 그래프 확장·재순위와 같은 원칙).

자료구조(Issue/IssueAnalysis)와 원문 오프셋 계산은 app.citations에 있다.
"""

import logging

from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field, field_validator

from app.citations import (
    DOMAIN_LABELS,
    REVIEW_CHECKLIST,
    DismissedDomain,
    Issue,
    IssueAnalysis,
    normalize_domain,
    resolve_spans,
)
from app.config import settings
from app.law_category import LAW_CATEGORIES

logger = logging.getLogger(__name__)

# 쟁점 하나당 검색 1회 + LLM 판단 1회가 더 붙는다. Gemini 무료 티어가 분당 15회라
# 상한을 두지 않으면 긴 진술문에서 429로 통째로 실패한다.
#
# 체크리스트 관점이 4개(REVIEW_CHECKLIST)라 5로 잡으면 여유가 1칸뿐이고, 그러면 모델이
# 성립하는 쟁점을 "개수 제한 때문에 제외한다"며 dismissed로 보낸다(실측). dismissed는
# 화면에 "해당 사항 없음"으로 뜨므로 그건 거짓 정보가 된다. 4 + 여유 2로 잡는다.
MAX_ISSUES = 6

_DOMAIN_CHOICES = "\n".join(
    f"- {code}: {label}" for code, label in LAW_CATEGORIES if code != "etc"
)


class _IssueItem(BaseModel):
    # domain을 Literal(=스키마 enum)로 못박아 보려 했지만, 그러면 Gemini가 쟁점을
    # 하나도 내놓지 않았다(실측: 4건 -> 0건). 목록 밖의 코드가 가끔 오는 편이
    # 아예 빈손이 되는 것보다 낫다 — 아래 extract_issues에서 'etc'로 떨어뜨린다.
    domain: str = ""
    label: str = ""
    query: str = ""
    quotes: list[str] = Field(default_factory=list)

    @field_validator("domain", "label", "query", mode="before")
    @classmethod
    def _str_or_empty(cls, value):
        # Gemini 구조화 출력은 값이 없으면 키를 빼는 대신 null을 채워 보낸다.
        return value or ""

    @field_validator("quotes", mode="before")
    @classmethod
    def _list_or_empty(cls, value):
        return value or []


class _DismissedItem(BaseModel):
    domain: str = ""
    reason: str = ""

    @field_validator("domain", "reason", mode="before")
    @classmethod
    def _str_or_empty(cls, value):
        return value or ""


class _IssueResponse(BaseModel):
    facts: str = ""
    issues: list[_IssueItem] = Field(default_factory=list)
    dismissed: list[_DismissedItem] = Field(default_factory=list)

    @field_validator("facts", mode="before")
    @classmethod
    def _str_or_empty(cls, value):
        return value or ""

    @field_validator("issues", "dismissed", mode="before")
    @classmethod
    def _list_or_empty(cls, value):
        return value or []


_PROMPT = PromptTemplate.from_template(
    "다음은 법적 검토가 필요한 사실관계 서술입니다.\n\n"
    "[사실관계]\n{text}\n\n"
    "[분야 코드]\n{domains}\n\n"
    "이 사실관계에서 성립 가능한 법적 쟁점을 최대 {max_issues}개까지 뽑아내세요.\n\n"
    "지시사항:\n"
    "1. 문장을 하나씩 따로 보지 말고, 여러 문장에 흩어진 사실을 합쳐서 해석하세요. "
    "특히 당사자 사이의 관계(가족·혼인·고용·계약·거래)는 그 자체로는 위법행위가 아니지만, "
    "다른 문장의 행위와 결합하면 별개의 법적 쟁점을 만들어냅니다. 이런 결합 쟁점을 빠뜨리지 마세요.\n"
    "2. 아래 관점을 **하나씩 차례로** 따져보고, 실제로 성립하는 것만 쟁점으로 남기세요. "
    "하나의 사실관계가 여러 관점에 동시에 걸리는 것이 정상입니다.\n"
    "   ① 형사: 범죄가 성립하는가, 가중·특례 처벌 법률이 따로 있는가\n"
    "   ② 민사: 손해배상·위자료 등 금전적 책임을 물을 근거가 있는가\n"
    "   ③ 노동: 사용자와 근로자 사이의 관계에서 생기는 의무 위반이 있는가\n"
    "   ④ 가족: 혼인·친족 관계에서 생기는 권리(이혼 청구 등)에 영향을 주는가\n"
    "   ⑤ 그 밖에 행정 규제·절차상 쟁점이 있는가\n"
    "   같은 관점 안에서 쟁점이 여럿이면 나눠서 적으세요.\n"
    "3. query는 법령 조문을 검색할 때 쓸 문장입니다. 원문 표현을 그대로 쓰지 말고, "
    "그 쟁점을 다루는 조문의 본문에 실제로 적혀 있을 법한 **완결된 서술문 한 문장**으로 "
    "쓰세요. 법령명·조번호나 키워드를 나열하지 마세요 — 검색은 문장의 의미를 비교하므로 "
    "키워드 나열은 오히려 엉뚱한 조문을 물어옵니다.\n"
    "   나쁜 예: '폭행죄 형법 조문 반의사불벌죄', '근로기준법 폭행 금지 직장 내 괴롭힘'\n"
    "   좋은 예: '사람의 신체에 대하여 폭행을 가한 자는 처벌한다', "
    "'사용자는 사고의 발생이나 그 밖의 어떠한 이유로도 근로자에게 폭행을 하지 못한다'\n"
    "4. quotes에는 그 쟁점의 근거가 된 [사실관계] 원문 문구를 그대로 발췌해 넣으세요. "
    "관계를 서술한 문장이 근거라면 그 문장도 반드시 포함하세요.\n"
    "5. facts에는 당사자와 그들 사이의 관계를 한 문장으로 정리하세요.\n"
    "6. 위 ①~④ 관점 중 **검토했지만 쟁점이 성립하지 않는다**고 판단한 것은 issues에 넣지 말고 "
    "dismissed에 그 관점의 분야 코드와 판단 이유를 적으세요. ①~④는 반드시 issues와 dismissed "
    "둘 중 한쪽에 나와야 합니다 — 어느 쪽에도 없으면 '검토를 빠뜨렸다'는 뜻이 됩니다.\n"
    "   dismissed는 오직 **그 관점의 쟁점이 실제로 성립하지 않을 때**만 씁니다. 성립하는데 "
    "덜 중요하다거나 개수를 맞추려고 dismissed로 보내면 안 됩니다 — 화면에 '해당 사항 없음'으로 "
    "표시되어 사용자에게 거짓을 알리게 됩니다. ①~④에서 성립하는 쟁점은 개수와 무관하게 "
    "모두 issues에 넣으세요.\n\n"
    "실제로 성립하지 않는 쟁점을 억지로 issues에 채우지는 마세요. 성립하지 않으면 dismissed로 "
    "보내면 됩니다."
)


def _build_chain():
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    return _PROMPT | llm.with_structured_output(_IssueResponse)


def extract_issues(text: str, *, max_issues: int = MAX_ISSUES) -> IssueAnalysis:
    """사실관계에서 분야별 쟁점을 뽑는다. 실패하면 빈 결과(호출부는 청크 검색으로 진행)."""
    if not text.strip():
        return IssueAnalysis(facts="", issues=[])

    try:
        chain = _build_chain()
        parsed = chain.invoke(
            {"text": text, "domains": _DOMAIN_CHOICES, "max_issues": max_issues}
        )
    except Exception:
        logger.exception("쟁점 추출 실패 — 청크 단위 검색만으로 진행한다")
        return IssueAnalysis(facts="", issues=[])

    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for item in parsed.issues:
        if not item.query.strip():
            continue
        domain = normalize_domain(item.domain)
        if domain != item.domain:
            logger.info("분야 코드를 %r -> %r로 맞췄다 (%s)", item.domain, domain, item.label)
        key = (domain, item.label.strip())
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            Issue(
                domain=domain,
                domain_label=DOMAIN_LABELS[domain],
                label=item.label.strip() or DOMAIN_LABELS[domain],
                query=item.query.strip(),
                spans=resolve_spans(text, item.quotes),
            )
        )
        if len(issues) >= max_issues:
            break

    # 쟁점이 성립한 분야는 dismissed에서 뺀다. 모델이 같은 분야를 양쪽에 다 넣는 일이
    # 있는데(같은 분야 안에 성립하는 쟁점과 안 하는 쟁점이 섞일 때), 그러면 화면에
    # "조문 있음"과 "해당 사항 없음"이 동시에 뜬다.
    established = {i.domain for i in issues}
    dismissed: list[DismissedDomain] = []
    seen_domains: set[str] = set()
    for item in parsed.dismissed:
        domain = normalize_domain(item.domain)
        if domain in established or domain in seen_domains:
            continue
        seen_domains.add(domain)
        dismissed.append(
            DismissedDomain(
                domain=domain,
                domain_label=DOMAIN_LABELS[domain],
                reason=item.reason.strip(),
            )
        )

    # 체크리스트 관점 중 어느 쪽에도 안 나온 것은 모델이 검토를 빠뜨렸다는 뜻이다.
    # 화면에서는 "미검토"로 드러나지만, 로그에도 남겨야 원인을 추적할 수 있다.
    unreviewed = [d for d in REVIEW_CHECKLIST if d not in established and d not in seen_domains]
    logger.info(
        "쟁점 추출: 성립 %d건 (%s) / 불성립 %d건 (%s)%s",
        len(issues),
        ", ".join(f"{i.domain_label}/{i.label}" for i in issues) or "없음",
        len(dismissed),
        ", ".join(d.domain_label for d in dismissed) or "없음",
        f" / 미검토 {', '.join(DOMAIN_LABELS[d] for d in unreviewed)}" if unreviewed else "",
    )
    return IssueAnalysis(facts=parsed.facts.strip(), issues=issues, dismissed=dismissed)
