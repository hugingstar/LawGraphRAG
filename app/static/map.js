/** 행정구역 코로플레스 지도.
 *
 * 지역별 사건 수를 색 농도로 칠하고, 시도를 클릭하면 그 시도의 시군구로 드릴다운한다.
 * 색 농도 공식과 --heat CSS 변수는 이전 히트맵과 동일하게 유지해(0.18 + 비율*0.72)
 * 대시보드 전체의 시각 언어를 통일한다.
 */

const MAP_VIEWBOX = { width: 800, height: 900 };

/** 위도를 메르카토르 y로. 결과에 180/π를 곱해 경도(도 단위)와 같은 축척으로 맞춘다.
 *
 * 이 변환을 빼먹으면 y는 라디안 기반(한국 기준 폭 약 0.14)이고 x는 도 단위(약 7.3)라,
 * 가로세로에 같은 배율을 적용하는 순간 세로가 50배 눌려 지도가 납작해진다.
 */
function mercatorY(lat) {
  return (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
}

/** 그릴 지역들의 경계에 딱 맞게 배율을 정한다(지역마다 화면을 꽉 채우도록). */
function buildProjection(features) {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  for (const f of features) {
    for (const ring of f.rings) {
      for (const [lon, lat] of ring) {
        const y = mercatorY(lat);
        if (lon < minX) minX = lon;
        if (lon > maxX) maxX = lon;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }

  const pad = 20;
  const scale = Math.min(
    (MAP_VIEWBOX.width - pad * 2) / (maxX - minX),
    (MAP_VIEWBOX.height - pad * 2) / (maxY - minY)
  );
  // 남은 여백을 반씩 나눠 가운데 정렬한다.
  const offsetX = (MAP_VIEWBOX.width - (maxX - minX) * scale) / 2;
  const offsetY = (MAP_VIEWBOX.height - (maxY - minY) * scale) / 2;

  return ([lon, lat]) => [
    (lon - minX) * scale + offsetX,
    // SVG는 y가 아래로 증가하므로 위아래를 뒤집는다.
    (maxY - mercatorY(lat)) * scale + offsetY,
  ];
}

function ringToPath(ring, project) {
  let d = "";
  for (let i = 0; i < ring.length; i++) {
    const [x, y] = project(ring[i]);
    d += `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }
  return d + "Z";
}

/**
 * 지도를 그린다.
 *   container : 그릴 DOM 요소
 *   features  : decodeTopology 결과 (그릴 지역만 걸러서 넘긴다)
 *   countByCode : { [지역코드]: 건수 }
 *   onClick   : (feature) => void
 */
function renderChoropleth(container, features, countByCode, onClick) {
  if (!features.length) {
    container.innerHTML = '<p class="empty-state">표시할 지역이 없습니다.</p>';
    return;
  }

  const project = buildProjection(features);
  const max = Math.max(1, ...features.map((f) => countByCode[f.code] || 0));

  const paths = features
    .map((f) => {
      const count = countByCode[f.code] || 0;
      // 이전 히트맵과 같은 정규화 공식 — 0건은 완전히 비우고, 나머지는 0.18~0.90 사이로.
      const intensity = count === 0 ? 0 : 0.18 + (count / max) * 0.72;
      const d = f.rings.map((ring) => ringToPath(ring, project)).join(" ");
      return `<path class="region-path ${count === 0 ? "is-zero" : ""} ${intensity > 0.5 ? "is-heavy" : ""}"
        style="--heat:${intensity}" d="${d}" fill-rule="evenodd"
        data-code="${f.code}" data-name="${f.name}" data-count="${count}"
        tabindex="0" role="button" aria-label="${f.name} ${count}건"><title>${f.name}: ${count}건</title></path>`;
    })
    .join("");

  container.innerHTML = `<svg class="region-map" viewBox="0 0 ${MAP_VIEWBOX.width} ${MAP_VIEWBOX.height}"
    preserveAspectRatio="xMidYMid meet" role="group">${paths}</svg>`;

  if (onClick) {
    for (const path of container.querySelectorAll(".region-path")) {
      path.addEventListener("click", () => onClick(path.dataset));
      // 키보드 접근성: 포커스 상태에서 Enter/Space로도 드릴다운되게 한다.
      path.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick(path.dataset);
        }
      });
    }
  }
}
