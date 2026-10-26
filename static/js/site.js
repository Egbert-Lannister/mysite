(() => {
  "use strict";

  const root = document.documentElement;
  const themeToggle = document.getElementById("theme-toggle");
  const mobileMenu = document.getElementById("mobile-menu");
  const mobileToggle = document.getElementById("mobile-menu-toggle");
  const iconMenu = document.getElementById("icon-menu");
  const iconClose = document.getElementById("icon-close");
  let menuWasOpened = false;

  function setTheme(isDark, persist = true) {
    root.classList.toggle("dark", isDark);
    root.style.colorScheme = isDark ? "dark" : "light";
    const themeColor = document.getElementById("theme-color-meta");
    if (themeColor) themeColor.content = isDark ? "#101312" : "#f7f8f6";
    if (persist) {
      try { localStorage.setItem("theme", isDark ? "dark" : "light"); } catch (_) { /* storage may be unavailable */ }
    }
    if (themeToggle) {
      themeToggle.setAttribute("aria-pressed", String(isDark));
      themeToggle.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
      themeToggle.title = isDark ? "Switch to light theme" : "Switch to dark theme";
    }
    window.dispatchEvent(new CustomEvent("themechange", { detail: { isDark } }));
  }

  setTheme(root.classList.contains("dark"), false);
  themeToggle?.addEventListener("click", () => setTheme(!root.classList.contains("dark")));
  const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
  colorScheme.addEventListener?.("change", (event) => {
    let stored = null;
    try { stored = localStorage.getItem("theme"); } catch (_) { /* ignore */ }
    if (!stored) setTheme(event.matches, false);
  });

  function menuFocusable() {
    return mobileMenu ? [...mobileMenu.querySelectorAll("a[href], input, button:not([disabled])")] : [];
  }

  function setMobileMenuOpen(open, { focus = false } = {}) {
    if (!mobileMenu || !mobileToggle) return;
    mobileMenu.classList.toggle("hidden", !open);
    iconMenu?.classList.toggle("hidden", open);
    iconClose?.classList.toggle("hidden", !open);
    mobileToggle.setAttribute("aria-expanded", String(open));
    mobileToggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
    if (open) {
      menuWasOpened = true;
      if (focus) menuFocusable()[0]?.focus();
    } else if (menuWasOpened && focus) {
      mobileToggle.focus();
    }
  }

  mobileToggle?.addEventListener("click", () => {
    setMobileMenuOpen(mobileMenu?.classList.contains("hidden"), { focus: true });
  });
  mobileMenu?.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMobileMenuOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (!mobileMenu || mobileMenu.classList.contains("hidden")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setMobileMenuOpen(false, { focus: true });
    }
    if (["ArrowDown", "ArrowUp"].includes(event.key) && mobileMenu.contains(document.activeElement)) {
      event.preventDefault();
      const items = menuFocusable();
      const current = items.indexOf(document.activeElement);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      items[(current + delta + items.length) % items.length]?.focus();
    }
  });
  document.addEventListener("click", (event) => {
    if (!mobileMenu || mobileMenu.classList.contains("hidden")) return;
    if (!mobileMenu.contains(event.target) && !mobileToggle?.contains(event.target)) setMobileMenuOpen(false);
  });
  window.matchMedia("(min-width: 1024px)").addEventListener?.("change", (event) => {
    if (event.matches) setMobileMenuOpen(false);
  });

  const toastContainer = document.getElementById("toast-container");
  window.showToast = (message, type = "success") => {
    if (!toastContainer || !message) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.dataset.type = type;
    toast.textContent = String(message);
    toastContainer.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3200);
  };

  // Native share is an enhancement; copy remains available everywhere.
  document.querySelectorAll("[data-native-share]").forEach((button) => {
    if (!navigator.share) button.remove();
  });
})();
