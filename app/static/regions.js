/** 시·도 -> 시·군·구 2단 셀렉트 연동.
 *
 * 회원가입/설정/사건작성/관리자검토 네 화면이 같은 동작을 쓰므로 한 곳에 모은다.
 * 시군구가 250개뿐이라 시도를 고를 때마다 서버에 되묻지 않고 /api/regions로 한 번만 받아
 * 클라이언트에서 걸러 쓴다.
 *
 * 마크업 계약: [data-region-sido]와 [data-region-sigungu] 셀렉트가 짝으로 존재하고,
 * 시군구 쪽 data-selected에 복원할 초기값(있으면)이 들어 있다.
 */
(function () {
  const sidoEl = document.querySelector("[data-region-sido]");
  const sigunguEl = document.querySelector("[data-region-sigungu]");
  if (!sidoEl || !sigunguEl) return;

  const placeholder = sigunguEl.querySelector('option[value=""]');
  const placeholderText = placeholder ? placeholder.textContent : "시·군·구 선택";
  let allSigungu = [];

  function repaint() {
    // 다시 그리기 전에 현재 선택을 기억해 둔다. 초기 로드에서는 서버가 준 data-selected를 쓴다.
    const keep = sigunguEl.value || sigunguEl.dataset.selected || "";
    const parent = sidoEl.value;
    const options = allSigungu.filter((r) => r.parent_code === parent);

    sigunguEl.innerHTML =
      `<option value="">${placeholderText}</option>` +
      options
        .map(
          (r) =>
            `<option value="${r.code}"${r.code === keep ? " selected" : ""}>${r.name}</option>`
        )
        .join("");

    // 시도를 바꿔서 기존 선택이 더 이상 유효하지 않게 됐다면 비운다.
    if (keep && !options.some((r) => r.code === keep)) sigunguEl.value = "";
  }

  fetch("/api/regions")
    .then((resp) => (resp.ok ? resp.json() : Promise.reject(new Error(resp.status))))
    .then((data) => {
      allSigungu = data.sigungu;
      repaint();
    })
    .catch(() => {
      // 지역 목록을 못 받으면 시군구를 고를 수 없다는 것만 알려주고 나머지 폼은 그대로 둔다.
      sigunguEl.innerHTML = '<option value="">지역 목록을 불러오지 못했습니다</option>';
    });

  sidoEl.addEventListener("change", repaint);
})();
