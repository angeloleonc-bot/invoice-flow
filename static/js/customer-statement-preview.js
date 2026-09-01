(() => {
  "use strict";

  if (
    window.__invoiceFlowStatementPreviewGuard
    === true
  ) {
    return;
  }

  const dialog = document.querySelector(
    "[data-statement-leave-dialog]"
  );

  /*
   * Este archivo sólo debería cargarse en PREVIEW.
   * Si no existe el diálogo, no hacemos nada.
   */
  if (!dialog) {
    console.error(
      "Invoice Flow: no se encontró el diálogo "
      + "de abandono del Estado de Cuenta."
    );
    return;
  }

  window.__invoiceFlowStatementPreviewGuard =
    true;

  document.documentElement.setAttribute(
    "data-statement-leave-guard",
    "active"
  );


  let allowExit = false;
  let pendingDestination = null;


  const confirmLeaveButton =
    dialog.querySelector(
      "[data-statement-leave-confirm]"
    );

  const cancelLeaveButtons =
    dialog.querySelectorAll(
      "[data-statement-leave-cancel]"
    );


  function openLeaveDialog(destination) {

    pendingDestination = destination;

    dialog.hidden = false;

    document.body.classList.add(
      "if-statement-leave-dialog-open"
    );

    if (confirmLeaveButton) {
      confirmLeaveButton.focus();
    }
  }


  function closeLeaveDialog() {

    dialog.hidden = true;

    pendingDestination = null;

    document.body.classList.remove(
      "if-statement-leave-dialog-open"
    );
  }


  // =========================================================
  // INTERCEPTAR NAVEGACIÓN POR ENLACES
  // =========================================================

  document.addEventListener(
    "click",
    function (event) {

      if (allowExit) {
        return;
      }

      const link =
        event.target.closest("a[href]");

      if (!link) {
        return;
      }


      /*
       * Descargas y nuevas pestañas no abandonan
       * esta vista previa.
       */
      if (
        link.target === "_blank"
        || link.hasAttribute("download")
        || event.ctrlKey
        || event.metaKey
        || event.shiftKey
        || event.altKey
      ) {
        return;
      }


      const rawHref =
        link.getAttribute("href");

      if (
        !rawHref
        || rawHref === "#"
        || rawHref.startsWith(
          "javascript:"
        )
      ) {
        return;
      }


      const destination =
        new URL(
          link.href,
          window.location.href
        );

      const current =
        new URL(
          window.location.href
        );


      /*
       * Una ancla de esta misma página
       * no corresponde a abandono.
       */
      if (
        destination.origin
          === current.origin
        && destination.pathname
          === current.pathname
        && destination.search
          === current.search
        && destination.hash
      ) {
        return;
      }


      /*
       * Bloqueamos la navegación real.
       */
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();


      openLeaveDialog(
        destination.href
      );
    },
    true
  );


  // =========================================================
  // SALIR SIN ENVIAR
  // =========================================================

  if (confirmLeaveButton) {

    confirmLeaveButton.addEventListener(
      "click",
      function () {

        if (!pendingDestination) {
          closeLeaveDialog();
          return;
        }

        const destination =
          pendingDestination;


        /*
         * Evita que beforeunload genere
         * una segunda confirmación.
         */
        allowExit = true;

        closeLeaveDialog();

        window.location.assign(
          destination
        );
      }
    );
  }


  // =========================================================
  // SEGUIR REVISANDO
  // =========================================================

  cancelLeaveButtons.forEach(
    function (button) {

      button.addEventListener(
        "click",
        closeLeaveDialog
      );
    }
  );


  document.addEventListener(
    "keydown",
    function (event) {

      if (
        event.key === "Escape"
        && !dialog.hidden
      ) {
        closeLeaveDialog();
      }
    }
  );


  // =========================================================
  // ENVÍO REAL
  // =========================================================

  const sendForm = document.querySelector(
    "[data-statement-send-form]"
  );

  if (sendForm) {

    sendForm.addEventListener(
      "submit",
      function (event) {

        const message = [
          "Se enviará este Estado de Cuenta "
            + "a los destinatarios indicados.",
          "El correo incluirá PDF y Excel.",
          "¿Confirmas el envío?"
        ].join("\n\n");


        if (
          !window.confirm(message)
        ) {
          event.preventDefault();
          return;
        }


        /*
         * El usuario confirmó un envío real.
         * Ya no corresponde advertencia de abandono.
         */
        allowExit = true;


        const button =
          sendForm.querySelector(
            "[data-statement-send-button]"
          );

        if (button) {
          button.disabled = true;
          button.textContent =
            "Enviando...";
        }
      }
    );
  }


  // =========================================================
  // RESPALDO DEL NAVEGADOR
  //
  // F5 / cerrar pestaña / escribir otra URL.
  //
  // Aquí el texto lo controla Chrome.
  // =========================================================

  window.addEventListener(
    "beforeunload",
    function (event) {

      if (allowExit) {
        return;
      }

      event.preventDefault();

      event.returnValue = "";
    }
  );


  console.info(
    "Invoice Flow: protección de vista previa activa."
  );

})();
