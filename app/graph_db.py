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
