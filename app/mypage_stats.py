"""마이페이지 통계 — 한 사람의 '요청 측'과 '검토 측' 활동을 같은 형식으로 산출한다.

이 앱에서 역할(requester/manager)은 설정에서 언제든 바꿀 수 있는 값이라, 한 계정이
양쪽 기록을 모두 갖는 경우가 흔하다. 그래서 마이페이지는 "지금 무슨 역할인가"를 보지 않고
두 측면을 항상 나란히 계산한다 — 자기가 낸 요청이 얼마나 성실했는지와, 자기가 답한 검토가
얼마나 충실했는지를 같은 잣대(0~100점)로 마주 보게 하는 것이 이 화면의 목적이다.

점수는 등수를 매기기 위한 것이 아니라 다음에 무엇을 더 쓰면 좋을지 알려주기 위한 것이라,
각 지표는 '왜 깎였는지'와 '어떻게 올리는지'를 함께 반환한다(advice).
"""

import datetime

from sqlalchemy.orm import Session

from app.models import (
    Incident,
    IncidentAttachment,
    IncidentComment,
    IncidentEvent,
    IncidentCategory,
    User,
)

# 잔디(활동 그래프)는 사람이 "내가 언제 활동했나"를 세는 감각과 맞아야 하므로 한국 시간
# 기준으로 날짜를 나눈다. DB의 timestamptz는 UTC라 그대로 쓰면 밤 9시 이후 활동이
# 다음 날 칸에 찍힌다.
KST = datetime.timezone(datetime.timedelta(hours=9))

# 잔디 표시 기간. 1년(53주)은 2단 레이아웃의 한 칸 폭에 들어가지 않아 가로 스크롤이
# 생기므로, 두 측면을 한눈에 비교할 수 있는 26주(약 6개월)로 자른다.
GRASS_WEEKS = 26

# 신고서에서 '채웠는지'를 세는 항목. 경위만 필수이고 나머지는 선택이라, 이 비율이 곧
# 요청자가 사건을 얼마나 구조적으로 진술했는지를 나타낸다.
STATEMENT_FIELDS = ("occurred_at", "location", "background", "situation", "action_taken", "damage")

# 지표 하나당 만점. 네 지표 x 25점 = 100점.
METRIC_MAX = 25.0

# 서술 분량 만점 기준(자). 이 이상 쓴다고 더 좋은 건 아니므로 여기서 만점 처리한다.
STATEMENT_FULL_LEN = 700
CONCLUSION_FULL_LEN = 500
# 담당 사건 1건당 이 정도 메시지를 주고받으면 소통이 충분하다고 본다.
MESSAGES_PER_INCIDENT_FULL = 3.0

MANAGER_COMMENT_KINDS = ("comment", "supplement_request", "conclusion")
REQUESTER_COMMENT_KINDS = ("supplement_reply", "follow_up")

# 등급은 품질 점수가 아니라 '최근 한 달에 얼마나 했는가'로 매긴다 — 점수는 한 건을 얼마나
# 잘 썼는지를 보고, 등급은 꾸준히 하고 있는지를 본다. 둘을 한 숫자로 섞으면 어느 쪽을
# 고쳐야 할지가 흐려진다.
RECENT_WINDOW_DAYS = 30

# 각 원소: (기준 건수, 이름, 이모지, 한 줄 설명). 반드시 기준 건수 오름차순.
REQUESTER_TIERS = (
    (0, "뾰족 밤송이", "🌰", "아직 웅크린 상태입니다."),
    (1, "기웃기웃 도치", "🦔", "조심스럽게 첫발을 뗐습니다."),
    (5, "우당탕탕 도치", "💥", "사건이 제법 몰아치는 한 달입니다."),
    (7, "사건 자석 도치", "🧲", "웬만한 사건은 다 끌어당기고 있습니다."),
    (10, "혼돈의 밤송이", "🌪️", "한 달에 10건 이상 — 가시를 세운 폭풍입니다."),
)

