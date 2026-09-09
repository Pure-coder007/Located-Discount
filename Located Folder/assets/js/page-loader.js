(() => {
  const loader = document.getElementById("page-loader");
  if (!loader) return;

  const showLoader = () => {
    document.body.classList.remove("page-ready");
    document.body.classList.add("page-loading");
    document.body.setAttribute("aria-busy", "true");
  };

  const hideLoader = () => {
    document.body.classList.remove("page-loading");
    document.body.classList.add("page-ready");
    document.body.removeAttribute("aria-busy");
  };

  const isSamePageHashLink = (link) => {
    const url = new URL(link.href, window.location.href);
    return url.pathname === window.location.pathname && url.search === window.location.search && url.hash;
  };

  window.addEventListener("load", hideLoader);
  window.addEventListener("pageshow", hideLoader);

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    const link = event.target.closest("a[href]");
    if (!link || link.dataset.noPageLoader !== undefined || link.hasAttribute("download")) return;
    if (link.target && link.target !== "_self") return;

    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || isSamePageHashLink(link)) return;

    showLoader();
  }, true);

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.noPageLoader !== undefined) return;
    if (form.target && form.target !== "_self") return;
    showLoader();
  }, true);
})();
