(() => {
  const closeMenu = (header, toggle) => {
    header.classList.remove("nav-is-open");
    toggle.setAttribute("aria-expanded", "false");
    const label = toggle.querySelector(".visually-hidden");
    if (label) label.textContent = "Open navigation";
  };

  document.querySelectorAll(".nav-menu-toggle").forEach((toggle) => {
    const header = toggle.closest("header");
    const nav = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!header || !nav) return;

    toggle.addEventListener("click", () => {
      const isOpen = header.classList.toggle("nav-is-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
      const label = toggle.querySelector(".visually-hidden");
      if (label) label.textContent = isOpen ? "Close navigation" : "Open navigation";
    });

    nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => closeMenu(header, toggle)));
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 901px)").matches) closeMenu(header, toggle);
    });
  });
})();
