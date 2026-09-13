(() => {
  const form = document.querySelector("[data-home-search]");
  const input = document.getElementById("home-search-query");
  const areaInput = document.getElementById("home-search-area");
  const categoryInput = document.getElementById("home-search-category");
  const menu = document.getElementById("home-search-suggestions");
  if (!form || !input || !areaInput || !categoryInput || !menu) return;

  let timer;
  let controller;

  const hide = () => {
    menu.replaceChildren();
    menu.hidden = true;
  };

  const choose = (suggestion) => {
    if (suggestion.kind === "category") {
      categoryInput.value = suggestion.value;
      input.value = "";
    } else if (suggestion.kind === "area") {
      areaInput.value = suggestion.value;
      input.value = "";
    } else {
      input.value = suggestion.value;
    }
    hide();
    form.requestSubmit();
  };

  const show = (suggestions) => {
    menu.replaceChildren();
    suggestions.forEach((suggestion) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "home-search-suggestion";
      const icon = suggestion.kind === "category" ? "bi-grid-3x3-gap-fill" : suggestion.kind === "area" ? "bi-geo-alt-fill" : "bi-bag-fill";
      button.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i><span><strong></strong><small></small></span>`;
      button.querySelector("strong").textContent = suggestion.label;
      button.querySelector("small").textContent = suggestion.detail;
      button.addEventListener("click", () => choose(suggestion));
      menu.append(button);
    });
    menu.hidden = suggestions.length === 0;
  };

  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) {
      hide();
      return;
    }
    timer = window.setTimeout(async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(`/search/suggestions?q=${encodeURIComponent(query)}`, { signal: controller.signal });
        if (!response.ok) return;
        const payload = await response.json();
        if (input.value.trim() === query) show(payload.suggestions || []);
      } catch (error) {
        if (error.name !== "AbortError") hide();
      }
    }, 180);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
  document.addEventListener("click", (event) => {
    if (!form.contains(event.target)) hide();
  });
})();
