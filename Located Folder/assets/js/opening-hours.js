document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-hours-time]").forEach((timeInput) => {
    const picker = timeInput.closest(".hours-time-picker");
    const period = picker?.querySelector("[data-hours-period]");
    if (!period) return;
    timeInput.addEventListener("change", () => {
      if (timeInput.value) period.value = Number(timeInput.value.slice(0, 2)) >= 12 ? "PM" : "AM";
    });
    period.addEventListener("change", () => {
      if (!timeInput.value) return;
      let hour = Number(timeInput.value.slice(0, 2));
      const minute = timeInput.value.slice(3, 5);
      if (period.value === "PM" && hour < 12) hour += 12;
      if (period.value === "AM" && hour >= 12) hour -= 12;
      timeInput.value = `${String(hour).padStart(2, "0")}:${minute}`;
    });
  });
});
