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

  // --- Faixas com rolagem lateral ([data-rolagem-lateral]) ---
  // No toque dá para deslizar; com mouse não. Aqui: rodinha rola de lado e dá para arrastar.
  function rolagemLateral(el) {
    // Marca se ainda há conteúdo à direita (o CSS esmaece a borda só nesse caso).
    function atualizarIndicador() {
      el.classList.toggle("tem-mais", el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
    }
    el.addEventListener("scroll", atualizarIndicador, {passive: true});
    window.addEventListener("resize", atualizarIndicador);
    atualizarIndicador();

    el.addEventListener("wheel", function (ev) {
      if (el.scrollWidth <= el.clientWidth || Math.abs(ev.deltaY) <= Math.abs(ev.deltaX)) return;
      var antes = el.scrollLeft;
      el.scrollLeft += ev.deltaY;
      if (el.scrollLeft !== antes) ev.preventDefault();  // só "prende" a rodinha se rolou de fato
    }, {passive: false});

    var inicioX = null, inicioScroll = 0, arrastou = false;
    el.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType !== "mouse" || ev.button !== 0 || el.scrollWidth <= el.clientWidth) return;
      inicioX = ev.clientX; inicioScroll = el.scrollLeft; arrastou = false;
    });
    window.addEventListener("pointermove", function (ev) {
      if (inicioX === null) return;
      var dx = ev.clientX - inicioX;
      if (!arrastou && Math.abs(dx) < 6) return;  // tolerância: clique continua sendo clique
      arrastou = true;
      el.classList.add("is-arrastando");
      el.scrollLeft = inicioScroll - dx;
    });
    window.addEventListener("pointerup", function () {
      inicioX = null;
      el.classList.remove("is-arrastando");
    });
    // Depois de arrastar, o "soltar" não deve abrir o link que estava embaixo do mouse.
    el.addEventListener("click", function (ev) {
      if (arrastou) { ev.preventDefault(); ev.stopPropagation(); arrastou = false; }
    }, true);
  }
  document.querySelectorAll("[data-rolagem-lateral]").forEach(rolagemLateral);

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

  // --- Confirmação em formulários destrutivos (data-confirmar="…") ---
  // Diálogo próprio em vez de window.confirm: navegadores embutidos (ex.: painel de
  // navegador de apps) suprimem o confirm nativo e ele devolve false sem mostrar nada.
  var dialogo = document.getElementById("dialogo-confirmar");
  var formPendente = null;

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var msg = form.dataset.confirmar;
    if (!msg || form.dataset.confirmado === "1") return;
    ev.preventDefault();
    if (!dialogo || typeof dialogo.showModal !== "function") {
      // Sem suporte a <dialog>: segue sem confirmação em vez de travar a ação.
      form.dataset.confirmado = "1";
      form.requestSubmit ? form.requestSubmit(ev.submitter) : form.submit();
      return;
    }
    formPendente = { form: form, botao: ev.submitter };
    dialogo.querySelector("[data-dialogo-texto]").textContent = msg;
    var rotulo = ev.submitter && ev.submitter.textContent.trim();
    dialogo.querySelector("[data-dialogo-ok]").textContent = rotulo || "Confirmar";
    dialogo.showModal();
  });

  if (dialogo) {
    dialogo.addEventListener("close", function () {
      if (dialogo.returnValue === "ok" && formPendente) {
        var f = formPendente.form;
        f.dataset.confirmado = "1";
        f.requestSubmit ? f.requestSubmit(formPendente.botao) : f.submit();
      }
      formPendente = null;
      dialogo.returnValue = "";
    });
    // Clique fora do conteúdo fecha (= cancelar)
    dialogo.addEventListener("click", function (ev) {
      if (ev.target === dialogo) dialogo.close("cancelar");
    });
  }
})();
