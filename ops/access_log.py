"""앱 컨테이너의 uvicorn 액세스 로그에서 페이지별 트래픽을 뽑아낸다.

"어떤 사람이 어떤 페이지에서 신청을 넣었는가" 중 **페이지/요청** 쪽을 담당한다
(누가·무엇을은 ops/activity.py 가 DB 에서 가져온다).

앱에 미들웨어를 심는 대신 Docker 로그를 읽는 이유:
  - app/ 을 건드리지 않으므로 모니터링이 앱 배포 주기에 묶이지 않는다.
  - 앱 프로세스가 어떤 상태든 이미 찍힌 로그는 그대로 남아 있다.
한계도 분명하다 — uvicorn 기본 액세스 로그에는 응답 시간과 사용자 식별자가
없다. 그래서 경로·상태코드·건수까지만 얻고, 사용자 단위 활동은 DB 쪽에서 본다.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from ops.checks import CheckResult, _short
from ops.config import ops_settings as cfg

# 2026-08-17T06:24:00.123456789Z INFO:     172.21.0.1:36842 - "GET / HTTP/1.1" 303 See Other
_LINE = re.compile(
    r"^(?P<ts>\S+)\s+.*?\"(?P<method>[A-Z]+)\s+(?P<path>[^\"\s]+)\s+HTTP/[\d.]+\"\s+(?P<status>\d{3})"
)

# 경로에 섞인 식별자를 접어 넣는다. /api/incidents/12/comments 가 12, 13, 14 …
# 로 갈라지면 카디널리티가 폭발하고 어느 화면이 바쁜지 보이지 않는다.
_NUMERIC = re.compile(r"^\d+$")
_HEXISH = re.compile(r"^[0-9a-fA-F]{16,}$")

_MAX_LINES_PER_POLL = 5000
_RECENT_WINDOW = 2000

_lock = threading.Lock()
_totals: Counter[tuple[str, str, str]] = Counter()   # (method, path, status) -> 누적
_recent: deque[tuple[float, str, str, str]] = deque(maxlen=_RECENT_WINDOW)
_last_ts: datetime | None = None
_seen_at_last_ts: set[str] = set()


def normalize_path(path: str) -> str:
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 200:
        path = path[:200]
    segments = []
    for segment in path.split("/"):
        if _NUMERIC.match(segment) or _HEXISH.match(segment):
            segments.append("{id}")
        else:
            segments.append(segment)
    return "/".join(segments) or "/"


def _parse_ts(raw: str) -> datetime | None:
    # Docker 는 나노초까지 준다. datetime 은 마이크로초까지만 받으므로 잘라낸다.
    try:
        cleaned = raw.replace("Z", "+00:00")
        if "." in cleaned:
            head, tail = cleaned.split(".", 1)
            fraction, _, offset = tail.partition("+")
            cleaned = f"{head}.{fraction[:6]}+{offset}" if offset else f"{head}.{fraction[:6]}"
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def collect_access_log() -> CheckResult:
    global _last_ts, _seen_at_last_ts

    if not cfg.docker_enabled:
        return CheckResult("traffic", True, None, None, {"enabled": False})

    started = time.perf_counter()
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(cfg.app_container_name)
    except Exception as exc:
        return CheckResult("traffic", False, None, _short(exc), {"enabled": True})

    with _lock:
        # 첫 수집에서 로그 전체를 빨아들이면 누적 카운터가 한 번에 치솟아
        # 증가율 그래프가 망가진다. 최근 5분부터 시작한다.
        since = _last_ts or (datetime.now(timezone.utc) - timedelta(minutes=5))
        previous_ts = _last_ts
        seen = set(_seen_at_last_ts)

    try:
        raw = container.logs(
            since=since, timestamps=True, stdout=True, stderr=True
        ).decode("utf-8", "replace")
    except Exception as exc:
        return CheckResult("traffic", False, None, _short(exc), {"enabled": True})

    newest_ts = previous_ts
    newest_keys: set[str] = set(seen) if previous_ts else set()
    parsed = 0
    fresh: list[tuple[float, str, str, str]] = []

    for line in raw.splitlines()[:_MAX_LINES_PER_POLL]:
        match = _LINE.match(line)
        if not match:
            continue
        timestamp = _parse_ts(match["ts"])
        if timestamp is None:
            continue

        # since 는 초 단위로만 걸리므로 경계의 같은 초가 다시 딸려 온다.
        # 원본 줄을 키로 중복을 걸러야 카운트가 부풀지 않는다.
        if previous_ts and timestamp < previous_ts:
            continue
        if previous_ts and timestamp == previous_ts and line in seen:
            continue

        method = match["method"]
        path = normalize_path(match["path"])
        status = match["status"]
        fresh.append((timestamp.timestamp(), method, path, status))
        parsed += 1

        if newest_ts is None or timestamp > newest_ts:
            newest_ts = timestamp
            newest_keys = {line}
        elif timestamp == newest_ts:
            newest_keys.add(line)

    with _lock:
        for ts, method, path, status in fresh:
            _totals[(method, path, status)] += 1
            _recent.append((ts, method, path, status))
        _last_ts = newest_ts or since
        _seen_at_last_ts = newest_keys
        totals_snapshot = dict(_totals)
        recent_snapshot = list(_recent)

    cutoff_5m = time.time() - 300
    cutoff_1h = time.time() - 3600
    recent_5m = [r for r in recent_snapshot if r[0] >= cutoff_5m]
    recent_1h = [r for r in recent_snapshot if r[0] >= cutoff_1h]

    by_path = Counter(f"{m} {p}" for _, m, p, _ in recent_1h)
    by_status = Counter(s for *_, s in recent_1h)
    errors = [r for r in recent_1h if r[3][0] in "45"]

    latency = (time.perf_counter() - started) * 1000
    return CheckResult(
        "traffic",
        True,
        latency,
        None,
        {
            "enabled": True,
            "container": cfg.app_container_name,
            "new_lines": parsed,
            "requests_5m": len(recent_5m),
            "requests_1h": len(recent_1h),
            "errors_1h": len(errors),
            "top_paths_1h": by_path.most_common(12),
            "by_status_1h": dict(sorted(by_status.items())),
            "tracked_series": len(totals_snapshot),
            "window_size": len(recent_snapshot),
        },
    )


def totals() -> dict[tuple[str, str, str], int]:
    """Prometheus 카운터용 누적치. 프로세스 재시작 시 0 으로 돌아가는데,
    Prometheus 의 counter reset 처리가 이를 정상적으로 흡수한다."""
    with _lock:
        return dict(_totals)
