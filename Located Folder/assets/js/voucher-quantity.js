document.addEventListener('DOMContentLoaded', () => {
  const input = document.querySelector('#voucher-quantity');
  const output = document.querySelector('#voucher-cost');
  if (!input || !output) return;
  const update = () => {
    if (!/^\d+$/.test(input.value) || BigInt(input.value) < 1n) {
      output.textContent = 'Enter a positive whole number of vouchers.';
      return;
    }
    const kobo = BigInt(input.value) * BigInt(input.dataset.unitPrice);
    output.textContent = `Total: ₦${(kobo / 100n).toLocaleString('en-NG')}.${(kobo % 100n).toString().padStart(2, '0')}`;
  };
  input.addEventListener('input', update);
  update();
});
