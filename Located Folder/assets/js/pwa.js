(() => {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    });
  }

  const DISMISS_KEY = "located_install_dismissed_at";
  const DISMISS_DAYS = 14;

  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

  const recentlyDismissed = () => {
    try {
      const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
      return Date.now() - at < DISMISS_DAYS * 86400 * 1000;
    } catch (error) {
      return false;
    }
  };

  const rememberDismissal = () => {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch (error) {
      // Storage may be unavailable (private mode); the banner just shows again.
    }
  };

  if (isStandalone()) return;

  let banner = null;

  const hideBanner = () => {
    if (banner) banner.remove();
    banner = null;
  };

  const showBanner = ({ message, actionLabel, onAction }) => {
    if (banner || recentlyDismissed()) return;
    banner = document.createElement("div");
    banner.className = "pwa-install-banner";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-label", "Install Locatediscount");

    const icon = document.createElement("img");
    icon.className = "pwa-install-icon";
    icon.src = "/assets/img/pwa/icon-192.png";
    icon.alt = "";

    const text = document.createElement("div");
    text.className = "pwa-install-text";
    const title = document.createElement("strong");
    title.textContent = "Get the Locatediscount app";
    const body = document.createElement("span");
    body.textContent = message;
    text.append(title, body);

    const close = document.createElement("button");
    close.type = "button";
    close.className = "pwa-install-close";
    close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
    close.addEventListener("click", () => {
      rememberDismissal();
      hideBanner();
    });

    banner.append(icon, text);
    if (actionLabel) {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "pwa-install-action";
      action.textContent = actionLabel;
      action.addEventListener("click", onAction);
      banner.append(action);
    }
    banner.append(close);
    document.body.append(banner);
  };

  // Chrome, Edge, Samsung Internet and other Chromium browsers.
  let deferredPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    showBanner({
      message: "Install it on your device for quick access to deals.",
      actionLabel: "Install",
      onAction: async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === "dismissed") rememberDismissal();
        deferredPrompt = null;
        hideBanner();
      },
    });
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    hideBanner();
  });

  // iOS Safari has no install prompt, so explain the Share → Add to Home Screen step.
  const ua = window.navigator.userAgent;
  const isIos = /iphone|ipad|ipod/i.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
  if (isIos) {
    window.addEventListener("load", () => {
      setTimeout(() => {
        showBanner({ message: "Tap the Share button, then “Add to Home Screen”." });
      }, 2500);
    });
  }
})();
