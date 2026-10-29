// Ask before destructive form submissions. The CSP blocks inline onsubmit
// handlers, so forms opt in with data-confirm="Question to ask".
document.addEventListener('submit', (event) => {
  const form = event.target.closest('form[data-confirm]');
  if (form && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
  }
});
