"""폴링 결과의 시계열 보관.

Prometheus 를 같이 띄우긴 하지만, ops 서비스 혼자서도 최근 추이(스파크라인)를
그릴 수 있어야 한다. Prometheus 가 죽어도 대시보드가 반쪽이 되지 않도록
가벼운 SQLite 사본을 따로 남긴다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from ops.config import ops_settings as cfg

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts         REAL NOT NULL,
    component  TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    latency_ms REAL
);
CREATE INDEX IF NOT EXISTS samples_component_ts ON samples (component, ts);

CREATE TABLE IF NOT EXISTS gauges (
    ts    REAL NOT NULL,
    name  TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gauges_name_ts ON gauges (name, ts);
"""


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        directory = os.path.dirname(cfg.history_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        _conn = sqlite3.connect(cfg.history_path, check_same_thread=False)
        # 폴러 스레드가 쓰는 동안 HTTP 핸들러가 읽는다. WAL 이 아니면 서로 막는다.
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def record(results: list[dict[str, Any]], gauges: dict[str, float]) -> None:
    now = time.time()
    with _lock:
        conn = _connect()
        conn.executemany(
            "INSERT INTO samples (ts, component, ok, latency_ms) VALUES (?, ?, ?, ?)",
            [(now, r["component"], 1 if r["ok"] else 0, r["latency_ms"]) for r in results],
        )
        conn.executemany(
            "INSERT INTO gauges (ts, name, value) VALUES (?, ?, ?)",
            [(now, name, float(value)) for name, value in gauges.items()],
        )
        conn.commit()


def prune() -> None:
    cutoff = time.time() - cfg.history_retention_days * 86400
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM gauges WHERE ts < ?", (cutoff,))
        conn.commit()


def series(component: str, hours: int = 6, limit: int = 400) -> list[dict[str, Any]]:
    """최근 구간을 오래된 것부터 돌려준다(그래프가 왼쪽→오른쪽으로 흐르도록)."""
    since = time.time() - hours * 3600
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT ts, ok, latency_ms FROM samples "
            "WHERE component = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (component, since, limit),
        ).fetchall()
    return [{"ts": r[0], "ok": bool(r[1]), "latency_ms": r[2]} for r in reversed(rows)]


def gauge_series(name: str, hours: int = 24, limit: int = 400) -> list[dict[str, Any]]:
    since = time.time() - hours * 3600
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT ts, value FROM gauges WHERE name = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (name, since, limit),
        ).fetchall()
    return [{"ts": r[0], "value": r[1]} for r in reversed(rows)]


def uptime_ratio(component: str, hours: int = 24) -> float | None:
    since = time.time() - hours * 3600
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT count(*), sum(ok) FROM samples WHERE component = ? AND ts >= ?",
            (component, since),
        ).fetchone()
    total, up = row
    if not total:
        return None
    return (up or 0) / total


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
