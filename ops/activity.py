"""서비스 활동(비즈니스 레벨) 수집.

인프라 점검이 "서버가 살아 있는가"라면, 여기는 "사람들이 실제로 쓰고 있는가"다.
사고 신청이 몇 건 들어왔는지, 누가 처리했는지, 로그인 세션이 몇 개인지.

앱 코드에 계측을 심지 않고 DB 를 읽어서 얻는다. app/ 에 미들웨어를 넣으면
모니터링이 앱 배포에 묶여 버리고, 앱이 죽으면 지표도 같이 사라진다.

개인정보는 담지 않는다. incidents 의 reporter_name / reporter_contact /
reporter_info 는 신고자 개인정보라 모니터링 화면에 올릴 이유가 없다. 행위자
식별은 운영자 계정(username)까지만 한다.
"""

from __future__ import annotations

import time
from typing import Any

import psycopg

from ops.checks import CheckResult, _short
from ops.config import ops_settings as cfg

# 신청 접수 현황. 상태별 분포는 적체를 바로 드러낸다.
_INCIDENTS_BY_STATUS = """
SELECT coalesce(status, 'unknown') AS status, count(*) AS n
FROM incidents GROUP BY 1 ORDER BY 2 DESC
"""

_INCIDENT_RATES = """
SELECT
    count(*) FILTER (WHERE created_at > now() - interval '1 hour')  AS h1,
    count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS h24,
    count(*) FILTER (WHERE created_at > now() - interval '7 days')   AS d7,
    count(*)                                                          AS total,
    max(created_at)                                                   AS latest
FROM incidents
"""

_EVENT_RATES = """
SELECT
    count(*) FILTER (WHERE created_at > now() - interval '1 hour')  AS h1,
    count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS h24,
    count(*)                                                          AS total
FROM incident_events
"""

_COMMENT_RATES = """
SELECT
    count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS h24,
    count(*)                                                          AS total
FROM incident_comments
"""

_USERS = """
SELECT
    (SELECT count(*) FROM users)                                            AS users_total,
    (SELECT count(*) FROM user_sessions WHERE expires_at > now())           AS sessions_active,
    (SELECT count(DISTINCT user_id) FROM user_sessions
      WHERE created_at > now() - interval '24 hours')                       AS users_active_24h,
    (SELECT count(*) FROM user_law_selections)                              AS law_selections
"""

# 최근 접수 목록. 신고자 개인정보 컬럼은 의도적으로 제외했다.
_RECENT_INCIDENTS = """
SELECT i.id, i.status, i.created_at, i.sido_code, i.sigungu_code, u.username
FROM incidents i
LEFT JOIN users u ON u.id = i.created_by_user_id
ORDER BY i.created_at DESC NULLS LAST
LIMIT 10
"""

# "누가 무엇을 했는가" 타임라인.
_RECENT_EVENTS = """
SELECT e.id, e.incident_id, e.status, e.created_at, u.username
FROM incident_events e
LEFT JOIN users u ON u.id = e.actor_user_id
ORDER BY e.created_at DESC NULLS LAST
LIMIT 10
"""


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def check_activity() -> CheckResult:
    started = time.perf_counter()
    try:
        with psycopg.connect(cfg.postgres_dsn, connect_timeout=cfg.postgres_timeout_seconds) as conn:
            with conn.cursor() as cur:
                cur.execute(_INCIDENTS_BY_STATUS)
                by_status = {row[0]: row[1] for row in cur.fetchall()}

                cur.execute(_INCIDENT_RATES)
                inc_h1, inc_h24, inc_d7, inc_total, inc_latest = cur.fetchone()

                cur.execute(_EVENT_RATES)
                ev_h1, ev_h24, ev_total = cur.fetchone()

                cur.execute(_COMMENT_RATES)
                cm_h24, cm_total = cur.fetchone()

                cur.execute(_USERS)
                users_total, sessions_active, users_active_24h, law_selections = cur.fetchone()

                cur.execute(_RECENT_INCIDENTS)
                recent_incidents = [
                    {
                        "id": r[0],
                        "status": r[1],
                        "created_at": _iso(r[2]),
                        "region": " ".join(x for x in (r[3], r[4]) if x) or None,
                        "username": r[5],
                    }
                    for r in cur.fetchall()
                ]

                cur.execute(_RECENT_EVENTS)
                recent_events = [
                    {
                        "id": r[0],
                        "incident_id": r[1],
                        "status": r[2],
                        "created_at": _iso(r[3]),
                        "username": r[4],
                    }
                    for r in cur.fetchall()
                ]
    except Exception as exc:
        return CheckResult("activity", False, None, _short(exc))

    latency = (time.perf_counter() - started) * 1000
    return CheckResult(
        "activity",
        True,
        latency,
        None,
        {
            "incidents_total": inc_total,
            "incidents_1h": inc_h1,
            "incidents_24h": inc_h24,
            "incidents_7d": inc_d7,
            "incidents_latest": _iso(inc_latest),
            "incidents_by_status": by_status,
            "events_1h": ev_h1,
            "events_24h": ev_h24,
            "events_total": ev_total,
            "comments_24h": cm_h24,
            "comments_total": cm_total,
            "users_total": users_total,
            "sessions_active": sessions_active,
            "users_active_24h": users_active_24h,
            "law_selections": law_selections,
            "recent_incidents": recent_incidents,
            "recent_events": recent_events,
        },
    )
