"""Saved offers, deal reviews, renewals and authenticated staff WebSockets."""
import hmac
import json
import math
import time
from datetime import timedelta
from uuid import uuid4

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import HTTPException


def register_community(app, get_db, current_user, consumer_profile, business_for_user,
                       roles_required, timestamp, utcnow, parse_timestamp,
                       redemption_fee, audit, voucher_remaining):
    # Explicit IDs work with both database adapters without changing existing IDs.
    key_type = 'UUID' if app.config['DATABASE_URL'] else 'TEXT'
    with app.app_context():
        db = get_db()
        db.execute(f'''CREATE TABLE IF NOT EXISTS chat_messages (
            id {key_type} PRIMARY KEY, business_id {key_type} NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            sender_id {key_type} NOT NULL REFERENCES users(id), body TEXT NOT NULL,
            created_at TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE)''')
        db.execute('CREATE INDEX IF NOT EXISTS chat_business_sequence ON chat_messages(business_id, sequence)')
        db.execute(f'''CREATE TABLE IF NOT EXISTS chat_reads (business_id {key_type} NOT NULL REFERENCES businesses(id) ON DELETE CASCADE, user_id {key_type} NOT NULL REFERENCES users(id), last_sequence INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(business_id, user_id))''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS deal_ratings (
            deal_id {key_type} NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            user_id {key_type} NOT NULL REFERENCES users(id), stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
            updated_at TEXT NOT NULL, PRIMARY KEY(deal_id, user_id))''')

    def days_left(deal):
        return max(0, math.ceil((parse_timestamp(deal['expires_at']) - utcnow()).total_seconds() / 86400))

    def deal_state(deal):
        if days_left(deal) == 0:
            return 'expired'
        if not deal['is_active'] or not deal['is_approved']:
            return 'pending' if not deal['is_approved'] else 'closed'
        return 'expiring' if days_left(deal) <= 3 else 'live'

    def rating_summary(deal_id):
        return get_db().execute('SELECT AVG(stars) average, COUNT(*) total FROM deal_ratings WHERE deal_id = ?', (deal_id,)).fetchone()

    @app.context_processor
    def globals_for_community():
        consumer = consumer_profile()
        ids = session.get('saved_deals', [])
        db = get_db()
        if consumer:
            ids = list(set(ids + [str(row['deal_id']) for row in db.execute('SELECT deal_id FROM favorites WHERE user_id = ?', (consumer['id'],)).fetchall()]))
        reminders = 0
        if ids:
            reminders = db.execute(f"SELECT COUNT(*) total FROM deals WHERE id IN ({','.join('?' for _ in ids)}) AND is_active = 1 AND is_approved = 1 AND expires_at > ? AND expires_at <= ?", [*ids, timestamp(), timestamp(utcnow() + timedelta(days=3))]).fetchone()['total']
        def gallery(deal_id):
            return db.execute('SELECT * FROM deal_images WHERE deal_id = ? ORDER BY sort_order, id', (deal_id,)).fetchall()
        def own_rating(deal_id):
            if not consumer:
                return None
            return db.execute('SELECT stars FROM deal_ratings WHERE deal_id = ? AND user_id = ?', (deal_id, consumer['id'])).fetchone()
        return dict(days_left=days_left, deal_state=deal_state, rating_summary=rating_summary,
                    customer_profile=consumer, saved_reminder_count=reminders, deal_gallery=gallery, saved_deal_ids=set(ids), own_rating=own_rating)

    @app.get('/saved-deals')
    def saved_deals():
        consumer = consumer_profile()
        ids = session.get('saved_deals', [])
        db = get_db()
        if consumer:
            for deal_id in ids:
                if db.execute('SELECT id FROM deals WHERE id = ?', (deal_id,)).fetchone():
                    db.execute('INSERT INTO favorites(user_id, deal_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING', (consumer['id'], deal_id, timestamp()))
            session['saved_deals'] = []
            ids = [str(row['deal_id']) for row in db.execute('SELECT deal_id FROM favorites WHERE user_id = ?', (consumer['id'],)).fetchall()]
        offers = []
        if ids:
            offers = db.execute(f'''SELECT deals.*, businesses.name business_name, businesses.address, businesses.city, businesses.opening_hours,
                businesses.is_approved business_approved, businesses.is_blocked,
                (SELECT phone FROM users WHERE id = businesses.owner_id) business_phone,
                (SELECT file_name FROM deal_images WHERE deal_id = deals.id ORDER BY sort_order, id LIMIT 1) image_file_name,
                (SELECT secure_url FROM deal_images WHERE deal_id = deals.id ORDER BY sort_order, id LIMIT 1) image_secure_url
                FROM deals JOIN businesses ON businesses.id = deals.business_id
                WHERE deals.id IN ({','.join('?' for _ in ids)}) AND deals.deleted_at IS NULL ORDER BY deals.expires_at''', ids).fetchall()
        return render_template('saved_deals.html', deals=offers)

    @app.post('/deals/<string:deal_id>/rate')
    def rate_deal(deal_id):
        consumer = consumer_profile()
        if not consumer:
            flash('Generate a voucher before rating this deal.', 'info')
            return redirect(url_for('deal_detail', deal_id=deal_id))
        if not request.form.get('stars', '').strip():
            flash('Choose your stars to save a rating, or leave it for later.', 'info')
            code = get_db().execute('SELECT id FROM codes WHERE deal_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1', (deal_id, consumer['id'])).fetchone()
            return redirect(url_for('code_detail', code_id=code['id']) if code else url_for('saved_deals'))
        try:
            stars = int(request.form.get('stars', ''))
        except ValueError:
            abort(400)
        if stars not in range(1, 6):
            abort(400)
        db = get_db()
        code = db.execute("SELECT id FROM codes WHERE deal_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1", (deal_id, consumer['id'])).fetchone()
        if not code:
            flash('You can rate a deal when generating its voucher.', 'warning')
        else:
            db.execute('''INSERT INTO deal_ratings(deal_id, user_id, stars, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(deal_id, user_id) DO UPDATE SET stars = excluded.stars, updated_at = excluded.updated_at''', (deal_id, consumer['id'], stars, timestamp()))
            flash('Your deal rating has been saved.', 'success')
        return redirect(url_for('code_detail', code_id=code['id']) if code else url_for('deal_detail', deal_id=deal_id))

    @app.route('/deals/<string:deal_id>/renew', methods=['GET', 'POST'])
    @roles_required('admin', 'business')
    def renew_deal(deal_id):
        db = get_db()
        user = current_user()
        deal = db.execute('SELECT * FROM deals WHERE id = ? AND deleted_at IS NULL', (deal_id,)).fetchone()
        if not deal:
            abort(404)
        business = db.execute('SELECT * FROM businesses WHERE id = ?', (deal['business_id'],)).fetchone()
        if user['role'] == 'business' and str(business['owner_id']) != str(user['id']):
            abort(403)
        if request.method == 'POST':
            try:
                days = int(request.form.get('days', '30'))
                limit = int(request.form.get('redemption_limit', deal['redemption_limit']))
                if not deal['redemption_count'] < limit <= 100000:
                    raise ValueError()
                if not 1 <= days <= 365:
                    raise ValueError()
            except ValueError:
                flash('Choose 1 to 365 days and a redemption limit above the number already redeemed (maximum 100,000).', 'danger')
                return render_template('renew_deal.html', deal=deal), 400
            if not business['is_approved'] or business['is_blocked']:
                flash('The business must be approved and unblocked before renewal.', 'danger')
                return render_template('renew_deal.html', deal=deal), 400
            approved = user['role'] == 'admin'
            fee = deal['redemption_fee'] if deal['redemption_fee'] is not None else redemption_fee(db, business)
            if approved and business['wallet_balance'] < fee:
                flash('Fund the business wallet before publishing this renewal.', 'danger')
                return render_template('renew_deal.html', deal=deal), 400
            expiry = max(utcnow(), parse_timestamp(deal['expires_at'])) + timedelta(days=days)
            # Preserve codes, prices and customer limits; let the owner set renewed capacity.
            db.execute('''UPDATE deals SET expires_at = ?, redemption_limit = ?, is_active = ?, is_approved = ?,
                review_status = ?, review_reason = NULL, approved_at = ?, approved_by = ? WHERE id = ?''',
                (timestamp(expiry), limit, int(approved), int(approved),
                 'approved' if approved else 'pending', timestamp() if approved else None, user['id'] if approved else None, deal_id))
            if approved:
                audit('renew_deal', 'deal', deal_id, f'Renewed for {days} days')
            flash('Deal renewed and live.' if approved else 'Deal renewed and submitted for admin approval.', 'success')
            return redirect(url_for('admin_deals', status='all') if approved else url_for('business_deals'))
        return render_template('renew_deal.html', deal=deal)

    def chat_business(business_id):
        user = current_user()
        if not user or user['role'] not in ('admin', 'business'):
            abort(403)
        business = get_db().execute('SELECT * FROM businesses WHERE id = ?', (business_id,)).fetchone()
        if not business or (user['role'] == 'business' and str(business['owner_id']) != str(user['id'])):
            abort(403)
        return business

    def chat_counts():
        user = current_user()
        if not user or user['role'] not in ('admin', 'business'):
            return []
        sql = """SELECT businesses.id, businesses.name, COUNT(chat_messages.id) unread
            FROM businesses LEFT JOIN chat_reads ON chat_reads.business_id=businesses.id AND chat_reads.user_id=?
            LEFT JOIN chat_messages ON chat_messages.business_id=businesses.id
                AND chat_messages.sender_id<>? AND chat_messages.sequence>COALESCE(chat_reads.last_sequence,0)"""
        params = [user['id'], user['id']]
        if user['role'] == 'business':
            sql += ' WHERE businesses.owner_id=?'
            params.append(user['id'])
        sql += ' GROUP BY businesses.id, businesses.name ORDER BY businesses.name'
        return [dict(id=str(row['id']), name=row['name'], unread=row['unread']) for row in get_db().execute(sql, params).fetchall()]

    @app.context_processor
    def chat_badges():
        return dict(chat_unread_total=sum(row['unread'] for row in chat_counts()))

    @app.get('/chat/unread')
    @roles_required('admin', 'business')
    def chat_unread():
        rows = chat_counts()
        return jsonify(businesses=rows, total=sum(row['unread'] for row in rows))

    @app.get('/chat')
    @roles_required('admin', 'business')
    def chat():
        user = current_user()
        businesses = chat_counts()
        selected = request.args.get('business_id') or (str(businesses[0]['id']) if businesses and user['role'] == 'business' else None)
        business = chat_business(selected) if selected else None
        return render_template('chat.html', businesses=businesses, business=business)

    sock = Sock(app)
    app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25, 'max_message_size': 8192}

    @sock.route('/ws/chat/<string:business_id>')
    def chat_socket(ws, business_id):
        # Require a same-origin browser handshake and a session-bound token in its first frame.
        scheme = 'https' if app.config['SESSION_COOKIE_SECURE'] or request.is_secure else 'http'
        if request.headers.get('Origin') != f'{scheme}://{request.host}':
            ws.close(); return
        chat_business(business_id)
        try:
            auth = json.loads(ws.receive(timeout=10) or '{}')
            if not isinstance(auth, dict):
                ws.close(); return
            expected = session.get('csrf_token', '')
            if not expected or not hmac.compare_digest(str(auth.get('csrf_token', '')), expected):
                ws.close(); return
            last = 0
            db = get_db()
            while True:
                chat_business(business_id)  # Recheck account status/ownership after connection.
                rows = db.execute('''SELECT chat_messages.*, users.name sender_name, users.role sender_role
                    FROM chat_messages JOIN users ON users.id = sender_id
                    WHERE business_id = ? AND sequence > ? ORDER BY sequence LIMIT 100''', (business_id, last)).fetchall()
                for row in rows:
                    payload = dict(row)
                    payload['id'] = str(payload['id']); payload['business_id'] = str(payload['business_id']); payload['sender_id'] = str(payload['sender_id'])
                    ws.send(json.dumps(dict(type='message', message=payload)))
                    last = row['sequence']
                incoming = ws.receive(timeout=0.5)
                if incoming:
                    try:
                        data = json.loads(incoming)
                    except (ValueError, TypeError):
                        ws.send(json.dumps(dict(type='error', error='Invalid message. Please retry.'))); continue
                    if isinstance(data, dict) and data.get('type') == 'read':
                        sequence = data.get('sequence')
                        if isinstance(sequence, int) and 0 <= sequence <= last:
                            db.execute('INSERT INTO chat_reads(business_id,user_id,last_sequence) VALUES (?,?,?) ON CONFLICT(business_id,user_id) DO UPDATE SET last_sequence=CASE WHEN excluded.last_sequence>chat_reads.last_sequence THEN excluded.last_sequence ELSE chat_reads.last_sequence END', (business_id,current_user()['id'],sequence))
                            db.commit()
                        continue
                    if not isinstance(data, dict) or not isinstance(data.get('body'), str):
                        ws.send(json.dumps(dict(type='error', error='Enter a text message.'))); continue
                    body = data['body'].strip()
                    if not 1 <= len(body) <= 2000:
                        ws.send(json.dumps(dict(type='error', error='Enter a message of 1 to 2,000 characters.'))); continue
                    now = time.monotonic()
                    if now - getattr(ws, '_last_message_time', 0) < 0.5:
                        ws.send(json.dumps(dict(type='error', error='Please wait a moment before sending again.'))); continue
                    # Serialize sequence assignment across workers through a database transaction.
                    db.execute('BEGIN IMMEDIATE')
                    if app.config['DATABASE_URL']:
                        db.execute('LOCK TABLE chat_messages IN EXCLUSIVE MODE')
                    seq = db.execute('SELECT COALESCE(MAX(sequence), 0) + 1 next FROM chat_messages').fetchone()['next']
                    db.execute('INSERT INTO chat_messages(id, business_id, sender_id, body, created_at, sequence) VALUES (?, ?, ?, ?, ?, ?)',
                               (str(uuid4()), business_id, current_user()['id'], body, timestamp(), seq))
                    db.commit()
                    ws._last_message_time = now
                    ws.send(json.dumps(dict(type='sent')))
        except HTTPException:
            ws.close(); return
        except (ConnectionClosed, ValueError, TypeError):
            return
        finally:
            get_db().rollback()

    from .support import register_support
    register_support(app, get_db, timestamp, days_left, voucher_remaining)
