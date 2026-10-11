(() => {
  document.querySelectorAll('[data-rating-picker]').forEach(picker => {
    const inputs = [...picker.querySelectorAll('input[name="stars"]')];
    const preview = picker.querySelector('[data-rating-preview]');
    const show = (hover) => {
      const selected = Number(inputs.find(input => input.checked)?.value || 0);
      const stars = hover || selected;
      inputs.forEach(input => input.closest('label').classList.toggle('is-filled', Number(input.value) <= stars));
      preview.textContent = selected ? `${selected}/5 — ${['', 'Poor', 'Fair', 'Good', 'Very good', 'Excellent'][selected]}` : 'Optional · choose your stars';
      const form = inputs[0]?.form;
      if (form?.action.endsWith('/rate')) form.querySelector('button[type=submit]').disabled = !selected;
    };
    inputs.forEach(input => {
      input.addEventListener('change', () => show());
      input.closest('label').addEventListener('pointerenter', () => show(Number(input.value)));
    });
    picker.querySelector('.rating-stars').addEventListener('pointerleave', () => show());
    picker.querySelector('[data-rating-clear]').addEventListener('click', () => { inputs.forEach(input => input.checked = false); show(); });
    show();
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
