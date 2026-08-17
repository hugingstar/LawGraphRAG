const listEl = document.getElementById("incident-list");
const sidoSelect = document.getElementById("sido_code");
const sigunguSelect = document.getElementById("sigungu_code");
const categorySelect = document.getElementById("category-select");
const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const countEl = document.getElementById("result-count");
const statusFilterEl = document.getElementById("status-filter");
let activeStatusFilter = "";

function nextAction(status) {
  if (status === "review_requested") return { next: "in_review", label: "검토 시작" };
  if (status === "supplement_requested") return { next: "in_review", label: "검토 재개" };
  return null;
}

function renderIncidentDetail(incident) {
  const action = nextAction(incident.status);
  // 담당자가 아직 없으면(=검토 시작 전) 아무나 시작할 수 있고, 있으면 그 사람만 계속 진행할 수 있다.
  const isOwner = !incident.assigned_manager_id || incident.assigned_manager_id === window.CURRENT_USER_ID;

  let composeSection;
  if (incident.status === "review_requested") {
    composeSection = `<p class="hint compose-locked-hint">아래 "검토 시작"을 눌러야 보완 요청·코멘트·최종 결과 등록을 할 수 있습니다.</p>`;
  } else if (!isOwner) {
    composeSection = `<p class="hint compose-locked-hint">이 사건은 <strong>${escapeHtml(incident.assigned_manager)}</strong>님이 검토를 시작한 담당 건입니다. 담당자만 코멘트를 남길 수 있습니다.</p>`;
  } else {
    composeSection = `<div class="thread-compose">
      <textarea data-role="body" rows="3" placeholder="보완이 필요한 항목이나 검토 코멘트를 작성하세요."></textarea>
      <div class="compose-actions">
        <button data-role="send" data-kind="supplement_request" class="btn-warn compose-btn">보완 요청<br>보내기</button>
        <button data-role="send" data-kind="comment" class="btn-comment compose-btn">코멘트<br>남기기</button>
        <button data-role="send" data-kind="conclusion" class="btn-conclude compose-btn">최종 결과 등록<br>(검토 완료)</button>
        <span class="compose-status" data-role="compose-status"></span>
      </div>
    </div>`;
  }

  return `
    <h3 class="timeline-title">심층 검토 요청에 작성된 내용</h3>
    ${renderRequestDetail(incident)}

    ${
      incident.attachments && incident.attachments.length
        ? `<h3 class="timeline-title section-gap">첨부파일</h3>${renderAttachments(incident)}`
        : ""
    }

    <h3 class="timeline-title section-gap">Law Owly 분석 결과</h3>
    <p class="hint analysis-hint">문구를 마우스로 드래그해서 선택하면 해당 구간만 다시 분석해 조문을 추가합니다. 이미 표시된 마커를 클릭하면 조문 보기/제거를 할 수 있습니다.</p>
    <div class="highlighted-wrap" data-role="analysis-wrap">${renderHighlighted(incident.statement, incident.citations)}</div>
    <div class="analysis-status" data-role="analysis-status"></div>
    <ul class="citation-list section-gap" data-role="citation-list">${renderEditableCitationList(incident.citations)}</ul>

    <h3 class="timeline-title section-gap">요청자와 주고받은 내용${
      incident.assigned_manager ? `<span class="assignee-tag">담당자: ${escapeHtml(incident.assigned_manager)}</span>` : ""
    }</h3>
    ${renderThread(incident.comments)}

    ${composeSection}

    <h3 class="timeline-title section-gap">처리 이력</h3>
    ${renderTimeline(incident.events)}
    ${
      action && isOwner
        ? `<div class="reviewer-actions">
             <button data-role="advance" data-next="${action.next}">${action.label}</button>
           </div>`
        : ""
    }
  `;
}

/** 인용 조문 목록 + 각 항목마다 회색 x 제거 버튼 (관리자 검토 전용 편집 가능 버전) */
function renderEditableCitationList(citations) {
  if (!citations.length) return '<li class="empty">적용되는 조문을 찾지 못했습니다.</li>';
  return citations
    .map(
      // 여기서는 분야별로 묶지 않는다 — data-index가 incident.citations 배열 순서와
      // 1:1로 맞아야 제거/추가가 동작한다. 분야는 배지로만 보여준다.
      (c, i) => `<li data-index="${i}">
        <button type="button" class="citation-remove-btn" data-role="remove-citation" data-index="${i}" aria-label="조문 삭제">×</button>
        <div class="citation-title">${
          c.domain_label ? `<span class="domain-badge">${escapeHtml(c.domain_label)}</span> ` : ""
        }${escapeHtml(c.law_name)} ${escapeHtml(c.article_label)}${
        c.title ? ` <span class="citation-subtitle">(${escapeHtml(c.title)})</span>` : ""
      }</div>
        ${c.issue_label ? `<div class="citation-issue">${escapeHtml(c.issue_label)}</div>` : ""}
        <div class="citation-reason">${escapeHtml(c.reason)}</div>
        <a class="citation-link" href="${c.url}" target="_blank" rel="noopener">법제처 원문 보기 →</a>
      </li>`
    )
    .join("");
}

