"use strict";

const nodeMap = new Map();   // id -> 노드 객체(라이브러리가 x/y/z 를 여기에 심는다)
const linkKeys = new Set();  // "src|type|dst"
const linkStore = [];        // { s, t, type, weight }
const expanded = new Set();  // 이미 펼친 노드 (같은 노드 재요청 방지)

let graph = null;
let selected = null;

// 서버가 내려준 팔레트. 첫 응답에서 받아 두고 이후 조각 응답에는 실려 오지 않는다.
let palette = null;
let colorMode = "category";
// 범례에서 고른 묶음. 비어 있으면 전체를 보여 준다.
const isolated = new Set();
// 호버한 노드와 그 이웃. 비어 있으면 흐리게 처리하지 않는다.
let highlight = null;

const el = (id) => document.getElementById(id);
const status = (text) => { el("graph-status").textContent = text; };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// --------------------------------------------------------------------------
// 색
// --------------------------------------------------------------------------

function hexToRgb(hex) {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function rgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${alpha})`;
}

/** 배경 쪽으로 끌어당겨 뒤로 물린다. 식별이 아니라 강조의 반대편이라 생성색이어도 된다. */
function recede(hex, amount) {
  const [r, g, b] = hexToRgb(hex);
  const [br, bg, bb] = hexToRgb(palette ? palette.surface : "#0e1116");
  const mix = (a, b2) => Math.round(a + (b2 - a) * amount);
  return `rgb(${mix(r, br)},${mix(g, bg)},${mix(b, bb)})`;
}

/** 현재 기준에서 이 노드가 가질 '진짜' 색. 흐리게 처리하기 전 값이다. */
function baseColor(node) {
  if (!palette) return "#6e7681";
  if (colorMode === "heat") {
    if (node.heat === null || node.heat === undefined) return palette.neutral;
    const ramp = palette.heat_ramp;
    return ramp[Math.min(ramp.length - 1, Math.floor(node.heat * (ramp.length - 1)))];
  }
  return (node.colors && node.colors[colorMode]) || palette.neutral;
}

/** 범례에서 이 노드가 속한 칸의 키. 인용도 기준에서는 묶음이 없다. */
function legendKey(node) {
  if (colorMode === "category") return node.group;
  if (colorMode !== "label") return null;
  // 범례에 칸이 없는 종류(Region·Incident)는 '기타' 칸에 얹는다.
  const keys = (palette.legend.label || []).map((entry) => entry.key);
  return keys.includes(node.label) ? node.label : "Unknown";
}

function isDimmed(node) {
  if (highlight && !highlight.has(node.id)) return true;
  if (isolated.size && colorMode !== "heat" && !isolated.has(legendKey(node))) return true;
  return false;
}

function nodeColor(node) {
  const color = baseColor(node);
  return isDimmed(node) ? recede(color, 0.78) : color;
}

/** 간선은 출발 노드의 색을 물려받는다. 구조 간선만 배경으로 뺀다. */
function linkColor(link) {
  const source = typeof link.source === "object" ? link.source : nodeMap.get(link.source);
  const target = typeof link.target === "object" ? link.target : nodeMap.get(link.target);
  const structural = palette && palette.structural_links.includes(link.type);
  const dimmed = (source && isDimmed(source)) || (target && isDimmed(target));

  if (structural) return rgba(palette ? palette.muted : "#898781", dimmed ? 0.05 : 0.3);

  const hex = source ? baseColor(source) : (palette ? palette.neutral : "#6e7681");
  if (dimmed) return rgba(hex, 0.03);
  // 많이 인용할수록 진하게. 로그로 눌러야 215회짜리 하나가 화면을 태우지 않는다.
  const weight = link.weight || 1;
  const alpha = Math.min(0.55, 0.16 + Math.log10(weight) * 0.16);
  return rgba(hex, highlight ? Math.min(0.9, alpha * 2.2) : alpha);
}

/** 라이브러리는 접근자를 다시 심어야 색을 다시 계산한다. */
function repaint() {
  if (!graph) return;
  graph.nodeColor(nodeColor).linkColor(linkColor).linkDirectionalParticleColor(particleColor);
}

function particleColor(link) {
  const source = typeof link.source === "object" ? link.source : nodeMap.get(link.source);
  return source ? baseColor(source) : (palette ? palette.neutral : "#6e7681");
}

// --------------------------------------------------------------------------
// 데이터 병합·렌더
// --------------------------------------------------------------------------

/** 서버가 준 조각을 현재 그래프에 병합한다. 새로 늘어난 노드 수를 돌려준다. */
function merge(payload) {
  if (payload.palette) palette = payload.palette;
  let added = 0;
  for (const node of payload.nodes || []) {
    if (!nodeMap.has(node.id)) { nodeMap.set(node.id, node); added += 1; }
  }
  for (const link of payload.links || []) {
    const key = `${link.source}|${link.type}|${link.target}`;
    if (linkKeys.has(key)) continue;
    // 양끝이 모두 로드된 링크만 넣는다. 없는 노드를 가리키면 라이브러리가 터진다.
    if (!nodeMap.has(link.source) || !nodeMap.has(link.target)) continue;
    linkKeys.add(key);
    linkStore.push({ s: link.source, t: link.target, type: link.type, weight: link.weight });
  }
  return added;
}

function draw() {
  // 링크 객체는 매번 새로 만든다. 라이브러리가 source/target 을 노드 객체 참조로
  // 덮어쓰기 때문에, 같은 객체를 재사용하면 두 번째 렌더에서 id 해석이 깨진다.
  graph.graphData({
    nodes: [...nodeMap.values()],
    links: linkStore.map((l) => ({ source: l.s, target: l.t, type: l.type, weight: l.weight })),
  });
  status(`노드 ${nodeMap.size.toLocaleString("ko-KR")} · 링크 ${linkStore.length.toLocaleString("ko-KR")}`);
}

function showPanel(node) {
  selected = node;
  el("panel").classList.remove("hidden");
  el("panel-title").textContent = node.name;
  el("panel-sub").innerHTML =
    `<i class="swatch" style="background:${escapeHtml(baseColor(node))}"></i>` +
    `${escapeHtml(node.label)}${node.sub ? " · " + escapeHtml(node.sub) : ""}` +
    `${node.group_name ? " · " + escapeHtml(node.group_name) : ""}`;
  el("panel-meta").innerHTML = Object.entries(node.meta || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`)
    .join("");
  el("panel-expand").textContent = expanded.has(node.id) ? "이미 펼침 (다시 시도)" : "이웃 펼치기";
}

