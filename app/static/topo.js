/** 최소 TopoJSON 디코더.
 *
 * 왜 직접 구현하는가: 같은 경계 데이터가 GeoJSON으로는 7.5MB/18MB인데 TopoJSON으로는
 * 219KB/553KB다(30배 차이). 이 프로젝트는 외부 라이브러리를 쓰지 않으므로, 포맷이 단순한
 * 만큼 디코더를 직접 둔다.
 *
 * TopoJSON이 작은 이유는 두 가지다.
 *  1) 좌표를 정수로 양자화하고 이전 점과의 '차이'만 저장한다(delta encoding).
 *     -> transform.scale/translate로 원래 경위도로 되돌린다.
 *  2) 인접한 두 지역이 공유하는 경계선을 arc 하나로 두고 양쪽이 그 번호를 참조한다.
 *     -> 음수 인덱스 ~i는 "i번 arc를 뒤집어 쓴다"는 뜻이다.
 */

/** 양자화된 arc를 실제 경위도 좌표열로 되돌린다. */
function decodeArc(arc, transform) {
  const [sx, sy] = transform.scale;
  const [tx, ty] = transform.translate;
  let x = 0;
  let y = 0;
  return arc.map(([dx, dy]) => {
    x += dx;
    y += dy;
    return [x * sx + tx, y * sy + ty];
  });
}

/** arc 인덱스 목록을 이어붙여 하나의 링(닫힌 좌표열)으로 만든다. */
function stitchRing(arcIndexes, arcs) {
  const ring = [];
  for (const rawIndex of arcIndexes) {
    // 음수는 해당 arc를 역방향으로 쓴다는 표시다(~-1 === 0, ~-2 === 1).
    const reversed = rawIndex < 0;
    const points = arcs[reversed ? ~rawIndex : rawIndex];
    const ordered = reversed ? points.slice().reverse() : points;
    // 이어붙일 때 앞 arc의 끝점과 다음 arc의 시작점이 겹치므로 하나를 버린다.
    ring.push(...(ring.length ? ordered.slice(1) : ordered));
  }
  return ring;
}

/**
 * TopoJSON을 {code, name, rings} 목록으로 푼다.
 * Polygon/MultiPolygon을 구분하지 않고 링 배열로 평탄화한다 — 지도를 칠하는 데는
 * 외곽선과 구멍을 같은 path에 evenodd로 넣으면 되기 때문이다.
 */
function decodeTopology(topo, objectKey) {
  const arcs = topo.arcs.map((arc) => decodeArc(arc, topo.transform));
  const geometries = topo.objects[objectKey].geometries;

  return geometries.map((geom) => {
    // Polygon: [ring, hole...] / MultiPolygon: [[ring, hole...], ...]
    const polygons = geom.type === "MultiPolygon" ? geom.arcs : [geom.arcs];
    const rings = [];
    for (const polygon of polygons) {
      for (const ringArcs of polygon) rings.push(stitchRing(ringArcs, arcs));
    }
    return {
      code: geom.properties.code,
      name: geom.properties.name,
      rings,
    };
  });
}
