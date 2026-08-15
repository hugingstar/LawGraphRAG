/** 설정 > 법 활성화: 무료 티어 한도(data-law-limit)까지만 체크할 수 있게 막고, 전체/분야별
 * 선택 개수 표시를 갱신하며, 마지막 저장 이후 바뀐 게 있을 때만 저장 바를 강조한다. */
(function () {
  const form = document.querySelector("form[data-law-limit]");
  const saveBar = document.querySelector("[data-law-toggle-savebar]");
  const counter = document.querySelector("[data-law-toggle-count] b");
  if (!form || !saveBar) return;

  const limit = parseInt(form.dataset.lawLimit, 10);
  const groups = Array.from(form.querySelectorAll(".law-toggle-group"));
  // 법이 분야별로 여러 개의 .law-toggle-grid(각 <details> 안)로 나뉘어 있으므로
  // 폼 전체에서 체크박스를 모은다.
  const boxes = Array.from(form.querySelectorAll('.law-toggle-grid input[type="checkbox"]'));
  const savedChecked = new Set(boxes.filter((b) => b.checked).map((b) => b.value));

  function refreshGroupCount(group) {
    const countEl = group.querySelector(".law-toggle-group-count");
    if (!countEl) return;
    const groupBoxes = group.querySelectorAll('input[type="checkbox"]');
    const checked = Array.from(groupBoxes).filter((b) => b.checked).length;
    countEl.textContent = `${checked}/${groupBoxes.length}개 선택됨`;
  }

  function refresh() {
    const checkedCount = boxes.filter((b) => b.checked).length;
    if (counter) counter.textContent = checkedCount;

    const atLimit = checkedCount >= limit;
    boxes.forEach((b) => {
      b.disabled = atLimit && !b.checked;
    });

    groups.forEach(refreshGroupCount);

    const isDirty = boxes.some((b) => b.checked !== savedChecked.has(b.value));
    saveBar.classList.toggle("is-dirty", isDirty);
  }

  boxes.forEach((b) => b.addEventListener("change", refresh));

  const clearAllBtn = document.querySelector('[data-law-toggle-all="false"]');
  if (clearAllBtn) {
    clearAllBtn.addEventListener("click", () => {
      boxes.forEach((b) => {
        b.checked = false;
      });
      refresh();
    });
  }

  refresh();
})();
