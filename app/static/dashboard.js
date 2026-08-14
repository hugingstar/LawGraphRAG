function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// 광주·전남 행정통합(2026-07-01). 경계 파일은 2018년 스냅샷이라 광주광역시(24)·전라남도(36)가
// 아직 따로 그려져 있다 — 구·시·군 모양 자체는 안 바뀌었으므로 도형을 새로 그리는 대신, 두 시도
// 코드를 통합 코드 하나로 묶어서 칠한다(app/regions_seed.py의 _SIDO_MERGES와 대응).
const SIDO_MERGES = { "36": "24" };
const MERGED_SIDO_NAMES = { "24": "전남광주통합특별시" };

function sidoPrefixOf(code) {
  const prefix = code.slice(0, 2);
  return SIDO_MERGES[prefix] || prefix;
}

// 경계 파일은 한 번만 받아 재사용한다(합쳐서 ~770KB이므로 기간을 바꿀 때마다 다시 받을 이유가 없다).
let sidoFeatures = null;
let sigunguFeatures = null;
let latestStats = null;
// null이면 전국(시도) 보기, 값이 있으면 해당 시도의 시군구 보기.
let drilledSido = null;

const mapEl = document.getElementById("region-map");
const mapTitleEl = document.getElementById("map-title");
const backBtn = document.getElementById("map-back");

async function loadGeo() {
  if (sidoFeatures && sigunguFeatures) return;
  const [sido, sigungu] = await Promise.all([
    fetch("/static/geo/sido.topo.json").then((r) => r.json()),
    fetch("/static/geo/sigungu.topo.json").then((r) => r.json()),
  ]);
  sidoFeatures = decodeTopology(sido, "skorea_provinces_2018_geo").map((f) => {
    const mergedCode = SIDO_MERGES[f.code] || f.code;
    return { ...f, code: mergedCode, name: MERGED_SIDO_NAMES[mergedCode] || f.name };
  });
  sigunguFeatures = decodeTopology(sigungu, "skorea_municipalities_2018_geo");
}

function countMap(rows, key) {
  const out = {};
  for (const r of rows) out[r[key]] = r.count;
  return out;
}

function renderRank(rows, labelFor) {
  const el = document.getElementById("region-rank");
  const top = rows.filter((r) => r.count > 0).sort((a, b) => b.count - a.count).slice(0, 10);
  if (!top.length) {
    el.innerHTML = '<li class="empty-state">해당 기간에 접수된 사건이 없습니다.</li>';
    return;
  }
  // 지역명 아래 작은 글씨로 그 지역에서 가장 많은 사건 유형과 건수를 덧붙인다. 폭이 16rem뿐인
  // 사이드 컬럼이라 한 줄에 다 넣으면 넘치므로 2번째 줄로 내리고, 둘 다 말줄임으로 막는다.
  el.innerHTML = top
    .map((r) => {
      const sub = r.top_category
        ? `<div class="rank-sub">최다유형 · ${escapeHtml(r.top_category)} ${r.top_category_count}건</div>`
        : "";
      return `<li>
        <div class="rank-row">
          <span class="rank-name">${escapeHtml(labelFor(r))}</span>
          <span class="rank-count">${r.count}건</span>
        </div>
        ${sub}
      </li>`;
    })
    .join("");
}

function renderCategoryBars(byCategory) {
  const el = document.getElementById("category-bars");
  const max = Math.max(1, ...byCategory.map((c) => c.count));
  if (!byCategory.length) {
    el.innerHTML = '<p class="empty-state">등록된 사건 유형이 없습니다.</p>';
    return;
  }
  el.innerHTML = byCategory
    .map((c) => {
      const intensity = c.count === 0 ? 0 : 0.18 + (c.count / max) * 0.72;
      return `<a class="cat-bar-row" href="/review?category_id=${c.id}">
        <span class="cat-bar-label">${escapeHtml(c.name)}</span>
        <span class="cat-bar-track"><span class="cat-bar-fill ${c.count === 0 ? "is-zero" : ""}"
          style="--heat:${intensity}; width:${(c.count / max) * 100}%"></span></span>
        <span class="cat-bar-count">${c.count}</span>
      </a>`;
    })
    .join("");
}

function paintMap() {
  if (!latestStats || !sidoFeatures) return;

  if (drilledSido === null) {
    const counts = countMap(latestStats.by_sido, "code");
    mapTitleEl.textContent = "전국 지역별 사건 분포";
    backBtn.hidden = true;
    renderChoropleth(mapEl, sidoFeatures, counts, (data) => {
      drilledSido = data.code;
      paintMap();
    });
    renderRank(latestStats.by_sido, (r) => r.name);
  } else {
    // 시군구 코드 앞 2자리가 소속 시도다(통합된 시도는 sidoPrefixOf가 통합 코드로 바꿔준다).
    // 선택한 시도의 시군구만 그린다.
    const features = sigunguFeatures.filter((f) => sidoPrefixOf(f.code) === drilledSido);
    const rows = latestStats.by_sigungu.filter((r) => r.parent_code === drilledSido);
    const sidoName = (latestStats.by_sido.find((s) => s.code === drilledSido) || {}).name || "";

    mapTitleEl.textContent = `${sidoName} 시·군·구별 사건 분포`;
    backBtn.hidden = false;
    renderChoropleth(mapEl, features, countMap(rows, "code"), (data) => {
      window.location.href = `/review?sido_code=${drilledSido}&sigungu_code=${data.code}`;
    });
    renderRank(rows, (r) => r.name);
  }
}

async function loadStats() {
  const start = document.getElementById("start-date").value;
  const end = document.getElementById("end-date").value;

  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  const resp = await fetch(`/api/dashboard/stats?${params.toString()}`);
  if (!resp.ok) return;
  latestStats = await resp.json();

  document.getElementById("stat-total").textContent = latestStats.total;
  for (const key of ["review_requested", "in_review", "supplement_requested", "completed"]) {
    const el = document.getElementById(`stat-${key}`);
    if (el) el.textContent = latestStats.status_counts[key] ?? 0;
  }

  renderCategoryBars(latestStats.by_category);
  paintMap();
}

backBtn.addEventListener("click", () => {
  drilledSido = null;
  paintMap();
});

document.getElementById("apply-range").addEventListener("click", loadStats);
document.getElementById("reset-range").addEventListener("click", () => {
  document.getElementById("start-date").value = "";
  document.getElementById("end-date").value = "";
  loadStats();
});

// 지도 데이터를 먼저 받아둔 뒤 통계를 불러야 첫 렌더에서 바로 색칠까지 끝난다.
loadGeo()
  .then(loadStats)
  .catch(() => {
    mapEl.innerHTML = '<p class="empty-state">지도 데이터를 불러오지 못했습니다.</p>';
  });
