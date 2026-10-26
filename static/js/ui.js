(() => {
  "use strict";

  const root = document.documentElement;
  root.classList.add("js");

  function ready(callback) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", callback, { once: true });
    else callback();
  }

  // ---- Toasts ------------------------------------------------------------
  window.showToast = (message, type = "info") => {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " error" : ""}`;
    toast.textContent = message;
    container.appendChild(toast);
    window.setTimeout(() => toast.remove(), 2400);
  };

  async function copyText(text) {
    if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
    const helper = document.createElement("textarea");
    helper.value = text;
    helper.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(helper);
    helper.select();
    const ok = document.execCommand("copy");
    helper.remove();
    if (!ok) throw new Error("Copy unavailable");
  }

  // ---- Theme -------------------------------------------------------------
  function applyTheme(theme, persist) {
    root.dataset.theme = theme;
    const meta = document.getElementById("theme-color-meta");
    if (meta) meta.content = theme === "dark" ? "#0e0f12" : "#f3f2ee";
    if (persist) {
      try { localStorage.setItem("theme", theme); } catch (_) { /* storage unavailable */ }
    }
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
      button.title = button.getAttribute("aria-label");
    });
    window.dispatchEvent(new CustomEvent("themechange", { detail: { isDark: theme === "dark" } }));
  }

  // ---- Modal helpers (focus return + trap) ---------------------------------
  const openStack = [];
  function openLayer(el, focusTarget) {
    if (!el) return;
    el.hidden = false;
    openStack.push({ el, returnTo: document.activeElement });
    document.body.style.overflow = "hidden";
    (focusTarget || el.querySelector("input, button, a[href]"))?.focus();
  }
  function closeLayer(el) {
    const index = openStack.findIndex((entry) => entry.el === el);
    if (index < 0) return;
    const [entry] = openStack.splice(index, 1);
    el.hidden = true;
    if (!openStack.length) document.body.style.overflow = "";
    entry.returnTo?.focus?.();
  }

  ready(() => {
    applyTheme(root.dataset.theme === "dark" ? "dark" : "light", false);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => applyTheme(root.dataset.theme === "dark" ? "light" : "dark", true));
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", (event) => {
      let stored = null;
      try { stored = localStorage.getItem("theme"); } catch (_) { /* ignore */ }
      if (!stored) applyTheme(event.matches ? "dark" : "light", false);
    });

    // ---- Top bar: scrolled state + reading progress ----
    const topbar = document.querySelector("[data-topbar]");
    const progress = document.querySelector("[data-read-progress]");
    const progressBar = progress?.querySelector("span");
    const article = document.querySelector("[data-article]");
    if (article && progress) progress.hidden = false;
    let ticking = false;
    function onScroll() {
      ticking = false;
      topbar?.classList.toggle("is-scrolled", window.scrollY > 8);
      if (article && progressBar) {
        const rect = article.getBoundingClientRect();
        const total = rect.height - window.innerHeight * 0.6;
        const ratio = total > 0 ? Math.min(1, Math.max(0, -rect.top / total)) : 0;
        progressBar.style.transform = `scaleX(${ratio})`;
      }
    }
    window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
    onScroll();

    // ---- Mobile menu ----
    const menuToggle = document.querySelector("[data-menu-toggle]");
    const menu = document.getElementById("mobile-menu");
    menuToggle?.addEventListener("click", () => {
      const open = menu.hidden;
      menu.hidden = !open;
      menuToggle.setAttribute("aria-expanded", String(open));
      menuToggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
    });

    // ---- Search dialog ----
    const searchDialog = document.getElementById("search-dialog");
    const searchInput = document.getElementById("site-search");
    document.querySelectorAll("[data-search-open]").forEach((button) => button.addEventListener("click", () => {
      openLayer(searchDialog, searchInput);
      searchInput?.select();
    }));
    searchDialog?.querySelectorAll("[data-search-close]").forEach((el) => el.addEventListener("click", () => closeLayer(searchDialog)));

    document.addEventListener("keydown", (event) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable;
      if (!typing && ((event.key === "/" ) || (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)))) {
        if (searchDialog?.hidden) {
          event.preventDefault();
          openLayer(searchDialog, searchInput);
        }
      }
      const top = openStack[openStack.length - 1];
      if (!top) return;
      if (event.key === "Escape") { event.preventDefault(); closeLayer(top.el); }
      if (event.key === "Tab") {
        const items = [...top.el.querySelectorAll("a[href], input, button:not([disabled]):not([tabindex='-1'])")].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });

    // ---- Reveal on scroll (geometry check; never leaves content hidden) ----
    let pending = [...document.querySelectorAll("[data-reveal]")];
    pending.forEach((el, i) => { el.style.transitionDelay = `${(i % 4) * 60}ms`; });
    function reveal() {
      const limit = window.innerHeight * 0.96;
      pending = pending.filter((el) => {
        if (el.getBoundingClientRect().top < limit) { el.classList.add("is-in"); return false; }
        return true;
      });
    }
    reveal();
    window.addEventListener("scroll", () => requestAnimationFrame(reveal), { passive: true });
    window.addEventListener("resize", reveal);

    // ---- Prose enhancements ----
    document.querySelectorAll(".prose").forEach((prose) => {
      if (window.renderMathInElement) {
        try {
          window.renderMathInElement(prose, {
            delimiters: [{ left: "$$", right: "$$", display: true }, { left: "\\(", right: "\\)", display: false }, { left: "$", right: "$", display: false }],
            throwOnError: false,
            ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"]
          });
        } catch (_) { /* raw math stays readable */ }
      }
      prose.querySelectorAll("pre").forEach((pre) => {
        const code = pre.querySelector("code") || pre;
        const block = pre.parentElement?.classList.contains("highlight") ? pre.parentElement : pre;
        if (block.parentElement?.classList.contains("code-block")) return;
        const wrapper = document.createElement("div");
        wrapper.className = "code-block";
        block.parentNode.insertBefore(wrapper, block);
        wrapper.appendChild(block);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "copy-btn";
        button.textContent = "Copy";
        button.setAttribute("aria-label", "Copy code block");
        button.addEventListener("click", async () => {
          try { await copyText(code.innerText); button.textContent = "Copied"; button.classList.add("done"); }
          catch (_) { button.textContent = "Failed"; }
          window.setTimeout(() => { button.textContent = "Copy"; button.classList.remove("done"); }, 1600);
        });
        wrapper.appendChild(button);
      });
    });

    // ---- Reading time ----
    const readTarget = document.querySelector("[data-reading-time]");
    const articleBody = document.querySelector("[data-article] .prose");
    if (readTarget && articleBody) {
      const text = articleBody.innerText || "";
      const cjk = (text.match(/[㐀-鿿]/g) || []).length;
      const words = (text.replace(/[㐀-鿿]/g, " ").match(/\S+/g) || []).length;
      const minutes = Math.max(1, Math.round(words / 230 + cjk / 450));
      readTarget.textContent = `${minutes} min read`;
      readTarget.hidden = false;
    }

    // ---- Copy / share ----
    document.querySelectorAll("[data-copy-link]").forEach((button) => button.addEventListener("click", async () => {
      try { await copyText(window.location.href); window.showToast("Link copied"); }
      catch (_) { window.showToast("Unable to copy the link", "error"); }
    }));
    document.querySelectorAll("[data-copy-target]").forEach((button) => button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copyTarget);
      try { await copyText(source?.innerText || ""); window.showToast("Copied to clipboard"); }
      catch (_) { window.showToast("Unable to copy", "error"); }
    }));
    document.querySelectorAll("[data-native-share]").forEach((button) => {
      if (!navigator.share) { button.hidden = true; return; }
      button.addEventListener("click", async () => {
        try { await navigator.share({ title: button.dataset.shareTitle || document.title, url: window.location.href }); }
        catch (error) { if (error.name !== "AbortError") window.showToast("Sharing is unavailable", "error"); }
      });
    });

    const qrModal = document.getElementById("qr-modal");
    document.querySelectorAll("[data-wechat-share]").forEach((button) => button.addEventListener("click", () => {
      const box = document.getElementById("qr-code");
      if (!box || typeof window.QRCode !== "function") { window.showToast("QR component did not load; use Copy link instead", "error"); return; }
      box.replaceChildren();
      try {
        new window.QRCode(box, { text: window.location.href, width: 200, height: 200, colorDark: "#000000", colorLight: "#ffffff", correctLevel: window.QRCode.CorrectLevel.M });
        openLayer(qrModal, qrModal.querySelector("[data-modal-close]"));
      } catch (_) { window.showToast("Unable to create QR code; use Copy link instead", "error"); }
    }));
    qrModal?.querySelectorAll("[data-modal-close]").forEach((el) => el.addEventListener("click", () => closeLayer(qrModal)));

    // ---- TOC: scrollspy + mobile sheet ----
    const tocLinks = [...document.querySelectorAll("[data-toc-link]")];
    const headings = document.querySelectorAll(".prose h2[id], .prose h3[id], .prose h4[id]");
    if (tocLinks.length && headings.length && "IntersectionObserver" in window) {
      const spy = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          tocLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${entry.target.id}`));
        });
      }, { rootMargin: "-80px 0px -72% 0px" });
      headings.forEach((heading) => spy.observe(heading));
    }
    const sheet = document.getElementById("toc-sheet");
    document.querySelector("[data-toc-open]")?.addEventListener("click", () => openLayer(sheet, sheet.querySelector("[data-toc-close]")));
    sheet?.querySelectorAll("[data-toc-close], [data-toc-link]").forEach((el) => el.addEventListener("click", () => closeLayer(sheet)));

    // ---- Project section tabs scrollspy ----
    const tabs = [...document.querySelectorAll("[data-section-tab]")];
    if (tabs.length && "IntersectionObserver" in window) {
      const targets = tabs.map((tab) => document.querySelector(tab.getAttribute("href"))).filter(Boolean);
      const tabSpy = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          tabs.forEach((tab) => {
            const on = tab.getAttribute("href") === `#${entry.target.id}`;
            tab.classList.toggle("active", on);
            if (on) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
          });
        });
      }, { rootMargin: "-30% 0px -60% 0px" });
      targets.forEach((target) => tabSpy.observe(target));
    }

    // ---- Giscus ----
    const giscus = document.getElementById("giscus-container");
    if (giscus) {
      const data = giscus.dataset;
      const script = document.createElement("script");
      script.src = "https://giscus.app/client.js";
      script.async = true;
      script.crossOrigin = "anonymous";
      const attributes = {
        repo: data.repo, repoId: data.repoId, category: data.category, categoryId: data.categoryId,
        mapping: data.mapping, strict: "0", reactionsEnabled: data.reactions, emitMetadata: data.metadata,
        inputPosition: data.inputPosition, theme: root.dataset.theme === "dark" ? "dark" : "light", lang: data.lang
      };
      Object.entries(attributes).forEach(([key, value]) => script.setAttribute(`data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`, value || ""));
      giscus.appendChild(script);
      window.addEventListener("themechange", (event) => {
        document.querySelector("iframe.giscus-frame")?.contentWindow?.postMessage({ giscus: { setConfig: { theme: event.detail.isDark ? "dark" : "light" } } }, "https://giscus.app");
      });
    }
  });
})();
