from app.law_links import article_url


def test_article_url_without_sub():
    url = article_url("산업안전보건법", 38)
    assert url == "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%82%B0%EC%97%85%EC%95%88%EC%A0%84%EB%B3%B4%EA%B1%B4%EB%B2%95/%EC%A0%9C38%EC%A1%B0"


def test_article_url_with_sub():
    url = article_url("산업안전보건법", 38, 2)
    assert url.endswith("%EC%A0%9C38%EC%A1%B0%EC%9D%982")


def test_article_url_encodes_korean():
    url = article_url("산업안전보건기준에 관한 규칙", 1)
    assert url.startswith("https://www.law.go.kr/")
    assert " " not in url
