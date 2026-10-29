(() => {
  "use strict";
  const t = (message) => window.publicTranslate?.(message) || message;

  function ready(callback) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", callback, { once: true });
    else callback();
  }

  ready(() => {
    document.querySelectorAll(".article-content").forEach((content) => {
      if (!window.renderMathInElement) return;
      try {
        window.renderMathInElement(content, {
          delimiters: [{ left: "$$", right: "$$", display: true }, { left: "\\(", right: "\\)", display: false }, { left: "$", right: "$", display: false }],
          throwOnError: false,
          ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
        });
      } catch (_) { /* Raw, escaped math remains readable. */ }
    });

    async function copyText(text) {
      if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      const copied = document.execCommand("copy");
      helper.remove();
      if (!copied) throw new Error("Copy unavailable");
    }

    document.querySelectorAll(".article-content pre").forEach((pre) => {
      const code = pre.querySelector("code");
      if (!code || pre.closest(".code-block-wrapper")) return;
      const block = pre.parentElement?.classList.contains("highlight") ? pre.parentElement : pre;
      const wrapper = document.createElement("div");
      wrapper.className = "code-block-wrapper";
      block.parentNode.insertBefore(wrapper, block);
      wrapper.appendChild(block);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy-btn";
      button.textContent = t("Copy");
      button.setAttribute("aria-label", t("Copy code block"));
      button.addEventListener("click", async () => {
        try {
          await copyText(code.innerText);
          button.textContent = t("Copied");
          button.classList.add("copied");
        } catch (_) { button.textContent = t("Failed"); }
        window.setTimeout(() => { button.textContent = t("Copy"); button.classList.remove("copied"); }, 1800);
      });
      wrapper.appendChild(button);
    });

    document.querySelectorAll(".article-content table").forEach((table) => {
      if (table.parentElement?.classList.contains("table-scroll")) return;
      const wrapper = document.createElement("div");
      wrapper.className = "table-scroll";
      wrapper.setAttribute("tabindex", "0");
      wrapper.setAttribute("aria-label", t("Scrollable table"));
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    });

    const tocToggle = document.getElementById("mobile-toc-toggle");
    const tocDialog = document.getElementById("mobile-toc");
    let tocReturnFocus = null;
    function setTocOpen(open) {
      if (!tocDialog || !tocToggle) return;
      tocDialog.classList.toggle("hidden", open === false);
      tocToggle.setAttribute("aria-expanded", String(open));
      document.body.style.overflow = open ? "hidden" : "";
      if (open) {
        tocReturnFocus = document.activeElement;
        tocDialog.querySelector("[data-toc-close]")?.focus();
      } else { tocReturnFocus?.focus(); }
    }
    tocToggle?.addEventListener("click", () => setTocOpen(true));
    tocDialog?.querySelectorAll("[data-toc-close]").forEach((button) => button.addEventListener("click", () => setTocOpen(false)));
    tocDialog?.querySelectorAll("[data-toc-mobile-link]").forEach((link) => link.addEventListener("click", () => setTocOpen(false)));

    const seriesButton = document.getElementById("toggle-series-toc");
    const seriesToc = document.getElementById("series-toc");
    seriesButton?.addEventListener("click", () => {
      const open = seriesToc?.classList.toggle("hidden") === false;
      seriesButton.setAttribute("aria-expanded", String(open));
    });

    document.getElementById("copy-link-btn")?.addEventListener("click", async () => {
      try { await copyText(window.location.href); window.showToast?.(t("Link copied")); }
      catch (_) { window.showToast?.(t("Unable to copy the link"), "error"); }
    });
    document.querySelector("[data-native-share]")?.addEventListener("click", async (event) => {
      try { await navigator.share({ title: event.currentTarget.dataset.shareTitle || document.title, url: window.location.href }); }
      catch (error) { if (error.name !== "AbortError") window.showToast?.(t("Sharing is unavailable"), "error"); }
    });

    const qrModal = document.getElementById("qr-modal");
    const qrClose = document.getElementById("qr-close");
    let qrReturnFocus = null;
    function setQrOpen(open) {
      if (!qrModal) return;
      qrModal.classList.toggle("hidden", !open);
      qrModal.classList.toggle("flex", open);
      document.body.style.overflow = open ? "hidden" : "";
      if (open) { qrReturnFocus = document.activeElement; qrClose?.focus(); }
      else qrReturnFocus?.focus();
    }
    document.getElementById("wechat-share-btn")?.addEventListener("click", () => {
      const container = document.getElementById("qr-code");
      if (!container || typeof QRCode !== "function") {
        setQrOpen(false);
        window.showToast?.(t("QR component did not load; use Copy link instead"), "error");
        return;
      }
      container.replaceChildren();
      try {
        new QRCode(container, {
          text: window.location.href,
          width: 200,
          height: 200,
          colorDark: "#000000",
          colorLight: "#ffffff",
          correctLevel: QRCode.CorrectLevel.M
        });
        setQrOpen(true);
      } catch (_) {
        container.replaceChildren();
        setQrOpen(false);
        window.showToast?.(t("Unable to create QR code; use Copy link instead"), "error");
      }
    });
    qrClose?.addEventListener("click", () => setQrOpen(false));
    qrModal?.addEventListener("click", (event) => { if (event.target === qrModal) setQrOpen(false); });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (qrModal && !qrModal.classList.contains("hidden")) setQrOpen(false);
        else if (tocDialog && !tocDialog.classList.contains("hidden")) setTocOpen(false);
      }
      const dialog = qrModal && !qrModal.classList.contains("hidden") ? qrModal : tocDialog && !tocDialog.classList.contains("hidden") ? tocDialog : null;
      if (event.key === "Tab" && dialog) {
        const items = [...dialog.querySelectorAll("a[href], button:not([disabled]), [tabindex='0']")].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });

    const tocLinks = [...document.querySelectorAll("[data-toc-link]")];
    const headings = document.querySelectorAll(".article-content h1[id], .article-content h2[id], .article-content h3[id], .article-content h4[id]");
    if (tocLinks.length && headings.length && "IntersectionObserver" in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          tocLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${entry.target.id}`));
        });
      }, { rootMargin: "-90px 0px -75% 0px" });
      headings.forEach((heading) => observer.observe(heading));
    }

    const giscusContainer = document.getElementById("giscus-container");
    if (giscusContainer) {
      const data = giscusContainer.dataset;
      const script = document.createElement("script");
      script.src = "https://giscus.app/client.js";
      script.async = true;
      script.crossOrigin = "anonymous";
      const attributes = {
        repo: data.repo, repoId: data.repoId, category: data.category, categoryId: data.categoryId,
        mapping: data.mapping, strict: "0", reactionsEnabled: data.reactions, emitMetadata: data.metadata,
        inputPosition: data.inputPosition, theme: document.documentElement.classList.contains("dark") ? "dark" : "light", lang: data.lang
      };
      Object.entries(attributes).forEach(([key, value]) => script.setAttribute(`data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`, value || ""));
      giscusContainer.appendChild(script);
      window.addEventListener("themechange", (event) => {
        const iframe = document.querySelector("iframe.giscus-frame");
        iframe?.contentWindow?.postMessage({ giscus: { setConfig: { theme: event.detail.isDark ? "dark" : "light" } } }, "https://giscus.app");
      });
    }
  });
})();
