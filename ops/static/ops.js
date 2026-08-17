"use strict";

const TITLES = {
  web: "웹 (FastAPI)",
  postgres: "PostgreSQL + pgvector",
  neo4j: "Neo4j (그래프)",
  docker: "Docker 컨테이너",
  activity: "서비스 활동 (신청·사용자)",
  traffic: "요청 트래픽 (페이지별)",
};
const ORDER = ["web", "postgres", "neo4j", "docker", "activity", "traffic"];

const el = (id) => document.getElementById(id);

function bytes(n) {
  if (n === null || n === undefined) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function num(n) {
  return n === null || n === undefined ? "–" : n.toLocaleString("ko-KR");
}

function pct(ratio) {
  return ratio === null || ratio === undefined ? "–" : `${(ratio * 100).toFixed(1)}%`;
}

function duration(seconds) {
  if (!seconds) return "–";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}일 ${h}시간`;
  if (h) return `${h}시간 ${m}분`;
  return `${m}분`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function dl(pairs) {
  const rows = pairs
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`)
    .join("");
  return rows ? `<dl class="kv">${rows}</dl>` : "";
}

/** 최근 지연시간 추이. 실패 구간은 빨간 점으로 남겨 눈에 띄게 한다. */
function sparkline(samples) {
  if (!samples || samples.length < 2) return "";
  const W = 300, H = 34, PAD = 2;
  const values = samples.map((s) => (s.ok && s.latency_ms != null ? s.latency_ms : 0));
  const max = Math.max(...values, 1);
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (samples.length - 1);
  const y = (v) => H - PAD - (v / max) * (H - 2 * PAD);

  const path = samples.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(values[i]).toFixed(1)}`).join("");
  const fails = samples
    .map((s, i) => (s.ok ? "" : `<circle cx="${x(i).toFixed(1)}" cy="${H - PAD}" r="2.2" fill="var(--bad)"/>`))
    .join("");

  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
    aria-label="최근 응답시간 추이, 최대 ${Math.round(max)}ms">
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="1.4"/>${fails}</svg>`;
}

function renderWeb(d) {
  return dl([
    ["URL", d.url],
    ["상태 코드", d.status_code],
    ["응답 크기", bytes(d.content_length)],
  ]);
}

function renderPostgres(d) {
  const conns = d.connections_by_state || {};
  const states = Object.entries(conns).map(([k, v]) => `${k} ${v}`).join(", ");
  const tables = (d.tables || []).slice(0, 8)
    .map((t) => `<tr><td>${escapeHtml(t.name)}</td><td>${num(t.rows)}</td><td>${bytes(t.bytes)}</td></tr>`)
    .join("");

  return dl([
    ["버전", d.version],
    ["가동 시간", duration(d.uptime_seconds)],
    ["DB 크기", bytes(d.db_bytes)],
    ["연결", `${num(d.connections)} / ${num(d.max_connections)}`],
    ["연결 상태", states],
    ["캐시 적중률", pct(d.cache_hit_ratio)],
    ["장기 실행 쿼리", num(d.long_running_queries)],
    ["데드락(누적)", num(d.deadlocks)],
    ["pgvector", d.pgvector ? `있음 (${d.extensions?.vector ?? ""})` : "없음"],
  ]) + (tables
    ? `<table class="mini"><thead><tr><th>테이블</th><th>행(추정)</th><th>크기</th></tr></thead>
       <tbody>${tables}</tbody></table>`
    : "");
}

function renderNeo4j(d) {
  const labels = Object.entries(d.nodes_by_label || {})
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${num(v)}</td></tr>`).join("");
  const types = Object.entries(d.relationships_by_type || {})
    .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${num(v)}</td></tr>`).join("");

  return dl([
    ["버전", `${d.version ?? "–"} (${d.edition ?? "–"})`],
    ["노드", num(d.nodes)],
    ["관계", num(d.relationships)],
  ]) + (labels
    ? `<table class="mini"><thead><tr><th>라벨</th><th>노드</th></tr></thead><tbody>${labels}</tbody></table>`
    : "") + (types
    ? `<table class="mini"><thead><tr><th>관계 타입</th><th>수</th></tr></thead><tbody>${types}</tbody></table>`
    : "");
}

