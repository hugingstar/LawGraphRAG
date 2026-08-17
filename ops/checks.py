"""각 구성요소의 상태를 한 번 찍어오는 블로킹 함수들.

전부 "실패해도 예외를 밖으로 내보내지 않는다"는 규칙을 지킨다. 모니터링 서비스가
감시 대상 때문에 500 을 내면 아무 쓸모가 없다. 실패는 ok=False 와 error 문자열로
표현하고, 나머지 구성요소의 결과는 정상적으로 돌려준다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg
from neo4j import GraphDatabase, NotificationDisabledClassification

from ops.config import ops_settings as cfg


@dataclass
class CheckResult:
    component: str
    ok: bool
    latency_ms: float | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "detail": self.detail,
        }


def _short(exc: BaseException, limit: int = 300) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------
# 웹 (블랙박스)
# --------------------------------------------------------------------------

def check_web() -> CheckResult:
    """HTTP 응답이 오기만 하면 '떠 있다'로 본다.

    401/302 도 정상 기동의 증거다. 로그인 리다이렉트를 장애로 오인하지 않도록
    5xx 와 연결 실패만 실패로 취급한다.
    """
    url = cfg.app_url.rstrip("/") + cfg.app_health_path
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=cfg.app_timeout_seconds, follow_redirects=False) as client:
            response = client.get(url)
    except Exception as exc:  # 연결 거부, DNS, 타임아웃
        return CheckResult("web", False, None, _short(exc), {"url": url})

    latency = (time.perf_counter() - started) * 1000
    ok = response.status_code < 500
    return CheckResult(
        "web",
        ok,
        latency,
        None if ok else f"HTTP {response.status_code}",
        {
            "url": url,
            "status_code": response.status_code,
            "content_length": len(response.content),
        },
    )


# --------------------------------------------------------------------------
# Postgres
# --------------------------------------------------------------------------

_PG_SUMMARY = """
SELECT
    current_setting('server_version')                       AS version,
    pg_database_size(current_database())                    AS db_bytes,
    current_setting('max_connections')::int                 AS max_connections,
    EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time())) AS uptime_seconds
"""

_PG_CONNECTIONS = """
SELECT state, count(*) AS n
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
"""

_PG_CACHE = """
SELECT blks_hit, blks_read, xact_commit, xact_rollback, deadlocks
FROM pg_stat_database
WHERE datname = current_database()
"""

# n_live_tup 은 통계 수집기가 갱신하는 추정치다. count(*) 를 테이블마다 도는 것보다
# 훨씬 싸고, 대시보드 용도로는 정확도가 충분하다.
_PG_TABLES = """
SELECT relname, n_live_tup, pg_total_relation_size(relid) AS bytes
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC
LIMIT 12
"""

_PG_EXTENSIONS = "SELECT extname, extversion FROM pg_extension ORDER BY extname"

_PG_LONG_QUERIES = """
SELECT count(*) AS n
FROM pg_stat_activity
WHERE datname = current_database()
  AND state = 'active'
  AND now() - query_start > interval '30 seconds'
