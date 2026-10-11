(() => {
  const form = document.querySelector('[data-business-location]');
  if (!form) return;
  const lat = form.elements.latitude, lng = form.elements.longitude, map = form.querySelector('[data-pin-map]'), status = form.querySelector('[data-pin-status]'), button = form.querySelector('[data-pin-current]');
  const update = () => {
    const valid = lat.value !== '' && lng.value !== '' && lat.checkValidity() && lng.checkValidity();
    map.hidden = !valid;
    if (valid) map.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat.value},${lng.value}`)}`;
  };
  [lat,lng].forEach(input => input.addEventListener('input',update)); update();
  button.addEventListener('click', () => {
    if (!navigator.geolocation) { status.textContent='This browser does not support location. Enter your map coordinates instead.'; return; }
    button.disabled=true; status.textContent='Allow location access. Use this only while at your business.';
    navigator.geolocation.getCurrentPosition(({coords}) => {
      lat.value=coords.latitude; lng.value=coords.longitude; update(); button.disabled=false;
      status.textContent=`Position found (accuracy about ${Math.round(coords.accuracy)} metres). Check the map, then save.`;
    }, () => {button.disabled=false;status.textContent='Location unavailable. Enter your coordinates from your business map pin.';}, {enableHighAccuracy:true,maximumAge:0,timeout:20000});
  });
})();
