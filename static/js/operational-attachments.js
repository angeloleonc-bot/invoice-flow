document.addEventListener("DOMContentLoaded", function () {
  const MAX_FILE_SIZE = 30 * 1024 * 1024;

  function formatFileSize(bytes) {
    if (!Number.isFinite(bytes)) {
      return "";
    }

    if (bytes < 1024) {
      return `${bytes} B`;
    }

    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }

    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function createAttachmentManager(container) {
    const input = container.querySelector(
      "[data-operational-attachment-input]"
    );
    const list = container.querySelector(
      "[data-operational-attachment-list]"
    );
    const emptyState = container.querySelector(
      "[data-operational-attachment-empty]"
    );
    const errorBox = container.querySelector(
      "[data-operational-attachment-error]"
    );

    if (!input || !list) {
      return;
    }

    let selectedFiles = [];

    function showError(message) {
      if (!errorBox) {
        return;
      }

      errorBox.textContent = message;
      errorBox.hidden = false;
    }

    function clearError() {
      if (!errorBox) {
        return;
      }

      errorBox.textContent = "";
      errorBox.hidden = true;
    }

    function synchronizeInput() {
      const transfer = new DataTransfer();

      selectedFiles.forEach(function (file) {
        transfer.items.add(file);
      });

      input.files = transfer.files;
    }

    function getFileIdentifier(file) {
      return [
        file.name,
        file.size,
        file.lastModified,
      ].join("-");
    }

    function removeFile(identifier) {
      selectedFiles = selectedFiles.filter(function (file) {
        return getFileIdentifier(file) !== identifier;
      });

      synchronizeInput();
      renderFiles();
    }

    function renderFiles() {
      list.innerHTML = "";

      if (emptyState) {
        emptyState.hidden = selectedFiles.length > 0;
      }

      selectedFiles.forEach(function (file) {
        const identifier = getFileIdentifier(file);

        const item = document.createElement("div");
        item.className = "if-upload-file";

        const icon = document.createElement("div");
        icon.className = "if-upload-file-icon";
        icon.innerHTML = '<i class="bi bi-paperclip"></i>';

        const content = document.createElement("div");
        content.className = "if-upload-file-content";

        const filename = document.createElement("strong");
        filename.textContent = file.name;

        const metadata = document.createElement("span");
        metadata.textContent = formatFileSize(file.size);

        content.appendChild(filename);
        content.appendChild(metadata);

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "if-upload-file-remove";
        removeButton.setAttribute(
          "aria-label",
          `Quitar ${file.name}`
        );
        removeButton.innerHTML = '<i class="bi bi-x-lg"></i>';

        removeButton.addEventListener("click", function () {
          removeFile(identifier);
        });

        item.appendChild(icon);
        item.appendChild(content);
        item.appendChild(removeButton);

        list.appendChild(item);
      });
    }

    input.addEventListener("change", function () {
      clearError();

      const incomingFiles = Array.from(input.files || []);
      const currentIdentifiers = new Set(
        selectedFiles.map(getFileIdentifier)
      );

      for (const file of incomingFiles) {
        if (file.size > MAX_FILE_SIZE) {
          showError(
            `${file.name} supera el máximo permitido de 30 MB.`
          );
          continue;
        }

        const identifier = getFileIdentifier(file);

        if (currentIdentifiers.has(identifier)) {
          continue;
        }

        selectedFiles.push(file);
        currentIdentifiers.add(identifier);
      }

      synchronizeInput();
      renderFiles();
    });

    renderFiles();
  }

  function configureUploadForm(form) {
    const submitButton = form.querySelector(
      "[data-upload-submit]"
    );
    const progressBox = form.querySelector(
      "[data-upload-progress]"
    );
    const progressBar = form.querySelector(
      "[data-upload-progress-bar]"
    );
    const progressLabel = form.querySelector(
      "[data-upload-progress-label]"
    );

    if (!submitButton) {
      return;
    }

    form.addEventListener("submit", function () {
      if (form.dataset.submitting === "true") {
        return;
      }

      form.dataset.submitting = "true";
      submitButton.disabled = true;

      const files = Array.from(
        form.querySelectorAll(
          "[data-operational-attachment-input]"
        )
      ).flatMap(function (input) {
        return Array.from(input.files || []);
      });

      const fileCount = files.length;

      submitButton.dataset.originalText =
        submitButton.textContent.trim();

      submitButton.innerHTML = [
        '<span class="spinner-border spinner-border-sm" ',
        'aria-hidden="true"></span>',
        fileCount
          ? ` Guardando y cargando ${fileCount} archivo${fileCount === 1 ? "" : "s"}…`
          : " Guardando…",
      ].join("");

      if (progressBox) {
        progressBox.hidden = false;
      }

      if (progressBar) {
        progressBar.style.width = "100%";
        progressBar.setAttribute("aria-valuenow", "100");
        progressBar.classList.add("is-indeterminate");
      }

      if (progressLabel) {
        progressLabel.textContent = fileCount
          ? "La carga está en proceso. No cierres esta ventana."
          : "Guardando la información…";
      }
    });
  }

  document
    .querySelectorAll("[data-operational-attachments]")
    .forEach(createAttachmentManager);

  document
    .querySelectorAll("[data-operational-upload-form]")
    .forEach(configureUploadForm);
});