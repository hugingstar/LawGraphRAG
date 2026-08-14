from contextlib import contextmanager

from neo4j import Driver, GraphDatabase

from app.config import settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
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
