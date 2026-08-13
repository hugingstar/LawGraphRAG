function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// 사업장 카드 색상. 사업장 id 순서대로 고정 배정해 새로고침해도 색이 바뀌지 않는다.
const SITE_TONES = ["site-a", "site-b", "site-c", "site-d", "site-e", "site-f"];

function renderSiteCards(bySite) {
  const container = document.getElementById("site-grid");
  if (!bySite.length) {
    container.innerHTML = '<p class="empty-state">등록된 사업장이 없습니다.</p>';
    return;
  }
  container.innerHTML = bySite
    .map(
      (s, i) => `<a class="site-card ${SITE_TONES[i % SITE_TONES.length]}" href="/review?site_id=${s.id}">
        <div class="site-name">${escapeHtml(s.name)}</div>
        <div class="site-count">${s.count}</div>
        <div class="site-unit">건</div>
      </a>`
    )
    .join("");
}

function renderHeatmap(bySite, byDepartment, matrix) {
  const container = document.getElementById("dept-heatmap");
  if (!bySite.length || !byDepartment.length) {
    container.innerHTML = '<p class="empty-state">등록된 사업장 또는 부서가 없습니다.</p>';
    return;
  }

  const countOf = {};
  for (const m of matrix) countOf[`${m.site_id}:${m.department_id}`] = m.count;
  const max = Math.max(1, ...matrix.map((m) => m.count));

  const header = `<div class="heatmap-row heatmap-head">
    <div class="heatmap-corner"></div>
    ${bySite.map((s) => `<div class="heatmap-col-label" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</div>`).join("")}
  </div>`;

  const rows = byDepartment
    .map((d) => {
      const cells = bySite
        .map((s) => {
          const count = countOf[`${s.id}:${d.id}`] || 0;
          const intensity = count === 0 ? 0 : 0.18 + (count / max) * 0.72;
          const heavy = intensity > 0.5 ? "is-heavy" : "";
          const href = `/review?site_id=${s.id}&department_id=${d.id}`;
          const label = `${s.name} · ${d.name}: ${count}건`;
          return `<a class="heatmap-cell ${count === 0 ? "is-zero" : ""} ${heavy}" style="--heat:${intensity}" href="${href}" title="${escapeHtml(label)}">${count || ""}</a>`;
        })
        .join("");
      return `<div class="heatmap-row">
        <div class="heatmap-row-label" title="${escapeHtml(d.name)}">${escapeHtml(d.name)}</div>
        ${cells}
      </div>`;
    })
    .join("");

  container.innerHTML = `<div class="heatmap" style="--cols:${bySite.length}">${header}${rows}</div>`;
}

async function loadStats() {
  const start = document.getElementById("start-date").value;
  const end = document.getElementById("end-date").value;

  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  const resp = await fetch(`/api/dashboard/stats?${params.toString()}`);
  if (!resp.ok) return;
  const data = await resp.json();

  document.getElementById("stat-total").textContent = data.total;
  for (const key of ["review_requested", "in_review", "supplement_requested", "completed"]) {
    const el = document.getElementById(`stat-${key}`);
    if (el) el.textContent = data.status_counts[key] ?? 0;
  }

  renderSiteCards(data.by_site);
  renderHeatmap(data.by_site, data.by_department, data.matrix);
}

document.getElementById("apply-range").addEventListener("click", loadStats);
document.getElementById("reset-range").addEventListener("click", () => {
  document.getElementById("start-date").value = "";
  document.getElementById("end-date").value = "";
  loadStats();
});

loadStats();
