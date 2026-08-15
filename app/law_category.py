"""법령 분야(Domain) 대분류 및 분류 규칙.

법령이 수천 건으로 늘어나면 벡터/그래프 검색을 분야로 먼저 좁혀야 무관한 분야의 조문이
후보에 섞이지 않는다. 법령 하나하나를 LLM으로 분류하면(5천 건 이상) 무료 쿼터로는 시간이
너무 걸리므로, 법령명 접미사/키워드 기반의 결정적 규칙으로 분류한다. 한국 법령명은
"~법", "~에 관한 법률"처럼 접미사가 분야를 강하게 암시하므로 규칙만으로도 충분히 정확하다.
"""

import re

LAW_CATEGORIES: list[tuple[str, str]] = [
    ("constitutional", "헌법·정부조직"),
    ("civil", "민사"),
    ("criminal", "형사"),
    ("civil_procedure", "소송·절차"),
    ("labor", "노동"),
    ("occupational_safety", "산업안전·중대재해"),
    ("real_estate_construction", "부동산·건설"),
    ("traffic", "교통"),
    ("environment_disaster", "환경·재난"),
    ("tax", "조세"),
    ("corporate_commercial", "회사·상행위"),
    ("family_inheritance", "가족·상속"),
    ("consumer_product", "소비자·제조물·배상"),
    ("ip", "지식재산권"),
    ("finance_insurance", "금융·보험"),
    ("it_privacy", "정보통신·개인정보"),
    ("etc", "기타"),
]

LAW_CATEGORY_CODES = [code for code, _ in LAW_CATEGORIES]

# 서비스 초기(산업안전 어드바이저 시절)에 수집했던 25개 법령은 이미 사람이 분야별로
# 분류해 둔 것이라 규칙보다 우선해 정확히 매핑한다. 나머지는 아래 _SUFFIX_RULES로 분류한다.
_EXACT_NAME_OVERRIDES: dict[str, str] = {
    "산업안전보건기준에 관한 규칙": "occupational_safety",
    "산업안전보건법": "occupational_safety",
    "산업안전보건법 시행령": "occupational_safety",
    "산업안전보건법 시행규칙": "occupational_safety",
    "중대재해 처벌 등에 관한 법률": "occupational_safety",
    "대한민국헌법": "constitutional",
    "민법": "civil",
    "형법": "criminal",
    "상법": "corporate_commercial",
    "민사소송법": "civil_procedure",
    "형사소송법": "civil_procedure",
    "근로기준법": "labor",
    "노동조합 및 노동관계조정법": "labor",
    "도로교통법": "traffic",
    "교통사고처리 특례법": "traffic",
    "자동차손해배상 보장법": "traffic",
    "소방기본법": "occupational_safety",
    "건축법": "real_estate_construction",
    "건설산업기본법": "real_estate_construction",
    "시설물의 안전 및 유지관리에 관한 특별법": "real_estate_construction",
    "환경정책기본법": "environment_disaster",
    "화학물질관리법": "environment_disaster",
    "재난 및 안전관리 기본법": "environment_disaster",
    "국가배상법": "consumer_product",
    "제조물 책임법": "consumer_product",
}

# (정규식, 분야코드) 순서대로 첫 매치를 채택한다. 구체적인 규칙을 앞에 둔다.
_SUFFIX_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(세법|관세법|조세.*법)$"), "tax"),
    (re.compile(r"(형법|형사소송법|처벌 등에 관한 법률|처벌법)$"), "criminal"),
    (re.compile(r"(민사소송법|민사집행법|가사소송법)$"), "civil_procedure"),
    (re.compile(r"(가족관계|혼인|이혼|상속|유언)"), "family_inheritance"),
    (re.compile(r"(특허법|상표법|디자인보호법|저작권법|실용신안법|부정경쟁방지)"), "ip"),
    (re.compile(r"(산업안전|산업재해|중대재해|소방)"), "occupational_safety"),
    (re.compile(r"(근로|노동|고용보험|최저임금)"), "labor"),
    (re.compile(r"(도로교통법|자동차관리법|자동차손해배상|여객자동차)"), "traffic"),
    (re.compile(r"(건축법|건설산업|주택법|택지|국토의 계획|부동산|공동주택|시설물)"), "real_estate_construction"),
    (re.compile(r"(환경|화학물질|재난|안전관리|대기환경|수질)"), "environment_disaster"),
    (re.compile(r"(은행법|보험업법|자본시장|여신전문|금융)"), "finance_insurance"),
    (re.compile(r"(개인정보 보호법|정보통신망|전자상거래|전자문서)"), "it_privacy"),
    (re.compile(r"(제조물 책임법|소비자기본법|국가배상법)"), "consumer_product"),
    (re.compile(r"(상법|회사법|협동조합)"), "corporate_commercial"),
    (re.compile(r"^대한민국헌법$|정부조직법"), "constitutional"),
    (re.compile(r"^민법$"), "civil"),
]

