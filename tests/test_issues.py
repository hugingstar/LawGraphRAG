"""쟁점 기반 분석에서 LLM을 타지 않는 부분(원문 오프셋 매핑, 분석 단위 구성, 병합)만
검증한다. LLM 응답 자체는 비결정적이라 여기서 고정하지 않는다.

기준 케이스는 "대표가 직원을 폭행했다. 대표는 직원의 남편이다." — 두 번째 문장은 그
자체로 위법행위 서술이 아니라 관계 사실이라, 예전 구조에서는 화면에 아무 표시도 남지
않았다(app/issues.py 참고).
"""

from app.chunking import locate_quote
from app.citations import (
    DOMAIN_LABELS,
    REVIEW_CHECKLIST,
    AnalysisUnit,
    Citation,
    DismissedDomain,
    Issue,
    IssueAnalysis,
    citation_spans,
    clean_law_name,
    dismissed_to_dict,
    issue_units,
    merge_citations,
    normalize_domain,
    resolve_spans,
)

STATEMENT = "대표가 직원을 폭행했다. 대표는 직원의 남편이다."
SENT1 = (0, len("대표가 직원을 폭행했다."))
SENT2 = (SENT1[1] + 1, len(STATEMENT))


def _issue(domain="criminal", label="배우자 폭행", spans=None):
    return Issue(
        domain=domain,
        domain_label="형사",
        label=label,
        query="가정구성원 사이의 폭력범죄 처벌",
        spans=spans if spans is not None else [SENT1, SENT2],
    )


def test_normalize_domain_accepts_exact_code():
    assert normalize_domain("family_inheritance") == "family_inheritance"


def test_normalize_domain_expands_shorthand():
    """LLM은 목록을 주어도 짧은 쪽을 골라 보낸다(실측: 'family')."""
    assert normalize_domain("family") == "family_inheritance"
    assert normalize_domain("consumer") == "consumer_product"
    assert normalize_domain("occupational") == "occupational_safety"


def test_normalize_domain_prefers_exact_over_prefix():
    """'civil'은 그 자체로 정식 코드다 — 'civil_procedure'로 끌려가면 안 된다."""
    assert normalize_domain("civil") == "civil"


def test_normalize_domain_rejects_ambiguous_and_unknown():
    assert normalize_domain("c") == "etc"  # civil/criminal/... 여러 개에 걸린다
    assert normalize_domain("환경") == "etc"
    assert normalize_domain("") == "etc"


def test_locate_quote_exact():
    assert locate_quote(STATEMENT, "대표는 직원의 남편이다.") == SENT2


def test_locate_quote_ignores_whitespace_differences():
    """LLM이 공백/줄바꿈을 바꿔 보내도 원문 위치를 찾아야 한다."""
    assert locate_quote(STATEMENT, "대표가  직원을\n폭행했다.") == SENT1


def test_locate_quote_prefers_given_span():
    text = "폭행했다. 다른 문장. 폭행했다."
    second = text.rindex("폭행했다.")
    assert locate_quote(text, "폭행했다.", within=(second, len(text))) == (second, second + 5)


def test_locate_quote_returns_none_when_absent():
    assert locate_quote(STATEMENT, "임금을 체불했다.") is None


def test_resolve_spans_merges_overlaps():
    spans = resolve_spans(STATEMENT, ["대표가 직원을 폭행했다.", "직원을 폭행했다."])
    assert spans == [SENT1]


def test_resolve_spans_skips_unfindable_quotes():
    spans = resolve_spans(STATEMENT, ["대표는 직원의 남편이다.", "존재하지 않는 문장"])
    assert spans == [SENT2]


def test_issue_unit_prompt_text_covers_both_sentences():
    """관계 사실 문장이 쟁점의 근거로 들어오면 LLM 입력에도 포함되어야 한다."""
    analysis = IssueAnalysis(facts="대표는 피해 직원의 배우자이자 사용자다.", issues=[_issue()])
    unit = issue_units(STATEMENT, analysis)[0]

    assert "대표가 직원을 폭행했다." in unit.prompt_text
    assert "대표는 직원의 남편이다." in unit.prompt_text
    assert "대표는 피해 직원의 배우자이자 사용자다." in unit.context
    assert unit.domain == "criminal"


