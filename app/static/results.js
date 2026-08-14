const listEl = document.getElementById("incident-list");
const countEl = document.getElementById("result-count");
const statusFilterEl = document.getElementById("status-filter");
let activeStatusFilter = "";

const STATUS_GUIDE = {
  review_requested: "안전부서 접수 완료. 검토 시작을 기다리고 있습니다.",
  in_review: "안전부서가 검토를 진행하고 있습니다.",
  supplement_requested: "안전부서가 내용 보완을 요청했습니다. 아래에서 신고 내용을 수정하거나 보완 내용을 작성해 주세요.",
  supplement_completed: "보완 내용을 제출했습니다. 안전부서 확인을 기다리고 있습니다.",
  completed: "검토가 완료되었습니다. 추가로 궁금한 점은 아래에서 문의할 수 있습니다.",
};

// 처음 작성했던 틀(사고일시/사고장소/사고경위/당시상황/조치내용/피해상황)을 그대로 유지한 채
// 값만 고쳐 쓸 수 있게 한다. datetime-local은 시간대 변환 없이 저장된 그대로의 숫자를 쓴다
// (작성 시에도 브라우저가 입력한 숫자를 그대로 보냈으므로 대칭적으로 맞춘다).
function renderEditForm(incident) {
  const occurredVal = incident.occurred_at ? incident.occurred_at.slice(0, 16) : "";
  return `<div class="edit-form">
    <div class="field-row">
      <div class="field">
        <label class="field-label">사고일시</label>
        <input class="text-input" type="datetime-local" data-role="edit-occurred_at" value="${occurredVal}">
      </div>
      <div class="field">
        <label class="field-label">사고장소</label>
        <input class="text-input" type="text" data-role="edit-location" value="${escapeHtml(incident.location || "")}">
      </div>
    </div>
    <div class="field">
      <label class="field-label">사고경위 <span class="required-mark">*</span></label>
      <textarea data-role="edit-background" rows="4">${escapeHtml(incident.background || "")}</textarea>
    </div>
    <div class="field">
      <label class="field-label">당시상황</label>
      <textarea data-role="edit-situation" rows="3">${escapeHtml(incident.situation || "")}</textarea>
    </div>
    <div class="field">
      <label class="field-label">조치내용</label>
      <textarea data-role="edit-action_taken" rows="3">${escapeHtml(incident.action_taken || "")}</textarea>
    </div>
    <div class="field">
      <label class="field-label">피해상황</label>
      <textarea data-role="edit-damage" rows="3">${escapeHtml(incident.damage || "")}</textarea>
    </div>
    <div class="compose-actions">
      <button type="button" data-role="edit-save" class="btn-comment compose-btn">저장</button>
      <button type="button" data-role="edit-cancel" class="btn-secondary compose-btn">취소</button>
      <span class="compose-status" data-role="edit-status"></span>
    </div>
  </div>`;
}

function renderRequestSection(incident, editing) {
  const canEdit = incident.status !== "completed";
  return `<div class="list-head">
      <h3 class="timeline-title" style="margin:0">작성한 신고 내용</h3>
      ${
        canEdit
          ? `<button type="button" class="btn-secondary edit-toggle-btn" data-role="${editing ? "edit-cancel-top" : "edit-toggle"}">${editing ? "수정 취소" : "내용 수정"}</button>`
          : ""
      }
    </div>
    ${editing ? renderEditForm(incident) : renderRequestDetail(incident)}`;
}

