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
        response = self.post(f'/deals/{self.deal}/renew', {'days': 1}, follow=True)
        self.assertIn(b'Deal renewed and live', response.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT is_active,is_approved FROM deals').fetchone(), (1, 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM admin_audit_logs WHERE action='renew_deal'").fetchone()[0], 1)

    def test_expired_deals_are_red_and_absent_from_live_admin_view(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET expires_at = ?', (timestamp(utcnow() - timedelta(days=1)),))
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
        self.assertIn(b"exceeds the vouchers remaining", exhausted.data)
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
        self.assertIn('⭐️⭐️⭐️⭐️⭐️'.encode(), response.data)
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
        self.assertIn('+234 803 733 8514', contact['message'])
        self.assertTrue(contact['whatsapp'].startswith('https://wa.me/2348037338514'))
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

    def test_actual_stock_large_quantity_midnight_and_existing_redemption(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE deals SET discount_price_kobo=1050, regular_price_kobo=1500')
        with self.client.session_transaction() as state:
            state['consumer_id'] = self.consumer
        response = self.post(f'/deals/{self.deal}/claim', {'quantity': '100'})
        self.assertIn('/codes/', response.headers['Location'])
        self.assertIn('₦1,050.00'.encode(), self.client.get(response.headers['Location']).data)
        live = self.client.get('/deals')
        self.assertIn(b'SOLD OUT', live.data)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT quantity FROM codes').fetchone()[0], 100)
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
            self.assertEqual(db.execute('SELECT redemption_count FROM deals').fetchone()[0], 100)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0], 1)

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
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000',
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

    def test_vendor_expiry_is_capped_on_create_edit_and_renew(self):
        self.login_as(self.owner)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO categories(id,name,created_at) VALUES ('shopping','Shopping',?)", (timestamp(),))
        payload = {'title': 'Seven day offer', 'description': 'A discount on quality products.', 'terms': 'Valid in store only.',
                   'category': 'Shopping', 'regular_price': '2500', 'discount_price': '2000', 'redemption_limit': '50',
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
