"""최신 스냅샷 보관과 백그라운드 폴러.

HTTP 요청이 들어올 때마다 Postgres/Neo4j/Docker 를 찌르면, 대시보드를 여러 개
열어두는 것만으로 감시 대상에 부하가 간다. 폴러 한 곳에서만 실제 점검을 하고
모든 요청은 마지막 스냅샷을 읽는다.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from ops import history
from ops.access_log import collect_access_log
from ops.activity import check_activity
from ops.checks import INFRA_CHECKS
from ops.config import ops_settings as cfg

# 인프라 점검 + 서비스 활동 + 요청 트래픽. 화면·지표·이력이 모두 이 순서를 따른다.
ALL_CHECKS = {
    **INFRA_CHECKS,
    "activity": check_activity,
    "traffic": collect_access_log,
}

logger = logging.getLogger("ops.state")

_lock = threading.Lock()
_snapshot: dict[str, Any] = {
    "ts": None,
    "results": {},
    "poll_duration_ms": None,
}


def get_snapshot() -> dict[str, Any]:
    with _lock:
        return _snapshot


def _set_snapshot(snapshot: dict[str, Any]) -> None:
    global _snapshot
    with _lock:
        _snapshot = snapshot


def scalar_gauges(results: dict[str, Any]) -> dict[str, float]:
    """SQLite 이력에 남길 라벨 없는 지표만 추린다.

    라벨이 붙은 지표(테이블별 행 수 등)까지 넣으면 이력 테이블이 카디널리티로
    터진다. 그쪽은 Prometheus 에 맡기고 여기엔 요약치만 둔다.
    """
    gauges: dict[str, float] = {}
    pg = results.get("postgres", {}).get("detail", {})
    if pg:
        gauges["pg_database_bytes"] = float(pg.get("db_bytes") or 0)
        gauges["pg_connections"] = float(pg.get("connections") or 0)
        if pg.get("cache_hit_ratio") is not None:
            gauges["pg_cache_hit_ratio"] = float(pg["cache_hit_ratio"])

    neo = results.get("neo4j", {}).get("detail", {})
    if neo:
        gauges["neo4j_nodes"] = float(neo.get("nodes") or 0)
        gauges["neo4j_relationships"] = float(neo.get("relationships") or 0)

    docker = results.get("docker", {}).get("detail", {})
    containers = docker.get("containers") or []
    if containers:
        gauges["docker_containers_running"] = float(
            sum(1 for c in containers if c["status"] == "running")
        )
        gauges["docker_containers_total"] = float(len(containers))

    act = results.get("activity", {}).get("detail", {})
    if act:
        for key in ("incidents_total", "incidents_24h", "sessions_active",
                    "users_active_24h", "events_24h"):
            if act.get(key) is not None:
                gauges[key] = float(act[key])

    traffic = results.get("traffic", {}).get("detail", {})
    if traffic.get("enabled"):
        gauges["requests_5m"] = float(traffic.get("requests_5m") or 0)
        gauges["requests_1h"] = float(traffic.get("requests_1h") or 0)
        gauges["errors_1h"] = float(traffic.get("errors_1h") or 0)

    return gauges


async def poll_once() -> dict[str, Any]:
    started = time.perf_counter()

    # 드라이버들이 전부 블로킹이라 스레드로 던진다. 병렬로 돌려야 한 대상의
    # 타임아웃(최대 5초)이 다른 대상의 점검을 밀어내지 않는다.
    names = list(ALL_CHECKS)
    outcomes = await asyncio.gather(
        *(asyncio.to_thread(ALL_CHECKS[name]) for name in names),
        return_exceptions=True,
    )

    results: dict[str, Any] = {}
    for name, outcome in zip(names, outcomes):
        if isinstance(outcome, BaseException):
            logger.exception("check %s raised", name, exc_info=outcome)
            results[name] = {
                "component": name,
                "ok": False,
                "latency_ms": None,
                "error": f"{type(outcome).__name__}: {outcome}",
                "detail": {},
            }
        else:
            results[name] = outcome.as_dict()

    snapshot = {
        "ts": time.time(),
        "results": results,
        "poll_duration_ms": (time.perf_counter() - started) * 1000,
    }
    _set_snapshot(snapshot)

    try:
        history.record(list(results.values()), scalar_gauges(results))
    except Exception:
        logger.exception("failed to record history")

    return snapshot


async def poller() -> None:
    prune_every = max(1, int(3600 / max(cfg.poll_interval_seconds, 1)))
    tick = 0
    while True:
        try:
            await poll_once()
            tick += 1
            if tick % prune_every == 0:
                await asyncio.to_thread(history.prune)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("poll cycle failed")
        await asyncio.sleep(cfg.poll_interval_seconds)