function renderComposer(incident) {
  // 보완 요청을 받은 상태면 보완 답변(파일 첨부 가능), 그 외에는 추가 문의를 남길 수 있다.
  const isSupplement = incident.status === "supplement_requested";
  const kind = isSupplement ? "supplement_reply" : "follow_up";
  const label = isSupplement ? "보완 내용 제출" : "추가 문의 보내기";
  const placeholder = isSupplement
    ? "안전부서가 요청한 보완 내용을 작성해 주세요."
    : "검토 결과에 대해 추가로 문의할 내용을 작성해 주세요.";

  return `<div class="thread-compose">
    <textarea data-role="body" rows="3" placeholder="${placeholder}"></textarea>
    ${
      isSupplement
        ? `<div class="field">
             <label class="field-label" for="supplement-files-${incident.id}">보완 파일 첨부 (선택)</label>
             <input class="file-input" type="file" id="supplement-files-${incident.id}" data-role="supplement-files" multiple accept=".pdf,.doc,.docx,.hwp,.hwpx,.xls,.xlsx,.jpg,.jpeg,.png">
             <ul class="file-picked-list" data-role="supplement-file-list"></ul>
           </div>`
        : ""
    }
    <div class="compose-actions">
      <button data-role="send" data-kind="${kind}" class="${isSupplement ? "btn-warn" : ""}">${label}</button>
      <span class="compose-status" data-role="compose-status"></span>
    </div>
  </div>`;
}

function renderDetail(incident, editing) {
  return `
    <p class="status-guide">${escapeHtml(STATUS_GUIDE[incident.status] || "")}</p>
    ${renderRequestSection(incident, editing)}

    ${
      incident.attachments && incident.attachments.length
        ? `<h3 class="timeline-title section-gap">첨부파일</h3>${renderAttachments(incident)}`
        : ""
    }

    <h3 class="timeline-title section-gap">Law Owly 분석 결과</h3>
    <ul class="citation-list">${renderCitationList(incident.citations)}</ul>

    ${
      incident.status === "completed" && incident.reviewer_note
        ? `<h3 class="timeline-title section-gap">최종 검토 결과</h3>
           <div class="final-note">${escapeHtml(incident.reviewer_note)}</div>`
        : ""
    }

    <h3 class="timeline-title section-gap">안전부서와 주고받은 내용</h3>
    ${renderThread(incident.comments)}
    ${renderComposer(incident)}

    <h3 class="timeline-title section-gap">처리 이력</h3>
    ${renderTimeline(incident.events)}
  `;
}

