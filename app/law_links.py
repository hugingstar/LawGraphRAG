"""법령명 + 조번호로 law.go.kr 조문 딥링크를 생성한다.

법제처가 URL 스킴을 바꾸더라도 이 함수만 고치면 되도록 로직을 격리해둔다.
"""

from urllib.parse import quote


def article_url(law_name: str, article_no: int, article_no_sub: int = 0) -> str:
    label = f"제{article_no}조"
    if article_no_sub:
        label += f"의{article_no_sub}"
    return f"https://www.law.go.kr/{quote('법령')}/{quote(law_name)}/{quote(label)}"
