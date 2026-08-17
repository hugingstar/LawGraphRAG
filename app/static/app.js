// 실시간 조문 조회: NDJSON 스트림(app/main.py의 /analyze/stream)을 한 줄씩 읽어서
// 조문이 발견되는 즉시 화면에 반영한다(전체가 끝날 때까지 기다리지 않는다).
async function streamAnalyze(text, { onIssues, onCitation, onDone, onError }) {
  const resp = await fetch("/analyze/stream", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ text }),
  });
  if (!resp.ok || !resp.body) throw new Error(`서버 오류 (${resp.status})`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex;
    while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line) continue;

      const event = JSON.parse(line);
      if (event.type === "issues") onIssues(event);
      else if (event.type === "citation") onCitation(event.citation);
      else if (event.type === "done") onDone(event.citations);
      else if (event.type === "error") onError(new Error(event.message));
    }
  }
}

const analyzeBtn = document.getElementById("analyze-btn");
if (analyzeBtn)
analyzeBtn.addEventListener("click", async () => {
  const text = document.getElementById("input-text").value.trim();
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const btn = document.getElementById("analyze-btn");
  const emptyEl = document.getElementById("result-empty");

  statusEl.classList.remove("status-error");

  if (!text) {
    statusEl.textContent = "텍스트를 입력하세요.";
    return;
  }

  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  statusEl.textContent = "분석 중... (조문을 찾는 대로 실시간으로 표시됩니다)";
  resultEl.hidden = false;
  if (emptyEl) emptyEl.hidden = true;

  const citations = [];
  let issues = null; // 아직 도착 전 — renderCitationList가 체크리스트를 깔지 않게 한다
  let dismissed = [];
  const highlightedEl = document.getElementById("highlighted-text");
  const citationListEl = document.getElementById("citation-list");
  const issuesEl = document.getElementById("issue-panel");
  highlightedEl.innerHTML = renderHighlighted(text, citations);
  citationListEl.innerHTML = '<li class="empty">조문을 찾는 중...</li>';
  if (issuesEl) issuesEl.hidden = true;

  const repaint = () => {
    highlightedEl.innerHTML = renderHighlighted(text, citations);
    citationListEl.innerHTML = renderCitationList(citations, issues, dismissed);
    bindHighlightClicks(highlightedEl);
  };

  try {
    await streamAnalyze(text, {
      // 조문보다 먼저 도착한다. 어떤 관점에서 검토 중인지 바로 보여주면, 뒤늦게 도착하는
      // 민사·노동 조문을 사용자가 기다릴 수 있다(빈 목록을 보고 끝났다고 오해하지 않는다).
      onIssues: (event) => {
        issues = event.issues || [];
        dismissed = event.dismissed || [];
        const bodyEl = document.getElementById("issue-panel-body");
        if (issuesEl && bodyEl && (issues.length || dismissed.length)) {
          issuesEl.hidden = false;
          const chip = (domainLabel, label, cls) =>
            `<li class="issue-chip ${cls}"><span class="domain-badge">${escapeHtml(
              domainLabel
            )}</span>${escapeHtml(label)}</li>`;
          bodyEl.innerHTML = `
            ${event.facts ? `<p class="issue-facts">${escapeHtml(event.facts)}</p>` : ""}
            <ul class="issue-chips">
              ${issues.map((i) => chip(i.domain_label, i.label, "")).join("")}
              ${dismissed
                .map((d) => chip(d.domain_label, d.reason || "해당 사항 없음", "issue-chip-dismissed"))
                .join("")}
            </ul>`;
        }
        repaint();
      },
      onCitation: (citation) => {
        citations.push(citation);
        statusEl.textContent = `분석 중... 지금까지 ${citations.length}개 조문 발견`;
        repaint();
      },
      onDone: (finalCitations) => {
        citations.length = 0;
        citations.push(...finalCitations);
        repaint();
        statusEl.textContent = "";
      },
      onError: (err) => {
        throw err;
      },
    });
  } catch (err) {
    statusEl.classList.add("status-error");
    statusEl.textContent = `오류: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
});
