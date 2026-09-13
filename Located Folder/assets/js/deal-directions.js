(() => {
  const mapsUrl = (destination, origin) => {
    const url = new URL("https://www.google.com/maps/dir/");
    url.searchParams.set("api", "1");
    url.searchParams.set("destination", destination);
    if (origin) url.searchParams.set("origin", origin);
    url.searchParams.set("travelmode", "driving");
    return url.toString();
  };

  document.querySelectorAll("[data-directions]").forEach((link) => {
    const destination = link.dataset.destination;
    if (!destination) return;
    link.href = mapsUrl(destination);

    link.addEventListener("click", (event) => {
      if (!navigator.geolocation) return;
      event.preventDefault();
      const originalLabel = link.innerHTML;
      link.setAttribute("aria-busy", "true");
      link.classList.add("is-locating");

      const continueToMap = (origin) => {
        link.href = mapsUrl(destination, origin);
        window.location.assign(link.href);
      };
      const fallback = () => continueToMap("");

      navigator.geolocation.getCurrentPosition(
        ({ coords }) => continueToMap(`${coords.latitude},${coords.longitude}`),
        fallback,
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 120000 }
      );

      // A visible status is useful while the browser asks for permission.
      window.setTimeout(() => {
        if (!link.classList.contains("is-locating")) return;
        link.removeAttribute("aria-busy");
        link.innerHTML = originalLabel;
      }, 11000);
    });
  });
})();
