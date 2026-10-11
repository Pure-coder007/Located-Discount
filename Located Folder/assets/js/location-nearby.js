(() => {
  const button = document.querySelector('[data-use-location]');
  const status = document.querySelector('[data-location-status]');
  let locating = false;
  const say = message => { if (status) status.textContent = message; };
  const locate = () => new Promise((resolve,reject) => {
    let best, watch, finished=false;
    const finish = error => {
      if (finished) return;
      finished=true; clearTimeout(timer); if (watch !== undefined) navigator.geolocation.clearWatch(watch);
      if (best) resolve(best); else reject(error || {code:3});
    };
    const timer = setTimeout(() => finish(),18000);
    watch = navigator.geolocation.watchPosition(position => {
      if (!best || position.coords.accuracy < best.coords.accuracy) best=position;
      say(`Location found within about ${Math.round(best.coords.accuracy)} metres. Refining your position…`);
      if (best.coords.accuracy <= 80) finish();
    },error => { if (error.code===1) finish(error); },{enableHighAccuracy:true,maximumAge:0,timeout:15000});
  });
  const find = async () => {
    if (locating) return;
    if (!navigator.geolocation || !window.isSecureContext) {say('Location needs HTTPS or localhost and a browser with location support. You can still choose your area.');return;}
    locating=true;button.disabled=true;button.textContent='Finding your location…';say('Allow location access to find nearby businesses.');
    try {
      const {coords} = await locate();
      if (coords.accuracy > 3000) throw new Error('Your device location is too broad. Enable precise location or select your neighbourhood.');
      let result;
      const cacheKey=`locatediscount:reverse:${coords.latitude.toFixed(4)}:${coords.longitude.toFixed(4)}`;
      try { const cached=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(cached && Date.now()-cached.at<900000) result=cached.result;} catch (_) {}
      if (!result) {
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
        try {
          const endpoint=new URL('https://nominatim.openstreetmap.org/reverse');
          endpoint.search=new URLSearchParams({format:'jsonv2',addressdetails:'1',zoom:'18',lat:String(coords.latitude),lon:String(coords.longitude)});
          const response=await fetch(endpoint,{headers:{Accept:'application/json','Accept-Language':'en'},signal:controller.signal});
          if (!response.ok) throw new Error('Address lookup unavailable');result=await response.json();
          try {sessionStorage.setItem(cacheKey,JSON.stringify({result,at:Date.now()}));} catch (_) {}
        } catch (_) {
          // A failed address lookup must not block a valid GPS distance search.
          result={}; say('Your position was found. Finding businesses with confirmed map locations…');
        } finally {clearTimeout(timer);}
      }
      const a=result.address||{},clean=value=>value?.replace(/\s+(State|Local Government Area|LGA)$/i,'');
      const level=['neighbourhood','suburb','quarter','city_district','village','road','city','town','county','state'].find(key=>a[key])||'city';
      const local=clean(a.neighbourhood||a.suburb||a.quarter||a.city_district||a.village||a.road||a.city||a.town||a.county||a.state);
      const area=[...new Set([local,clean(a.city||a.town||a.county),clean(a.state)].filter(Boolean))].join(', ');
      const label=[a.house_number,a.road,local,a.city||a.town,a.state,a.postcode].filter(Boolean).filter((value,index,items)=>items.indexOf(value)===index).join(', ');
      const url=new URL(location.href);
      url.searchParams.set('nearby','1');url.searchParams.set('area',area);
      url.searchParams.set('lat',String(coords.latitude));url.searchParams.set('lng',String(coords.longitude));
      url.searchParams.set('location_level',level);
      url.searchParams.set('accuracy',String(Math.round(coords.accuracy)));url.searchParams.set('location_label',label||'Your device location');
      url.searchParams.delete('page');url.searchParams.delete('category_page');
      location.assign(url.toString());
    } catch(error) {
      say(error.code===1?'Location permission was denied. Enable precise location for this site, or select your area.':error.code===3?'Your device could not find a location. Try outdoors, enable location services, or select your area.':error.message==='Address lookup unavailable' || error.name==='AbortError'?'Your position was found, but the address lookup failed. Try again or select your neighbourhood.':error.message||'We could not locate you. Select your neighbourhood instead.');
      button.disabled=false;button.textContent='Deals near me';locating=false;
    }
  };
  button?.addEventListener('click',event=>{event.preventDefault();find();});
  document.querySelector('#home-search-area')?.addEventListener('change',event=>{
    event.target.form.querySelectorAll('[data-nearby-param]').forEach(input=>input.remove());
  });
})();
