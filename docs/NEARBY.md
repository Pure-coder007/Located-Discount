# Nearby discovery

The hero now uses a light veil over the original city image so dark headings stay readable. Descriptions have solid white backgrounds and a readable font size on small screens.

“Deals near me” asks for high-accuracy browser location, keeps the best reading for up to 18 seconds, and shows the device's reported accuracy. HTTPS (or localhost) and browser permission are required. A location estimate broader than 3 km is rejected rather than presented as precise. Street/house, neighbourhood, city, state and postcode details are requested from OpenStreetMap/Nominatim where available. If the address provider is unavailable, the valid GPS position still searches businesses with confirmed map pins. Reverse results are cached for 15 minutes by rounded position; attribution appears on the result page. Each user-initiated lookup makes at most one external request. No background geocoding or address autocomplete requests are made.

Business owners can save a confirmed map location under **Business location**. The “use current location” button is intended for use while at the business; vendors can also paste coordinates and check the map before saving. No bulk map-pin migration is performed.

Confirmed pins are checked against the customer's coordinates with great-circle distance, within a 10 km radius, and sorted nearest first. This is straight-line distance, not driving distance. A business with a known pin outside the radius is excluded even if its city name matches. Unpinned businesses can match the most specific detected area and geographic context by address, with “distance unverified” shown on their cards. When GPS only identifies a city/state/county, unpinned businesses are excluded to avoid pretending the whole state is nearby.

A precise nearby search with no matching offers stays empty and offers a clear-location link; it never silently switches to every deal. Nearby coordinates and filters persist in pagination and category links. Changing the region selection clears GPS filters. Ordinary manual city/area searches retain the existing broad discovery fallback, with an explicit notice.

Actual accuracy depends on device permissions, GPS/Wi-Fi availability, map coverage and vendors' saved pins. Street-level address lookup does not guarantee an exact house position.
