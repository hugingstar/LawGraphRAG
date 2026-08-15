document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("law-activation-modal");
  const modalBody = document.getElementById("law-activation-modal-body");
  const openBtns = document.querySelectorAll("[data-open-law-modal]");
  const closeBtns = document.querySelectorAll("[data-close-law-modal]");

  if (!modal) return;

  openBtns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      modal.showModal();
      modalBody.innerHTML = '<p class="empty-state">로딩 중...</p>';
      
      try {
        const resp = await fetch("/api/settings/laws/fragment");
        if (!resp.ok) throw new Error("로드 실패");
        const html = await resp.text();
        modalBody.innerHTML = html;
        
        // Re-bind the settings_laws.js logic
        bindLawFormLogic();
      } catch (err) {
        modalBody.innerHTML = `<p class="form-error">법 목록을 불러오지 못했습니다: ${err.message}</p>`;
      }
    });
  });

  closeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      modal.close();
    });
  });

  function bindLawFormLogic() {
    const form = modalBody.querySelector("form[data-law-limit]");
    const saveBar = modalBody.querySelector("[data-law-toggle-savebar]");
    const counter = modalBody.querySelector("[data-law-toggle-count] b");
    if (!form) return;

    const limit = parseInt(form.dataset.lawLimit, 10);
    const groups = Array.from(form.querySelectorAll(".law-toggle-group"));
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
      if (saveBar) saveBar.classList.toggle("is-dirty", isDirty);
    }

    boxes.forEach((b) => b.addEventListener("change", refresh));

    const clearAllBtn = form.querySelector('[data-law-toggle-all="false"]');
    if (clearAllBtn) {
      clearAllBtn.addEventListener("click", () => {
        boxes.forEach((b) => {
          b.checked = false;
        });
        refresh();
      });
    }

    refresh();

    // Handle submit via AJAX
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = form.querySelector('button[type="submit"]');
      const originalText = submitBtn.textContent;
      submitBtn.disabled = true;
      submitBtn.textContent = "저장 중...";

      try {
        const formData = new FormData(form);
        const resp = await fetch("/api/settings/laws/fragment", {
          method: "POST",
          body: formData
        });
        
        let result;
        try {
          result = await resp.json();
        } catch(err) {
          throw new Error("서버 응답을 처리할 수 없습니다.");
        }

        if (!resp.ok || !result.success) {
          throw new Error(result.error || "저장 실패");
        }

        // Close modal and update list
        modal.close();
        
        // Update the active laws list on the page if it exists
        const listEl = document.querySelector(".law-list");
        if (listEl && result.active_names) {
          if (result.active_names.length > 0) {
            listEl.innerHTML = "";
            result.active_names.forEach(name => {
              const li = document.createElement("li");
              li.textContent = name;
              listEl.appendChild(li);
            });
          } else {
            listEl.innerHTML = '<p class="empty-state">아직 수집된 법령이 없습니다.</p>';
          }
        }
      } catch (err) {
        alert(err.message);
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      }
    });
  }
});
