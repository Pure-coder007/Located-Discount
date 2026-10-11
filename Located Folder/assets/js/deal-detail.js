document.addEventListener("DOMContentLoaded", () => {
  const page = document.querySelector(".deal-page");
  if (!page) return;

  const main = page.querySelector(".deal-main-image");
  const lightbox = document.querySelector("[data-lightbox]");
  const lightboxImage = document.querySelector("[data-lightbox-image]");
  const urls = [...new Set([...page.querySelectorAll('[data-gallery-image]')].map(el => el.dataset.galleryImage).filter(Boolean))];
  const previous = lightbox?.querySelector('[data-lightbox-prev]');
  const next = lightbox?.querySelector('[data-lightbox-next]');
  let index = 0, returnFocus, pointer;
  const show = (value) => {
    index = (value + urls.length) % urls.length;
    lightboxImage.src = urls[index];
    if (main) { main.src = urls[index]; main.dataset.galleryImage = urls[index]; }
    page.querySelectorAll('.deal-gallery-thumb').forEach(el => el.classList.toggle('is-selected', el.dataset.galleryImage === urls[index]));
    lightbox.querySelector('[data-lightbox-counter]').textContent = `${index + 1} / ${urls.length}`;
    [previous, next].forEach(button => button.hidden = urls.length < 2);
  };
  const open = (url) => {
    if (!urls.length || !lightboxImage) return;
    returnFocus = document.activeElement;
    show(Math.max(0, urls.indexOf(url)));
    lightbox.hidden = false;
    document.body.classList.add('deal-lightbox-open');
    lightbox.querySelector('[data-lightbox-close]').focus();
  };
  const close = () => {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    document.body.classList.remove('deal-lightbox-open');
    returnFocus?.focus();
  };
  page.addEventListener('click', event => {
    const image = event.target.closest('[data-gallery-image]');
    if (image) { event.preventDefault(); open(image.dataset.galleryImage); }
  });
  main?.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); open(main.dataset.galleryImage); } });
  previous?.addEventListener('click', () => show(index - 1));
  next?.addEventListener('click', () => show(index + 1));
  lightbox?.querySelector('[data-lightbox-close]').addEventListener('click', close);
  lightbox?.addEventListener('click', event => { if (event.target === lightbox) close(); });
  lightboxImage?.addEventListener('pointerdown', event => { pointer = {x:event.clientX, y:event.clientY}; });
  lightboxImage?.addEventListener('pointerup', event => {
    if (!pointer) return;
    const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) show(index + (dx < 0 ? 1 : -1));
    pointer = null;
  });
  lightboxImage?.addEventListener('pointercancel', () => { pointer = null; });
  document.addEventListener('keydown', event => {
    if (!lightbox || lightbox.hidden) return;
    if (event.key === 'Escape') close();
    if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); show(index + (event.key === 'ArrowRight' ? 1 : -1)); }
    if (event.key === 'Tab') {
      const buttons = [...lightbox.querySelectorAll('button')].filter(button => !button.hidden);
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  const countdown = page.querySelector("[data-countdown]");
  if (countdown) {
    const raw = countdown.dataset.countdown || "";
    const target = new Date((raw.includes("T") ? raw : raw.replace(" ", "T")) + (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? "" : "Z")).getTime();
    const label = countdown.querySelector("span");
    if (!Number.isNaN(target) && label) {
      const tick = () => {
        const remaining = Math.max(0, target - Date.now());
        const days = Math.ceil(remaining / 86400000);
        label.textContent = remaining ? `${days} day${days === 1 ? '' : 's'} to go` : "Deal ended";
      };
      tick();
      window.setInterval(tick, 1000);
    }
  }
});
