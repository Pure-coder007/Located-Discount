"""A public catalogue-backed assistant. No private staff/customer data is exposed."""
import re
from urllib.parse import urlencode
from flask import jsonify, request, url_for
from .discovery import location_terms, location_match_sql

WHATSAPP = 'https://wa.me/2348037338514'
FAQS = [
    (('voucher', 'claim', 'generate', 'code', 'how it works'), 'To claim an offer, open a live deal and select Get voucher. Choose 1–10 items, provide your name and phone on your first claim, and optionally rate the deal with stars. Your QR and text voucher appear immediately. Pay the discounted total at the shop and let that business validate your voucher once.', 'My vouchers', 'my_codes'),
    (('restriction', 'limit', 'duplicate', 'already', 'error', 'cannot', "can't", 'blocked'), 'Each browser/device can generate one voucher per deal. A business can also set a customer limit. If you have already claimed, reopen your existing voucher in My vouchers. Availability varies by offer; an exhausted allocation may replenish the next day if the deal is still live. A voucher lasts up to seven days or until the offer expires, whichever comes first. Phone-profile recovery from a new browser needs admin help; never share your voucher code here.', 'Open my vouchers', 'my_codes'),
    (('save', 'saved', 'favorite', 'favourite', 'reminder'), 'Save for later keeps an offer without generating a voucher. Open Saved deals to see its images, prices, address, terms and status. In-app reminders highlight saved offers expiring within three days. Keep using the same browser to access your private dashboard.', 'Saved deals', 'saved_deals'),
    (('rate', 'rating', 'stars', 'review'), 'Ratings belong to individual deals. You can choose ⭐️ through ⭐️⭐️⭐️⭐️⭐️ when generating your voucher, or add/update your rating from your voucher page afterward. One rating per customer per deal is included in its average.', 'My vouchers', 'my_codes'),
    (('renew', 'expired', 'expiry', 'expire'), 'Businesses can renew an existing offer from My deals without re-entering its content. A business renewal goes for admin review; admins can renew and publish when business and wallet checks pass. Green means live, yellow means expiry within three days, and red means expired. Existing vouchers and customer limits are retained.', 'Browse current deals', 'deals'),
    (('register', 'business account', 'list my', 'merchant', 'owner', 'publish'), 'Register your business, then wait for admin approval. Approved businesses can add products, submit deals, fund their wallet, redeem customer vouchers and message the admin team. New and renewed business offers require review before they appear publicly.', 'List a business', 'register'),
    (('product', 'products', 'catalogue', 'catalog'), 'Browse the Products catalogue for items from approved local businesses. Product pages show images, descriptions, prices and the business contact/location, plus that business’s current deal links when available.', 'Browse products', 'products'),
    (('wallet', 'fee', 'top up', 'topup', 'paystack', 'fund'), 'Customers do not pay a platform fee. Businesses fund a prepaid wallet through Paystack or an admin-reviewed transfer. The applicable platform fee is deducted when a voucher is validated. Your business wallet shows the current fee and balance; contact admin about payment disputes.', 'Business sign in', 'login'),
    (('redeem', 'payment', 'pay', 'discount'), 'Show your voucher to the business in-store and receive the advertised discount. Pay the item total shown on your voucher at the shop. Only the business that owns the offer can validate it, and each voucher can be used once.', 'My vouchers', 'my_codes'),
    (('password', 'login', 'sign in'), 'Business and admin accounts sign in through the login page. Use Forgot password for a reset email. Customers can claim vouchers without creating a password account.', 'Sign in', 'login'),
    (('locatediscount', 'platform', 'what is this'), 'Locatediscount connects customers with approved local business offers. Browse live deals, compare prices and terms, save favourites and generate a private one-time voucher. Redeem it at the business in-store. Businesses manage offers and wallets through their dashboard, with admin review before publication.', 'Explore deals', 'deals'),
    (('policy', 'privacy', 'terms of service', 'faq'), 'Platform policies and frequently asked questions are available from the Policies page. Each deal also has its own terms, shown on the offer and voucher.', 'Platform policies', 'policies'),
]


