"""Regression coverage for private chat and customer offer flows."""
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from flask import session
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import Forbidden
from app import create_app, timestamp, utcnow


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE_URL': '', 'DATABASE': self.path, 'SECRET_KEY': 'test'})
        self.client = self.app.test_client()
        self.admin, self.owner, self.other, self.consumer, self.business, self.deal = [str(uuid4()) for _ in range(6)]
        with sqlite3.connect(self.path) as db:
            for index, (uid, role) in enumerate(((self.admin, 'admin'), (self.owner, 'business'), (self.other, 'business'), (self.consumer, 'consumer'))):
                db.execute('INSERT INTO users(id,email,phone,password_hash,role,name,created_at) VALUES (?,?,?,?,?,?,?)', (uid, f'{index}@example.test', f'+234801234560{index}', 'unused', role, role.title(), timestamp()))
            db.execute('INSERT INTO consumer_profiles(user_id,created_at) VALUES (?,?)', (self.consumer, timestamp()))
            db.execute('INSERT INTO businesses(id,owner_id,name,category,address,city,is_approved,wallet_balance,created_at) VALUES (?,?,?,?,?,?,1,5000,?)', (self.business, self.owner, 'Local Store', 'Shopping', '12 Allen Avenue, Ikeja', 'Lagos', timestamp()))
            db.execute("INSERT INTO deals(id,business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,review_status,created_at) VALUES (?,?,?,?,?,?,?,100,1,1,'approved',?)", (self.deal, self.business, 'Local offer', 'An excellent local offer', 'Shopping', 'In store only', timestamp(utcnow() + timedelta(days=2)), timestamp()))

    def tearDown(self):
        self.temp.cleanup()

    def login_as(self, uid):
        with self.client.session_transaction() as state:
            state['user_id'] = uid

    def post(self, path, data=None, follow=False):
        self.client.get('/')
        with self.client.session_transaction() as state:
            token = state['csrf_token']
        return self.client.post(path, data={'csrf_token': token, **(data or {})}, follow_redirects=follow)

    def test_save_without_claim_and_expiry_reminders(self):
        response = self.post(f'/deals/{self.deal}/favorite', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Local offer', response.data)
        self.assertIn(b'Reminder:', response.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM codes').fetchone()[0], 0)
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        self.assertEqual(self.client.get('/saved-deals').status_code, 200)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT user_id FROM favorites').fetchone()[0], self.consumer)
        self.post(f'/deals/{self.deal}/favorite')
        self.assertIn(b'No saved deals yet', self.client.get('/saved-deals').data)

    def test_location_matches_address_aliases_and_case(self):
        for route in ('/', '/deals'):
            response = self.client.get(route, query_string={'area': 'ikeja, Lagos State', 'nearby': '1'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'Showing the full marketplace', response.data)
        self.assertIn(b'Local offer', self.client.get('/deals?area=ALLEN').data)

    def test_ratings_require_generated_voucher_and_update_one_review(self):
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        self.post(f'/deals/{self.deal}/rate', {'stars': 5})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deal_ratings').fetchone()[0], 0)
            db.execute("INSERT INTO codes(deal_id,user_id,value,status,expires_at,created_at) VALUES (?,?,'LD-ABCDEF12','active',?,?)", (self.deal, self.consumer, timestamp(), timestamp()))
        self.post(f'/deals/{self.deal}/rate', {'stars': 5})
        self.post(f'/deals/{self.deal}/rate', {'stars': 4})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT stars FROM deal_ratings').fetchall(), [(4,)])
        self.assertIn(b'4.0/5', self.client.get(f'/deals/{self.deal}').data)
        self.assertEqual(self.post(f'/deals/{self.deal}/rate', {'stars': 6}).status_code, 400)

    def test_renewal_ownership_review_and_admin_publication(self):
        self.login_as(self.other)
        self.assertEqual(self.client.get(f'/deals/{self.deal}/renew').status_code, 403)
        self.login_as(self.owner)
        self.assertEqual(self.post(f'/deals/{self.deal}/renew', {'days': 0}).status_code, 400)
        response = self.post(f'/deals/{self.deal}/renew', {'days': 7}, follow=True)
        self.assertIn(b'submitted for admin approval', response.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT is_active,is_approved FROM deals').fetchone(), (0, 0))
        self.login_as(self.admin)
        # The renewal waits in its own tab, not among new deals, and the admin never re-enters days.
        self.assertNotIn(b'Local offer', self.client.get('/admin/deals?status=pending').data)
        renewals = self.client.get('/admin/deals?status=renewals').data
        self.assertIn(b'Local offer', renewals)
        self.assertIn(b'Approve renewal (7 days)', renewals)
        self.assertNotIn(f'/deals/{self.deal}/renew'.encode(), renewals)
        self.assertEqual(self.client.get(f'/deals/{self.deal}/renew').status_code, 302)
        response = self.post(f'/admin/deals/{self.deal}/approve', follow=True)
        self.assertIn(b'Renewal approved', response.data)
        with sqlite3.connect(self.path) as db:
            active, approved, expiry, requested = db.execute('SELECT is_active,is_approved,expires_at,renewal_requested_at FROM deals').fetchone()
            self.assertEqual((active, approved, requested), (1, 1, None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='approve_renewal'").fetchone()[0], 1)
        from datetime import datetime
        remaining = datetime.fromisoformat(expiry) - utcnow()
        self.assertTrue(timedelta(days=6, hours=23) < remaining <= timedelta(days=7))
        # An admin renewing a live deal directly is also capped at seven days.
        self.assertEqual(self.post(f'/deals/{self.deal}/renew', {'days': 8}).status_code, 400)
        response = self.post(f'/deals/{self.deal}/renew', {'days': 3}, follow=True)
        self.assertIn(b'Deal renewed and live', response.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='renew_deal'").fetchone()[0], 1)

    def test_expired_deals_are_red_and_absent_from_live_admin_view(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET expires_at = ?', (timestamp(utcnow() - timedelta(days=1)),))
        self.assertNotIn(b'data-offer-card', self.client.get('/').data)
        self.assertNotIn(b'data-offer-card', self.client.get('/deals').data)
        self.login_as(self.admin)
        self.assertNotIn(b'Local offer', self.client.get('/admin/deals?status=live').data)
        page = self.client.get('/admin/deals?status=all')
        self.assertIn(b'offer-status expired', page.data)
        self.assertIn(b'0 days to go', page.data)

    def test_customer_quantity_duplicate_and_voucher_limits(self):
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        response = self.post(f'/deals/{self.deal}/claim', {'quantity': 'bad'})
        self.assertEqual(response.status_code, 400)
        first = self.post(f'/deals/{self.deal}/claim', {'quantity': '2'})
        self.assertIn('/codes/', first.headers['Location'])
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET redemption_limit = 1')
        # A duplicate is reported as a device restriction even when allocation is exhausted.
        duplicate = self.post(f'/deals/{self.deal}/claim')
        self.assertIn('/codes/', duplicate.headers['Location'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM codes').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT quantity FROM codes').fetchone()[0], 2)
        with sqlite3.connect(self.path) as db:
            for value in ('LD-123ABC01', 'LD-123ABC02'):
                db.execute("INSERT INTO codes(deal_id,user_id,value,status,expires_at,created_at) VALUES (?,?,?,'active',?,?)", (self.deal, self.consumer, value, timestamp(utcnow() + timedelta(days=1)), timestamp()))
        other = self.app.test_client()
        other.get('/')
        with other.session_transaction() as state:
            token = state['csrf_token']
        exhausted = other.post(f'/deals/{self.deal}/claim', data={'csrf_token': token, 'name': 'Another customer', 'phone': '+2348098765432'}, follow_redirects=True)
        self.assertIn(b"This deal has no vouchers left.", exhausted.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM codes').fetchone()[0], 3)
            self.assertIsNone(db.execute("SELECT id FROM users WHERE phone = '+2348098765432'").fetchone())

    def test_expired_customer_codes_move_to_history(self):
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO codes(deal_id,user_id,value,status,expires_at,created_at) VALUES (?,?,'LD-ABCDEF45','active',?,?)", (self.deal, self.consumer, timestamp(utcnow() - timedelta(days=1)), timestamp()))
        page = self.client.get('/my-codes?status=expired')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Local offer', page.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM codes').fetchone()[0], 'expired')

    def test_rating_is_recorded_atomically_at_voucher_generation(self):
        invalid = self.post(f'/deals/{self.deal}/claim', {'name': 'Customer', 'phone': '+2348097654321', 'stars': '9'})
        self.assertEqual(invalid.status_code, 400)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM codes').fetchone()[0], 0)
        response = self.post(f'/deals/{self.deal}/claim', {'name': 'Customer', 'phone': '+2348097654321', 'stars': '5'}, follow=True)
        self.assertIn(b'How would you rate this deal', response.data)
        self.assertIn(b'value="5" checked', response.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT stars FROM deal_ratings').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT status FROM codes').fetchone()[0], 'active')

    def test_images_and_full_details_on_home_and_saved_deals(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO deal_images(deal_id,file_name,secure_url,storage_provider,sort_order,created_at) VALUES (?,'test.jpg',?,'cloudinary',1,?)", (self.deal, 'https://res.cloudinary.com/example/image/upload/test.jpg', timestamp()))
        for page in (self.client.get('/'), self.client.get(f'/vendors/{self.business}'), self.post(f'/deals/{self.deal}/favorite', follow=True)):
            self.assertIn(b'https://res.cloudinary.com/example/image/upload/test.jpg', page.data)
            self.assertIn(b'In store only', page.data)
            self.assertIn(b'12 Allen Avenue', page.data)
            self.assertIn(b'data-offer-card', page.data)
            self.assertTrue(page.data.lstrip().startswith(b'<!doctype html>'))

    def test_vendor_offers_hide_unapproved_deleted_and_exhausted_deals(self):
        for field, value in (("is_approved", 0), ("deleted_at", timestamp()), ("redemption_count", 100)):
            with self.subTest(field=field):
                with sqlite3.connect(self.path) as db:
                    db.execute("UPDATE deals SET is_approved=1, deleted_at=NULL, redemption_count=0 WHERE id=?", (self.deal,))
                    db.execute(f"UPDATE deals SET {field}=? WHERE id=?", (value, self.deal))
                page = self.client.get(f'/vendors/{self.business}')
                self.assertEqual(page.status_code, 200)
                if field == 'redemption_count':
                    self.assertIn(b'SOLD OUT', page.data)
                    with sqlite3.connect(self.path) as db:
                        db.execute("UPDATE sold_out_deals SET hide_at = '2000-01-01 23:00:00'")
                    self.assertNotIn(b'data-offer-card', self.client.get(f'/vendors/{self.business}').data)
                else:
                    self.assertNotIn(b'data-offer-card', page.data)

    def test_location_handles_full_addresses_and_punctuation(self):
        for area in ('12 Allen Avenue Ikeja Lagos Nigeria', 'Allen-Avenue, Ikeja', 'Ikeja Local Government Area', ',,,'):
            page = self.client.get('/deals', query_string={'area': area})
            self.assertEqual(page.status_code, 200)
            if area != ',,,':
                self.assertIn(b'Local offer', page.data)
                self.assertNotIn(b'Showing the full marketplace', page.data)

    def test_assistant_reads_new_approved_offers_and_excludes_private_deals(self):
        response = self.post('/support/ask', {'question': 'Latest deals'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['offers'][0]['title'], 'Local offer')
        self.assertEqual(response.json['offers'][0]['phone'], '+2348012345601')
        with sqlite3.connect(self.path) as db:
            for title, approved in (('Brand new public offer', 1), ('Private unapproved offer', 0)):
                db.execute("INSERT INTO deals(business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,created_at) VALUES (?,?,?,'Shopping','Valid in store',?,100,1,?,?)", (self.business, title, 'A new offer description.', timestamp(utcnow() + timedelta(days=10)), approved, timestamp(utcnow() + timedelta(seconds=1))))
        fresh = self.post('/support/ask', {'question': 'Latest deals'}).json
        self.assertEqual(fresh['offers'][0]['title'], 'Brand new public offer')
        self.assertNotIn('Private unapproved offer', str(fresh))
        self.assertEqual(fresh['total_live'], 2)
        self.assertNotIn('wallet_balance', str(fresh))
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE businesses SET is_blocked = 1')
        blocked = self.post('/support/ask', {'question': 'Latest deals'}).json
        self.assertEqual(blocked['offers'], [])
        self.assertEqual(blocked['total_live'], 0)

    def test_assistant_questions_contact_and_csrf(self):
        voucher = self.post('/support/ask', {'question': 'How do I generate a voucher?'}).json
        self.assertIn('select Get voucher', voucher['message'])
        self.assertNotIn('Ratings belong', voucher['message'])
        contact = self.post('/support/ask', {'question': 'Contact admin'}).json
        self.assertNotIn('234', contact['message'])
        self.assertTrue(contact['whatsapp'].startswith('/contact/admin/whatsapp?'))
        redirect = self.client.get(contact['whatsapp'])
        self.assertEqual(redirect.status_code, 302)
        self.assertTrue(redirect.headers['Location'].startswith('https://wa.me/2349059476544?text='))
        lookup = self.post('/support/ask', {'question': 'What is the address and phone number for Local Store?'}).json
        self.assertEqual(lookup['offers'][0]['title'], 'Local offer')
        restrictions = self.post('/support/ask', {'question': 'Voucher restrictions'}).json
        self.assertIn('one voucher per deal', restrictions['message'])
        renewed = self.post('/support/ask', {'question': 'How do I renew a deal?'}).json
        self.assertIn('renew', renewed['message'])
        nearby = self.post('/support/ask', {'question': 'Deals near me', 'area': 'Ikeja, Lagos State'}).json
        self.assertEqual(len(nearby['offers']), 1)
        self.assertEqual(self.client.post('/support/ask', data={'question': 'Latest deals'}).status_code, 400)
        self.assertEqual(self.post('/support/ask', {'question': 'x' * 501}).status_code, 400)
        unknown = self.post('/support/ask', {'question': 'purple bananas on the moon'}).json
        self.assertEqual(unknown['offers'], [])
        self.assertIn('could not find', unknown['message'])

    def test_public_business_contacts_without_live_offers_and_private_saving(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET is_active = 0, is_approved = 0')
        response = self.post('/support/ask', {'question': 'Contact details for Local Store'}).json
        self.assertEqual(response['offers'], [])
        self.assertEqual(response['places'][0]['phone'], '+2348012345601')
        self.assertNotIn('wallet_balance', str(response))
        self.post(f'/deals/{self.deal}/favorite')
        self.assertIn(b'No saved deals yet', self.client.get('/saved-deals').data)

    def test_customer_device_and_saves_survive_staff_session_expiry(self):
        self.post(f'/deals/{self.deal}/favorite')
        with self.client.session_transaction() as state:
            state.clear()
        self.assertIn(b'Local offer', self.client.get('/saved-deals').data)
        claimed = self.post(f'/deals/{self.deal}/claim', {'name': 'Persistent customer', 'phone': '+2348098765430'})
        self.assertIn('/codes/', claimed.headers['Location'])
        with self.client.session_transaction() as state:
            state.clear()
        wallet = self.client.get('/my-codes')
        self.assertEqual(wallet.status_code, 200)
        self.assertIn(b'Persistent customer', wallet.data)
        self.assertIn(b'Local offer', self.client.get('/saved-deals').data)

    def test_chat_persistence_delivery_and_private_access(self):
        class Socket:
            def __init__(self, frames):
                self.frames = iter(frames); self.sent = []; self.closed = False
            def receive(self, timeout=None):
                try:
                    return next(self.frames)
                except StopIteration:
                    raise ConnectionClosed()
            def send(self, value):
                self.sent.append(json.loads(value))
            def close(self):
                self.closed = True
        handler = self.app.view_functions['chat_socket'].__wrapped__
        def run(uid, frames, origin='http://localhost'):
            ws = Socket(frames)
            with self.app.test_request_context(f'/ws/chat/{self.business}', headers={'Origin': origin}):
                session['user_id'] = uid; session['csrf_token'] = 'valid-token'
                handler(ws, self.business)
            return ws
        auth = json.dumps({'csrf_token': 'valid-token'})
        merchant = run(self.owner, [auth, json.dumps({'body': 'Hello admin'})])
        self.assertEqual([event['message']['body'] for event in merchant.sent if event['type'] == 'message'][0], 'Hello admin')
        admin = run(self.admin, [auth, json.dumps({'body': 'Hello business'})])
        self.assertEqual([x['message']['body'] for x in admin.sent if x['type'] == 'message'], ['Hello admin', 'Hello business'])
        reconnect = run(self.owner, [auth])
        self.assertEqual(len(reconnect.sent), 2)
        self.login_as(self.admin)
        counts = self.client.get('/chat/unread').json
        self.assertEqual(counts['total'], 1)
        self.assertEqual(counts['businesses'][0]['unread'], 1)
        run(self.admin, [auth, json.dumps({'type': 'read', 'sequence': 2})])
        self.assertEqual(self.client.get('/chat/unread').json['total'], 0)
        self.login_as(self.owner)
        self.assertEqual(self.client.get('/chat/unread').json['total'], 1)
        run(self.owner, [auth, json.dumps({'type': 'read', 'sequence': 2})])
        self.assertEqual(self.client.get('/chat/unread').json['total'], 0)

        with self.assertRaises(Forbidden):
            run(self.other, [auth])
        self.assertTrue(run(self.owner, [auth], 'https://evil.test').closed)
        self.assertTrue(run(self.owner, [json.dumps({'csrf_token': 'wrong'})]).closed)
        self.login_as(self.other)
        self.assertEqual(self.client.get(f'/chat?business_id={self.business}').status_code, 403)
        self.login_as(self.admin)
        self.assertNotIn(b'data-chat-history', self.client.get('/chat').data)
        self.assertIn(b'data-chat-history', self.client.get(f'/chat?business_id={self.business}').data)

    def test_actual_stock_midnight_and_existing_redemption(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET discount_price_kobo=1050, regular_price_kobo=1500, redemption_limit=3')
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        response = self.post(f'/deals/{self.deal}/claim', {'quantity': '3'})
        self.assertIn('/codes/', response.headers['Location'])
        self.assertIn('₦31.50'.encode(), self.client.get(response.headers['Location']).data)
        live = self.client.get('/deals')
        self.assertIn(b'SOLD OUT', live.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT quantity FROM codes').fetchone()[0], 3)
            hidden_at = db.execute('SELECT hide_at FROM sold_out_deals').fetchone()[0]
            self.assertTrue(hidden_at.endswith('23:00:00'))
            db.execute("UPDATE sold_out_deals SET hide_at='2000-01-01 23:00:00'")
        self.assertNotIn(b'data-offer-card', self.client.get('/deals').data)
        self.login_as(self.owner)
        with sqlite3.connect(self.path) as db:
            value = db.execute('SELECT value FROM codes').fetchone()[0]
        redeemed = self.post('/business/redeem', {'code': value}, follow=True)
        self.assertIn(b'Voucher validated', redeemed.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT redemption_count FROM deals').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0], 1)

    def test_daily_allocation_is_real_and_resets_at_lagos_midnight(self):
        from locatediscount.corrections import daily_voucher_remaining
        from unittest.mock import patch
        from datetime import datetime, timezone
        now = datetime(2026, 10, 11, 22, 59, tzinfo=timezone.utc)
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            deal = db.execute('SELECT * FROM deals').fetchone()
            initial = daily_voucher_remaining(db, deal, now)
            self.assertTrue(3 <= initial <= 7)
            db.execute("INSERT INTO codes(deal_id,user_id,value,status,expires_at,created_at,quantity) VALUES (?,?,?,'active',?,?,?)", (self.deal, self.other, 'LD-DAILY123', timestamp(now + timedelta(days=1)), timestamp(now), initial))
            self.assertEqual(daily_voucher_remaining(db, deal, now), 0)
            tomorrow = now + timedelta(minutes=2)
            self.assertTrue(3 <= daily_voucher_remaining(db, deal, tomorrow) <= 7)
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        with patch('app.utcnow', return_value=now):
            response = self.post(f'/deals/{self.deal}/claim', {'quantity': '1'}, follow=True)
            self.assertIn(b"Today&#39;s vouchers for this deal are all claimed", response.data)
            page = self.client.get(f'/deals/{self.deal}').data
            self.assertIn(b"Today's allocation claimed", page)
            self.assertNotIn(b'>SOLD OUT<', page)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM codes').fetchone()[0], 1)

    def test_vendor_expiry_timezone_conversion(self):
        from app import parse_vendor_expiry
        self.assertEqual(parse_vendor_expiry({'expires_at': '2026-10-12T14:00', 'expiry_timezone_offset': '-60'}).isoformat(), '2026-10-12T13:00:00+00:00')
        self.assertEqual(parse_vendor_expiry({'expires_at': '2026-10-12T14:00', 'expiry_timezone_offset': '240'}).isoformat(), '2026-10-12T18:00:00+00:00')

    def test_nearby_does_not_widen_to_state_or_whole_marketplace(self):
        for route in ('/', '/deals'):
            page = self.client.get(route, query_string={'area':'Unknown Neighbourhood, Lagos','nearby':'1'})
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(b'data-offer-card', page.data)
            self.assertNotIn(b'Showing the full marketplace', page.data)
            found = self.client.get(route, query_string={'area':'Ikeja, Lagos','nearby':'1'})
            self.assertIn(b'data-offer-card', found.data)
            self.assertIn(b'distance unverified', found.data)

    def test_nearby_uses_confirmed_distance_and_excludes_far_pins(self):
        self.login_as(self.owner)
        invalid = self.post('/business/location', {'latitude':'nan','longitude':'3.35'}, follow=True)
        self.assertIn(b'Choose a valid latitude', invalid.data)
        self.post('/business/location', {'latitude':'6.6','longitude':'3.35'})
        page = self.client.get('/?nearby=1&area=Ikeja,Lagos&lat=6.6&lng=3.35')
        self.assertIn(b'0.0 km away', page.data)
        self.post('/business/location', {'latitude':'7.6','longitude':'3.35'})
        page = self.client.get('/?nearby=1&area=Ikeja,Lagos&lat=6.6&lng=3.35')
        self.assertNotIn(b'data-offer-card', page.data)
        self.login_as(self.other)
        self.assertEqual(self.client.get('/business/location').status_code, 403)

    def test_nearby_pins_sort_by_distance_on_home_and_catalogue(self):
        from uuid import uuid4
        second_business, second_deal = str(uuid4()), str(uuid4())
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO businesses(id,owner_id,name,category,address,city,is_approved,wallet_balance,created_at) VALUES (?,?,?,?,?,?,1,5000,?)", (second_business,self.other,'Farther shop','Shopping','Ikeja','Lagos',timestamp()))
            db.execute("INSERT INTO deals(id,business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,review_status,created_at) VALUES (?,?,?,?,?,?,?,100,1,1,'approved',?)", (second_deal,second_business,'Farther offer','Farther deal','Shopping','In store only',timestamp(utcnow()+timedelta(days=2)),timestamp(utcnow()+timedelta(seconds=1))))
            db.execute('INSERT INTO business_locations VALUES (?,?,?,?)', (self.business,6.6,3.35,timestamp()))
            db.execute('INSERT INTO business_locations VALUES (?,?,?,?)', (second_business,6.61,3.35,timestamp()))
        for route in ('/', '/deals'):
            data = self.client.get(route,query_string={'nearby':'1','lat':'6.6','lng':'3.35','area':'Ikeja,Lagos'}).data
            self.assertLess(data.index(b'Local offer'),data.index(b'Farther offer'))
            self.assertIn(b'0.0 km away',data)
            self.assertIn(b'name="lat" value="6.6" data-nearby-param',data)
        home = self.client.get('/').data
        self.assertIn(b'data-use-location',home)
        self.assertIn(b'class="hero-copy"',home)
        # A city-only device lookup must not treat unpinned shops as near.
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM business_locations')
        data = self.client.get('/?nearby=1&lat=6.6&lng=3.35&area=Lagos&location_level=city').data
        self.assertNotIn(b'data-offer-card',data)

    def test_password_recovery_sends_designed_mail_and_change_confirmation(self):
        self.post('/forgot-password', {'email':'1@example.test'})
        emails = self.app.extensions['mail_outbox']
        self.assertEqual(emails[-1]['event'],'password_reset')
        link = self.app.extensions['password_reset_links'][-1][1]
        from urllib.parse import urlparse
        changed = self.post(urlparse(link).path, {'password':'NewPasswordSecure123','password_confirmation':'NewPasswordSecure123'}, follow=True)
        self.assertIn(b'Your password has been reset',changed.data)
        self.assertEqual(emails[-1]['event'],'password_changed')
        self.assertNotIn('NewPasswordSecure123',emails[-1]['html'])
        self.assertNotIn('NewPasswordSecure123',emails[-1]['text'])

    def test_email_verification_single_use_expiry_and_resend_cooldown(self):
        import re
        self.login_as(self.owner)
        self.post('/account/verify-email')
        messages = self.app.extensions['mail_outbox']
        self.assertEqual(messages[-1]['event'], 'verification')
        token = re.search(r'/verify-email/([^"<]+)', messages[-1]['html']).group(1)
        before = len(messages)
        self.post('/account/verify-email')
        self.assertEqual(len(messages), before)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE email_verifications SET expires_at='2000-01-01',requested_at='2000-01-01'")
        invalid = self.client.get('/verify-email/'+token, follow_redirects=True)
        self.assertIn(b'invalid, expired, or already used', invalid.data)
        self.post('/account/verify-email')
        token = re.search(r'/verify-email/([^"<]+)', messages[-1]['html']).group(1)
        confirmed = self.client.get('/verify-email/'+token, follow_redirects=True)
        self.assertIn(b'Your email address is verified', confirmed.data)
        repeated = self.client.get('/verify-email/'+token, follow_redirects=True)
        self.assertIn(b'already used', repeated.data)
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute('SELECT verified_at FROM email_verifications').fetchone()[0])

    def test_resend_delivery_contract_failure_and_no_key(self):
        from locatediscount.mailer import send_email
        from unittest.mock import patch, Mock
        import requests
        self.app.config.update(TESTING=False, RESEND_API_KEY='re_unit_test', MAIL_FROM='Locatediscount <hello@example.com>', PUBLIC_BASE_URL='https://example.com')
        with self.app.test_request_context(), patch('locatediscount.mailer.requests.post') as send:
            send.return_value = Mock(json=lambda:{'id':'email-test'})
            self.assertTrue(send_email('password_reset','test@example.com',name='Ada',message='Choose a password',action_url='https://example.com/reset',event_id='one'))
            args = send.call_args
            self.assertEqual(args.args[0], 'https://api.resend.com/emails')
            self.assertEqual(args.kwargs['headers']['Authorization'], 'Bearer re_unit_test')
            self.assertIn('Idempotency-Key', args.kwargs['headers'])
            self.assertIn('html', args.kwargs['json']); self.assertIn('text', args.kwargs['json'])
            send.side_effect = requests.Timeout('timeout')
            self.assertFalse(send_email('password_changed','test@example.com',message='Updated'))
            self.app.config['RESEND_API_KEY']=''
            send.reset_mock()
            self.assertFalse(send_email('password_changed','test@example.com',message='Updated'))
            send.assert_not_called()

    def test_email_previews_are_complete_escaped_and_admin_only(self):
        from locatediscount.mailer import render_email, EMAIL_EVENTS
        self.assertEqual(self.client.get('/admin/email-previews').status_code, 302)
        self.login_as(self.owner)
        self.assertEqual(self.client.get('/admin/email-previews').status_code, 403)
        self.login_as(self.admin)
        self.assertEqual(self.client.get('/admin/email-previews').status_code, 200)
        for event in EMAIL_EVENTS:
            preview = self.client.get('/admin/email-previews/'+event)
            self.assertEqual(preview.status_code, 200)
            self.assertNotIn(b'gradient', preview.data)
            self.assertIn("style-src 'unsafe-inline'", preview.headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-inline'", self.client.get('/').headers['Content-Security-Policy'])
        with self.app.test_request_context():
            message = render_email('verification',name='<script>alert(1)</script>',message='Confirm email')
            self.assertNotIn('<script>', message['html'])
            self.assertIn('&lt;script&gt;', message['html'])

    def test_home_has_twelve_deals_per_page(self):
        with sqlite3.connect(self.path) as db:
            for index in range(12):
                db.execute("INSERT INTO deals(id,business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,review_status,created_at) VALUES (?,?,?,?,?,?,?,100,1,1,'approved',?)", (str(uuid4()), self.business, f'Offer {index}', 'Local discount', 'Shopping', 'In store only', timestamp(utcnow() + timedelta(days=2)), timestamp()))
        first = self.client.get('/').data
        self.assertEqual(first.count(b'data-offer-card'), 12)
        self.assertIn(b'Next', first)
        self.assertEqual(self.client.get('/?page=2').data.count(b'data-offer-card'), 1)

    def test_sub_admin_permissions_enforced_and_updated(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO users(id,email,phone,password_hash,role,name,created_at,created_by_admin) VALUES ('staff','staff@example.test','+2348055555555','unused','admin','Staff',?,?)", (timestamp(), self.admin))
            db.execute("INSERT INTO admin_permissions VALUES ('staff', 'categories')")
        self.login_as('staff')
        self.assertEqual(self.client.get('/admin/categories').status_code, 200)
        self.assertEqual(self.client.get('/admin/tags').status_code, 200)
        for path in ('/admin/team', '/admin/users', '/admin/businesses', '/admin/deals', '/admin/analytics', '/admin/audit-logs', '/chat', '/chat/unread'):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        page = self.client.get('/admin')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'Wallet balances', page.data)
        self.assertNotIn(b'Chat with Admin', page.data)
        self.assertEqual(self.post(f'/admin/businesses/{self.business}/adjust-wallet', {'amount': '1000'}).status_code, 403)
        self.login_as(self.admin)
        self.assertEqual(self.post('/admin/team/staff/permissions', {'permissions': ['deals', 'chat', 'finance']}).status_code, 302)
        self.login_as('staff')
        self.assertEqual(self.client.get('/admin/deals').status_code, 200)
        self.assertEqual(self.client.get('/chat').status_code, 200)
        self.assertEqual(self.client.get('/admin/categories').status_code, 403)
        self.assertEqual(self.client.get('/admin').status_code, 200)

    def test_category_tag_search_synonyms_and_admin_crud(self):
        self.login_as(self.admin)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        self.post('/admin/tags', {'category_id': 'shopping', 'name': 'Mechanic workshop', 'keywords': 'drive, car, repair'})
        with sqlite3.connect(self.path) as db:
            tag = db.execute('SELECT id FROM category_tags').fetchone()[0]
            db.execute('INSERT INTO deal_tags VALUES (?,?)', (self.deal, tag))
        for term in ('drive', 'repair', 'Mechanic workshop'):
            page = self.client.get('/deals', query_string={'q': term})
            self.assertIn(b'Local offer', page.data)
        suggestions = self.client.get('/search/suggestions?q=drive').json['suggestions']
        self.assertTrue(any(row['label'] == 'Mechanic workshop' for row in suggestions))
        self.post('/admin/tags', {'tag_id': tag, 'category_id': 'shopping', 'name': 'Furniture', 'keywords': 'dining, table'})
        self.assertIn(b'Local offer', self.client.get('/deals?q=dining').data)
        self.post(f'/admin/tags/{tag}/delete')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deal_tags').fetchone()[0], 0)

    def test_public_navigation_gallery_and_customer_only_assistant(self):
        with sqlite3.connect(self.path) as db:
            for order in (1, 2):
                db.execute("INSERT INTO deal_images(deal_id,file_name,sort_order,created_at) VALUES (?,?,?,?)", (self.deal, f'photo{order}.jpg', order, timestamp()))
        home = self.client.get('/')
        self.assertNotIn(b'THE LOCAL EDIT', home.data)
        self.assertNotIn(b'offer-thumbnails', home.data)
        self.assertIn(b'Ask Locatediscount', home.data)
        self.assertNotIn(b'>About Us<', home.data)
        self.assertNotIn(b'<h3>Explore</h3>', home.data)
        self.assertNotIn(b'<h3>Business</h3>', home.data)
        detail = self.client.get(f'/deals/{self.deal}')
        self.assertIn(b'deal-gallery-thumbs', detail.data)
        self.assertNotIn(b'Ask Locatediscount', detail.data)
        for user in (self.admin, self.owner):
            self.login_as(user)
            self.assertNotIn(b'Ask Locatediscount', self.client.get('/').data)

    def test_vendor_tag_selection_validates_category_and_preserves_on_edit(self):
        with sqlite3.connect(self.path) as db:
            for key, name in (('shopping', 'Shopping'), ('beauty', 'Beauty')):
                db.execute('INSERT INTO categories(id,name,created_at) VALUES (?,?,?)', (key, name, timestamp()))
            db.execute("INSERT INTO category_tags(id,category_id,name) VALUES ('shop-tag','shopping','Furniture')")
            db.execute("INSERT INTO category_tags(id,category_id,name) VALUES ('beauty-tag','beauty','Hair')")
        self.login_as(self.owner)
        payload = {'title': 'Furniture offer', 'description': 'A discount on new furniture.', 'terms': 'Valid in store only.',
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'accept_policies': '1',
                   'redemption_limit': '50', 'expires_at': (utcnow() + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M'), 'tags': ['beauty-tag']}
        invalid = self.post('/business/deals/new', payload, follow=True)
        self.assertIn(b'Choose tags from the selected category', invalid.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deals WHERE title='Furniture offer'").fetchone()[0], 0)
        payload['tags'] = ['shop-tag']
        valid = self.post('/business/deals/new', payload)
        self.assertEqual(valid.status_code, 302)
        with sqlite3.connect(self.path) as db:
            deal_id = db.execute("SELECT id FROM deals WHERE title='Furniture offer'").fetchone()[0]
            self.assertEqual(db.execute('SELECT tag_id FROM deal_tags WHERE deal_id=?', (deal_id,)).fetchone()[0], 'shop-tag')
        self.assertIn(b'value="shop-tag" checked', self.client.get(f'/business/deals/{deal_id}/edit').data)
        payload['tags'] = ['beauty-tag']
        invalid_edit = self.post(f'/business/deals/{deal_id}/edit', payload, follow=True)
        self.assertIn(b'Choose tags from the selected category', invalid_edit.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT tag_id FROM deal_tags WHERE deal_id=?', (deal_id,)).fetchone()[0], 'shop-tag')

    def test_admin_chat_search_and_vendor_conversation_privacy(self):
        other_business = str(uuid4())
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO businesses(id,owner_id,name,category,address,city,is_approved,created_at) VALUES (?,?,?,?,?,?,1,?)',
                       (other_business, self.other, 'Bakery Corner', 'Shopping', '1 Market Street', 'Lagos', timestamp()))
        self.login_as(self.admin)
        filtered = self.client.get('/chat?q=local')
        self.assertEqual(filtered.status_code, 200)
        self.assertIn(b'Local Store', filtered.data)
        self.assertNotIn(b'Bakery Corner', filtered.data)
        self.assertIn(b'Open conversation', filtered.data)
        selected = self.client.get('/chat', query_string={'q': 'local', 'business_id': self.business})
        self.assertIn(f'data-business-id="{self.business}"'.encode(), selected.data)
        no_match = self.client.get('/chat?q=missing-name')
        self.assertIn(b'No businesses match this name', no_match.data)
        self.assertIn(b'Bakery Corner', self.client.get('/chat').data)
        self.login_as(self.owner)
        vendor = self.client.get('/chat?q=Bakery')
        self.assertNotIn(b'chat-business-search', vendor.data)
        self.assertNotIn(b'Bakery Corner', vendor.data)
        self.assertIn(f'data-business-id="{self.business}"'.encode(), vendor.data)
        self.assertEqual(self.client.get('/chat', query_string={'business_id': other_business}).status_code, 403)

    def test_wallet_fee_suspension_and_funding_resume(self):
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        claimed = self.post(f'/deals/{self.deal}/claim', {'quantity': 1})
        self.assertIn('/codes/', claimed.headers['Location'])
        with sqlite3.connect(self.path) as db:
            value = db.execute('SELECT value FROM codes').fetchone()[0]
            db.execute('UPDATE businesses SET wallet_balance=499, needs_top_up=0 WHERE id=?', (self.business,))
            db.execute('INSERT INTO favorites(user_id,deal_id,created_at) VALUES (?,?,?)', (self.consumer, self.deal, timestamp()))
        self.assertNotIn(b'data-offer-card', self.client.get('/deals').data)
        self.assertEqual(self.client.get(f'/deals/{self.deal}').status_code, 404)
        self.assertNotIn('/codes/', self.post(f'/deals/{self.deal}/claim').headers['Location'])
        self.assertIn(b'SUSPENDED', self.client.get('/saved-deals').data)
        self.login_as(self.owner)
        self.assertIn('Suspended — wallet'.encode(), self.client.get('/business/deals').data)
        blocked = self.post('/business/redeem', {'code': value}, follow=True)
        self.assertIn(b'cannot cover', blocked.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM codes').fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0], 0)
            # Exactly one fee must be sufficient even below the alert threshold.
            db.execute('UPDATE businesses SET wallet_balance=500, needs_top_up=1 WHERE id=?', (self.business,))
        self.assertIn(b'data-offer-card', self.client.get('/deals').data)
        redeemed = self.post('/business/redeem', {'code': value}, follow=True)
        self.assertIn(b'Voucher validated', redeemed.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT wallet_balance FROM businesses WHERE id=?', (self.business,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wallet_suspended_deals').fetchone()[0], 1)
        self.assertNotIn(b'data-offer-card', self.client.get('/deals').data)

    def test_wallet_suspension_uses_each_deals_effective_fee(self):
        second = str(uuid4())
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE businesses SET wallet_balance=400, redemption_fee=300 WHERE id=?', (self.business,))
            db.execute('UPDATE deals SET redemption_fee=500 WHERE id=?', (self.deal,))
            db.execute("INSERT INTO deals(id,business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,created_at) VALUES (?,?,?,'Good value','Shopping','In store',?,100,1,1,?)", (second, self.business, 'Affordable deal', timestamp(utcnow()+timedelta(days=2)), timestamp()))
        page = self.client.get('/deals')
        self.assertIn(b'Affordable deal', page.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT deal_id FROM wallet_suspended_deals').fetchall(), [(self.deal,)])
            db.execute('UPDATE businesses SET wallet_balance=500 WHERE id=?', (self.business,))
        self.client.get('/deals')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wallet_suspended_deals').fetchone()[0], 0)

    def test_admin_dashboard_totals_wallet_funding(self):
        old = timestamp(utcnow() - timedelta(days=40))
        with sqlite3.connect(self.path) as db:
            for amount, status, approved in ((1000, 'approved', timestamp()), (3000, 'approved', old), (500, 'pending', None), (700, 'rejected', None)):
                db.execute("INSERT INTO wallet_transactions(business_id,amount,kind,status,reference,note,created_at,approved_at) VALUES (?,?,'topup',?,?,'t',?,?)",
                           (self.business, amount, status, f'PSTK-{uuid4().hex}', approved or timestamp(), approved))
            db.execute("INSERT INTO wallet_transactions(business_id,amount,kind,status,reference,note,created_at,approved_at) VALUES (?,-1000,'adjustment','posted','ADJ-1','fix',?,?)", (self.business, timestamp(), timestamp()))
        self.login_as(self.admin)
        page = self.client.get('/admin').data.decode()
        self.assertIn('<small>Total received</small><strong>₦4,000</strong><em>2 payments, all time</em>', page)
        self.assertIn('<small>Today</small><strong>₦1,000</strong><em>1 payment</em>', page)
        self.assertIn('<strong>₦500</strong><em>1 started, not paid or verified</em>', page)

    def seed_funding(self):
        """Top-ups across periods, methods and statuses for the funding report."""
        other = str(uuid4())
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO businesses(id,owner_id,name,category,address,city,is_approved,wallet_balance,created_at) VALUES (?,?,?,?,?,?,1,0,?)",
                       (other, self.other, '=Bakery Corner', 'Shopping', '1 Market Street', 'Lagos', timestamp()))
            rows = ((self.business, 1000, 'approved', timestamp(), 'PSTK-TODAY1'),
                    (self.business, 3000, 'approved', timestamp(utcnow() - timedelta(days=40)), 'PSTK-OLD001'),
                    (other, 2500, 'approved', timestamp(), 'MAN-CASH01'),
                    (self.business, 500, 'pending', None, 'PSTK-PEND01'),
                    (self.business, 700, 'rejected', None, 'PSTK-FAIL01'))
            for business, amount, status, approved, reference in rows:
                db.execute("INSERT INTO wallet_transactions(business_id,amount,kind,status,reference,note,created_at,approved_at) VALUES (?,?,'topup',?,?,'t',?,?)",
                           (business, amount, status, reference, approved or timestamp(), approved))
            # Local Store: 5000 balance, 4000 of confirmed records -> flagged; the other wallet is 0 vs 2500.
        return other

    def test_funding_report_totals_filters_and_balance_check(self):
        other = self.seed_funding()
        self.login_as(self.admin)
        page = self.client.get('/admin/topups/report?range=all').data.decode()
        self.assertIn('<small>All time</small><strong>₦6,500</strong><em>3 payments</em>', page)
        self.assertIn('<small>Today</small><strong>₦3,500</strong><em>2 payments</em>', page)
        self.assertIn('<small>Total</small><strong>₦6,500</strong><em>3 payments</em>', page)
        self.assertIn('<small>Paystack</small><strong>₦4,000</strong>', page)
        self.assertIn('<small>Manual</small><strong>₦2,500</strong>', page)
        self.assertNotIn('PSTK-PEND01', page)
        self.assertIn('2 to review', page)
        self.assertIn('+₦1,000', page)
        self.assertIn('−₦2,500', page)
        pending = self.client.get('/admin/topups/report?status=pending&range=all').data.decode()
        self.assertIn('PSTK-PEND01', pending)
        self.assertNotIn('PSTK-TODAY1', pending)
        manual = self.client.get('/admin/topups/report?method=manual&range=all').data.decode()
        self.assertIn('MAN-CASH01', manual)
        self.assertNotIn('PSTK-TODAY1', manual)
        business = self.client.get(f'/admin/topups/report?business={other}&range=all').data.decode()
        self.assertIn('<small>Total</small><strong>₦2,500</strong>', business)
        self.assertIn('PSTK-OLD001', self.client.get('/admin/topups/report?q=old001&range=all').data.decode())
        old_day = (utcnow() - timedelta(days=40)).date()
        custom = self.client.get(f'/admin/topups/report?start={old_day + timedelta(days=1)}&end={old_day - timedelta(days=1)}').data.decode()
        self.assertIn('<small>Total</small><strong>₦3,000</strong><em>1 payment</em>', custom)
        self.assertEqual(self.client.get('/admin/topups/report?status=nonsense&range=bad&start=notadate').status_code, 200)

    def test_funding_report_csv_and_access(self):
        self.seed_funding()
        self.login_as(self.admin)
        response = self.client.get('/admin/topups/report.csv?range=all')
        self.assertEqual(response.mimetype, 'text/csv')
        self.assertIn('attachment; filename="wallet-funding-all.csv"', response.headers['Content-Disposition'])
        body = response.data.decode('utf-8-sig')
        self.assertIn('Date (Lagos),Business,Reference,Method,Amount (NGN),Status,Confirmed by,Note', body)
        self.assertIn("'=Bakery Corner,MAN-CASH01,Manual,2500,Confirmed", body)
        self.assertEqual(body.count('PSTK-'), 2)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO users(id,email,phone,password_hash,role,name,created_at,created_by_admin) VALUES ('staff','staff@example.test','+2348055555555','unused','admin','Staff',?,?)", (timestamp(), self.admin))
        self.login_as('staff')
        self.assertEqual(self.client.get('/admin/topups/report').status_code, 403)
        self.assertEqual(self.client.get('/admin/topups/report.csv').status_code, 403)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO admin_permissions(user_id, permission) VALUES ('staff', 'finance')")
        self.assertEqual(self.client.get('/admin/topups/report').status_code, 200)
        self.login_as(self.owner)
        self.assertEqual(self.client.get('/admin/topups/report').status_code, 403)

    def test_deal_form_shows_vendor_policy_agreement(self):
        self.login_as(self.owner)
        form = self.client.get('/business/deals/new').data.decode()
        self.assertIn('I confirm that I have read and agree to the', form)
        for key in ('terms', 'vendor', 'privacy'):
            self.assertIn(f'href="/policies/{key}"', form)
        self.assertIn('Platform Protection Policy', self.client.get('/policies/vendor').data.decode())
        document = self.client.get('/policies/vendor/document')
        self.assertEqual(document.mimetype, 'application/pdf')
        self.assertTrue(document.data.startswith(b'%PDF'))

    def test_review_queue_moves_sent_back_and_disapproved_deals_out(self):
        pending, returned, rejected = str(uuid4()), str(uuid4()), str(uuid4())
        with sqlite3.connect(self.path) as db:
            for deal_id, title in ((pending, 'Waiting offer'), (returned, 'Returned offer'), (rejected, 'Rejected offer')):
                db.execute("INSERT INTO deals(id,business_id,title,description,category,terms,expires_at,redemption_limit,is_active,is_approved,review_status,created_at) VALUES (?,?,?,?,?,?,?,10,0,0,'pending',?)",
                           (deal_id, self.business, title, 'A good offer for you', 'Shopping', 'In store', timestamp(utcnow() + timedelta(days=3)), timestamp()))
        self.login_as(self.admin)
        queue = self.client.get('/admin/deals').data.decode()
        self.assertIn('Returned offer', queue)
        self.assertEqual(self.post(f'/admin/deals/{returned}/review', {'action': 'changes_requested', 'reason': 'Please add a clearer photo'}).headers['Location'], '/admin/deals?status=pending')
        self.post(f'/admin/deals/{rejected}/review', {'action': 'disapproved', 'reason': 'Misleading price claim'})
        queue = self.client.get('/admin/deals').data.decode()
        self.assertIn('Waiting offer', queue)
        self.assertNotIn('Returned offer', queue)
        self.assertNotIn('Rejected offer', queue)
        sent_back = self.client.get('/admin/deals?status=sent_back').data.decode()
        self.assertIn('Returned offer', sent_back)
        self.assertIn('Please add a clearer photo', sent_back)
        self.assertNotIn('Rejected offer', sent_back)
        self.assertIn('Rejected offer', self.client.get('/admin/deals?status=disapproved').data.decode())
        self.assertIn('<b class="portal-nav-badge">1</b>', self.client.get('/admin/deals').data.decode().split('</nav>')[0])
        # The vendor resubmits: the deal returns to the review queue.
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        self.login_as(self.owner)
        self.post(f'/business/deals/{returned}/edit', {'title': 'Returned offer', 'description': 'A good offer for you', 'terms': 'In store',
                  'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'redemption_limit': '10', 'accept_policies': '1',
                  'expires_at': (utcnow() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')})
        self.login_as(self.admin)
        self.assertIn('Returned offer', self.client.get('/admin/deals').data.decode())
        self.assertNotIn('Returned offer', self.client.get('/admin/deals?status=sent_back').data.decode())

    def test_claim_keeps_quantity_and_enforces_limits_in_units(self):
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        page = self.client.get(f'/deals/{self.deal}/claim').data.decode()
        # Today's allocation is 3-7 units, so a voucher can always hold 3.
        today_max = int(page.split('data-max="')[1].split('"')[0])
        self.assertGreaterEqual(today_max, 3)
        self.assertIn(f'You can get up to {today_max} vouchers', page)
        # Asking for more than today's allocation keeps the chosen quantity on the page.
        too_many = self.post(f'/deals/{self.deal}/claim', {'quantity': str(today_max + 1)})
        self.assertEqual(too_many.status_code, 409)
        self.assertIn(f'value="{today_max + 1}"', too_many.data.decode())
        self.assertIn(f'Only {today_max} vouchers can be claimed for this deal today', too_many.data.decode())
        done = self.post(f'/deals/{self.deal}/claim', {'quantity': '3'}, follow=True).data.decode()
        self.assertIn('3 vouchers', done)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT quantity FROM codes WHERE deal_id=?', (self.deal,)).fetchone()[0], 3)
        again = self.post(f'/deals/{self.deal}/claim', {'quantity': '1'}, follow=True).data.decode()
        self.assertIn('already have a voucher for 3 units', again)

    def test_deal_text_lines_render_as_bullets(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE deals SET description=?, terms=? WHERE id=?", ('\u2022 Juicy <b>burger</b>\n- Free fries\n\n', 'Weekdays only', self.deal))
        page = self.client.get(f'/deals/{self.deal}').data
        self.assertIn(b'<ul class="deal-bullets"><li>Juicy &lt;b&gt;burger&lt;/b&gt;</li><li>Free fries</li></ul>', page)
        self.assertIn(b'<p>Weekdays only</p>', page)
        self.assertIn('Juicy &lt;b&gt;burger&lt;/b&gt; \u2022 Free fries'.encode(), self.client.get('/deals').data)

    def test_successful_deal_submit_clears_the_device_draft(self):
        self.login_as(self.owner)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        form = self.client.get('/business/deals/new').data
        self.assertIn(f'data-deal-draft="{self.business}:new"'.encode(), form)
        self.assertNotIn(b'data-clear-deal-draft', form)
        payload = {'title': 'Draft offer', 'description': 'A discount on quality products.', 'terms': 'Valid in store only.',
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'redemption_limit': '50', 'accept_policies': '1',
                   'expires_at': (utcnow()+timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')}
        failed = self.post('/business/deals/new', {**payload, 'accept_policies': ''}, follow=True).data
        self.assertNotIn(b'data-clear-deal-draft', failed)
        self.assertIn(b'data-draft-rerender="1"', failed)
        done = self.post('/business/deals/new', payload, follow=True).data
        self.assertIn(f'data-clear-deal-draft="{self.business}:new"'.encode(), done)
        self.assertNotIn(b'data-clear-deal-draft', self.client.get('/business').data)

    def test_deal_submission_requires_policy_acknowledgement(self):
        self.login_as(self.owner)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        payload = {'title': 'Policy offer', 'description': 'A discount on quality products.', 'terms': 'Valid in store only.',
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'redemption_limit': '50',
                   'expires_at': (utcnow()+timedelta(days=5)).strftime('%Y-%m-%dT%H:%M')}
        self.assertIn(b'name="accept_policies"', self.client.get('/business/deals/new').data)
        self.assertIn(b'tick the box to confirm you agree to the Vendor Terms and Conditions', self.post('/business/deals/new', payload, follow=True).data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deals WHERE title='Policy offer'").fetchone()[0], 0)
        self.assertEqual(self.post('/business/deals/new', {**payload, 'accept_policies': '1'}).status_code, 302)
        with sqlite3.connect(self.path) as db:
            deal_id = db.execute("SELECT id FROM deals WHERE title='Policy offer'").fetchone()[0]
        self.assertIn(b'tick the box to confirm you agree to the Vendor Terms and Conditions', self.post(f'/business/deals/{deal_id}/edit', {**payload, 'title': 'Changed offer'}, follow=True).data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT title FROM deals WHERE id=?", (deal_id,)).fetchone()[0], 'Policy offer')

    def test_vendor_expiry_is_capped_on_create_edit_and_renew(self):
        self.login_as(self.owner)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        payload = {'title': 'Seven day offer', 'description': 'A discount on quality products.', 'terms': 'Valid in store only.',
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'accept_policies': '1', 'redemption_limit': '50',
                   'expires_at': (utcnow()+timedelta(days=8)).strftime('%Y-%m-%dT%H:%M')}
        invalid = self.post('/business/deals/new', payload, follow=True)
        self.assertIn(b'next seven days', invalid.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deals WHERE title='Seven day offer'").fetchone()[0], 0)
        payload['expires_at'] = (utcnow()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
        self.assertEqual(self.post('/business/deals/new', payload).status_code, 302)
        with sqlite3.connect(self.path) as db:
            deal_id = db.execute("SELECT id FROM deals WHERE title='Seven day offer'").fetchone()[0]
            old_expiry = db.execute('SELECT expires_at FROM deals WHERE id=?', (deal_id,)).fetchone()[0]
        payload['expires_at'] = (utcnow()+timedelta(days=8)).strftime('%Y-%m-%dT%H:%M')
        self.assertIn(b'next seven days', self.post(f'/business/deals/{deal_id}/edit', payload, follow=True).data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT expires_at FROM deals WHERE id=?', (deal_id,)).fetchone()[0], old_expiry)
        self.assertEqual(self.post(f'/deals/{deal_id}/renew', {'days': 8}).status_code, 400)
        self.assertEqual(self.post(f'/deals/{deal_id}/renew', {'days': 7}).status_code, 302)
        with sqlite3.connect(self.path) as db:
            expiry = db.execute('SELECT expires_at FROM deals WHERE id=?', (deal_id,)).fetchone()[0]
        from datetime import datetime
        self.assertLessEqual(datetime.fromisoformat(expiry), utcnow()+timedelta(days=7))