"""


def check_postgres() -> CheckResult:
    started = time.perf_counter()
    try:
        with psycopg.connect(cfg.postgres_dsn, connect_timeout=cfg.postgres_timeout_seconds) as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SUMMARY)
                version, db_bytes, max_conn, uptime = cur.fetchone()

                cur.execute(_PG_CONNECTIONS)
                by_state = {(row[0] or "unknown"): row[1] for row in cur.fetchall()}

                cur.execute(_PG_CACHE)
                cache_row = cur.fetchone()

                cur.execute(_PG_TABLES)
                tables = [
                    {"name": r[0], "rows": r[1], "bytes": r[2]} for r in cur.fetchall()
                ]

                cur.execute(_PG_EXTENSIONS)
                extensions = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute(_PG_LONG_QUERIES)
                long_running = cur.fetchone()[0]
    except Exception as exc:
        return CheckResult("postgres", False, None, _short(exc))

    latency = (time.perf_counter() - started) * 1000

    blks_hit, blks_read, commits, rollbacks, deadlocks = cache_row or (0, 0, 0, 0, 0)
    total_blocks = (blks_hit or 0) + (blks_read or 0)
    cache_hit_ratio = (blks_hit / total_blocks) if total_blocks else None

    return CheckResult(
        "postgres",
        True,
        latency,
        None,
        {
            "version": version,
            "uptime_seconds": float(uptime or 0),
            "db_bytes": int(db_bytes or 0),
            "connections": sum(by_state.values()),
            "connections_by_state": by_state,
            "max_connections": int(max_conn),
            "cache_hit_ratio": cache_hit_ratio,
            "xact_commit": commits,
            "xact_rollback": rollbacks,
            "deadlocks": deadlocks,
            "long_running_queries": long_running,
            "tables": tables,
            "extensions": extensions,
            "pgvector": "vector" in extensions,
        },
    )


# --------------------------------------------------------------------------
# Neo4j
# --------------------------------------------------------------------------

_neo4j_driver = None


def get_neo4j_driver():
    """모듈 수준에서 재사용한다. 폴링마다 드라이버를 새로 만들면 30초마다
    커넥션 풀을 버리는 셈이라 Neo4j 쪽 로그가 지저분해진다."""
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            cfg.neo4j_uri,
            auth=(cfg.neo4j_user, cfg.neo4j_password),
            connection_timeout=cfg.neo4j_timeout_seconds,
            notifications_disabled_classifications=[
                NotificationDisabledClassification.UNRECOGNIZED
            ],
        )
    return _neo4j_driver


def close_neo4j_driver() -> None:
    global _neo4j_driver
    if _neo4j_driver is not None:
        _neo4j_driver.close()
        _neo4j_driver = None


def _counts(session, names: list[str], branch) -> dict[str, int]:
    """라벨/관계타입별 카운트를 한 번의 왕복으로 가져온다.

    이름마다 따로 실행하면 라벨 6개 + 타입 9개에 15번 왕복이라 30초 주기 점검이
    900ms 가까이 걸렸다. UNION ALL 로 묶어도 각 가지는 여전히 카운트 스토어를
    읽으므로 O(1)이다. 이름은 라벨 리터럴로만 쓰고(백틱 이스케이프), 식별은
    문자열 대신 인덱스로 되돌려 받아 따옴표 이스케이프 문제를 피한다.
    """
    if not names:
        return {}
    branches = [
        f"{branch(name.replace('`', '``'))} AS value, {index} AS idx"
        for index, name in enumerate(names)
    ]
    query = " UNION ALL ".join(branches)
    result = {}
    for record in session.run(query):
        result[names[record["idx"]]] = record["value"]
    return result


def check_neo4j() -> CheckResult:
    started = time.perf_counter()
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            components = session.run(
                "CALL dbms.components() YIELD name, versions, edition "
                "RETURN name, versions[0] AS version, edition"
            ).data()

            # 필터 없는 count 는 카운트 스토어를 읽으므로 그래프 크기와 무관하게 O(1)이다.
            # 라벨/타입을 하나 붙인 count 도 마찬가지라, 전수 스캔 걱정 없이 쪼갤 수 있다.
            node_count = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]

            labels = [r["label"] for r in session.run("CALL db.labels() YIELD label RETURN label")]
            by_label = _counts(session, labels, lambda name: f"MATCH (n:`{name}`) RETURN count(n)")

            rel_types = [
                r["relationshipType"]
                for r in session.run(
                    "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
                )
            ]
            by_type = _counts(
                session, rel_types, lambda name: f"MATCH ()-[r:`{name}`]->() RETURN count(r)"
            )
    except Exception as exc:
        return CheckResult("neo4j", False, None, _short(exc))

    latency = (time.perf_counter() - started) * 1000
    component = components[0] if components else {}
    return CheckResult(
        "neo4j",
        True,
        latency,
        None,
        {
            "version": component.get("version"),
            "edition": component.get("edition"),
            "nodes": node_count,
            "relationships": rel_count,
            "nodes_by_label": dict(sorted(by_label.items(), key=lambda kv: -kv[1])),
            "relationships_by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        },
    )


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------

def check_docker() -> CheckResult:
    if not cfg.docker_enabled:
        return CheckResult("docker", True, None, None, {"enabled": False, "containers": []})

    started = time.perf_counter()
    try:
        import docker  # 소켓이 없는 환경에서도 import 자체는 성공한다

        client = docker.from_env()
        raw = client.containers.list(all=True)
    except Exception as exc:
        # 소켓 미마운트는 흔한 정상 상황(로컬 실행 등)이라 에러는 남기되 별도로 구분한다.
        return CheckResult("docker", False, None, _short(exc), {"enabled": True, "containers": []})

    latency = (time.perf_counter() - started) * 1000
    filters = cfg.docker_name_filters
    containers = []
    for container in raw:
        name = container.name
        if filters and not any(f in name for f in filters):
            continue
        state = (container.attrs.get("State") or {})
        containers.append(
            {
                "name": name,
                "status": container.status,
                "health": (state.get("Health") or {}).get("Status"),
                "image": (container.image.tags or ["<none>"])[0],
                "started_at": state.get("StartedAt"),
                "restart_count": container.attrs.get("RestartCount", 0),
            }
        )

    containers.sort(key=lambda c: c["name"])
    # health=starting 은 healthcheck 의 시작 유예 구간이라 장애가 아니다. ops 컨테이너는
    # 자기 자신도 감시하므로, 이걸 비정상으로 세면 ops 를 재시작할 때마다 카드가
    # 빨갛게 뜬다. 확정된 unhealthy 만 실패로 본다.
    unhealthy = [
        c for c in containers
        if c["status"] != "running" or c["health"] == "unhealthy"
    ]
    return CheckResult(
        "docker",
        not unhealthy,
        latency,
        None if not unhealthy else f"{len(unhealthy)}개 컨테이너 비정상",
        {"enabled": True, "containers": containers, "total": len(containers)},
    )


# 인프라 점검만 여기 둔다. 활동(activity)·트래픽(access_log) 수집기는 이 모듈의
# CheckResult 를 재사용하므로, 여기서 그것들을 import 하면 순환이 된다.
# 전체 목록 조립은 두 쪽 모두를 import 하는 ops/state.py 가 맡는다.
INFRA_CHECKS = {
    "web": check_web,
    "postgres": check_postgres,
    "neo4j": check_neo4j,
    "docker": check_docker,
}
