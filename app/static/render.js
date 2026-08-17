const PALETTE = ["#fde68a", "#bfdbfe", "#bbf7d0", "#fbcfe8", "#fecaca", "#ddd6fe"];

function colorForLaw(lawName) {
  let hash = 0;
  for (let i = 0; i < lawName.length; i++) {
    hash = (hash * 31 + lawName.charCodeAt(i)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderHighlighted(text, citations) {
  const sorted = [...citations].sort((a, b) => a.start - b.start);
  let cursor = 0;
  let html = "";

  for (const c of sorted) {
    if (c.start < cursor) continue; // 안전장치: 겹치는 구간 스킵
    html += escapeHtml(text.slice(cursor, c.start));
    const color = colorForLaw(c.law_name);
    const tooltip = `${c.law_name} ${c.article_label}${c.title ? " " + c.title : ""} — ${c.reason}`;
    html += `<mark style="background:${color}" title="${escapeHtml(tooltip)}" data-url="${c.url}" data-start="${c.start}" data-end="${c.end}">`;
    html += escapeHtml(text.slice(c.start, c.end));
    html += "</mark>";
    cursor = c.end;
  }
  html += escapeHtml(text.slice(cursor));
  return html;
}

const UNGROUPED_LABEL = "기타";

// 쟁점 추출이 반드시 훑는 관점(app/citations.py의 REVIEW_CHECKLIST와 같은 순서).
// 결과가 없어도 자리를 지켜야, 모델이 관점을 통째로 빠뜨렸을 때 "미검토"로 드러난다.
const REVIEW_CHECKLIST_LABELS = ["형사", "민사", "노동", "가족·상속"];

function citationItemHtml(c) {
  return `<li>
    <div class="citation-title">${escapeHtml(c.law_name)} ${escapeHtml(c.article_label)}${
    c.title ? ` <span class="citation-subtitle">(${escapeHtml(c.title)})</span>` : ""
  }</div>
    ${c.issue_label ? `<div class="citation-issue">${escapeHtml(c.issue_label)}</div>` : ""}
    <div class="citation-reason">${escapeHtml(c.reason)}</div>
    <a class="citation-link" href="${c.url}" target="_blank" rel="noopener">법제처 원문 보기 →</a>
  </li>`;
}

/** 인용 조문을 분야(형사·민사·노동·가족 …)별로 묶어 보여준다.
 *
 * 하나의 사실관계는 여러 분야에 동시에 걸리는 것이 정상인데, 조문을 한 줄로 죽 늘어놓으면
 * 어느 관점에서 나온 조문인지 읽히지 않는다. 같은 조문이 여러 문장에 앵커되어 두 번
 * 들어올 수 있으므로 분야 안에서 법령+조번호로 중복을 없앤다(하이라이트는 그대로 둔다).
 *
 * issues/dismissed를 함께 넘기면(= /analyze 실시간 분석) 분야마다 네 상태를 구분한다:
 *   조문 있음 / 쟁점은 섰지만 조문 못 찾음 / 검토 후 불성립 / 미검토.
 * 마지막 상태가 핵심이다 — 쟁점 추출이 관점 하나를 통째로 빠뜨려도 예전에는 그 분야가
 * 화면에서 아예 사라져, "봤는데 아니다"와 구분이 안 됐다.
 *
 * 저장된 사건(results.js)은 이 정보가 없으므로 인자를 안 넘기고, 그때는 조문이 있는
 * 분야만 묶어 보여준다(없는 분야를 전부 "미검토"로 채우면 거짓말이 된다). */
function renderCitationList(citations, issues, dismissed) {
  const live = Array.isArray(issues);
  const groups = new Map(); // label -> citation[]
  const ensure = (label) => {
    if (!groups.has(label)) groups.set(label, []);
    return groups.get(label);
  };

  // 체크리스트 -> 추출된 쟁점 -> 불성립 순으로 미리 깔아두면, 조문이 도착하는 순서와
  // 무관하게 분야 순서가 안정적이다.
  if (live) {
    REVIEW_CHECKLIST_LABELS.forEach(ensure);
    issues.forEach((i) => ensure(i.domain_label || UNGROUPED_LABEL));
    (dismissed || []).forEach((d) => ensure(d.domain_label || UNGROUPED_LABEL));
  }

  const seen = new Set();
  for (const c of citations) {
    const label = c.domain_label || UNGROUPED_LABEL;
    const key = `${label}|${c.law_name}|${c.article_label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    ensure(label).push(c);
  }

  const reviewed = new Set((issues || []).map((i) => i.domain_label || UNGROUPED_LABEL));
  const dismissedBy = new Map(
    (dismissed || []).map((d) => [d.domain_label || UNGROUPED_LABEL, d.reason])
  );

  function statusHtml(label, count) {
    if (count) return `<span class="domain-count">${count}개 조문</span>`;
    if (dismissedBy.has(label)) {
      const reason = dismissedBy.get(label);
      return (
        '<span class="domain-count domain-dismissed">해당 사항 없음</span>' +
        (reason ? `<span class="domain-reason">${escapeHtml(reason)}</span>` : "")
      );
    }
    if (reviewed.has(label)) {
      return '<span class="domain-count">검토함 · 적용 조문 없음</span>';
    }
    return '<span class="domain-count domain-unreviewed">미검토</span>';
  }

  const rendered = [...groups.entries()]
    .map(
      ([label, items]) => `<li class="citation-domain">
        <div class="citation-domain-head">
          <span class="domain-badge">${escapeHtml(label)}</span>
          ${statusHtml(label, items.length)}
        </div>
        ${items.length ? `<ul class="citation-sublist">${items.map(citationItemHtml).join("")}</ul>` : ""}
      </li>`
    )
    .join("");

  if (!rendered) return '<li class="empty">적용되는 조문을 찾지 못했습니다.</li>';
  return rendered;
}

function bindHighlightClicks(root) {
  root.querySelectorAll("mark").forEach((el) => {
    el.addEventListener("click", () => window.open(el.dataset.url, "_blank"));
  });
}

const STATUS_LABELS = {
  draft: "임시 저장",
  review_requested: "검토 요청",
  in_review: "검토중",
  supplement_requested: "보완 요청",
  supplement_completed: "보완 완료",
  completed: "검토 완료",
};

function statusBadge(status) {
  return `<span class="status-badge status-${status}">${STATUS_LABELS[status] || status}</span>`;
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString("ko-KR");
}

/** 목록 열처럼 좁은 자리에 쓰는 날짜만 표기(시:분 생략). */
function formatDate(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("ko-KR");
}

/** 사건 내용을 "OO에 관한 요청" 형태의 핵심 키워드로 요약한다.
 * 조문 분석 결과가 있으면 첫 인용 조문 제목을, 없으면 사고장소를 사용한다. */
function contentLabel(i) {
  const citations = i.citations || [];
  if (citations.length) {
    const keyword = citations[0].title || citations[0].article_label;
    return `${keyword}에 관한 요청`;
  }
  if (i.location) return `${i.location}에 관한 요청`;
  return `${i.region || "지역 미상"} 사건 요청`;
}

/** 심층 검토 요청에 작성된 내용을 항목별로 보여준다. */
function renderRequestDetail(incident) {
  const rows = [
    ["발생 지역", incident.region],
    ["사건 유형", incident.category],
    ["작성자", incident.reporter_summary],
    ["사고일시", incident.occurred_at ? formatDateTime(incident.occurred_at) : ""],
    ["사고장소", incident.location],
    ["경위", incident.background],
    ["당시상황", incident.situation],
    ["조치내용", incident.action_taken],
    ["피해상황", incident.damage],
  ].filter(([, value]) => value);

  return `<dl class="detail-grid">
    ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}
  </dl>`;
}

/** 상태 변경 처리 이력(IncidentEvent 배열)을 타임라인으로 그린다.
 * review.js(관리자 검토)와 results.js(나의 요청 리스트)가 공유한다. */
function renderTimeline(events) {
  if (!events || !events.length) return "";
  return `<ul class="timeline">
    ${events
      .map(
        (e) => `<li>
          <span class="status-badge status-${e.status}">${escapeHtml(e.status_label)}</span>
          ${e.actor ? `<span class="timeline-actor">${escapeHtml(e.actor)}</span>` : ""}
          <span class="timeline-date">${formatDateTime(e.created_at)}</span>
          ${e.note ? `<div class="timeline-note">${escapeHtml(e.note)}</div>` : ""}
        </li>`
      )
      .join("")}
  </ul>`;
}

/** 요청자 <-> 안전부서가 주고받은 스레드 */
function renderThread(comments) {
  if (!comments || !comments.length) {
    return '<p class="empty-state">아직 주고받은 메시지가 없습니다.</p>';
  }
  return `<ul class="thread">
    ${comments
      .map(
        (c) => `<li class="kind-${c.kind}">
          <div class="thread-head">
            <span class="thread-kind thread-kind-${c.kind}">${escapeHtml(c.kind_label)}</span>
            <span class="thread-author">${escapeHtml(c.author || "-")}</span>
            <span class="thread-date">${formatDateTime(c.created_at)}</span>
          </div>
          <div class="thread-body">${escapeHtml(c.body)}</div>
        </li>`
      )
      .join("")}
  </ul>`;
}

const PREVIEWABLE_TYPES = new Set(["application/pdf", "image/jpeg", "image/png"]);

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

/** 사건에 첨부된 파일 목록: 다운로드는 항상, 미리보기는 PDF/이미지에서만 제공한다. */
function renderAttachments(incident) {
  const files = incident.attachments || [];
  if (!files.length) return "";

  const base = `/api/incidents/${incident.id}/attachments`;
  return `<ul class="attachment-list">
    ${files
      .map(
        (a) => `<li class="attachment-item">
          <span class="attachment-icon" aria-hidden="true">📎</span>
          <span class="attachment-name" title="${escapeHtml(a.filename)}">${escapeHtml(a.filename)}</span>
          <span class="attachment-size">${formatFileSize(a.size_bytes)}</span>
          ${
            PREVIEWABLE_TYPES.has(a.content_type)
              ? `<a class="attachment-link" href="${base}/${a.id}?disposition=inline" target="_blank" rel="noopener">미리보기</a>`
              : ""
          }
          <a class="attachment-link" href="${base}/${a.id}?disposition=attachment">다운로드</a>
        </li>`
      )
      .join("")}
  </ul>`;
}

/** <input type=file multiple> 선택 목록을 개별 제거 가능한 리스트로 그려준다.
 * request.js(작성 폼)와 results.js(보완 답변)가 공유한다. */
function wireFilePicker(fileInput, listEl) {
  function removeFileAt(index) {
    const dt = new DataTransfer();
    [...fileInput.files].forEach((f, i) => {
      if (i !== index) dt.items.add(f);
    });
    fileInput.files = dt.files;
    repaint();
  }

  function repaint() {
    const files = [...fileInput.files];
    listEl.innerHTML = files
      .map(
        (f, i) => `<li>
          <span class="file-picked-name">${escapeHtml(f.name)}</span>
          <span class="file-picked-size">${formatFileSize(f.size)}</span>
          <button type="button" class="file-remove-btn" data-index="${i}" aria-label="첨부 제거">×</button>
        </li>`
      )
      .join("");
    listEl.querySelectorAll(".file-remove-btn").forEach((btn) => {
      btn.addEventListener("click", () => removeFileAt(Number(btn.dataset.index)));
    });
  }

  fileInput.addEventListener("change", repaint);
}

async function postComment(incidentId, kind, body, files) {
  const formData = new FormData();
  formData.append("kind", kind);
  formData.append("body", body);
  (files || []).forEach((f) => formData.append("files", f));

  const resp = await fetch(`/api/incidents/${incidentId}/comments`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail && detail.detail ? detail.detail : `서버 오류 (${resp.status})`);
  }
  return resp.json();
}
