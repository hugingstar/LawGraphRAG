"""조문 원문에서 그래프 구축용 엔티티/관계를 추출한다.

app/annotate.py의 구조화 출력 체인 패턴(PromptTemplate | with_structured_output)을 재사용한다.
"""

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field, field_validator

from app.config import settings

# Neo4j 관계 타입으로 그대로 쓰이므로 화이트리스트로 제한한다(Cypher 관계 타입은 파라미터 바인딩이 안 됨).
ENTITY_RELATIONS = ("DEFINES", "APPLIES_TO", "PENALIZED_BY", "REQUIRES")


class EntityRelation(BaseModel):
    entity_name: str
    entity_type: str = Field(description="예: 의무주체, 정의어, 적용대상, 처벌 등")
    relation: str = Field(description=f"{'|'.join(ENTITY_RELATIONS)} 중 하나")


class ArticleReference(BaseModel):
    article_no: int
    article_no_sub: int = Field(default=0)
    law_name: str | None = Field(
        default=None,
        description="다른 법령을 인용한 경우 그 법령명(예: '형법'). 같은 법령 내 인용이면 비워둘 것.",
    )

    @field_validator("article_no_sub", mode="before")
    @classmethod
    def _null_sub_to_zero(cls, value):
        # Gemini는 가지번호가 없어도 키를 빼지 않고 null을 채워 보낸다. Field(default=0)은
        # 키가 없을 때만 쓰이므로 여기서 직접 0으로 바꿔야 검증에서 터지지 않는다.
        return 0 if value is None else value


class ArticleGraphData(BaseModel):
    entities: list[EntityRelation] = Field(default_factory=list)
    references: list[ArticleReference] = Field(default_factory=list)


_PROMPT = PromptTemplate.from_template(
    "다음은 {law_name} {article_label}의 원문입니다.\n\n"
    "[조문 원문]\n{full_text}\n\n"
    "이 조문에서 다음 두 가지를 추출하세요.\n"
    "1. references: 이 조문이 명시적으로 인용하는 다른 조(예: \"제38조에 따라\")의 조번호 목록. "
    "같은 조문 내 항·호 번호는 무시하고, 다른 조(article) 인용만 포함하세요. "
    "\"형법 제30조\"처럼 다른 법령을 함께 밝힌 인용이면 law_name에 그 법령명을 적고, "
    "같은 법령 안에서의 인용이면 law_name은 비워 두세요.\n"
    "2. entities: 이 조문에 등장하는 핵심 개념(의무주체, 정의어, 적용대상, 처벌 관련 개념 등)과 "
    "관계 유형을 추출하세요. 관계 유형은 다음 중 하나입니다: "
    "DEFINES(이 조문이 정의하는 용어), APPLIES_TO(이 조문이 적용되는 대상), "
    "PENALIZED_BY(이 조문 위반과 관련된 처벌 개념), REQUIRES(이 조문이 요구하는 의무·조치).\n"
    "관련이 없으면 빈 목록으로 두세요."
)


def build_graph_chain() -> Runnable:
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    return _PROMPT | llm.with_structured_output(ArticleGraphData)


def extract_article_graph(
    chain: Runnable, law_name: str, article_label: str, full_text: str
) -> ArticleGraphData:
    result = chain.invoke(
        {"law_name": law_name, "article_label": article_label, "full_text": full_text}
    )
    return result or ArticleGraphData()
