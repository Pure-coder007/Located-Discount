"""Exact map distances plus explicit, bounded address matches."""
import math
from flask import abort, flash, redirect, render_template, request, url_for
from .discovery import location_terms, location_expression


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(min(1, value)))


def coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:
            return None
        return lat, lon
    except (ValueError, TypeError):
        return None


def nearby_filter(db, args):
    """Never silently widen a precise nearby search to a whole city/state."""
    point = coordinates(args.get('lat'), args.get('lng'))
    if point is None and (args.get('lat') or args.get('lng')):
        return ' AND 1=0', [], {}, False
    area = args.get('area', '')[:300]
    rows = db.execute('''SELECT businesses.id, businesses.address, businesses.city, bl.latitude, bl.longitude
      FROM businesses LEFT JOIN business_locations bl ON bl.business_id=businesses.id
      WHERE businesses.is_approved=1 AND businesses.is_blocked=0''').fetchall()
    # GPS reverse lookup supplies neighbourhood/district as the search area,
    # and the full house/street address separately for display.
    primary = area.split(',')[0]
    terms = location_terms(primary) if primary.strip() else []
    words = [word for word in terms[0].split() if len(word)>=2] if terms else []
    address_ids = set()
    if words:
        expr = '('+location_expression('businesses.address')+" || ' ' || "+location_expression('businesses.city')+')'
        context = location_terms(area.split(',')[-1])[0].split() if ',' in area else []
        words.extend(word for word in context if word not in words and len(word)>=2)
        sql = ' AND '.join(f'{expr} LIKE ?' for _ in words)
        address_ids = {row['id'] for row in db.execute(f'SELECT businesses.id FROM businesses WHERE {sql}', [f'%{word}%' for word in words]).fetchall()}
    if point and args.get('location_level') in ('city','state','county'):
        address_ids = set()
    selected, distances, unpinned = [], {}, False
    for row in rows:
        pin = coordinates(row['latitude'], row['longitude'])
        if point and pin:
            distance = distance_km(*point, *pin)
            if distance <= 10:
                selected.append(row['id']); distances[str(row['id'])] = round(distance, 1)
        elif row['id'] in address_ids:
            selected.append(row['id']); unpinned = True
    if not selected:
        return ' AND 1=0', [], {}, False
    return ' AND businesses.id IN ('+','.join('?' for _ in selected)+')', selected, distances, unpinned


def register_nearby(app, get_db, current_user, business_for_user, roles_required, timestamp):
    key_type = 'UUID' if app.config['DATABASE_URL'] else 'TEXT'
    with app.app_context():
        get_db().execute(f'''CREATE TABLE IF NOT EXISTS business_locations (
          business_id {key_type} PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
          latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL,
          updated_at TEXT NOT NULL)''')

    @app.route('/business/location', methods=['GET','POST'])
    @roles_required('business')
    def business_location():
        business = business_for_user(current_user()['id'])
        if not business: abort(403)
        pin = get_db().execute('SELECT * FROM business_locations WHERE business_id=?', (business['id'],)).fetchone()
        if request.method=='POST':
            point = coordinates(request.form.get('latitude'), request.form.get('longitude'))
            if not point:
                flash('Choose a valid latitude and longitude for your business.', 'danger')
            else:
                get_db().execute('''INSERT INTO business_locations VALUES (?,?,?,?) ON CONFLICT(business_id)
                  DO UPDATE SET latitude=excluded.latitude,longitude=excluded.longitude,updated_at=excluded.updated_at''', (business['id'], *point, timestamp()))
                flash('Business map location saved. Nearby searches can now use your actual distance.', 'success')
                return redirect(url_for('business_location'))
        return render_template('business_location.html', business=business, pin=pin)

    @app.context_processor
    def nearby_context():
        return {'nearby_position_valid': coordinates(request.args.get('lat'), request.args.get('lng')) is not None, 'nearby_query': {key:request.args[key] for key in ('nearby','lat','lng','accuracy','location_label','location_level') if key in request.args}}
