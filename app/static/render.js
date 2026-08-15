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

function renderCitationList(citations) {
  if (!citations.length) return '<li class="empty">적용되는 조문을 찾지 못했습니다.</li>';
  return citations
    .map(
      (c) => `<li>
        <div class="citation-title">${escapeHtml(c.law_name)} ${escapeHtml(c.article_label)}${
        c.title ? ` <span class="citation-subtitle">(${escapeHtml(c.title)})</span>` : ""
      }</div>
        <div class="citation-reason">${escapeHtml(c.reason)}</div>
        <a class="citation-link" href="${c.url}" target="_blank" rel="noopener">법제처 원문 보기 →</a>
      </li>`
    )
    .join("");
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
