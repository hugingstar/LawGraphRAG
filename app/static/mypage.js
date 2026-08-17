// 마이페이지 — 요청 측/검토 측을 같은 렌더러로 두 번 그린다.
// 서버(app/mypage_stats.py)가 두 측면을 완전히 같은 형태(headline/counts/metrics/facts/grass)로
// 내려주므로, 여기서는 좌우를 구분하는 분기 없이 색조(--side-tone)만 다르게 준다.

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const MONTH_LABELS = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];
const WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"];

function renderScoreRing(score, caption, size) {
  // conic-gradient로 점수만큼 채운 링. 캔버스나 SVG 없이도 되고, 색은 --side-tone을
  // 상속하므로 좌우 컬럼이 각자의 색으로 그려진다.
  return `<div class="score-ring ${size}" style="--pct:${score}" role="img" aria-label="품질 점수 ${score}점">
    <div class="score-ring-inner">
      <span class="score-ring-value">${score}</span>
      <span class="score-ring-unit">/ 100</span>
    </div>
  </div>
  <div class="score-caption">${escapeHtml(caption)}</div>`;
}

// 등급은 품질 점수와 별개로 '최근 한 달 활동량'으로 매겨진다. 지금 등급 + 다음 등급까지
// 남은 건수(왼쪽)와 전체 등급표(오른쪽)를 한 블록으로 묶는다.
function renderGradeBlock(side) {
  const t = side.tier;
  const next = t.next_label
    ? `<div class="grade-next">${escapeHtml(t.next_emoji)} ${escapeHtml(t.next_label)}까지 <b>${t.remaining}건</b></div>`
    : `<div class="grade-next">최고 등급입니다</div>`;

  const rows = side.ladder
    .map(
      (row) => `<tr class="${row.current ? "is-current" : ""} ${row.reached ? "is-reached" : ""}">
        <th scope="row"><span class="ladder-emoji" aria-hidden="true">${row.emoji}</span>${escapeHtml(row.label)}</th>
        <td>${escapeHtml(row.threshold)}</td>
      </tr>`,
    )
    .join("");

  return `<div class="grade-block">
      <div class="grade-now">
        <span class="grade-now-emoji" aria-hidden="true">${t.emoji}</span>
        <div class="grade-now-body">
          <div class="grade-now-label">${escapeHtml(t.label)}</div>
          <div class="grade-now-sub">${escapeHtml(side.recent_label)} <b>${side.recent_count}건</b></div>
          <span class="grade-progress" aria-hidden="true"><span style="width:${(t.progress * 100).toFixed(1)}%"></span></span>
          ${next}
          <p class="grade-now-message">${escapeHtml(t.message)}</p>
        </div>
      </div>
      <table class="grade-ladder">
        <caption>등급표 (최근 ${side.recent_days}일 기준)</caption>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderIdentity(data) {
  const p = data.profile;
  const c = data.combined;
  const initial = escapeHtml((p.display_name || "?").trim().slice(0, 1));
  const scope =
    c.sides_counted === 2
      ? "요청·검토 두 측면의 평균"
      : c.sides_counted === 1
        ? "기록이 있는 한 측면 기준"
        : "아직 기록이 없습니다";

  // 등급 두 개(요청/검토)를 프로필 옆에 함께 띄워, 어느 쪽을 얼마나 하고 있는지 먼저 보이게 한다.
  const tiers = data.sides
    .map(
      (side) => `<span class="tier-chip tone-${side.key}">
        <span aria-hidden="true">${side.tier.emoji}</span>
        <b>${escapeHtml(side.tier.label)}</b>
        <span class="tier-chip-count">${escapeHtml(side.recent_label)} ${side.recent_count}건</span>
      </span>`,
    )
    .join("");

  return `<div class="identity-main">
      <span class="identity-avatar" aria-hidden="true">${initial}</span>
      <div class="identity-body">
        <div class="identity-name-row">
          <span class="identity-name">${escapeHtml(p.display_name)}</span>
          <span class="identity-role">${escapeHtml(p.role_label)}</span>
        </div>
        <dl class="identity-meta">
          <div><dt>아이디</dt><dd>${escapeHtml(p.username)}</dd></div>
          <div><dt>직종</dt><dd>${escapeHtml(p.occupation)}</dd></div>
          <div><dt>활동 지역</dt><dd>${escapeHtml(p.region)}</dd></div>
          <div><dt>가입일</dt><dd>${escapeHtml(p.joined_at)}</dd></div>
        </dl>
        <div class="identity-tiers">${tiers}</div>
      </div>
    </div>
    <div class="identity-score">
      <div class="identity-score-title">종합 품질 점수</div>
      ${renderScoreRing(c.score, scope, "ring-lg")}
      <p class="identity-score-note">점수는 <b>한 건을 얼마나 잘 썼는지</b>,
        등급은 <b>최근 ${c.recent_days}일에 얼마나 했는지</b>로 각각 매깁니다.</p>
    </div>`;
}

function renderGrass(grass) {
  // 서버가 일요일부터 날짜순으로 칸을 내려주므로, grid-auto-flow:column + 7행이면
  // 별도 계산 없이 GitHub와 같은 배치가 된다.
  const months = [];
  let lastMonth = -1;
  for (let w = 0; w < grass.weeks; w++) {
    const cell = grass.cells[w * 7];
    const month = new Date(`${cell.date}T00:00:00`).getMonth();
    // 달이 바뀐 첫 열에만 라벨을 붙인다. 첫 열은 그 달이 반쯤 지난 상태라 생략한다.
    if (month !== lastMonth && w > 0) {
      months.push(`<span class="grass-month">${MONTH_LABELS[month]}</span>`);
      lastMonth = month;
    } else {
      months.push('<span class="grass-month"></span>');
      if (w === 0) lastMonth = month;
    }
  }

  const cells = grass.cells
    .map((cell) => {
      const label = cell.count ? `${cell.date} · ${cell.count}건` : `${cell.date} · 활동 없음`;
      const cls = `grass-cell level-${cell.level}${cell.future ? " is-future" : ""}`;
      return `<span class="${cls}" title="${label}"></span>`;
    })
    .join("");

  const weekdays = WEEKDAY_LABELS.map(
    // 월·수·금만 표기한다(7줄 모두 쓰면 칸보다 글자가 커진다).
    (label, i) => `<span class="grass-weekday">${i % 2 === 1 ? label : ""}</span>`,
  ).join("");

  return `<div class="grass-block">
      <div class="grass-head">
        <h3 class="side-section-title">최근 6개월 활동</h3>
        <span class="grass-summary">${grass.window_total}회 · ${grass.active_days}일</span>
      </div>
      <div class="grass-scroll">
        <div class="grass-months" style="--weeks:${grass.weeks}">${months.join("")}</div>
        <div class="grass-body">
          <div class="grass-weekdays">${weekdays}</div>
          <div class="grass-grid" style="--weeks:${grass.weeks}">${cells}</div>
        </div>
      </div>
      <div class="grass-foot">
        <span>연속 ${grass.current_streak}일 · 최장 ${grass.best_streak}일</span>
        <span class="grass-scale">
          적음
          <span class="grass-cell level-0"></span><span class="grass-cell level-1"></span><span class="grass-cell level-2"></span><span class="grass-cell level-3"></span><span class="grass-cell level-4"></span>
          많음
        </span>
      </div>
    </div>`;
}

function renderSide(side) {
  const headline = side.headline
    .map(
      (h) => `<div class="side-headline-item">
        <div class="side-headline-value">${h.value}<span class="side-headline-unit">${h.unit}</span></div>
        <div class="side-headline-label">${escapeHtml(h.label)}</div>
      </div>`,
    )
    .join("");

  const counts = side.counts
    .map(
      (c) => `<div class="side-count">
        <span class="side-count-value">${c.value}</span>
        <span class="side-count-label">${escapeHtml(c.label)}</span>
      </div>`,
    )
    .join("");

  const metrics = side.metrics
    .map(
      (m) => `<li class="metric-row">
        <div class="metric-top">
          <span class="metric-label">${escapeHtml(m.label)}</span>
          <span class="metric-points">${m.points}<span class="metric-points-max"> / ${m.max_points}</span></span>
        </div>
        <span class="metric-track"><span class="metric-fill" style="width:${(m.ratio * 100).toFixed(1)}%"></span></span>
        <p class="metric-detail">${escapeHtml(m.detail)}</p>
      </li>`,
    )
    .join("");

  const facts = side.facts
    .map((f) => `<div><dt>${escapeHtml(f.label)}</dt><dd>${escapeHtml(f.value)}</dd></div>`)
    .join("");

  const advice = side.advice.map((a) => `<li>${escapeHtml(a)}</li>`).join("");

  // 기록이 아예 없으면 0점 지표를 나열해 봐야 자책만 남으므로, 무엇을 하면 되는지만 보여준다.
  const body = side.has_data
    ? `<ul class="metric-list">${metrics}</ul>
       <h3 class="side-section-title">세부 기록</h3>
       <dl class="fact-list">${facts}</dl>`
    : `<div class="side-empty">
         <p>${escapeHtml(side.empty_message)}</p>
         ${side.empty_cta ? `<a class="btn-link-strong" href="${side.empty_cta.href}">${escapeHtml(side.empty_cta.label)} →</a>` : ""}
       </div>`;

  const note = side.note
    ? `<div class="side-role-note">
         <p>${escapeHtml(side.note.text)}</p>
         <a class="btn-link-strong" href="${side.note.href}">${escapeHtml(side.note.link_label)} →</a>
       </div>`
    : "";

  return `<section class="card mypage-side side-${side.key}">
      <header class="side-head">
        <div>
          <h2 class="side-title">${escapeHtml(side.title)}</h2>
          <p class="hint side-note">${escapeHtml(side.subtitle)}</p>
        </div>
        <div class="side-score">${renderScoreRing(side.score, "품질 점수", "ring-sm")}</div>
      </header>

      ${note}
      ${renderGradeBlock(side)}

      <div class="side-headline">${headline}</div>
      <div class="side-counts">${counts}</div>

      ${renderGrass(side.grass)}

      <h3 class="side-section-title">품질 지표</h3>
      ${body}

      <div class="side-advice">
        <h3 class="side-section-title">다음에 이렇게</h3>
        <ul>${advice}</ul>
      </div>
    </section>`;
}

async function loadMypage() {
  const identityEl = document.getElementById("mypage-identity");
  const splitEl = document.getElementById("mypage-split");

  const resp = await fetch("/api/mypage/stats");
  if (!resp.ok) {
    identityEl.innerHTML = '<p class="empty-state">기록을 불러오지 못했습니다.</p>';
    return;
  }
  const data = await resp.json();

  identityEl.innerHTML = renderIdentity(data);
  splitEl.innerHTML = data.sides.map(renderSide).join("");
}

loadMypage();
