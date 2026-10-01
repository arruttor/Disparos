/* Comportamentos globais do painel. Sem dependências. */
(function () {
  "use strict";

  // --- Tema: sistema / claro / escuro (preferência só deste navegador) ---
  var CHAVE_TEMA = "painel-tema";

  function lerTema() {
    try { return localStorage.getItem(CHAVE_TEMA) || "sistema"; } catch (e) { return "sistema"; }
  }

  function aplicarTema(tema) {
    var raiz = document.documentElement;
    if (tema === "claro") raiz.setAttribute("data-theme", "light");
    else if (tema === "escuro") raiz.setAttribute("data-theme", "dark");
    else raiz.removeAttribute("data-theme");
    document.querySelectorAll("[data-tema]").forEach(function (botao) {
      botao.setAttribute("aria-pressed", String(botao.dataset.tema === tema));
    });
  }

  document.addEventListener("click", function (ev) {
    var botao = ev.target.closest("[data-tema]");
    if (!botao) return;
    try { localStorage.setItem(CHAVE_TEMA, botao.dataset.tema); } catch (e) { /* modo privado */ }
    aplicarTema(botao.dataset.tema);
  });

  aplicarTema(lerTema());

  // --- Menus <details>: fecha ao clicar fora ou apertar Esc ---
  document.addEventListener("click", function (ev) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(ev.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      menu.removeAttribute("open");
      menu.querySelector("summary").focus();
    });
  });

  // --- Toasts: fecham sozinhos (exceto erros) ---
  function fecharToast(toast) {
    toast.classList.add("is-leaving");
    setTimeout(function () { toast.remove(); }, 200);
  }
  document.querySelectorAll(".toast").forEach(function (toast) {
    toast.querySelector(".toast__close").addEventListener("click", function () { fecharToast(toast); });
    if (!toast.classList.contains("toast--error")) {
      setTimeout(function () { if (toast.isConnected) fecharToast(toast); }, 6000);
    }
  });

  // --- Confirmação em formulários destrutivos ---
  document.addEventListener("submit", function (ev) {
    var msg = ev.target.dataset.confirmar;
    if (msg && !window.confirm(msg)) ev.preventDefault();
  });
})();