async function expandNode(node, { focus = true } = {}) {
  status(`${node.name} 펼치는 중…`);
  try {
    const res = await fetch(`/api/graph/expand?node_id=${encodeURIComponent(node.id)}`);
    if (!res.ok) {
      status(`펼치기 실패: ${(await res.json()).detail ?? res.status}`);
      return;
    }
    const added = merge(await res.json());
    expanded.add(node.id);
    draw();
    if (focus) focusOn(node);
    if (added === 0) status(`${node.name}: 새로 붙일 이웃이 없습니다.`);
  } catch (err) {
    status(`펼치기 실패: ${err}`);
  }
}

function focusOn(node) {
  const distance = 160;
  const ratio = 1 + distance / Math.hypot(node.x || 1, node.y || 1, node.z || 1);
  graph.cameraPosition(
    { x: (node.x || 0) * ratio, y: (node.y || 0) * ratio, z: (node.z || 0) * ratio },
    node,
    1200,
  );
}

async function loadOverview() {
  status("불러오는 중…");
  nodeMap.clear();
  linkKeys.clear();
  linkStore.length = 0;
  expanded.clear();
  isolated.clear();
  highlight = null;
  el("panel").classList.add("hidden");

  try {
    const res = await fetch("/api/graph/overview");
    if (!res.ok) {
      status(`불러오기 실패: ${(await res.json()).detail ?? res.status}`);
      return;
    }
    const payload = await res.json();
    merge(payload);
    draw();
    repaint();
    renderLegend();
    if (payload.truncated) {
      status(`${nodeMap.size}개에서 잘렸습니다 (GRAPH_MAX_NODES 조정 가능)`);
    } else if (nodeMap.size === 0) {
      status("그래프가 비어 있습니다. graph_ingest 로 적재해 주세요.");
    }
  } catch (err) {
    status(`불러오기 실패: ${err}`);
  }
}

// --------------------------------------------------------------------------
// 범례
// --------------------------------------------------------------------------

