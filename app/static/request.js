// 사업장/부서/작성자 정보는 서버가 로그인 계정 프로필에서 채우므로 전송하지 않는다.
const FIELDS = {
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

function clearInputs() {
  for (const elementId of Object.values(FIELDS)) {
    document.getElementById(elementId).value = "";
  }
  if (fileInput) {
    fileInput.value = "";
    fileInput.dispatchEvent(new Event("change"));
  }
}

// 프로필이 미완성이면 폼 자체가 렌더링되지 않는다.
const analyzeBtn = document.getElementById("analyze-btn");
if (analyzeBtn)
analyzeBtn.addEventListener("click", async () => {
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const btn = document.getElementById("analyze-btn");
  const background = document.getElementById("background").value.trim();

  statusEl.classList.remove("status-error");

  if (!background) {
    statusEl.classList.add("status-error");
    statusEl.textContent = "사고경위는 반드시 입력해야 합니다.";
    document.getElementById("background").focus();
    return;
  }

  const formData = new FormData();
  for (const [name, elementId] of Object.entries(FIELDS)) {
    formData.append(name, document.getElementById(elementId).value.trim());
  }
  if (fileInput) {
    [...fileInput.files].forEach((f) => formData.append("files", f));
  }

  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  statusEl.textContent = "분석 및 접수 중...";
  resultEl.hidden = true;

  try {
    const resp = await fetch("/api/incidents", { method: "POST", body: formData });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => null);
      throw new Error(detail && detail.detail ? detail.detail : `서버 오류 (${resp.status})`);
    }

    const incident = await resp.json();
    document.getElementById("result-meta").textContent =
      `${incident.department} · ${incident.site} · ${new Date(incident.created_at).toLocaleString("ko-KR")} 접수됨 — 관리자 검토가 요청되었습니다.`;
    document.getElementById("highlighted-text").innerHTML = renderHighlighted(incident.statement, incident.citations);
    document.getElementById("citation-list").innerHTML = incident.analysis_failed
      ? '<li class="empty">자동 조문 분석에 실패했습니다. 요청은 정상 접수되었으며 안전부서가 직접 검토합니다.</li>'
      : renderCitationList(incident.citations);

    resultEl.hidden = false;
    statusEl.textContent = "";

    bindHighlightClicks(document.getElementById("highlighted-text"));
    clearInputs();
    resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    statusEl.classList.add("status-error");
    statusEl.textContent = `오류: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
});