async function saveIncidentEdit(incidentId, fields) {
  const resp = await fetch(`/api/incidents/${incidentId}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail && detail.detail ? detail.detail : `서버 오류 (${resp.status})`);
  }
  return resp.json();
}

function paintDetail(item, incident, editing = false) {
  const summary = item.querySelector(".incident-summary");
  const detail = item.querySelector(".incident-detail");

  detail.innerHTML = renderDetail(incident, editing);
  bindHighlightClicks(detail);
  summary.querySelector(".status-badge").outerHTML = statusBadge(incident.status);

  const toggleBtn = detail.querySelector('[data-role="edit-toggle"]');
  if (toggleBtn) toggleBtn.addEventListener("click", () => paintDetail(item, incident, true));

  const cancelTopBtn = detail.querySelector('[data-role="edit-cancel-top"]');
  if (cancelTopBtn) cancelTopBtn.addEventListener("click", () => paintDetail(item, incident, false));

  const cancelBtn = detail.querySelector('[data-role="edit-cancel"]');
  if (cancelBtn) cancelBtn.addEventListener("click", () => paintDetail(item, incident, false));

  const saveBtn = detail.querySelector('[data-role="edit-save"]');
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const statusEl = detail.querySelector('[data-role="edit-status"]');
      const fields = {
        occurred_at: detail.querySelector('[data-role="edit-occurred_at"]').value,
        location: detail.querySelector('[data-role="edit-location"]').value.trim(),
        background: detail.querySelector('[data-role="edit-background"]').value.trim(),
        situation: detail.querySelector('[data-role="edit-situation"]').value.trim(),
        action_taken: detail.querySelector('[data-role="edit-action_taken"]').value.trim(),
        damage: detail.querySelector('[data-role="edit-damage"]').value.trim(),
      };
      if (!fields.background) {
        statusEl.textContent = "사고경위는 반드시 입력해야 합니다.";
        return;
      }
      saveBtn.disabled = true;
      statusEl.textContent = "저장 중...";
      try {
        Object.assign(incident, await saveIncidentEdit(incident.id, fields));
        paintDetail(item, incident, false);
      } catch (err) {
        statusEl.textContent = err.message;
        saveBtn.disabled = false;
      }
    });
  }

  const supplementFileInput = detail.querySelector('[data-role="supplement-files"]');
  const supplementFileList = detail.querySelector('[data-role="supplement-file-list"]');
  if (supplementFileInput) wireFilePicker(supplementFileInput, supplementFileList);

  const sendBtn = detail.querySelector('[data-role="send"]');
  sendBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const bodyEl = detail.querySelector('[data-role="body"]');
    const statusEl = detail.querySelector('[data-role="compose-status"]');
    const body = bodyEl.value.trim();
    if (!body) {
      statusEl.textContent = "내용을 입력해 주세요.";
      return;
    }
    const files = supplementFileInput ? [...supplementFileInput.files] : [];
    sendBtn.disabled = true;
    statusEl.textContent = "전송 중...";
    try {
      Object.assign(incident, await postComment(incident.id, sendBtn.dataset.kind, body, files));
      paintDetail(item, incident);
    } catch (err) {
      statusEl.textContent = err.message;
      sendBtn.disabled = false;
    }
  });
}

function renderList(incidents) {
  if (!incidents.length) {
    listEl.innerHTML = activeStatusFilter
      ? '<li class="empty-state">조건에 해당하는 요청이 없습니다.</li>'
      : '<li class="empty-state">요청한 심층 검토가 없습니다. "나의 요청" 메뉴에서 요청할 수 있습니다.</li>';
    return;
  }

  listEl.innerHTML = incidents
    .map(
      (i) => `
    <li class="incident-item" data-id="${i.id}">
      <div class="incident-summary">
        <span class="col-status">${statusBadge(i.status)}</span>
        <span class="incident-dept">${escapeHtml(i.category || "미분류")}</span>
        <span class="incident-site-tag">${escapeHtml(i.region || "지역 미상")}</span>
        <span class="incident-excerpt">${escapeHtml(contentLabel(i))}</span>
        <span class="incident-date" title="사고일시: ${i.occurred_at ? formatDateTime(i.occurred_at) : "미기재"}">${formatDate(i.occurred_at)}</span>
        <span class="incident-date" title="요청일시: ${formatDateTime(i.created_at)}">${formatDate(i.created_at)}</span>
      </div>
      <div class="incident-detail"></div>
    </li>`
    )
    .join("");

  listEl.querySelectorAll(".incident-item").forEach((item) => {
    const incident = incidents.find((i) => String(i.id) === item.dataset.id);
    item.querySelector(".incident-summary").addEventListener("click", () => {
      const expanded = item.classList.toggle("expanded");
      const detail = item.querySelector(".incident-detail");
      if (expanded && !detail.dataset.loaded) {
        detail.dataset.loaded = "1";
        paintDetail(item, incident);
      }
    });
  });
}

async function load() {
  history.replaceState(null, "", activeStatusFilter ? `/results?status=${activeStatusFilter}` : "/results");
  listEl.innerHTML = '<li class="empty-state">불러오는 중...</li>';
  countEl.textContent = "";

  const params = new URLSearchParams();
  if (activeStatusFilter) params.set("status", activeStatusFilter);

  const resp = await fetch(`/api/results/my-incidents?${params.toString()}`);
  if (!resp.ok) {
    listEl.innerHTML = '<li class="empty-state">목록을 불러오지 못했습니다.</li>';
    return;
  }
  const incidents = await resp.json();
  countEl.textContent = `${incidents.length}건`;
  renderList(incidents);
}

statusFilterEl.querySelectorAll(".status-filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    activeStatusFilter = btn.dataset.status;
    statusFilterEl
      .querySelectorAll(".status-filter-btn")
      .forEach((b) => b.classList.toggle("active", b === btn));
    load();
  });
});

load();