/** DOM Range를 highlighted-wrap의 textContent 기준 문자 오프셋으로 변환한다.
 * renderHighlighted가 마커로 감싸도 글자 하나 빠뜨리지 않으므로 wrap.textContent는
 * incident.statement와 정확히 같다 — indexOf 추측 없이 정확한 위치를 구할 수 있다. */
function rangeToOffset(container, range) {
  const preRange = document.createRange();
  preRange.selectNodeContents(container);
  preRange.setEnd(range.startContainer, range.startOffset);
  return preRange.toString().length;
}

function citationKey(c) {
  return `${c.law_name}|${c.article_no ?? c.article_label}|${c.article_no_sub ?? ""}|${c.start}|${c.end}`;
}

async function saveCitations(incidentId, citations) {
  const resp = await fetch(`/api/incidents/${incidentId}/citations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(citations),
  });
  if (!resp.ok) throw new Error(`저장 실패 (${resp.status})`);
  return resp.json();
}

/** Law Owly 분석 결과 블록을 상호작용 가능하게 만든다: 드래그 선택 -> 재분석 추가,
 * 마커 클릭 -> 조문 보기/마커 제거 팝업, 조문 목록 x 버튼 -> 개별 삭제. */
function setupAnalysisWorkspace(detail, incident) {
  const wrap = detail.querySelector('[data-role="analysis-wrap"]');
  const listEl = detail.querySelector('[data-role="citation-list"]');
  const statusEl = detail.querySelector('[data-role="analysis-status"]');
  let popupEl = null;

  function closePopup() {
    if (popupEl) {
      popupEl.remove();
      popupEl = null;
    }
  }

  function repaint() {
    wrap.innerHTML = renderHighlighted(incident.statement, incident.citations);
    listEl.innerHTML = renderEditableCitationList(incident.citations);
    bindMarkClicks();
    bindRemoveButtons();
  }

  async function persist(successMessage) {
    try {
      await saveCitations(incident.id, incident.citations);
      if (successMessage) {
        statusEl.textContent = successMessage;
        setTimeout(() => {
          if (statusEl.textContent === successMessage) statusEl.textContent = "";
        }, 2500);
      }
    } catch (err) {
      statusEl.classList.add("status-error");
      statusEl.textContent = err.message;
    }
  }

  function removeOverlapping(start, end) {
    const before = incident.citations.length;
    incident.citations = incident.citations.filter((c) => !(c.start < end && c.end > start));
    return before !== incident.citations.length;
  }

  function showMarkPopup(markEl) {
    closePopup();
    const start = Number(markEl.dataset.start);
    const end = Number(markEl.dataset.end);
    const overlapping = incident.citations.filter((c) => c.start < end && c.end > start);
    if (!overlapping.length) return;

    popupEl = document.createElement("div");
    popupEl.className = "mark-popup";
    popupEl.innerHTML = `
      <div class="mark-popup-laws">
        ${overlapping
          .map(
            (c) => `<div class="mark-popup-law">
              <span>${escapeHtml(c.law_name)} ${escapeHtml(c.article_label)}</span>
              <a href="${c.url}" target="_blank" rel="noopener" class="mark-popup-view">조문 보기</a>
            </div>`
          )
          .join("")}
      </div>
      <button type="button" class="mark-popup-remove">마커 제거${overlapping.length > 1 ? ` (${overlapping.length}건)` : ""}</button>
    `;

    const wrapRect = wrap.getBoundingClientRect();
    const markRect = markEl.getBoundingClientRect();
    popupEl.style.left = `${markRect.left - wrapRect.left}px`;
    popupEl.style.top = `${markRect.bottom - wrapRect.top + 6}px`;

    popupEl.querySelector(".mark-popup-remove").addEventListener("click", async (e) => {
      e.stopPropagation();
      removeOverlapping(start, end);
      closePopup();
      repaint();
      await persist("마커를 제거했습니다.");
    });

    wrap.appendChild(popupEl);
  }

  function bindMarkClicks() {
    wrap.querySelectorAll("mark").forEach((markEl) => {
      markEl.addEventListener("click", (e) => {
        e.stopPropagation();
        showMarkPopup(markEl);
      });
    });
  }

  function bindRemoveButtons() {
    listEl.querySelectorAll('[data-role="remove-citation"]').forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const idx = Number(btn.dataset.index);
        incident.citations.splice(idx, 1);
        repaint();
        await persist("조문을 삭제했습니다.");
      });
    });
  }

  async function analyzeSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return;
    const anchorInWrap = wrap.contains(selection.anchorNode);
    const focusInWrap = wrap.contains(selection.focusNode);
    if (!anchorInWrap || !focusInWrap) return;

    const range = selection.getRangeAt(0);
    const selectedText = range.toString();
    if (!selectedText.trim() || selectedText.length < 2) return;

    const start = rangeToOffset(wrap, range);
    const end = start + selectedText.length;
    selection.removeAllRanges();

    statusEl.classList.remove("status-error");
    statusEl.textContent = "선택한 구간 분석 중...";

    try {
      const resp = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ text: selectedText }),
      });
      if (!resp.ok) throw new Error(`서버 오류 (${resp.status})`);
      const data = await resp.json();

      const existingKeys = new Set(incident.citations.map(citationKey));
      let added = 0;
      for (const c of data.citations) {
        const shifted = { ...c, start: c.start + start, end: c.end + start };
        if (existingKeys.has(citationKey(shifted))) continue;
        incident.citations.push(shifted);
        existingKeys.add(citationKey(shifted));
        added += 1;
      }

      if (added === 0) {
        statusEl.textContent = "선택한 구간에서 적용되는 조문을 찾지 못했습니다.";
        return;
      }

      repaint();
      statusEl.textContent = `${added}개 조문을 추가했습니다.`;
      await persist();
    } catch (err) {
      statusEl.classList.add("status-error");
      statusEl.textContent = err.message;
    }
  }

  wrap.addEventListener("mouseup", () => {
    // 팝업 버튼 클릭 등으로 인한 의도치 않은 재분석을 막기 위해 다음 이벤트 루프에서 처리
    setTimeout(analyzeSelection, 0);
  });

  document.addEventListener("click", (e) => {
    if (popupEl && !popupEl.contains(e.target) && e.target.tagName !== "MARK") closePopup();
  });

  bindMarkClicks();
  bindRemoveButtons();
}

async function updateStatus(incidentId, next) {
  const resp = await fetch(`/api/incidents/${incidentId}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ status: next }),
  });
  if (!resp.ok) throw new Error(`서버 오류 (${resp.status})`);
  return resp.json();
}

