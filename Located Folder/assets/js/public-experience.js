(() => {
  document.querySelectorAll('[data-rating-picker]').forEach(picker => {
    const select = picker.querySelector('select');
    const preview = picker.querySelector('[data-rating-preview]');
    const show = () => { const stars = Number(select.value); if (select.form?.action.endsWith('/rate')) select.form.querySelector('button[type=submit]').disabled = !stars; preview.textContent = stars ? `${'⭐️'.repeat(stars)} — ${stars} out of 5` : 'Your rating is optional.'; };
    select.addEventListener('change', show); show();
  });
  document.addEventListener('click', event => {
    const thumbnail = event.target.closest('[data-offer-image]');
    if (thumbnail) {
      const card = thumbnail.closest('[data-offer-card]');
      card.querySelector('[data-offer-cover]').src = thumbnail.dataset.offerImage;
      card.querySelectorAll('[data-offer-image]').forEach(item => item.setAttribute('aria-pressed', String(item === thumbnail)));
    }
  });
  document.querySelector('.homepage-mobile-primary')?.addEventListener('click', () => document.querySelector('.home-menu-toggle')?.click());
  document.querySelector('.homepage-mobile-search')?.addEventListener('click', () => document.querySelector('#home-search-query')?.focus());
})();
