document.addEventListener("DOMContentLoaded", () => {
  const page = document.querySelector(".deal-page");
  if (!page) return;

  const main = page.querySelector(".deal-main-image");
  const lightbox = document.querySelector("[data-lightbox]");
  const lightboxImage = document.querySelector("[data-lightbox-image]");
  const thumbs = page.querySelector(".deal-gallery-thumbs");

  const openLightbox = (url) => {
    if (!url || !lightbox || !lightboxImage) return;
    lightboxImage.src = url;
    lightbox.hidden = false;
    document.body.classList.add("deal-lightbox-open");
  };
  const closeLightbox = () => {
    if (!lightbox) return;
    lightbox.hidden = true;
    if (lightboxImage) lightboxImage.removeAttribute("src");
    document.body.classList.remove("deal-lightbox-open");
  };

  page.addEventListener("click", (event) => {
    const target = event.target.closest("[data-gallery-image]");
    if (!target || !page.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    const url = target.dataset.galleryImage;
    if (target === main) {
      openLightbox(url);
      return;
    }
    if (main) main.src = url;
    page.querySelectorAll(".deal-gallery-thumb").forEach((item) => item.classList.toggle("is-selected", item === target));
    openLightbox(url);
  });
  document.querySelector("[data-lightbox-close]")?.addEventListener("click", closeLightbox);
  lightbox?.addEventListener("click", (event) => { if (event.target === lightbox) closeLightbox(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeLightbox(); });

  const countdown = page.querySelector("[data-countdown]");
  if (countdown) {
    const raw = countdown.dataset.countdown || "";
    const target = new Date(raw.includes("T") ? raw : raw.replace(" ", "T")).getTime();
    const label = countdown.querySelector("span");
    if (!Number.isNaN(target) && label) {
      const tick = () => {
        const remaining = Math.max(0, target - Date.now());
        const hours = Math.floor(remaining / 3600000);
        const minutes = Math.floor((remaining % 3600000) / 60000);
        const seconds = Math.floor((remaining % 60000) / 1000);
        label.textContent = remaining ? `Ends in ${hours}h ${minutes}m ${seconds}s` : "Deal ended";
      };
      tick();
      window.setInterval(tick, 1000);
    }
  }
});