def register_support(app, get_db, timestamp, days_left, voucher_remaining):
    @app.post('/support/ask')
    def support_ask():
        question = ' '.join(request.form.get('question', '').split())
        if not 1 <= len(question) <= 500:
            return jsonify(error='Please ask a question of 1 to 500 characters.'), 400
        q = question.casefold()
        db = get_db()
        base = '''FROM deals JOIN businesses ON businesses.id = deals.business_id
                  JOIN users ON users.id = businesses.owner_id
                  WHERE deals.is_active = 1 AND deals.is_approved = 1 AND deals.deleted_at IS NULL
                  AND deals.expires_at > ? AND deals.redemption_count < deals.redemption_limit
                  AND businesses.is_approved = 1 AND businesses.is_blocked = 0'''
        params = [timestamp()]
        extra = ''
        area = request.form.get('area', '').strip()[:300]
        nearby = any(word in q for word in ('near me', 'nearby', 'around me'))
        show_new = bool(re.search(r'\b(new|latest|recent)\b', q))
        show_expiring = any(word in q for word in ('expiring', 'ending soon', 'last chance'))
        contact = any(word in q for word in ('contact', 'whatsapp', 'admin', 'support', 'help desk', 'phone number'))
        generic = any(word in q for word in ('live deals', 'latest deals', 'new deals', 'all deals', 'offers today', 'expiring deals')) or q in ('hello', 'hi', 'help')
        faq_order = [FAQS[1], FAQS[2], FAQS[3], *[item for index, item in enumerate(FAQS) if index not in (1, 2, 3)]]
        faq = next((item for item in faq_order if any(re.search(r'(?<!\w)' + re.escape(word) + r'(?:s|es)?(?!\w)', q) for word in item[0])), None)
        # Search the title/vendor first, then meaningful words across public offer details.
        stop = {'show', 'find', 'me', 'the', 'a', 'an', 'about', 'tell', 'please', 'what', 'is', 'are', 'do', 'you', 'have', 'deals', 'deal', 'offers', 'offer', 'in', 'at', 'for', 'with', 'and', 'contact', 'details', 'phone', 'number', 'where', 'can', 'i', 'business', 'get', 'vouchers', 'price', 'pricing', 'cost', 'address', 'directions', 'opening', 'hours', 'available', 'today', 'any', 'would', 'like', 'know', 'how', 'does', 'work', 'when', 'expires', 'expire', 'much', 'many', 'location', 'looking', 'all', 'on'}
        words = [w for w in re.findall(r'[\w]+', q) if len(w) >= 3 and w not in stop][:10]
        matched_search = False
        if nearby and area:
            terms = location_terms(area)
            extra = ' AND (' + ' OR '.join(location_match_sql() for _ in terms) + ')'
            for term in terms:
                params.extend([f'%{term}%', f'%{term}%'])
        elif not generic and not show_new and not show_expiring and words:
            fragments = []
            for word in words:
                fragments.append('(LOWER(deals.title) LIKE ? OR LOWER(businesses.name) LIKE ? OR LOWER(deals.description) LIKE ? OR LOWER(businesses.city) LIKE ? OR LOWER(businesses.address) LIKE ? OR LOWER(deals.category) LIKE ?)')
            proposed = ' AND (' + ' AND '.join(fragments) + ')'
            search_params = [timestamp(), *[f'%{w}%' for w in words for _ in range(6)]]
            if db.execute('SELECT COUNT(*) total ' + base + proposed, search_params).fetchone()['total']:
                extra, params, matched_search = proposed, search_params, True
        places = []
        if not matched_search and not generic and words:
            conditions = ' AND '.join('(LOWER(businesses.name) LIKE ? OR LOWER(businesses.city) LIKE ? OR LOWER(businesses.address) LIKE ?)' for _ in words)
            vendors = db.execute('SELECT businesses.id, businesses.name, businesses.category, businesses.address, businesses.city, businesses.opening_hours, users.phone FROM businesses JOIN users ON users.id = businesses.owner_id WHERE businesses.is_approved = 1 AND businesses.is_blocked = 0 AND ' + conditions + ' ORDER BY businesses.name LIMIT 6', [f'%{word}%' for word in words for _ in range(3)]).fetchall()
            places = [dict(name=row['name'], category=row['category'], address=row['address'], city=row['city'], hours=row['opening_hours'], phone=row['phone'], url=url_for('vendor_detail', business_id=row['id'])) for row in vendors]
        count = db.execute('SELECT COUNT(*) total ' + base + extra, params).fetchone()['total']
        total = db.execute('SELECT COUNT(*) total ' + base, [timestamp()]).fetchone()['total']
        order = 'deals.expires_at ASC' if show_expiring else 'deals.created_at DESC, deals.id'
        rows = db.execute('''SELECT deals.*, businesses.name business_name, businesses.address,
                  businesses.city, businesses.opening_hours, users.phone business_phone,
                  (SELECT secure_url FROM deal_images WHERE deal_id = deals.id ORDER BY sort_order, id LIMIT 1) image_secure_url,
                  (SELECT file_name FROM deal_images WHERE deal_id = deals.id ORDER BY sort_order, id LIMIT 1) image_file_name ''' + base + extra + f' ORDER BY {order} LIMIT 6', params).fetchall()
        offers = []
        for row in rows:
            rating = db.execute('SELECT AVG(stars) average, COUNT(*) total FROM deal_ratings WHERE deal_id = ?', (row['id'],)).fetchone()
            image = row['image_secure_url'] or (url_for('static', filename='uploads/products/' + row['image_file_name']) if row['image_file_name'] else None)
            offers.append(dict(id=str(row['id']), title=row['title'], business=row['business_name'],
                description=row['description'], category=row['category'], terms=row['terms'],
                address=row['address'], city=row['city'], phone=row['business_phone'], hours=row['opening_hours'],
                price_kobo=row['discount_price_kobo'], regular_price_kobo=row['regular_price_kobo'],
                days_left=days_left(row), vouchers_remaining=voucher_remaining(row), image=image,
                rating=round(rating['average'], 1) if rating['total'] else None,
                url=url_for('deal_detail', deal_id=row['id'])))
        links = []
        if matched_search:
            message = f'I found {count} matching live offer(s). Here are their current prices, addresses and contact details. Open an offer for its full terms and image gallery.'
        elif places:
            message = 'Here are the matching approved businesses and their public contact details. Open a business to see its catalogue and any live offers.'
            offers = []
        elif any(word in q for word in ('categories', 'which category', 'what category')):
            names = [row['name'] for row in db.execute('SELECT name FROM categories WHERE is_active = 1 ORDER BY name').fetchall()]
            message = 'Current marketplace categories: ' + (', '.join(names) if names else 'Categories will appear as admins add them.')
            links.append(dict(label='Explore categories and offers', url=url_for('deals')))
            offers = []
        elif contact:
            message = 'The admin WhatsApp number is +234 803 733 8514. Use the WhatsApp button below for personal support, account recovery or detailed enquiries. I can also look up current public business contacts: ask using the business or deal name.'
            offers = []
        elif faq and not generic and not show_new and not nearby and not show_expiring:
            message = faq[1]
            links.append(dict(label=faq[2], url=url_for(faq[3])))
            offers = []
        elif nearby and not area:
            message = 'Choose Deals near me on the homepage or enter your city/area in search, then ask me again. These are current offers from the marketplace while you choose your location.'
            links.append(dict(label='Choose my location', url=url_for('home') + '#home-search-area'))
        elif nearby and not count:
            message = f'I could not match a live offer to {area}. Try a nearby district or a shorter city/area name. I can only show approved, unexpired offers from available businesses.'
        elif generic or show_new or show_expiring or nearby:
            message = f'There are {total} live offer(s) on Locatediscount right now. ' + ('These are the offers ending soonest.' if show_expiring else 'These are the newest approved offers.' if show_new else 'Here are current offers you can explore.')
        else:
            message = 'I could not find a matching public offer for that enquiry. Try a deal title, business name, category or city, or choose a suggested question below. For personal account issues, contact the admin on WhatsApp.'
            offers = []
        if not total and (generic or show_new or nearby):
            message += ' No live offers are currently available; newly approved offers will appear automatically.'
        if offers:
            links.append(dict(label='Browse all matching offers' if matched_search else 'Browse live deals', url=url_for('deals', **({'q': ' '.join(words)} if matched_search else {'area': area} if nearby and area else {}))))
        suggestions = ['Latest deals', 'Deals near me', 'How do I generate a voucher?', 'Saved deals and reminders', 'Voucher restrictions', 'Contact admin']
        response = jsonify(message=message, offers=offers, places=places, links=links, suggestions=suggestions,
                           whatsapp=WHATSAPP + '?' + urlencode({'text': 'Hello Locatediscount admin, I need help with: ' + question[:250]}),
                           updated_at=timestamp(), total_live=total)
        response.headers['Cache-Control'] = 'no-store'
        return response
