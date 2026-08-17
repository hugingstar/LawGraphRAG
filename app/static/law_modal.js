/** 법 활성화 폼(_settings_laws_form.html)의 공통 동작.
 *
 * 같은 폼을 설정 페이지(/settings/laws)와 팝업 두 곳에서 쓰므로 선택 개수·검색 처리는
 * 여기 한 곳에 두고, 저장 방식만 각 화면이 따로 붙인다(페이지는 일반 폼 전송,
 * 팝업은 AJAX). settings_laws.js가 설정 페이지에서 이 함수를 부른다.
 */
window.bindLawFormLogic = function (form) {
  if (!form) return;

  const counter = form.querySelector("[data-law-toggle-count] b");
  const groups = Array.from(form.querySelectorAll(".law-toggle-group"));
  const boxes = Array.from(form.querySelectorAll('.law-toggle-grid input[type="checkbox"]'));

  function refreshGroupCount(group) {
    const countEl = group.querySelector(".law-toggle-group-count");
    if (!countEl) return;
    const groupBoxes = group.querySelectorAll('input[type="checkbox"]');
    const checked = group.querySelectorAll('input[type="checkbox"]:checked').length;
    countEl.textContent = `${checked}/${groupBoxes.length}개 선택됨`;
  }

  function refresh() {
    const checkedCount = boxes.filter((b) => b.checked).length;
    if (counter) counter.textContent = checkedCount;
    groups.forEach(refreshGroupCount);
  }

  boxes.forEach((b) => b.addEventListener("change", refresh));

  /* 전체 선택/해제. 검색으로 걸러 둔 상태에서도 화면 밖 항목까지 함께 바뀌도록
     보이는 것만이 아니라 전체 체크박스를 대상으로 한다. */
  form.querySelectorAll("[data-law-toggle-all]").forEach((btn) => {
    const checked = btn.dataset.lawToggleAll === "true";
    btn.addEventListener("click", () => {
      boxes.forEach((b) => { b.checked = checked; });
      refresh();
    });
  });

  /* ── 검색: 이름이 안 맞는 항목과 남는 게 없는 분야를 숨긴다 ── */
  const searchInput = form.querySelector("[data-law-search]");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      const q = searchInput.value.trim().toLowerCase();
      groups.forEach((group) => {
        let visible = 0;
        group.querySelectorAll(".law-toggle-item").forEach((item) => {
          const name = item.querySelector("span").textContent.toLowerCase();
          const show = !q || name.includes(q);
          item.style.display = show ? "" : "none";
          if (show) visible++;
        });
        group.style.display = visible > 0 ? "" : "none";
        if (q && visible > 0) group.open = true;
      });
    });
  }

  refresh();
};

document.addEventListener("DOMContentLoaded", () => {
  /* 상단 "활성화 된 법" 바 접기/펼치기. 목록이 길면 화면이 너무 길어지므로 기본은
     접힌 채로 두고, 요약 줄을 누르면 펼쳐진다(레이아웃엔 _macros.html의 law_list_card). */
  document.querySelectorAll("[data-law-bar-toggle]").forEach((toggle) => {
    const body = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!body) return;
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      body.hidden = expanded;
    });
  });

  const modal = document.getElementById("law-activation-modal");
  const modalBody = document.getElementById("law-activation-modal-body");
  const openBtns = document.querySelectorAll("[data-open-law-modal]");

  if (!modal) return;

  /* 헤더 X 버튼은 모달 자체의 마크업이라 페이지 로드 시 한 번만 바인딩하면 된다.
     폼 안의 "닫기"는 매번 새로 그려지므로 bindModalForm에서 따로 붙인다. */
  modal.querySelectorAll("[data-close-law-modal]").forEach((btn) => {
    btn.addEventListener("click", () => modal.close());
  });

  /* backdrop(바깥) 클릭으로도 닫기 */
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.close();
  });

  openBtns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      modal.showModal();
      modalBody.innerHTML = '<p class="empty-state">로딩 중...</p>';

      try {
        const resp = await fetch("/api/settings/laws/fragment");
        if (!resp.ok) throw new Error("로드 실패");
        modalBody.innerHTML = await resp.text();
        bindModalForm();
      } catch (err) {
        modalBody.innerHTML =
          `<p class="form-error">법 목록을 불러오지 못했습니다: ${err.message}</p>`;
      }
    });
  });

  /** 이 페이지의 "활성화 된 법" 카드를 저장 결과에 맞춰 다시 그린다.
   *  법이 하나도 없을 때는 macros.law_list_card와 똑같이 <ul> 대신 안내 문구를 두므로,
   *  둘 중 지금 무엇이 그려져 있든 상관없도록 카드 안의 목록 영역을 통째로 교체한다. */
  function updateLawListCard(activeNames) {
    const card = document.querySelector(".law-list-card");
    if (!card || !activeNames) return;

    const current = card.querySelector(".law-list, .empty-state");
    if (!current) return;

    let replacement;
    if (activeNames.length > 0) {
      replacement = document.createElement("ul");
      replacement.className = "law-list";
      activeNames.forEach((name) => {
        const li = document.createElement("li");
        li.textContent = name;
        replacement.appendChild(li);
      });
    } else {
      replacement = document.createElement("p");
      replacement.className = "empty-state";
      replacement.textContent = "활성화된 법령이 없습니다.";
    }
    current.replaceWith(replacement);
  }

  function bindModalForm() {
    const form = modalBody.querySelector("form[data-law-form]");
    if (!form) return;

    window.bindLawFormLogic(form);

    const cancelBtn = form.querySelector("[data-close-law-modal]");
    if (cancelBtn) cancelBtn.addEventListener("click", () => modal.close());

    /* ── 저장: 페이지를 떠나지 않도록 AJAX로 보낸다 ── */
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = form.querySelector('button[type="submit"]');
      const originalText = submitBtn.textContent;
      submitBtn.disabled = true;
      submitBtn.textContent = "저장 중...";

      try {
        /* FormData를 그대로 보내면 multipart가 되는데, 하나도 안 고른 채 저장할 때
           (= 전체 해제) 만들어지는 빈 multipart 본문을 python-multipart 0.0.9가
           파싱하지 못해 400이 난다. 보낼 값이 정수 몇 개뿐이라 urlencoded로 바꾼다. */
        const resp = await fetch("/api/settings/laws/fragment", {
          method: "POST",
          body: new URLSearchParams(new FormData(form)),
        });

        const ct = resp.headers.get("content-type") || "";
        if (!ct.includes("application/json")) {
          throw new Error("서버가 예상치 못한 응답을 반환했습니다.");
        }
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || "저장 실패");
        }

        modal.close();
        window.showToast("법령 활성화 설정이 저장되었습니다.", "success");

        updateLawListCard(result.active_names);
      } catch (err) {
        window.showToast(err.message, "error");
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      }
    });
  }
});

/** 화면 오른쪽 아래에 잠깐 떴다 사라지는 알림. */
window.showToast = function (message, type = "success") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.textContent = type === "success" ? "✓" : "!";
  // 메시지에는 서버가 보낸 문구가 섞이므로 textContent로 넣어 마크업으로 해석되지 않게 한다.
  const text = document.createElement("span");
  text.textContent = message;
  toast.append(icon, text);

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add("toast-out");
    toast.addEventListener("animationend", () => toast.remove());
    /* 탭이 백그라운드로 넘어가 있거나 애니메이션이 꺼진 환경에서는 animationend가
       오지 않아 알림이 영영 남으므로, 시간이 지나면 무조건 지운다. */
    setTimeout(() => toast.remove(), 1000);
  }, 3000);
};
