document.addEventListener("DOMContentLoaded", function () {
  const authActions = document.querySelectorAll("[data-auth-submit]");

  authActions.forEach(function (action) {
    action.addEventListener("click", function (event) {
      if (action.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      action.dataset.submitting = "true";
      action.setAttribute("aria-disabled", "true");
      action.classList.add("is-loading");

      const label = action.querySelector("[data-auth-label]");
      const spinner = action.querySelector(".if-auth-action-spinner");

      if (label) {
        label.dataset.originalText = label.textContent.trim();
        label.textContent = "Conectando…";
      }

      if (spinner) {
        spinner.hidden = false;
      }
    });
  });
});