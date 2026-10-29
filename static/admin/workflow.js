(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function debounce(fn, delay) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
  }

  function csrfToken(form) {
    return form?.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
  }

  function showStatus(element, message, isError = false) {
    if (!element) return;
    element.textContent = message;
    element.dataset.error = isError ? "true" : "false";
    element.style.color = isError ? "var(--admin-danger)" : "";
  }

  // These custom forms intentionally have no Unfold file-path text input.
  // Mark them before Unfold's window-load handler attaches its listener;
  // native file selection and the dropzone below still work normally.
  $$("form[data-workflow-form] input[type='file']").forEach((input) => { input.hasChangeListener = true; });

  const dropzone = $("#drop-zone");
  const fileInput = $("#file-input");
  const fileName = $("#file-name");
  if (dropzone && fileInput) {
    const updateName = () => { if (fileName) fileName.textContent = fileInput.files[0]?.name || "点击选择文件或拖拽到此处"; };
    fileInput.addEventListener("change", updateName);
    ["dragenter", "dragover", "dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
      event.preventDefault(); event.stopPropagation();
      dropzone.classList.toggle("dragging", name === "dragenter" || name === "dragover");
    }));
    dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer?.files?.length) {
        try { fileInput.files = event.dataTransfer.files; updateName(); }
        catch (_) { fileName.textContent = "请点击选择文件（此浏览器不支持拖放赋值）"; }
      }
    });
  }

  const form = $("[data-workflow-form], #post_form, #project_form");
  if (form) {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") { event.preventDefault(); return; }
      form.dataset.submitting = "true";
      const submitter = event.submitter;
      if (submitter?.dataset.action) {
        let action = form.querySelector("input[data-action-value]");
        if (!action) {
          action = document.createElement("input"); action.type = "hidden"; action.name = "action"; action.dataset.actionValue = "true";
          form.appendChild(action);
        }
        action.value = submitter.dataset.action;
      }
      requestAnimationFrame(() => {
        $$("button[type='submit']", form).forEach((button) => { button.disabled = true; });
        if (submitter) {
          submitter.disabled = false;
          submitter.dataset.originalText = submitter.textContent;
          submitter.textContent = submitter.dataset.pendingLabel || "处理中…";
        }
      });
    });
  }

  $$("[data-preview-root]").forEach((root) => {
    const field = root.dataset.previewField || "content";
    const textarea = $(`#id_${field}`, root) || $("#id_content", root);
    const preview = $("[data-preview-output]", root) || $("#markdown-preview", root);
    const status = $("[data-preview-status]", root) || $("#preview-status", root);
    const refresh = $("[data-preview-refresh]", root) || $("#preview-refresh", root);
    const endpoint = root.dataset.previewUrl;
    const ownerForm = root.closest("form") || form;
    let controller;
    let version = 0;
    const maxChars = Number(root.dataset.maxChars || 100000);

    async function updatePreview() {
      if (!textarea || !preview || !endpoint) return;
      if (textarea.value.length > maxChars) {
        showStatus(status, `内容超过 ${maxChars} 字符，请缩短后预览`, true);
        return;
      }
      controller?.abort();
      controller = new AbortController();
      const current = ++version;
      showStatus(status, "正在同步…");
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "X-CSRFToken": csrfToken(ownerForm), "X-Requested-With": "XMLHttpRequest" },
          body: new URLSearchParams({ content: textarea.value }),
          signal: controller.signal,
        });
        const payload = await response.json();
        if (current !== version) return;
        if (!response.ok || !payload.ok) throw new Error(payload.error || "预览失败");
        preview.innerHTML = payload.html;
        if (window.renderMathInElement) {
          window.renderMathInElement(preview, { delimiters: [{ left: "$$", right: "$$", display: true }, { left: "$", right: "$", display: false }], throwOnError: false, ignoredTags: ["script", "style", "textarea", "pre", "code"] });
        }
        $$("table", preview).forEach((table) => {
          if (table.parentElement.classList.contains("table-scroll")) return;
          const wrap = document.createElement("div"); wrap.className = "table-scroll"; table.parentNode.insertBefore(wrap, table); wrap.appendChild(table);
        });
        showStatus(status, "已同步");
      } catch (error) {
        if (error.name === "AbortError") return;
        showStatus(status, error.message || "预览失败", true);
      } finally { clearTimeout(timeout); }
    }
    textarea?.addEventListener("input", debounce(updatePreview, 900));
    refresh?.addEventListener("click", updatePreview);
  });

  const titleInput = $("#id_title");
  const contentInput = $("#id_content");
  async function generate(kind) {
    const output = kind === "tags" ? $("#id_tags") : $("#id_description");
    const status = $(`#${kind}-status`);
    if (!contentInput?.value.trim()) { showStatus(status, "请先填写正文", true); return; }
    showStatus(status, "生成中…");
    try {
      const config = $("[data-preview-root]");
      const endpoint = kind === "tags" ? config?.dataset.tagsUrl : config?.dataset.summaryUrl;
      const response = await fetch(endpoint, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "X-CSRFToken": csrfToken(form) }, body: new URLSearchParams({ title: titleInput?.value || "", content: contentInput.value, count: "3" }) });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "生成失败");
      if (kind === "tags") output.value = payload.tags.join(", ");
      else output.value = payload.summary;
      showStatus(status, "已生成，请核对后保存");
    } catch (error) { showStatus(status, error.message || "生成失败", true); }
  }
  $("#generate-tags")?.addEventListener("click", () => generate("tags"));
  $("#generate-summary")?.addEventListener("click", () => generate("summary"));

  if (window.flatpickr) {
    $$("input[data-datetime]").forEach((input) => window.flatpickr(input, { enableTime: true, enableSeconds: true, dateFormat: "Y-m-d H:i:S", allowInput: true, time_24hr: true }));
  }
})();