# 규칙에 매치되지 않을 때 소관부처명으로 보조 판단한다.
_DEPARTMENT_RULES: dict[str, str] = {
    "고용노동부": "labor",
    "국토교통부": "real_estate_construction",
    "환경부": "environment_disaster",
    "소방청": "occupational_safety",
    "행정안전부": "environment_disaster",
    "기획재정부": "tax",
    "국세청": "tax",
    "관세청": "tax",
    "법무부": "civil",
    "특허청": "ip",
    "문화체육관광부": "ip",
    "금융위원회": "finance_insurance",
    "공정거래위원회": "consumer_product",
    "개인정보보호위원회": "it_privacy",
    "과학기술정보통신부": "it_privacy",
    "경찰청": "traffic",
}


def classify_law(law_name: str, department: str | None = None) -> str:
    """법령명(+ 소관부처명)으로 분야 코드를 결정한다. 판단 불가하면 'etc'를 돌려준다."""
    override = _EXACT_NAME_OVERRIDES.get(law_name)
    if override:
        return override

    for pattern, code in _SUFFIX_RULES:
        if pattern.search(law_name):
            return code

    if department and department in _DEPARTMENT_RULES:
        return _DEPARTMENT_RULES[department]

    return "etc"


_query_domain_chain = None


def _build_query_domain_chain():
    from langchain_core.prompts import PromptTemplate
    from langchain_google_genai import ChatGoogleGenerativeAI
    from pydantic import BaseModel, Field

    from app.config import settings

    class _DomainsResponse(BaseModel):
        codes: list[str] = Field(
            default_factory=list,
            description=f"관련도가 높은 순으로 최대 3개. 다음 코드 중에서만 고를 것: {', '.join(LAW_CATEGORY_CODES)}",
        )

    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    categories_desc = "\n".join(f"- {code}: {name}" for code, name in LAW_CATEGORIES)
    prompt = PromptTemplate.from_template(
        "다음 글이 어떤 법률 분야와 가장 관련이 깊은지 판단하세요.\n\n"
        "[분야 목록]\n{categories_desc}\n\n"
        "[글]\n{text}\n\n"
        "관련도가 높은 분야를 최대 3개까지 코드로만 고르세요. 사고/피해 서술문은 여러 분야에 "
        "걸치는 경우가 많으니(예: 작업 중 부상은 노동+산업안전+민사 배상이 동시에 걸릴 수 있음) "
        "관련 가능성이 있으면 넓게 포함하세요. 애매하면 빈 목록도 가능합니다."
    ).partial(categories_desc=categories_desc)
    return prompt | llm.with_structured_output(_DomainsResponse)


def classify_query_domains(text: str) -> list[str] | None:
    """사용자 질의/진술문이 어떤 법령 분야에 속하는지 LLM으로 1~2개 판단한다.

    검색 필터링의 보조 신호일 뿐이므로, 실패하거나 애매하면 None을 돌려줘 상위 호출부가
    분야 제한 없이(기존과 동일하게) 전체 검색으로 폴백하게 한다.
    """
    global _query_domain_chain
    try:
        if _query_domain_chain is None:
            _query_domain_chain = _build_query_domain_chain()
        result = _query_domain_chain.invoke({"text": text})
        codes = [c for c in result.codes if c in LAW_CATEGORY_CODES]
        return codes or None
    except Exception:
        return None
