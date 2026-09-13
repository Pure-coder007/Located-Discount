(() => {
  if (!navigator.geolocation) return;

  const url = new URL(window.location.href);
  if (url.pathname !== "/" || url.searchParams.get("area") || url.searchParams.get("q") || url.searchParams.get("category")) return;

  const storageKey = "locatediscount:nearby-area";
  const savedArea = sessionStorage.getItem(storageKey);
  const showArea = (area) => {
    if (!area) return;
    sessionStorage.setItem(storageKey, area);
    url.searchParams.set("area", area);
    url.searchParams.set("nearby", "1");
    window.location.replace(url.toString());
  };

  if (savedArea) {
    showArea(savedArea);
    return;
  }

  navigator.geolocation.getCurrentPosition(async ({ coords }) => {
    try {
      const endpoint = new URL("https://nominatim.openstreetmap.org/reverse");
      endpoint.search = new URLSearchParams({
        format: "jsonv2", lat: String(coords.latitude), lon: String(coords.longitude), addressdetails: "1",
      });
      const response = await fetch(endpoint, { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const address = (await response.json()).address || {};
      showArea(address.city || address.town || address.village || address.suburb || address.county);
    } catch (_error) {
      // Location is optional: keep the normal all-deals view if lookup fails.
    }
  }, () => {}, { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 });
})();