function renderDocker(d) {
  if (d.enabled === false) return `<p class="muted">비활성화됨 (DOCKER_ENABLED=false)</p>`;
  const rows = (d.containers || []).map((c) => {
    const healthy = c.status === "running" && c.health !== "unhealthy";
    const badge = c.health ? `${c.status}/${c.health}` : c.status;
    return `<tr><td>${escapeHtml(c.name)}</td>
      <td><span class="pill ${healthy ? "ok" : "bad"}">${escapeHtml(badge)}</span></td>
      <td>${num(c.restart_count)}</td></tr>`;
  }).join("");

  return rows
    ? `<table class="mini"><thead><tr><th>컨테이너</th><th>상태</th><th>재시작</th></tr></thead>
       <tbody>${rows}</tbody></table>`
    : `<p class="muted">컨테이너를 찾지 못했습니다.</p>`;
}

function ago(iso) {
  if (!iso) return "–";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "방금";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}

function renderActivity(d) {
  const statuses = Object.entries(d.incidents_by_status || {})
    .map(([k, v]) => `<span class="pill warn">${escapeHtml(k)} ${num(v)}</span>`).join(" ");

  const incidents = (d.recent_incidents || []).map((i) => `<tr>
      <td>#${num(i.id)}</td>
      <td>${escapeHtml(i.username ?? "–")}</td>
      <td>${escapeHtml(i.status ?? "–")}</td>
      <td>${escapeHtml(ago(i.created_at))}</td></tr>`).join("");

  const events = (d.recent_events || []).map((e) => `<tr>
      <td>#${num(e.incident_id)}</td>
      <td>${escapeHtml(e.username ?? "–")}</td>
      <td>${escapeHtml(e.status ?? "–")}</td>
      <td>${escapeHtml(ago(e.created_at))}</td></tr>`).join("");

  return dl([
    ["신청 (1시간 / 24시간 / 7일)", `${num(d.incidents_1h)} / ${num(d.incidents_24h)} / ${num(d.incidents_7d)}`],
    ["신청 누적", num(d.incidents_total)],
    ["최근 접수", ago(d.incidents_latest)],
    ["처리 이벤트 (1시간 / 24시간)", `${num(d.events_1h)} / ${num(d.events_24h)}`],
    ["코멘트 (24시간)", num(d.comments_24h)],
    ["사용자 / 활성 세션", `${num(d.users_total)} / ${num(d.sessions_active)}`],
    ["24시간 내 로그인", num(d.users_active_24h)],
    ["법령 선택 총계", num(d.law_selections)],
  ])
    + (statuses ? `<p class="pills">${statuses}</p>` : "")
    + (incidents ? `<table class="mini"><thead><tr><th>신청</th><th>작성자</th><th>상태</th><th>시각</th></tr></thead>
        <tbody>${incidents}</tbody></table>` : "")
    + (events ? `<table class="mini"><thead><tr><th>대상</th><th>처리자</th><th>변경</th><th>시각</th></tr></thead>
        <tbody>${events}</tbody></table>` : "");
}

