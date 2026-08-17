"use strict";

const LINK_COLORS = {
  CONTAINS: "#f2b134",
  HAS_ARTICLE: "#4dabf7",
  REFERENCES: "#ff8787",
  CITES: "#ff8787",       // 법령 단위로 말아 올린 REFERENCES
};
const DEFAULT_LINK_COLOR = "#5c6470";

const nodeMap = new Map();   // id -> 노드 객체(라이브러리가 x/y/z 를 여기에 심는다)
const linkKeys = new Set();  // "src|type|dst"
const linkStore = [];        // { s, t, type, weight }
const expanded = new Set();  // 이미 펼친 노드 (같은 노드 재요청 방지)

let graph = null;
let selected = null;

const el = (id) => document.getElementById(id);
const status = (text) => { el("graph-status").textContent = text; };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** 서버가 준 조각을 현재 그래프에 병합한다. 새로 늘어난 노드 수를 돌려준다. */
function merge(payload) {
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
  el("panel-sub").textContent = `${node.label}${node.sub ? " · " + node.sub : ""}`;
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
    renderLegend(payload.legend);
    if (payload.truncated) {
      status(`${nodeMap.size}개에서 잘렸습니다 (GRAPH_MAX_NODES 조정 가능)`);
    } else if (nodeMap.size === 0) {
      status("그래프가 비어 있습니다. graph_ingest 로 적재해 주세요.");
    }
  } catch (err) {
    status(`불러오기 실패: ${err}`);
  }
}

function renderLegend(legend) {
  el("legend").innerHTML = Object.entries(legend || {})
    .filter(([label]) => label !== "Unknown")
    .map(([label, color]) => `<span><i style="background:${escapeHtml(color)}"></i>${escapeHtml(label)}</span>`)
    .join("");
}

function initGraph() {
  graph = ForceGraph3D()(el("graph"))
    .backgroundColor("#0e1116")
    .nodeVal("val")
    .nodeColor("color")
    .nodeOpacity(0.92)
    .nodeLabel((n) => `<div style="font:12px system-ui;background:#161b22;color:#e6edf3;
        border:1px solid #2a3140;border-radius:6px;padding:5px 8px;max-width:320px">
        <b>${escapeHtml(n.name)}</b><br><span style="color:#8b949e">${escapeHtml(n.label)}${n.sub ? " · " + escapeHtml(n.sub) : ""}</span>
      </div>`)
    .linkColor((l) => LINK_COLORS[l.type] || DEFAULT_LINK_COLOR)
    .linkOpacity(0.3)
    // 인용 횟수를 로그로 눌러 굵기에 반영한다. 선형으로 쓰면 215회짜리 한 쌍이
    // 화면을 다 덮는다.
    .linkWidth((l) => (l.weight ? Math.min(3.5, 0.5 + Math.log10(l.weight) * 1.2) : 0.6))
    .linkLabel((l) => (l.weight ? `${escapeHtml(l.type)} × ${l.weight}` : escapeHtml(l.type)))
    .linkDirectionalArrowLength(2.5)
    .linkDirectionalArrowRelPos(1)
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

async function searchLaw(query) {
  status(`"${query}" 검색 중…`);
  const res = await fetch(`/api/graph/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) { status("검색 실패"); return; }
  const { results } = await res.json();
  if (!results.length) { status(`"${query}" 결과 없음`); return; }

  const hit = results[0];
  let node = nodeMap.get(hit.id);
  if (!node) {
    // 개요 상한에 걸려 안 실린 법령일 수 있다. 그때는 그 노드만 따로 심는다.
    merge({
      nodes: [{
        id: hit.id, label: "Law", name: hit.law_name || hit.law_id,
        sub: `조문 ${hit.article_count}개`, val: 1 + Math.min(hit.article_count, 500) ** 0.5,
        color: "#4dabf7", meta: { law_id: hit.law_id, article_count: hit.article_count },
      }],
      links: [],
    });
    draw();
    node = nodeMap.get(hit.id);
  }
  showPanel(node);
  await expandNode(node);
  status(`"${query}" → ${node.name} (총 ${results.length}건 중 첫 번째)`);
}

el("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.value.trim()) {
    searchLaw(event.target.value.trim());
  }
});
el("reset").addEventListener("click", loadOverview);
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
