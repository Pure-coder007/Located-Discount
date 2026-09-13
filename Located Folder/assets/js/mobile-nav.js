(() => {
  const closeMenu = (header, toggle) => {
    header.classList.remove("nav-is-open");
    toggle.setAttribute("aria-expanded", "false");
  };

  document.querySelectorAll(".nav-menu-toggle").forEach((toggle) => {
    const header = toggle.closest("header");
    const nav = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!header || !nav) return;

    toggle.addEventListener("click", () => {
      const isOpen = header.classList.toggle("nav-is-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });

    nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => closeMenu(header, toggle)));
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 901px)").matches) closeMenu(header, toggle);
    });
  });

  document.querySelectorAll(".portal-menu-toggle").forEach((toggle) => {
    const sidebar = toggle.closest(".portal-sidebar");
    const nav = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!sidebar || !nav) return;

    const closePortalMenu = () => {
      sidebar.classList.remove("portal-nav-is-open");
      toggle.setAttribute("aria-expanded", "false");
    };

    toggle.addEventListener("click", () => {
      const isOpen = sidebar.classList.toggle("portal-nav-is-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });
    nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", closePortalMenu));
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 1024px)").matches) closePortalMenu();
    });
  });
})();
