document.addEventListener("DOMContentLoaded", () => {
  const expiry = document.querySelector("[data-future-datetime]");
  if (!expiry) return;

  const enforceExpiry = () => {
    const now = Date.now();
    const nextMinute = new Date(now + 60_000);
    nextMinute.setUTCSeconds(0, 0);
    // This field is labelled UTC and the server stores UTC timestamps.
    const minimum = nextMinute.toISOString().slice(0, 16);
    const maximum = new Date(now + Number(expiry.dataset.maxDays || 7) * 86_400_000).toISOString().slice(0, 16);
    expiry.min = minimum;
    expiry.max = maximum;
    if (expiry.value && expiry.value < minimum) {
      expiry.setCustomValidity("Choose an expiry date and time in the future.");
    } else if (expiry.value && expiry.value > maximum) {
      expiry.setCustomValidity("Deals must expire within seven days from now.");
    } else {
      expiry.setCustomValidity("");
    }
  };

  enforceExpiry();
  expiry.addEventListener("input", enforceExpiry);
  expiry.form?.addEventListener("submit", enforceExpiry);
});
