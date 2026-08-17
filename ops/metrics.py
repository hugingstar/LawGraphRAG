"""마지막 스냅샷을 Prometheus 노출 포맷으로 변환한다.

Gauge 객체에 값을 미리 set 해 두는 대신 커스텀 컬렉터를 쓰는 이유: 라벨 값이
사라졌을 때(컨테이너 삭제, 라벨 없는 그래프로 리셋 등) 이전 시계열이 마지막
값으로 계속 남는 걸 피하려는 것이다. 매 스크레이프마다 현재 스냅샷만으로
계열을 새로 만든다.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from ops import access_log
from ops.state import get_snapshot

PREFIX = "lawgraphrag"


class SnapshotCollector:
    def collect(self):  # noqa: C901 - 지표 나열이라 분기가 많은 게 자연스럽다
        snapshot = get_snapshot()
        results = snapshot.get("results") or {}

        up = GaugeMetricFamily(
            f"{PREFIX}_up", "구성요소 정상 여부(1=정상)", labels=["component"]
        )
        latency = GaugeMetricFamily(
            f"{PREFIX}_check_latency_ms", "점검 왕복 시간(ms)", labels=["component"]
        )
        for name, result in results.items():
            up.add_metric([name], 1 if result.get("ok") else 0)
            if result.get("latency_ms") is not None:
                latency.add_metric([name], result["latency_ms"])
        yield up
        yield latency

        age = GaugeMetricFamily(
            f"{PREFIX}_snapshot_age_seconds", "마지막 폴링 이후 경과 시간"
        )
        if snapshot.get("ts"):
            import time

            age.add_metric([], time.time() - snapshot["ts"])
            yield age

        # --- Postgres ---
        pg = (results.get("postgres") or {}).get("detail") or {}
        if pg:
            yield _simple(f"{PREFIX}_pg_database_bytes", "DB 크기(바이트)", pg.get("db_bytes"))
            yield _simple(f"{PREFIX}_pg_connections", "현재 연결 수", pg.get("connections"))
            yield _simple(
                f"{PREFIX}_pg_max_connections", "max_connections", pg.get("max_connections")
            )
            yield _simple(
                f"{PREFIX}_pg_cache_hit_ratio", "버퍼 캐시 적중률", pg.get("cache_hit_ratio")
            )
            yield _simple(
                f"{PREFIX}_pg_long_running_queries",
                "30초 이상 실행 중인 쿼리 수",
                pg.get("long_running_queries"),
            )
            yield _simple(f"{PREFIX}_pg_deadlocks_total", "누적 데드락", pg.get("deadlocks"))

            rows = GaugeMetricFamily(
                f"{PREFIX}_pg_table_rows", "테이블별 추정 행 수", labels=["table"]
            )
            size = GaugeMetricFamily(
                f"{PREFIX}_pg_table_bytes", "테이블별 총 크기(인덱스 포함)", labels=["table"]
            )
            for table in pg.get("tables") or []:
                rows.add_metric([table["name"]], table["rows"] or 0)
                size.add_metric([table["name"]], table["bytes"] or 0)
            yield rows
            yield size

        # --- Neo4j ---
        neo = (results.get("neo4j") or {}).get("detail") or {}
        if neo:
            yield _simple(f"{PREFIX}_neo4j_nodes", "전체 노드 수", neo.get("nodes"))
            yield _simple(
                f"{PREFIX}_neo4j_relationships", "전체 관계 수", neo.get("relationships")
            )

            by_label = GaugeMetricFamily(
                f"{PREFIX}_neo4j_nodes_by_label", "라벨별 노드 수", labels=["label"]
            )
            for label, count in (neo.get("nodes_by_label") or {}).items():
                by_label.add_metric([label], count)
            yield by_label

            by_type = GaugeMetricFamily(
                f"{PREFIX}_neo4j_relationships_by_type", "타입별 관계 수", labels=["type"]
            )
            for rel_type, count in (neo.get("relationships_by_type") or {}).items():
                by_type.add_metric([rel_type], count)
            yield by_type

        # --- Docker ---
        docker = (results.get("docker") or {}).get("detail") or {}
        containers = docker.get("containers") or []
        if containers:
            running = GaugeMetricFamily(
                f"{PREFIX}_container_up",
                "컨테이너 실행 여부(1=running)",
                labels=["name", "status", "health"],
            )
            restarts = GaugeMetricFamily(
                f"{PREFIX}_container_restarts", "컨테이너 재시작 횟수", labels=["name"]
            )
            for container in containers:
                running.add_metric(
                    [container["name"], container["status"], container["health"] or "none"],
                    1 if container["status"] == "running" else 0,
                )
                restarts.add_metric([container["name"]], container["restart_count"] or 0)
            yield running
            yield restarts

        # --- 서비스 활동 (누가 무엇을 신청/처리했는가) ---
        act = (results.get("activity") or {}).get("detail") or {}
        if act:
            for key, doc in [
                ("incidents_total", "누적 신청 건수"),
                ("incidents_1h", "최근 1시간 신청"),
                ("incidents_24h", "최근 24시간 신청"),
                ("incidents_7d", "최근 7일 신청"),
                ("events_1h", "최근 1시간 처리 이벤트"),
                ("events_24h", "최근 24시간 처리 이벤트"),
                ("comments_24h", "최근 24시간 코멘트"),
                ("users_total", "전체 사용자"),
                ("sessions_active", "유효한 로그인 세션"),
                ("users_active_24h", "24시간 내 로그인 사용자"),
                ("law_selections", "사용자별 법령 선택 총계"),
            ]:
                yield _simple(f"{PREFIX}_{key}", doc, act.get(key))

            by_status = GaugeMetricFamily(
                f"{PREFIX}_incidents_by_status", "상태별 신청 건수", labels=["status"]
            )
            for status_name, count in (act.get("incidents_by_status") or {}).items():
                by_status.add_metric([status_name], count)
            yield by_status

        # --- 요청 트래픽 (어떤 페이지가 얼마나 호출됐는가) ---
        traffic = (results.get("traffic") or {}).get("detail") or {}
        if traffic.get("enabled"):
            yield _simple(f"{PREFIX}_requests_5m", "최근 5분 요청 수", traffic.get("requests_5m"))
            yield _simple(f"{PREFIX}_requests_1h", "최근 1시간 요청 수", traffic.get("requests_1h"))
            yield _simple(f"{PREFIX}_errors_1h", "최근 1시간 4xx/5xx 응답", traffic.get("errors_1h"))

            # 경로·상태코드별 누적 카운터. rate() 로 초당 요청 수를 뽑을 수 있다.
            requests = CounterMetricFamily(
                f"{PREFIX}_http_requests",
                "경로·상태코드별 누적 요청 수",
                labels=["method", "path", "status"],
            )
            for (method, path, status), count in access_log.totals().items():
                requests.add_metric([method, path, status], count)
            yield requests


def _simple(name: str, documentation: str, value) -> GaugeMetricFamily:
    metric = GaugeMetricFamily(name, documentation)
    if value is not None:
        metric.add_metric([], float(value))
    return metric


registry = CollectorRegistry()
registry.register(SnapshotCollector())
