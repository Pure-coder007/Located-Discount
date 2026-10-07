(() => {
  const url = new URL(location.href);
  const button = document.querySelector('[data-use-location]');
  const status = document.querySelector('[data-location-status]');
  const key = 'locatediscount:nearby-area-v3';
  let locating = false;
  const say = message => { if (status) status.textContent = message; };
  const showArea = area => {
    if (!area) return;
    try { sessionStorage.setItem(key, JSON.stringify({area, at:Date.now()})); } catch (_) {}
    url.searchParams.set('area', area); url.searchParams.set('nearby', '1');
    url.searchParams.delete('page'); url.searchParams.delete('category_page');
    location.assign(url.toString());
  };
  const finish = () => { locating = false; if (button) { button.disabled = false; button.textContent = 'Deals near me'; } };
  const findLocation = () => {
    if (locating) return;
    if (!navigator.geolocation) { say('Location is unavailable in this browser. Choose a region or enter your area in search.'); return; }
    locating = true;
    if (button) { button.disabled = true; button.textContent = 'Finding your area…'; }
    say('Please allow location access when your browser asks.');
    navigator.geolocation.getCurrentPosition(async ({coords}) => {
      const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const endpoint = new URL('https://nominatim.openstreetmap.org/reverse');
        endpoint.search = new URLSearchParams({format:'jsonv2', addressdetails:'1', lat:String(coords.latitude), lon:String(coords.longitude)});
        const response = await fetch(endpoint, {headers:{Accept:'application/json', 'Accept-Language':'en'}, signal:controller.signal});
        if (!response.ok) throw new Error('Lookup failed');
        const address = (await response.json()).address || {};
        const areas = [...new Set([address.suburb, address.neighbourhood, address.city_district, address.city, address.town, address.village, address.county, address.state].filter(Boolean).map(value => value.replace(/\s+(State|Local Government Area|LGA)$/i, '')))];
        if (!areas.length) throw new Error('Area unavailable');
        showArea(areas.join(', '));
      } catch (_) {
        say('We couldn’t look up your location. Choose a region or type your city/area in search.');
        finish();
      } finally { clearTimeout(timeout); }
    }, error => { say(error.code === 1 ? 'Location permission was denied. Allow location for this site in your browser settings and try again, or choose a region.' : 'Your location was unavailable. Choose a region or enter your city/area in search.'); finish(); }, {timeout:10000, maximumAge:0, enableHighAccuracy:true});
  };
  button?.addEventListener('click', event => { event.preventDefault(); try { sessionStorage.removeItem(key); } catch (_) {} findLocation(); });
})();
