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
    html += `<mark style="background:${color}" title="${escapeHtml(tooltip)}" data-url="${c.url}">`;
    html += escapeHtml(text.slice(c.start, c.end));
    html += "</mark>";
    cursor = c.end;
  }
  html += escapeHtml(text.slice(cursor));
  return html;
}

function renderCitationList(citations) {
  return citations
    .map(
      (c) => `<li>
        <strong>${escapeHtml(c.law_name)} ${escapeHtml(c.article_label)}</strong>
        ${c.title ? `(${escapeHtml(c.title)})` : ""}
        — ${escapeHtml(c.reason)}
        <br><a href="${c.url}" target="_blank" rel="noopener">법제처 원문 보기</a>
      </li>`
    )
    .join("");
}

document.getElementById("analyze-btn").addEventListener("click", async () => {
  const text = document.getElementById("input-text").value.trim();
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const btn = document.getElementById("analyze-btn");

  if (!text) {
    statusEl.textContent = "텍스트를 입력하세요.";
    return;
  }

  btn.disabled = true;
  statusEl.textContent = "분석 중...";
  resultEl.hidden = true;

  try {
    const resp = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ text }),
    });
    if (!resp.ok) throw new Error(`서버 오류 (${resp.status})`);

    const data = await resp.json();
    document.getElementById("highlighted-text").innerHTML = renderHighlighted(data.text, data.citations);
    document.getElementById("citation-list").innerHTML = data.citations.length
      ? renderCitationList(data.citations)
      : "<li>적용되는 조문을 찾지 못했습니다.</li>";

    resultEl.hidden = false;
    statusEl.textContent = "";

    document.querySelectorAll("#highlighted-text mark").forEach((el) => {
      el.addEventListener("click", () => window.open(el.dataset.url, "_blank"));
    });
  } catch (err) {
    statusEl.textContent = `오류: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});
