document.addEventListener("DOMContentLoaded", () => {
  const storageKey = `locatediscount:form:${window.location.pathname}`;
  const failedSubmission = document.querySelector(".flash-danger");
  if (failedSubmission) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
      Object.entries(saved).forEach(([name, value]) => {
        const field = document.querySelector(`[name="${CSS.escape(name)}"]`);
        if (field && field.type !== "password" && field.type !== "file" && field.type !== "hidden") {
          field.value = value;
        }
      });
    } catch (_error) {
      sessionStorage.removeItem(storageKey);
    }
  } else {
    sessionStorage.removeItem(storageKey);
  }

  document.querySelectorAll('form[method="post"], form[method="POST"]').forEach((form) => {
    form.addEventListener("submit", () => {
      const values = {};
      new FormData(form).forEach((value, name) => {
        const field = form.elements.namedItem(name);
        if (typeof value === "string" && field && field.type !== "password" && field.type !== "hidden") {
          values[name] = value;
        }
      });
      sessionStorage.setItem(storageKey, JSON.stringify(values));
    });
  });

  document.querySelectorAll('input[name="phone"]').forEach((input) => {
    input.type = "tel";
    input.inputMode = "tel";
    input.pattern = "\\+?[0-9]{10,15}";
    input.addEventListener("input", () => {
      const hasLeadingPlus = input.value.startsWith("+");
      const digits = input.value.replace(/\D/g, "").slice(0, 15);
      input.value = `${hasLeadingPlus ? "+" : ""}${digits}`;
    });
  });

  document.querySelectorAll('input[name="price"]').forEach((input) => {
    input.type = "number";
    input.inputMode = "decimal";
    input.min = "0";
    input.step = "0.01";
  });

  document.querySelectorAll('input[type="number"]').forEach((input) => {
    input.addEventListener("keydown", (event) => {
      if (["e", "E", "+"].includes(event.key)) event.preventDefault();
    });
    input.addEventListener("input", () => {
      const allowsDecimal = input.step && input.step !== "1";
      const allowsNegative = !input.hasAttribute("min") || Number(input.min) < 0;
      const negative = allowsNegative && input.value.startsWith("-");
      let cleaned = input.value.replace(/[^0-9.]/g, "");
      if (!allowsDecimal) cleaned = cleaned.replace(/\./g, "");
      else {
        const [whole, ...fraction] = cleaned.split(".");
        cleaned = fraction.length ? `${whole}.${fraction.join("")}` : whole;
      }
      input.value = `${negative ? "-" : ""}${cleaned}`;
    });
  });

  document.querySelectorAll('input[type="password"]').forEach((input) => {
    if (input.dataset.visibilityReady === "true") return;

    const wrapper = document.createElement("div");
    wrapper.className = "password-field";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "password-visibility-toggle";
    button.setAttribute("aria-label", "Show password");
    button.setAttribute("aria-pressed", "false");
    button.innerHTML = '<i class="bi bi-eye" aria-hidden="true"></i>';
    wrapper.appendChild(button);
    input.dataset.visibilityReady = "true";

    button.addEventListener("click", () => {
      const isVisible = input.type === "text";
      input.type = isVisible ? "password" : "text";
      button.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
      button.setAttribute("aria-pressed", String(!isVisible));
      button.innerHTML = `<i class="bi ${isVisible ? "bi-eye" : "bi-eye-slash"}" aria-hidden="true"></i>`;
      input.focus();
    });
  });

  document.querySelectorAll('[data-numeric], input[type="tel"], input[inputmode="numeric"], input[inputmode="decimal"]').forEach((input) => {
    const clean = () => {
      if (input.dataset.numeric === "phone" || input.type === "tel") {
        const hasLeadingPlus = input.value.trim().startsWith("+");
        input.value = (hasLeadingPlus ? "+" : "") + input.value.replace(/\D/g, "");
      } else if (input.dataset.numeric === "decimal") {
        const parts = input.value.replace(/[^0-9.]/g, "").split(".");
        input.value = parts.shift() + (parts.length ? "." + parts.join("") : "");
      } else {
        input.value = input.value.replace(/\D/g, "");
      }
    };
    input.addEventListener("input", clean);
    input.addEventListener("paste", () => window.setTimeout(clean, 0));
  });
});