def test_citation_anchors_to_issue_spans_when_quote_missing():
    """관계 사실에는 발췌할 '위법행위 문구'가 없다 — 인용을 버리지 말고 근거 구간에 건다."""
    unit = issue_units(STATEMENT, IssueAnalysis(facts="", issues=[_issue()]))[0]
    assert citation_spans(STATEMENT, unit, "원문에 없는 표현") == [SENT1, SENT2]


def test_chunk_unit_drops_unfindable_quote():
    """청크 단위에는 근거 구간이 없다. 청크 전체를 하이라이트하느니 버린다."""
    unit = AnalysisUnit(query="q", prompt_text=STATEMENT, search_spans=[(0, len(STATEMENT))])
    assert citation_spans(STATEMENT, unit, "원문에 없는 표현") == []


def _citation(domain, start, end, article_no=260):
    return Citation(
        law_name="형법",
        article_no=article_no,
        article_no_sub=0,
        title="폭행",
        start=start,
        end=end,
        reason="",
        url="",
        domain=domain,
        domain_label="형사",
    )


def test_merge_keeps_same_article_under_different_domains():
    """같은 조문이라도 민사 쟁점에서 나온 것과 형사 쟁점에서 나온 것은 별개로 보여야 한다."""
    merged = merge_citations([_citation("criminal", *SENT1), _citation("civil", *SENT1)])
    assert {c.domain for c in merged} == {"criminal", "civil"}


def test_merge_combines_overlapping_spans_in_same_domain():
    merged = merge_citations([_citation("criminal", 0, 10), _citation("criminal", 5, 14)])
    assert [(c.start, c.end) for c in merged] == [(0, 14)]


def test_merge_keeps_disjoint_spans_so_both_sentences_highlight():
    merged = merge_citations([_citation("criminal", *SENT1), _citation("criminal", *SENT2)])
    assert [(c.start, c.end) for c in merged] == [SENT1, SENT2]


def test_clean_law_name_strips_article_tail():
    """LLM은 법령명에 조번호·조문제목을 붙여 보내기도 한다(실측)."""
    assert clean_law_name("민법 제836조의2 (이혼의 절차)") == "민법"
    assert clean_law_name("산업안전보건기준에 관한 규칙 제44조(안전대의 부착설비)") == (
        "산업안전보건기준에 관한 규칙"
    )


def test_clean_law_name_leaves_plain_name_alone():
    assert clean_law_name("근로기준법") == "근로기준법"
    assert clean_law_name("가정폭력범죄의 처벌 등에 관한 특례법") == (
        "가정폭력범죄의 처벌 등에 관한 특례법"
    )


def test_clean_law_name_keeps_something_when_name_is_only_an_article():
    """조번호만 온 경우까지 빈 문자열로 만들면 링크 생성이 더 망가진다."""
    assert clean_law_name("제260조") == "제260조"


def test_review_checklist_codes_are_real_domains():
    """체크리스트가 정식 분야 코드여야 화면에서 라벨이 붙는다."""
    assert all(code in DOMAIN_LABELS for code in REVIEW_CHECKLIST)


def test_issue_analysis_defaults_to_no_dismissals():
    """dismissed는 나중에 붙은 필드라, 없이 만들던 호출부가 그대로 동작해야 한다."""
    assert IssueAnalysis(facts="", issues=[]).dismissed == []


def test_dismissed_to_dict_carries_reason():
    d = DismissedDomain(domain="labor", domain_label="노동", reason="고용관계가 없다")
    assert dismissed_to_dict(d) == {
        "domain": "labor",
        "domain_label": "노동",
        "reason": "고용관계가 없다",
    }
