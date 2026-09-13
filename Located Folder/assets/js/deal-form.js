document.addEventListener("DOMContentLoaded", () => {
  const expiry = document.querySelector("[data-future-datetime]");
  if (!expiry) return;

  const minimumExpiry = () => {
    const nextMinute = new Date(Date.now() + 60 * 1000);
    nextMinute.setSeconds(0, 0);
    const offset = nextMinute.getTimezoneOffset() * 60_000;
    return new Date(nextMinute.getTime() - offset).toISOString().slice(0, 16);
  };

  const enforceFutureDate = () => {
    const minimum = minimumExpiry();
    expiry.min = minimum;
    if (expiry.value && expiry.value < minimum) {
      expiry.setCustomValidity("Choose an expiry date and time in the future.");
    } else {
      expiry.setCustomValidity("");
    }
  };

  enforceFutureDate();
  expiry.addEventListener("input", enforceFutureDate);
  expiry.form?.addEventListener("submit", enforceFutureDate);
});
