document.addEventListener('DOMContentLoaded', () => {
  const input = document.querySelector('#voucher-quantity');
  const output = document.querySelector('#voucher-cost');
  if (!input || !output) return;
  const max = Number(input.dataset.max || 0);
  const update = () => {
    output.classList.remove('is-error');
    if (!/^\d+$/.test(input.value) || BigInt(input.value) < 1n) {
      output.textContent = 'Enter a positive whole number of vouchers.';
      output.classList.add('is-error');
      return;
    }
    if (max && BigInt(input.value) > BigInt(max)) {
      output.textContent = `You can get up to ${max} voucher${max === 1 ? '' : 's'} for this deal right now.`;
      output.classList.add('is-error');
      input.setCustomValidity(output.textContent);
      return;
    }
    input.setCustomValidity('');
    const count = BigInt(input.value);
    const kobo = count * BigInt(input.dataset.unitPrice);
    output.textContent = `Total for ${count} voucher${count === 1n ? '' : 's'}: ₦${(kobo / 100n).toLocaleString('en-NG')}.${(kobo % 100n).toString().padStart(2, '0')}`;
  };
  input.addEventListener('input', update);
  update();
});
