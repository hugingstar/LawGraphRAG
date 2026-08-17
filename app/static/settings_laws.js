/* 설정 > 법 활성화 페이지(/settings/laws).
 * 선택 개수·검색 처리는 팝업과 똑같으므로 law_modal.js의 공통 함수를 그대로 쓴다.
 * 저장은 팝업과 달리 폼을 그냥 전송해서 페이지를 다시 그린다. */
document.addEventListener("DOMContentLoaded", () => {
  window.bindLawFormLogic(document.querySelector("form[data-law-form]"));
});
