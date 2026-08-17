from contextlib import contextmanager

from neo4j import Driver, GraphDatabase, NotificationDisabledClassification

from app.config import settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            # 그래프는 app.graph_ingest로 조금씩 채워지므로, 아직 안 만들어진 라벨·관계
            # 타입을 조회하는 건 정상 상태다. 그때마다 서버가 보내는 UNRECOGNIZED 경고
            # ("The label `Article` does not exist" 등)는 조회 결과에 영향이 없고 로그만
            # 가득 채우므로 끈다. 다른 분류(성능·지원중단 등)의 알림은 그대로 받는다.
            notifications_disabled_classifications=[
                NotificationDisabledClassification.UNRECOGNIZED
            ],
        )
    return _driver


@contextmanager
def graph_session():
    driver = get_driver()
    session = driver.session()
    try:
        yield session
    finally:
        session.close()


# MERGE는 매칭할 인덱스가 없으면 라벨 전체를 훑는다. 조문이 수만 개로 늘면 적재가
# 급격히 느려지므로 유일성 제약(= 인덱스)을 미리 만들어 둔다.
_CONSTRAINTS = [
    "CREATE CONSTRAINT domain_code_unique IF NOT EXISTS FOR (d:Domain) REQUIRE d.code IS UNIQUE",
    "CREATE CONSTRAINT law_id_unique IF NOT EXISTS FOR (l:Law) REQUIRE l.law_id IS UNIQUE",
    "CREATE CONSTRAINT article_key_unique IF NOT EXISTS FOR (a:Article) "
    "REQUIRE (a.law_id, a.article_no, a.article_no_sub) IS UNIQUE",
    "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS FOR (e:Entity) "
    "REQUIRE (e.name, e.type) IS UNIQUE",
]


def ensure_constraints() -> None:
    """그래프 적재 전에 한 번 호출한다. 이미 있으면 아무 일도 하지 않는다."""
    with graph_session() as gs:
        for statement in _CONSTRAINTS:
            gs.run(statement)
