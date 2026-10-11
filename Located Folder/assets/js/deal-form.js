document.addEventListener('DOMContentLoaded', () => {
  const expiry = document.querySelector('[data-future-datetime]');
  if (!expiry) return;
  const offset = expiry.form.querySelector('[name="expiry_timezone_offset"]');
  const localValue = date => new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  const maximum = () => new Date(Date.now() + Number(expiry.dataset.maxDays || 7) * 86_400_000);
  const label = document.querySelector('[data-expiry-label]');
  if (label) label.textContent = 'Expiry date and time (your local time)';
  // Preserve POST values; stored UTC expiry dates are converted only on initial GET.
  if (expiry.dataset.currentExpiry) {
    const stored = new Date(expiry.dataset.currentExpiry);
    expiry.value = !Number.isNaN(stored.getTime()) && stored <= maximum() ? localValue(stored) : '';
  }
  const enforce = () => {
    const minDate = new Date(Date.now() + 60_000);
    minDate.setSeconds(0, 0);
    expiry.min = localValue(minDate);
    expiry.max = localValue(maximum());
    const selected = new Date(expiry.value);
    if (offset) offset.value = String(Number.isNaN(selected.getTime()) ? new Date().getTimezoneOffset() : selected.getTimezoneOffset());
    if (expiry.value && (Number.isNaN(selected.getTime()) || selected < minDate)) {
      expiry.setCustomValidity('Choose an expiry date and time in the future.');
    } else if (expiry.value && selected > maximum()) {
      expiry.setCustomValidity('Deals must expire within seven days from now.');
    } else expiry.setCustomValidity('');
  };
  enforce();
  expiry.addEventListener('input', enforce);
  expiry.addEventListener('change', enforce);
  expiry.form.addEventListener('submit', enforce);
});