function paintDetail(item, incident) {
  const summary = item.querySelector(".incident-summary");
  const detail = item.querySelector(".incident-detail");

  detail.innerHTML = renderIncidentDetail(incident);
  summary.querySelector(".status-badge").outerHTML = statusBadge(incident.status);
  setupAnalysisWorkspace(detail, incident);

  detail.querySelectorAll('[data-role="send"]').forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const bodyEl = detail.querySelector('[data-role="body"]');
      const statusEl = detail.querySelector('[data-role="compose-status"]');
      const body = bodyEl.value.trim();
      if (!body) {
        statusEl.textContent = "내용을 입력해 주세요.";
        return;
      }
      detail.querySelectorAll('[data-role="send"]').forEach((b) => (b.disabled = true));
      statusEl.textContent = "전송 중...";
      try {
        Object.assign(incident, await postComment(incident.id, btn.dataset.kind, body));
        paintDetail(item, incident);
      } catch (err) {
        statusEl.textContent = err.message;
        detail.querySelectorAll('[data-role="send"]').forEach((b) => (b.disabled = false));
      }
    });
  });

  const advanceBtn = detail.querySelector('[data-role="advance"]');
  if (advanceBtn) {
    advanceBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      advanceBtn.disabled = true;
      try {
        Object.assign(incident, await updateStatus(incident.id, advanceBtn.dataset.next));
        paintDetail(item, incident);
      } catch (err) {
        alert(err.message);
        advanceBtn.disabled = false;
      }
    });
  }
}

function renderIncidentList(incidents) {
  if (!incidents.length) {
    listEl.innerHTML = '<li class="empty-state">조건에 해당하는 사건이 없습니다.</li>';
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
        <span class="incident-chevron" aria-hidden="true">▶</span>
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

function buildParams() {
  const params = new URLSearchParams();
  if (sidoSelect.value) params.set("sido_code", sidoSelect.value);
  if (sigunguSelect.value) params.set("sigungu_code", sigunguSelect.value);
  if (categorySelect.value) params.set("category_id", categorySelect.value);
  if (activeStatusFilter) params.set("status", activeStatusFilter);
  const keyword = searchInput.value.trim();
  if (keyword) params.set("q", keyword);
  return params;
}

async function loadIncidents() {
  const params = buildParams();
  const qs = params.toString();
  history.replaceState(null, "", qs ? `/review?${qs}` : "/review");

  listEl.innerHTML = '<li class="empty-state">불러오는 중...</li>';
  countEl.textContent = "";

  const resp = await fetch(`/api/review/incidents?${qs}`);
  if (!resp.ok) {
    listEl.innerHTML = '<li class="empty-state">사건 이력을 불러오지 못했습니다.</li>';
    return;
  }
  const incidents = await resp.json();
  countEl.textContent = `${incidents.length}건`;
  renderIncidentList(incidents);
}

// 시도를 바꾸면 regions.js가 시군구 목록을 다시 채우므로, 그 뒤에 목록을 새로 불러오도록
// 이벤트 순서에 기대지 않고 두 셀렉트 모두에 리스너를 건다.
sidoSelect.addEventListener("change", loadIncidents);
sigunguSelect.addEventListener("change", loadIncidents);
categorySelect.addEventListener("change", loadIncidents);
searchBtn.addEventListener("click", loadIncidents);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadIncidents();
});

statusFilterEl.querySelectorAll(".status-filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    activeStatusFilter = btn.dataset.status;
    statusFilterEl
      .querySelectorAll(".status-filter-btn")
      .forEach((b) => b.classList.toggle("active", b === btn));
    loadIncidents();
  });
});

loadIncidents();