function renderLegend() {
  const box = el("legend");
  if (!palette) { box.innerHTML = ""; return; }

  const entries = palette.legend[colorMode] || [];
  // 인용도는 연속량이라 켜고 끌 묶음이 없다. 램프만 보여 준다.
  const clickable = colorMode !== "heat";
  const counts = new Map();
  if (clickable) {
    for (const node of nodeMap.values()) {
      const key = legendKey(node);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }

  box.innerHTML = entries.map((entry) => {
    const active = isolated.size === 0 || isolated.has(entry.key);
    const count = counts.get(entry.key);
    return `<button type="button" class="legend-item${active ? "" : " off"}"
              data-key="${escapeHtml(entry.key)}"${clickable ? "" : " disabled"}>
        <i style="background:${escapeHtml(entry.color)}"></i>${escapeHtml(entry.name)}${
      count ? `<b>${count.toLocaleString("ko-KR")}</b>` : ""
    }</button>`;
  }).join("");
}

el("legend").addEventListener("click", (event) => {
  const item = event.target.closest(".legend-item");
  if (!item || item.disabled) return;
  const key = item.dataset.key;
  if (isolated.has(key)) isolated.delete(key);
  else isolated.add(key);
  // 전부 켜는 것과 전부 끄는 것은 같은 화면이다. 후자를 '해제'로 되돌린다.
  if (isolated.size === (palette.legend[colorMode] || []).length) isolated.clear();
  renderLegend();
  repaint();
});

// --------------------------------------------------------------------------
// 그래프
// --------------------------------------------------------------------------

function setColorMode(mode) {
  colorMode = mode;
  isolated.clear();
  renderLegend();
  repaint();
}

function setHighlight(node) {
  if (!node) {
    highlight = null;
  } else {
    highlight = new Set([node.id]);
    for (const link of linkStore) {
      if (link.s === node.id) highlight.add(link.t);
      else if (link.t === node.id) highlight.add(link.s);
    }
  }
  repaint();
}

function initGraph() {
  // preserveDrawingBuffer 가 없으면 WebGL 이 컴포지팅 직후 버퍼를 비워서, 캔버스가
  // 실제로 그려낸 프레임을 toDataURL 로 캡처했을 때 빈 화면이 나올 수 있다.
  graph = ForceGraph3D({ rendererConfig: { preserveDrawingBuffer: true } })(el("graph"))
    .backgroundColor("#0e1116")
    .nodeVal("val")
    .nodeColor(nodeColor)
    .nodeOpacity(0.92)
    .nodeLabel((n) => `<div style="font:12px system-ui;background:#161b22;color:#e6edf3;
        border:1px solid #2a3140;border-radius:6px;padding:5px 8px;max-width:320px">
        <b>${escapeHtml(n.name)}</b><br><span style="color:#8b949e">${escapeHtml(n.label)}${
          n.group_name ? " · " + escapeHtml(n.group_name) : ""
        }${n.sub ? " · " + escapeHtml(n.sub) : ""}</span>
      </div>`)
    .linkColor(linkColor)
    .linkOpacity(1)  // 투명도는 linkColor 의 알파가 간선마다 따로 정한다.
    // 인용 횟수를 로그로 눌러 굵기에 반영한다. 선형으로 쓰면 215회짜리 한 쌍이
    // 화면을 다 덮는다.
    .linkWidth((l) => (l.weight ? Math.min(3.5, 0.5 + Math.log10(l.weight) * 1.2) : 0.6))
    .linkLabel((l) => (l.weight ? `${escapeHtml(l.type)} × ${l.weight}` : escapeHtml(l.type)))
    .linkDirectionalArrowLength(2.5)
    .linkDirectionalArrowRelPos(1)
    // 굵은 인용선에만 입자를 흘린다. 전부 켜면 프레임이 먼저 죽는다.
    .linkDirectionalParticles((l) => ((l.weight || 0) >= 20 ? 2 : 0))
    .linkDirectionalParticleWidth(1.4)
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleColor(particleColor)
    .onNodeHover((node) => {
      el("graph").style.cursor = node ? "pointer" : null;
      setHighlight(node);
    })
    .onNodeClick((node) => {
      showPanel(node);
      if (!expanded.has(node.id)) expandNode(node);
      else focusOn(node);
    })
    .onBackgroundClick(() => el("panel").classList.add("hidden"));

  // 노드가 많아지면 기본 반발력으로는 뭉쳐서 아무것도 안 보인다.
  graph.d3Force("charge").strength(-45);

  window.addEventListener("resize", () => {
    graph.width(el("graph").clientWidth).height(el("graph").clientHeight);
  });
}

function screenshot() {
  if (!graph) return;
  const canvas = graph.renderer().domElement;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const link = document.createElement("a");
  link.download = `lawgraphrag-graph-${stamp}.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();
  status(`스크린샷 저장됨: ${link.download}`);
}

async function searchLaw(query) {
  status(`"${query}" 검색 중…`);
  const res = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) { status("검색 실패"); return; }
  const { results } = await res.json();
  if (!results.length) { status(`"${query}" 결과 없음`); return; }

  const hit = results[0];
  let node = nodeMap.get(hit.id);
  if (!node) {
    // 개요 상한에 걸려 안 실린 법령일 수 있다. 그때는 서버가 만들어 준 노드를 심는다.
    merge({ nodes: [hit.node], links: [] });
    draw();
    node = nodeMap.get(hit.id);
  }
  showPanel(node);
  await expandNode(node);
  renderLegend();
  status(`"${query}" → ${node.name} (총 ${results.length}건 중 첫 번째)`);
}

el("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.value.trim()) {
    searchLaw(event.target.value.trim());
  }
});
el("reset").addEventListener("click", loadOverview);
el("screenshot").addEventListener("click", screenshot);
el("color-mode").addEventListener("change", (event) => setColorMode(event.target.value));
el("panel-close").addEventListener("click", () => el("panel").classList.add("hidden"));
el("panel-expand").addEventListener("click", () => { if (selected) expandNode(selected); });

// vendor 파일이 없으면 HTML 쪽 onerror 가 CDN 스크립트를 뒤늦게 붙인다.
// 그 경우 이 스크립트가 먼저 실행될 수 있어, 라이브러리가 뜰 때까지 짧게 기다린다.
(function waitForLibrary(attempt = 0) {
  if (typeof ForceGraph3D === "function") {
    initGraph();
    loadOverview();
  } else if (attempt < 100) {
    setTimeout(() => waitForLibrary(attempt + 1), 100);
  } else {
    status("3d-force-graph 로드 실패 — 오프라인이면 ops/static/vendor/ 에 파일을 두세요.");
  }
})();