function renderTraffic(d) {
  if (d.enabled === false) return `<p class="muted">Docker 비활성화 상태라 액세스 로그를 읽지 않습니다.</p>`;

  const paths = (d.top_paths_1h || []).map(([route, n]) =>
    `<tr><td>${escapeHtml(route)}</td><td>${num(n)}</td></tr>`).join("");
  const statuses = Object.entries(d.by_status_1h || {}).map(([code, n]) => {
    const cls = code[0] === "2" || code[0] === "3" ? "ok" : "bad";
    return `<span class="pill ${cls}">${escapeHtml(code)} ${num(n)}</span>`;
  }).join(" ");

  return dl([
    ["대상 컨테이너", d.container],
    ["요청 (5분 / 1시간)", `${num(d.requests_5m)} / ${num(d.requests_1h)}`],
    ["오류 응답 (1시간)", num(d.errors_1h)],
    ["이번 수집 신규 로그", num(d.new_lines)],
  ])
    + (statuses ? `<p class="pills">${statuses}</p>` : "")
    + (paths ? `<table class="mini"><thead><tr><th>경로 (최근 1시간)</th><th>요청</th></tr></thead>
        <tbody>${paths}</tbody></table>`
      : `<p class="muted">최근 1시간 요청이 없습니다.</p>`);
}

const RENDERERS = {
  web: renderWeb, postgres: renderPostgres, neo4j: renderNeo4j,
  docker: renderDocker, activity: renderActivity, traffic: renderTraffic,
};

function card(name, result, uptime, samples) {
  const ok = result?.ok;
  const latency = result?.latency_ms != null ? `${result.latency_ms.toFixed(0)}ms` : "–";
  const body = ok ? (RENDERERS[name]?.(result.detail || {}) ?? "") : "";
  const error = result?.error
    ? `<p class="card-error">${escapeHtml(result.error)}</p>` : "";

  return `<section class="card ${ok ? "" : "bad"}">
    <div class="card-head">
      <span class="dot ${ok === undefined ? "dot-unknown" : ok ? "dot-ok" : "dot-bad"}"></span>
      <span class="name">${escapeHtml(TITLES[name] ?? name)}</span>
      <span class="latency">${latency}${uptime != null ? ` · 24h ${pct(uptime)}` : ""}</span>
    </div>
    ${error}${body}${sparkline(samples)}
  </section>`;
}

let historyCache = {};

async function loadHistory() {
  const entries = await Promise.all(ORDER.map(async (name) => {
    try {
      const res = await fetch(`/api/history/${name}?hours=6`);
      if (!res.ok) return [name, []];
      return [name, (await res.json()).samples];
    } catch { return [name, []]; }
  }));
  historyCache = Object.fromEntries(entries);
}

async function tick() {
  let data;
  try {
    const res = await fetch("/api/status");
    data = await res.json();
  } catch (err) {
    el("cards").innerHTML = `<p class="card-error">ops 서비스에 연결할 수 없습니다: ${escapeHtml(err)}</p>`;
    el("overall-dot").className = "dot dot-bad";
    return;
  }

  const results = data.results || {};
  el("overall-dot").className = `dot ${data.overall_ok ? "dot-ok" : "dot-bad"}`;
  el("last-poll").textContent = data.ts
    ? new Date(data.ts * 1000).toLocaleTimeString("ko-KR") : "–";
  el("poll-duration").textContent = data.poll_duration_ms != null
    ? `${data.poll_duration_ms.toFixed(0)}ms` : "–";
  document.title = `${data.overall_ok ? "🟢" : "🔴"} LawGraphRAG Ops`;

  el("cards").innerHTML = ORDER
    .map((name) => card(name, results[name], data.uptime_24h?.[name], historyCache[name]))
    .join("");
}

el("refresh").addEventListener("click", async (event) => {
  event.target.disabled = true;
  try {
    await fetch("/api/refresh", { method: "POST" });
    await loadHistory();
    await tick();
  } finally {
    event.target.disabled = false;
  }
});

(async function start() {
  await loadHistory();
  await tick();
  // 스냅샷은 폴러가 갱신하므로, 화면은 주기보다 조금 자주 읽어 갱신 지연만 줄인다.
  setInterval(tick, Math.max(5, (window.OPS_POLL_INTERVAL || 30) / 3) * 1000);
  setInterval(loadHistory, 60_000);
})();