MANAGER_TIERS = (
    (0, "부엉이알", "🥚", "아직 알 속입니다."),
    (25, "아기부엉이", "🐣", "이제 막 날개를 폈습니다."),
    (50, "소부엉이", "🐦", "한 달 50건 — 손에 익었습니다."),
    (75, "중부엉이", "🦉", "검토실의 기둥입니다."),
    (100, "대부엉이", "👑", "한 달 100건 이상 — 대부엉이입니다."),
)


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    """DB에서 온 값이 어떤 이유로든 naive면 UTC로 간주한다(시간 차 계산이 터지지 않도록)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def _kst_date(dt: datetime.datetime | None) -> datetime.date | None:
    aware = _aware(dt)
    return aware.astimezone(KST).date() if aware else None


def _hours_between(start: datetime.datetime, end: datetime.datetime) -> float:
    return (_aware(end) - _aware(start)).total_seconds() / 3600


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(part: float, whole: float) -> float:
    """0으로 나누는 경우(기록이 아직 없는 계정)를 0으로 눕힌다."""
    return part / whole if whole else 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _duration_label(hours: float | None) -> str:
    if hours is None:
        return "-"
    if hours < 1:
        return f"{int(round(hours * 60))}분"
    if hours < 48:
        return f"{hours:.1f}시간"
    return f"{hours / 24:.1f}일"


def _speed_ratio(hours: float | None, best: float, worst: float) -> float:
    """best 시간 안에 하면 만점, worst를 넘으면 0점, 그 사이는 선형.

    '빠를수록 좋다'를 점수로 바꿀 때 로그 스케일도 생각할 수 있지만, 화면에서
    "24시간 안에 착수하면 만점"처럼 사용자에게 그대로 설명할 수 있는 선형이 낫다.
    """
    if hours is None:
        return 0.0
    if hours <= best:
        return 1.0
    if hours >= worst:
        return 0.0
    return (worst - hours) / (worst - best)


def _metric(label: str, ratio: float, detail: str, tip: str) -> dict:
    ratio = _clamp01(ratio)
    return {
        "label": label,
        "ratio": ratio,
        "points": round(ratio * METRIC_MAX, 1),
        "max_points": METRIC_MAX,
        "detail": detail,
        "tip": tip,
    }


def _tier_for(count: int, tiers: tuple) -> dict:
    """최근 한 달 활동 건수로 등급과 '다음 등급까지 몇 건'을 낸다."""
    current = tiers[0]
    for tier in tiers:
        if count >= tier[0]:
            current = tier
    following = next((t for t in tiers if t[0] > current[0]), None)

    # 진행 막대는 현재 등급 문턱 -> 다음 등급 문턱 사이에서의 위치다(최고 등급이면 가득).
    if following:
        span = following[0] - current[0]
        progress = _clamp01((count - current[0]) / span) if span else 1.0
    else:
        progress = 1.0

    return {
        "label": current[1],
        "emoji": current[2],
        "message": current[3],
        "min_count": current[0],
        "next_label": following[1] if following else None,
        "next_emoji": following[2] if following else None,
        "next_min": following[0] if following else None,
        "remaining": max(0, following[0] - count) if following else 0,
        "progress": progress,
    }


def _ladder_for(count: int, tiers: tuple, current_label: str) -> list[dict]:
    """등급표용 행 목록. 높은 등급이 위로 오도록 내림차순으로 뒤집는다."""
    return [
        {
            "label": label,
            "emoji": emoji,
            "min_count": minimum,
            "threshold": f"{minimum}건 이상",
            "reached": count >= minimum,
            "current": label == current_label,
        }
        for minimum, label, emoji, _message in reversed(tiers)
    ]


def _build_grass(dates: list[datetime.date], today: datetime.date) -> dict:
    """GitHub 잔디와 같은 배치(세로 7칸 = 일~토, 가로 = 주)로 쓸 일별 활동 배열.

    시작일을 항상 일요일로 맞춰 두면 프런트는 날짜순으로 칸을 흘려 넣기만 해도
    요일 행이 저절로 맞는다(grid-auto-flow: column).
    """
    # weekday(): 월=0..일=6 -> 이번 주 토요일까지 채운다(일요일 시작 열 정렬).
    days_to_saturday = (5 - today.weekday()) % 7
    grid_end = today + datetime.timedelta(days=days_to_saturday)
    grid_start = grid_end - datetime.timedelta(days=GRASS_WEEKS * 7 - 1)

    counts: dict[datetime.date, int] = {}
    for d in dates:
        if d is not None:
            counts[d] = counts.get(d, 0) + 1

    cells = []
    for offset in range(GRASS_WEEKS * 7):
        day = grid_start + datetime.timedelta(days=offset)
        count = counts.get(day, 0)
        if count == 0:
            level = 0
        elif count <= 2:
            level = 1
        elif count <= 4:
            level = 2
        elif count <= 7:
            level = 3
        else:
            level = 4
        cells.append(
            {
                "date": day.isoformat(),
                "count": count,
                "level": level,
                # 아직 오지 않은 날(이번 주의 남은 칸)은 빈 칸과 구분해 옅게 그린다.
                "future": day > today,
            }
        )

    # 연속 활동일(스트릭)은 오늘(또는 어제까지)부터 거꾸로 센다 — 오늘 아직 활동이 없다고
    # 어제까지 이어온 기록이 0으로 보이면 억울하므로 어제부터 시작하는 것도 인정한다.
    def streak_from(day: datetime.date) -> int:
        length = 0
        while counts.get(day):
            length += 1
            day -= datetime.timedelta(days=1)
        return length

    current_streak = max(streak_from(today), streak_from(today - datetime.timedelta(days=1)))

    best_streak = 0
    run = 0
    previous: datetime.date | None = None
    for day in sorted(counts):
        run = run + 1 if previous and (day - previous).days == 1 else 1
        best_streak = max(best_streak, run)
        previous = day

    window_total = sum(c["count"] for c in cells)
    return {
        "cells": cells,
        "weeks": GRASS_WEEKS,
        "start": grid_start.isoformat(),
        "end": grid_end.isoformat(),
        "window_total": window_total,
        "active_days": sum(1 for c in cells if c["count"]),
        "current_streak": current_streak,
        "best_streak": best_streak,
        "busiest": max((c["count"] for c in cells), default=0),
    }


def _advice_from(metrics: list[dict], has_data: bool, empty_message: str) -> list[str]:
    """가장 낮은 지표 순으로 최대 3개의 조언을 뽑는다. 다 좋으면 칭찬 한 줄."""
    if not has_data:
        return [empty_message]
    weak = sorted((m for m in metrics if m["ratio"] < 0.75), key=lambda m: m["ratio"])[:3]
    if not weak:
        return ["네 지표 모두 안정적입니다. 지금 방식을 유지하세요."]
    return [f"{m['label']} — {m['tip']}" for m in weak]


def _side(
    *,
    key: str,
    title: str,
    subtitle: str,
    tiers: tuple,
    recent_count: int,
    recent_label: str,
    headline: list[dict],
    counts: list[dict],
    metrics: list[dict],
    facts: list[dict],
    grass: dict,
    has_data: bool,
    empty_message: str,
    empty_cta: dict | None,
    note: dict | None = None,
) -> dict:
    score = round(sum(m["points"] for m in metrics), 1) if has_data else 0.0
    tier = _tier_for(recent_count, tiers)
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "score": score,
        "tier": tier,
        "ladder": _ladder_for(recent_count, tiers, tier["label"]),
        "recent_count": recent_count,
        "recent_label": recent_label,
        "recent_days": RECENT_WINDOW_DAYS,
        "note": note,
        "headline": headline,
        "counts": counts,
        "metrics": metrics,
        "facts": facts,
        "grass": grass,
        "has_data": has_data,
        "empty_message": empty_message,
        "empty_cta": empty_cta,
        "advice": _advice_from(metrics, has_data, empty_message),
    }


def _requester_side(session: Session, user_id: int, today: datetime.date, cutoff: datetime.datetime) -> dict:
    incidents = (
        session.query(Incident)
        .filter(Incident.created_by_user_id == user_id)
        .order_by(Incident.created_at)
        .all()
    )
    incident_ids = [i.id for i in incidents]

    # 내가 올린 사건의 스레드 전체(담당자가 남긴 것 포함) — 보완 요청을 몇 번 받았는지,
    # 거기에 얼마나 빨리 답했는지를 보려면 상대방 메시지도 필요하다.
    thread: dict[int, list[IncidentComment]] = {}
    if incident_ids:
        for comment in (
            session.query(IncidentComment)
            .filter(IncidentComment.incident_id.in_(incident_ids))
            .order_by(IncidentComment.created_at)
            .all()
        ):
            thread.setdefault(comment.incident_id, []).append(comment)

    attached_incident_ids = set()
    if incident_ids:
        attached_incident_ids = {
            row[0]
            for row in session.query(IncidentAttachment.incident_id)
            .filter(
                IncidentAttachment.incident_id.in_(incident_ids),
                IncidentAttachment.uploaded_by_user_id == user_id,
            )
            .distinct()
            .all()
        }

    drafts = [i for i in incidents if i.status == "draft"]
    submitted = [i for i in incidents if i.status != "draft"]
    completed = [i for i in submitted if i.status == "completed"]
    in_progress = [i for i in submitted if i.status != "completed"]
    submitted_count = len(submitted)

    # --- 지표 1: 기재 충실도 ---
    filled_ratios = []
    for incident in submitted:
        filled = sum(1 for field in STATEMENT_FIELDS if getattr(incident, field, None))
        filled_ratios.append(filled / len(STATEMENT_FIELDS))
    fill_ratio = _avg(filled_ratios) or 0.0

    # --- 지표 2: 서술 구체성 ---
    avg_len = _avg([len(i.statement or "") for i in submitted]) or 0.0
    length_ratio = _clamp01(avg_len / STATEMENT_FULL_LEN)

    # --- 지표 3: 근거 자료 첨부 ---
    with_attachment = sum(1 for i in submitted if i.id in attached_incident_ids)
    attach_ratio = _ratio(with_attachment, submitted_count)

    # --- 지표 4: 보완 없이 통과 ---
    supplement_requests = 0
    supplement_asked_incidents = 0
    reply_hours: list[float] = []
    my_replies = 0
    my_follow_ups = 0
    for incident in submitted:
        comments = thread.get(incident.id, [])
        asked = [c for c in comments if c.kind == "supplement_request"]
        supplement_requests += len(asked)
        if asked:
            supplement_asked_incidents += 1
        for request in asked:
            reply = next(
                (
                    c
                    for c in comments
                    if c.kind == "supplement_reply"
                    and c.author_user_id == user_id
                    and _aware(c.created_at) > _aware(request.created_at)
                ),
                None,
            )
            if reply:
                reply_hours.append(_hours_between(request.created_at, reply.created_at))
        my_replies += sum(1 for c in comments if c.kind == "supplement_reply" and c.author_user_id == user_id)
        my_follow_ups += sum(1 for c in comments if c.kind == "follow_up" and c.author_user_id == user_id)

    clean_ratio = 1.0 - _ratio(supplement_asked_incidents, submitted_count) if submitted_count else 0.0

    metrics = [
        _metric(
            "신고서 기재 충실도",
            fill_ratio,
            f"{len(STATEMENT_FIELDS)}개 항목 중 평균 {fill_ratio * len(STATEMENT_FIELDS):.1f}개 작성",
            "사고일시·장소·조치내용처럼 비워둔 항목이 곧 담당자가 되묻는 항목입니다. 모르면 '확인 불가'라도 적어두세요.",
        ),
        _metric(
            "서술 구체성",
            length_ratio,
            f"요청 1건 평균 {int(avg_len)}자 (기준 {STATEMENT_FULL_LEN}자)",
            f"육하원칙으로 {STATEMENT_FULL_LEN}자 정도를 채우면 조문 검색이 훨씬 정확한 후보를 찾습니다.",
        ),
        _metric(
            "근거 자료 첨부",
            attach_ratio,
            f"{submitted_count}건 중 {with_attachment}건에 자료 첨부",
            "사진·진단서·계약서 같은 자료 한 장이 문장 열 줄보다 강한 근거가 됩니다.",
        ),
        _metric(
            "보완 없이 통과",
            clean_ratio,
            f"{submitted_count}건 중 {supplement_asked_incidents}건이 보완 요청을 받음",
            "처음 낼 때 담당자가 물어볼 만한 것을 미리 적어두면 검토가 며칠 앞당겨집니다.",
        ),
    ]

    citation_avg = _avg([len(i.citations or []) for i in submitted])
    completion_hours = [
        _hours_between(i.created_at, i.updated_at) for i in completed if i.created_at and i.updated_at
    ]
    last_submitted = max((_kst_date(i.created_at) for i in submitted), default=None)

    facts = [
        {"label": "자동 인용 조문", "value": f"요청당 평균 {citation_avg:.1f}개" if citation_avg is not None else "-"},
        {
            "label": "보완 요청 응답률",
            "value": f"{_clamp01(_ratio(my_replies, supplement_requests)) * 100:.0f}%" if supplement_requests else "받은 적 없음",
        },
        {"label": "평균 보완 응답 시간", "value": _duration_label(_avg(reply_hours))},
        {"label": "요청 후 검토 완료까지", "value": _duration_label(_avg(completion_hours))},
        {"label": "추가 문의", "value": f"{my_follow_ups}회"},
        {"label": "마지막 요청일", "value": last_submitted.isoformat() if last_submitted else "-"},
    ]

    # 잔디: 요청 접수일 + 내가 스레드에 남긴 메시지(보완 내용·추가 문의)일
    grass_dates = [_kst_date(i.created_at) for i in submitted]
    for comments in thread.values():
        grass_dates += [
            _kst_date(c.created_at)
            for c in comments
            if c.author_user_id == user_id and c.kind in REQUESTER_COMMENT_KINDS
        ]

    recent_count = sum(1 for i in submitted if _aware(i.created_at) >= cutoff)

    return _side(
        key="requester",
        title="요청 측 — 신청자",
        subtitle="내가 올린 사건이 얼마나 잘 전달됐는지",
        tiers=REQUESTER_TIERS,
        recent_count=recent_count,
        recent_label=f"최근 {RECENT_WINDOW_DAYS}일 접수한 요청",
        headline=[
            {"label": "요청한 횟수", "value": submitted_count, "unit": "건"},
            {"label": "검토 완료", "value": len(completed), "unit": "건"},
        ],
        counts=[
            {"label": "진행중", "value": len(in_progress)},
            {"label": "임시 저장", "value": len(drafts)},
            {"label": "보완 응답", "value": my_replies},
            {"label": "첨부 자료", "value": len(attached_incident_ids)},
        ],
        metrics=metrics,
        facts=facts,
        grass=_build_grass([d for d in grass_dates if d], today),
        has_data=submitted_count > 0,
        empty_message="아직 접수한 요청이 없습니다. 첫 요청을 올리면 여기에 품질 점수가 쌓입니다.",
        empty_cta={"label": "요청 작성하러 가기", "href": "/request"},
    )


def _manager_side(
    session: Session, user_id: int, today: datetime.date, cutoff: datetime.datetime, role: str
) -> dict:
    assigned = (
        session.query(Incident)
        .filter(Incident.assigned_manager_id == user_id)
        .order_by(Incident.created_at)
        .all()
    )
    assigned_ids = [i.id for i in assigned]

    my_comments = (
        session.query(IncidentComment)
        .filter(
            IncidentComment.author_user_id == user_id,
            IncidentComment.kind.in_(MANAGER_COMMENT_KINDS),
        )
        .order_by(IncidentComment.created_at)
        .all()
    )
    my_events = (
        session.query(IncidentEvent)
        .filter(IncidentEvent.actor_user_id == user_id)
        .order_by(IncidentEvent.created_at)
        .all()
    )

    conclusions = [c for c in my_comments if c.kind == "conclusion"]
    supplement_requests = [c for c in my_comments if c.kind == "supplement_request"]
    plain_comments = [c for c in my_comments if c.kind == "comment"]

    completed = [i for i in assigned if i.status == "completed"]
    in_progress = [i for i in assigned if i.status != "completed"]
    assigned_count = len(assigned)

    # 사건별 '내가 착수한 시각' / '내가 완료 처리한 시각'. 상태 이벤트는 append-only라
    # 같은 상태가 여러 번 찍힐 수 있으므로 가장 이른 것을 쓴다.
    started_at: dict[int, datetime.datetime] = {}
    finished_at: dict[int, datetime.datetime] = {}
    for event in my_events:
        if event.status == "in_review":
            started_at.setdefault(event.incident_id, event.created_at)
        elif event.status == "completed":
            finished_at.setdefault(event.incident_id, event.created_at)

    pickup_hours = [
        _hours_between(i.created_at, started_at[i.id]) for i in assigned if i.id in started_at and i.created_at
    ]
    handling_hours = [
        _hours_between(started_at[i.id], finished_at[i.id])
        for i in assigned
        if i.id in started_at and i.id in finished_at
    ]

    # --- 지표 1: 검토 완결률 ---
    completion_ratio = _ratio(len(completed), assigned_count)

    # --- 지표 2: 착수 속도 (요청 접수 -> 검토 시작) ---
    avg_pickup = _avg(pickup_hours)
    pickup_ratio = _speed_ratio(avg_pickup, best=24.0, worst=168.0)

    # --- 지표 3: 검토 의견 충실도 ---
    avg_conclusion_len = _avg([len(c.body or "") for c in conclusions]) or 0.0
    conclusion_ratio = _clamp01(avg_conclusion_len / CONCLUSION_FULL_LEN)

    # --- 지표 4: 소통 밀도 ---
    messages_per_incident = _ratio(len(my_comments), assigned_count)
    communication_ratio = _clamp01(messages_per_incident / MESSAGES_PER_INCIDENT_FULL)

    metrics = [
        _metric(
            "검토 완결률",
            completion_ratio,
            f"담당 {assigned_count}건 중 {len(completed)}건 완료",
            "손에 쥔 채 멈춰 있는 건이 있는지 확인하세요. 최종 결과를 남겨야 요청자의 절차가 끝납니다.",
        ),
        _metric(
            "착수 속도",
            pickup_ratio,
            f"접수 후 평균 {_duration_label(avg_pickup)} 만에 검토 시작 (24시간 이내 만점)",
            "먼저 '검토 시작'만 눌러도 요청자는 방치되지 않았다는 것을 압니다.",
        ),
        _metric(
            "검토 의견 충실도",
            conclusion_ratio,
            f"최종 결과 평균 {int(avg_conclusion_len)}자 (기준 {CONCLUSION_FULL_LEN}자)",
            f"결론만 적지 말고 근거 조문과 그 조문을 고른 이유까지 {CONCLUSION_FULL_LEN}자 정도로 남기세요.",
        ),
        _metric(
            "소통 밀도",
            communication_ratio,
            f"담당 1건당 평균 {messages_per_incident:.1f}개 메시지 (기준 {MESSAGES_PER_INCIDENT_FULL:.0f}개)",
            "중간 코멘트 한 줄이 불필요한 보완 요청을 대신하는 경우가 많습니다.",
        ),
    ]

    # 어떤 유형·지역을 주로 봤는지 — 자기 전문 영역이 어디로 쏠려 있는지 자각하게 한다.
    category_names = {
        row.id: row.name for row in session.query(IncidentCategory).all()
    }
    category_counts: dict[str, int] = {}
    for incident in assigned:
        name = category_names.get(incident.category_id, "미분류")
        category_counts[name] = category_counts.get(name, 0) + 1
    top_category = max(category_counts.items(), key=lambda kv: kv[1], default=None)
    region_count = len({i.sigungu_code for i in assigned if i.sigungu_code})
    last_action = max((_kst_date(c.created_at) for c in my_comments), default=None)

    facts = [
        {"label": "평균 검토 소요", "value": _duration_label(_avg(handling_hours))},
        {"label": "남긴 코멘트", "value": f"{len(plain_comments)}회"},
        {"label": "보완 요청", "value": f"{len(supplement_requests)}회"},
        {"label": "최다 검토 유형", "value": f"{top_category[0]} {top_category[1]}건" if top_category else "-"},
        {"label": "검토한 지역", "value": f"{region_count}개 시·군·구"},
        {"label": "마지막 검토 활동", "value": last_action.isoformat() if last_action else "-"},
    ]

    grass_dates = [_kst_date(c.created_at) for c in my_comments]
    # '검토 시작'은 코멘트를 남기지 않는 행동이라 이벤트에서 따로 세어준다(잔디에서 빠지면
    # 실제로 일한 날이 빈칸으로 남는다).
    grass_dates += [_kst_date(dt) for dt in started_at.values()]

    # 등급은 '최근 한 달에 답해준 검토'로 센다 — 맡아만 두고 결론을 안 낸 건은 요청자 입장에서
    # 아직 답을 못 받은 것이므로 세지 않는다.
    recent_count = sum(1 for c in conclusions if _aware(c.created_at) >= cutoff)

    # 역할은 설정에서 스스로 바꾸는 값이라, 검토 담당자였다가 신청자로 돌아온 사람도 이 칸을
    # 본다. 그 사람에게만 '검토 담당자가 되는 법'을 안내한다 — 이미 담당자인 사람에게는
    # 아는 내용을 반복하는 카드가 될 뿐이라 띄우지 않는다.
    note = None
    if role != "manager":
        note = {
            "text": "지금 역할은 신청자입니다. 설정 > 내 정보에서 역할을 '검토 담당자'로 바꾸면 "
            "상단에 '법부엉이 검토' 탭이 생기고, 전국 사건 중 원하는 건을 맡아 검토할 수 있습니다. "
            "역할은 언제든 다시 바꿀 수 있고, 지금까지 쌓인 기록은 그대로 남습니다.",
            "href": "/settings",
            "link_label": "설정에서 역할 바꾸기",
        }

    return _side(
        key="manager",
        title="검토 측 — 검토 담당자",
        subtitle="내가 답한 검토가 얼마나 충실했는지",
        tiers=MANAGER_TIERS,
        recent_count=recent_count,
        recent_label=f"최근 {RECENT_WINDOW_DAYS}일 답해준 검토",
        note=note,
        headline=[
            {"label": "답해준 횟수", "value": len(conclusions), "unit": "건"},
            {"label": "담당 사건", "value": assigned_count, "unit": "건"},
        ],
        counts=[
            {"label": "검토중", "value": len(in_progress)},
            {"label": "코멘트", "value": len(plain_comments)},
            {"label": "보완 요청", "value": len(supplement_requests)},
            {"label": "최종 결과", "value": len(conclusions)},
        ],
        metrics=metrics,
        facts=facts,
        grass=_build_grass([d for d in grass_dates if d], today),
        has_data=assigned_count > 0,
        empty_message="아직 담당한 검토가 없습니다. '법부엉이 검토'에서 사건을 하나 맡아보세요.",
        empty_cta={"label": "검토하러 가기", "href": "/review"},
    )


def build_mypage_stats(session: Session, user_id: int) -> dict:
    """마이페이지 한 화면에 필요한 값을 통째로 만든다."""
    profile = session.get(User, user_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.astimezone(KST).date()
    cutoff = now - datetime.timedelta(days=RECENT_WINDOW_DAYS)

    requester = _requester_side(session, user_id, today, cutoff)
    manager = _manager_side(session, user_id, today, cutoff, profile.role)

    # 종합 점수는 두 측면의 단순 평균이 아니라 '기록이 있는 쪽'의 평균이다 — 아직 검토를
    # 맡아본 적 없는 신청자의 점수가 0점짜리 검토 측 때문에 반토막 나면, 화면이 알려주려던
    # "요청을 이렇게 쓰면 된다"는 신호가 묻힌다.
    active = [side for side in (requester, manager) if side["has_data"]]
    combined_score = round(sum(s["score"] for s in active) / len(active), 1) if active else 0.0

    return {
        "profile": {
            "display_name": profile.display_name,
            "username": profile.username,
            "role": profile.role,
            "role_label": profile.role_label,
            "occupation": profile.occupation_label or "-",
            "region": profile.sigungu.full_name if profile.sigungu else (profile.sido.full_name if profile.sido else "-"),
            "joined_at": _kst_date(profile.created_at).isoformat() if profile.created_at else "-",
        },
        "combined": {
            "score": combined_score,
            "sides_counted": len(active),
            "recent_days": RECENT_WINDOW_DAYS,
        },
        "sides": [requester, manager],
    }
