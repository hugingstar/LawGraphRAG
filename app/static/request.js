// 작성자 정보(이름/직종/연락처)는 서버가 로그인 계정 프로필에서 채우므로 전송하지 않는다.
// 반면 사건 발생 지역/유형은 신고자 소속과 무관하므로 폼에서 받아 보낸다.
const FIELDS = {
  sido_code: "sido_code",
  sigungu_code: "sigungu_code",
  category_id: "category_id",
  occurred_at: "occurred-at",
  location: "location",
  background: "background",
  situation: "situation",
  action_taken: "action-taken",
  damage: "damage",
};

const fileInput = document.getElementById("attachments");
const filePickedListEl = document.getElementById("file-picked-list");
if (fileInput) wireFilePicker(fileInput, filePickedListEl);

// 접수 후 비우는 항목. 지역/유형은 남겨둔다 — 같은 지역에서 연달아 신고하는 경우가 많고,
// 시도를 비우면 시군구 목록이 시도 선택에 묶여 있어 어긋난 상태로 남기 때문이다.
const CLEARED_FIELDS = ["occurred-at", "location", "background", "situation", "action-taken", "damage"];

function clearInputs() {
  for (const elementId of CLEARED_FIELDS) {
    document.getElementById(elementId).value = "";
  }
  if (fileInput) {
    fileInput.value = "";
    fileInput.dispatchEvent(new Event("change"));
  }
}

const analyzeBtn = document.getElementById("analyze-btn");
const draftBtn = document.getElementById("draft-btn");

async function submitIncident(isDraft) {
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const btn = isDraft ? draftBtn : analyzeBtn;
  const background = document.getElementById("background").value.trim();

  statusEl.classList.remove("status-error");

  const sigunguCode = document.getElementById("sigungu_code").value;
  if (!sigunguCode) {
    statusEl.classList.add("status-error");
    statusEl.textContent = "사건 발생 지역(시·도와 시·군·구)을 선택해 주세요.";
    document.getElementById("sido_code").focus();
    return;
  }

  if (!isDraft && !background) {
    statusEl.classList.add("status-error");
    statusEl.textContent = "경위는 반드시 입력해야 합니다.";
    document.getElementById("background").focus();
    return;
  }

  const formData = new FormData();
  for (const [name, elementId] of Object.entries(FIELDS)) {
    formData.append(name, document.getElementById(elementId).value.trim());
  }
  if (isDraft) {
    formData.append("is_draft", "true");
  }
  if (fileInput) {
    [...fileInput.files].forEach((f) => formData.append("files", f));
  }

  const incidentIdStr = document.getElementById("incident-id")?.value;
  const url = incidentIdStr ? `/api/incidents/${incidentIdStr}/edit` : "/api/incidents";

  if (analyzeBtn) analyzeBtn.disabled = true;
  if (draftBtn) draftBtn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  statusEl.textContent = isDraft ? "임시 저장 중..." : "분석 및 접수 중...";
  resultEl.hidden = true;

  try {
    const resp = await fetch(url, { method: "POST", body: formData });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => null);
      throw new Error(detail && detail.detail ? detail.detail : `서버 오류 (${resp.status})`);
    }

    const incident = await resp.json();
    
    if (isDraft) {
      statusEl.textContent = "임시 저장되었습니다.";
      statusEl.classList.remove("status-error");
      
      const idEl = document.getElementById("incident-id");
      if (idEl) idEl.value = incident.id;
    } else {
      const metaParts = [incident.region, incident.category].filter(Boolean);
      document.getElementById("result-meta").textContent =
        `${metaParts.join(" · ")} · ${new Date(incident.created_at).toLocaleString("ko-KR")} 접수됨 — 관리자 검토가 요청되었습니다.`;
      document.getElementById("highlighted-text").innerHTML = renderHighlighted(incident.statement, incident.citations);
      document.getElementById("citation-list").innerHTML = incident.analysis_failed
        ? '<li class="empty">자동 조문 분석에 실패했습니다. 요청은 정상 접수되었으며 안전부서가 직접 검토합니다.</li>'
        : renderCitationList(incident.citations);

      resultEl.hidden = false;
      statusEl.textContent = "";

      bindHighlightClicks(document.getElementById("highlighted-text"));
      clearInputs();
      
      const idEl = document.getElementById("incident-id");
      if (idEl) idEl.value = "";
      
      resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (err) {
    statusEl.classList.add("status-error");
    statusEl.textContent = `오류: ${err.message}`;
  } finally {
    if (analyzeBtn) analyzeBtn.disabled = false;
    if (draftBtn) draftBtn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
}

if (analyzeBtn) {
  analyzeBtn.addEventListener("click", () => submitIncident(false));
}
if (draftBtn) {
  draftBtn.addEventListener("click", () => submitIncident(true));
}
