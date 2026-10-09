"""Category search tags, staff permissions and actual voucher stock."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import abort, flash, redirect, render_template, request, url_for

PERMISSIONS = {
    'businesses': 'Business review and management',
    'deals': 'Deal review and management',
    'finance': 'Wallets, funding and platform fees',
    'users': 'Customer and vendor accounts',
    'codes': 'Voucher activity',
    'analytics': 'Analytics',
    'categories': 'Categories and search tags',
    'chat': 'Vendor chat',
    'audit': 'Audit logs',
}


def stock_remaining(db, deal):
    issued = db.execute('SELECT COALESCE(SUM(quantity), 0) total FROM codes WHERE deal_id = ?', (deal['id'],)).fetchone()['total']
    return max(0, deal['redemption_limit'] - max(issued, deal['redemption_count']))


def register_corrections(app, get_db, current_user, roles_required, timestamp, utcnow, audit):
    key_type = 'UUID' if app.config['DATABASE_URL'] else 'TEXT'
    with app.app_context():
        db = get_db()
        db.execute(f'''CREATE TABLE IF NOT EXISTS admin_permissions (
            user_id {key_type} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission TEXT NOT NULL, PRIMARY KEY(user_id, permission))''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS category_tags (
            id {key_type} PRIMARY KEY, category_id {key_type} NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL, keywords TEXT NOT NULL DEFAULT '', UNIQUE(category_id, name))''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS deal_tags (
            deal_id {key_type} NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            tag_id {key_type} NOT NULL REFERENCES category_tags(id) ON DELETE CASCADE,
            PRIMARY KEY(deal_id, tag_id))''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS sold_out_deals (
            deal_id {key_type} PRIMARY KEY REFERENCES deals(id) ON DELETE CASCADE,
            sold_at TEXT NOT NULL, hide_at TEXT NOT NULL)''')

    def admin_can(permission, user=None):
        user = user or current_user()
        if not user or user['role'] != 'admin':
            return False
        if not user['created_by_admin']:
            return True
        return bool(get_db().execute('SELECT 1 FROM admin_permissions WHERE user_id = ? AND permission = ?', (user['id'], permission)).fetchone())

    def save_permissions(user_id):
        selected = set(request.form.getlist('permissions'))
        if not selected.issubset(PERMISSIONS):
            abort(400)
        db = get_db()
        db.execute('DELETE FROM admin_permissions WHERE user_id = ?', (user_id,))
        for permission in selected:
            db.execute('INSERT INTO admin_permissions(user_id, permission) VALUES (?, ?)', (user_id, permission))

    def save_tags(deal_id, category):
        db = get_db()
        selected = set(request.form.getlist('tags'))
        valid = {str(row['id']) for row in db.execute('SELECT category_tags.id FROM category_tags JOIN categories ON categories.id = category_tags.category_id WHERE categories.name = ? AND categories.is_active = 1', (category,)).fetchall()}
        if not selected.issubset(valid):
            raise ValueError('Choose tags from the selected category.')
        db.execute('DELETE FROM deal_tags WHERE deal_id = ?', (deal_id,))
        for tag in selected:
            db.execute('INSERT INTO deal_tags(deal_id, tag_id) VALUES (?, ?)', (deal_id, tag))

    def tag_query_matches(query):
        return bool(query and get_db().execute('SELECT 1 FROM category_tags WHERE LOWER(name) LIKE LOWER(?) OR LOWER(keywords) LIKE LOWER(?) LIMIT 1', (f'%{query}%', f'%{query}%')).fetchone())

    @app.before_request
    def staff_access_and_stock():
        if request.endpoint == 'static':
            return
        user = current_user()
        endpoint = request.endpoint or ''
        if user and user['role'] == 'admin' and user['created_by_admin']:
            permission = None
            if request.path.startswith('/admin/team'):
                abort(403)  # Only primary administrators can grant staff access.
            elif request.path.startswith('/admin/businesses'):
                permission = 'finance' if endpoint in ('adjust_wallet', 'update_business_fee') else 'businesses'
            elif request.path.startswith('/admin/deals'):
                permission = 'finance' if request.path.endswith('/fee') else 'deals'
            elif request.path.startswith(('/admin/topups', '/admin/fee-rule')):
                permission = 'finance'
            elif request.path.startswith('/admin/categories') or request.path.startswith('/admin/tags'):
                permission = 'categories'
            elif request.path.startswith('/admin/users'):
                permission = 'users'
            elif request.path.startswith('/admin/code-activity'):
                permission = 'codes'
            elif request.path.startswith('/admin/analytics'):
                permission = 'analytics'
            elif request.path.startswith('/admin/audit-logs'):
                permission = 'audit'
            elif request.path.startswith(('/chat', '/ws/chat')):
                permission = 'chat'
            elif endpoint == 'renew_deal':
                permission = 'deals'
            if permission and not admin_can(permission):
                abort(403)
        db = get_db()
        exhausted = db.execute('''SELECT deals.id, deals.redemption_limit, deals.redemption_count,
            COALESCE(SUM(codes.quantity), 0) issued, MAX(codes.created_at) last_claim
            FROM deals LEFT JOIN codes ON codes.deal_id = deals.id
            GROUP BY deals.id HAVING COALESCE(SUM(codes.quantity), 0) >= deals.redemption_limit
                OR deals.redemption_count >= deals.redemption_limit''').fetchall()
        for deal in exhausted:
            sold_at = deal['last_claim'] or timestamp()
            sold_time = datetime.fromisoformat(sold_at)
            lagos = timezone(timedelta(hours=1))
            local = sold_time.astimezone(lagos)
            midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=lagos)
            # Match SQL CURRENT_TIMESTAMP's representation on both database adapters.
            hide_at = midnight.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            db.execute('INSERT INTO sold_out_deals(deal_id,sold_at,hide_at) VALUES (?,?,?) ON CONFLICT(deal_id) DO NOTHING', (deal['id'], sold_at, hide_at))
        db.execute('''DELETE FROM sold_out_deals WHERE deal_id IN (SELECT deals.id FROM deals
            WHERE deals.redemption_count < deals.redemption_limit AND
            (SELECT COALESCE(SUM(quantity),0) FROM codes WHERE codes.deal_id=deals.id) < deals.redemption_limit)''')

    @app.context_processor
    def tag_context():
        db = get_db()
        tags = db.execute('SELECT category_tags.*, categories.name category_name FROM category_tags JOIN categories ON categories.id = category_tags.category_id WHERE categories.is_active = 1 ORDER BY LOWER(category_tags.name)').fetchall()
        def selected_tags(deal_id):
            return {str(row['tag_id']) for row in db.execute('SELECT tag_id FROM deal_tags WHERE deal_id = ?', (deal_id,)).fetchall()}
        def staff_permissions(user_id):
            return {row['permission'] for row in db.execute('SELECT permission FROM admin_permissions WHERE user_id = ?', (user_id,)).fetchall()}
        return dict(category_search_tags=tags, selected_deal_tags=selected_tags, staff_permissions=staff_permissions)

    @app.post('/admin/team/<string:user_id>/permissions')
    @roles_required('admin')
    def update_admin_permissions(user_id):
        user = get_db().execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if not user or not user['created_by_admin']:
            abort(403)
        save_permissions(user_id)
        audit('update_admin_permissions', 'user', user_id, 'Updated assigned permissions')
        flash('Administrator permissions updated.', 'success')
        return redirect(url_for('admin_team'))

    @app.route('/admin/tags', methods=['GET', 'POST'])
    @roles_required('admin')
    def admin_tags():
        db = get_db()
        if request.method == 'POST':
            category_id = request.form.get('category_id', '')
            category = db.execute('SELECT id FROM categories WHERE id = ?', (category_id,)).fetchone()
            name = ' '.join(request.form.get('name', '').split())
            keywords = request.form.get('keywords', '').strip()
            tag_id = request.form.get('tag_id')
            if not category or not 1 <= len(name) <= 100 or len(keywords) > 2000:
                flash('Choose a category and enter a tag of 1–100 characters with up to 2,000 characters of search keywords.', 'danger')
            elif db.execute('SELECT id FROM category_tags WHERE category_id = ? AND LOWER(name) = LOWER(?) AND id <> ?', (category_id, name, tag_id or str(uuid4()))).fetchone():
                flash('That category already has this tag.', 'danger')
            else:
                if tag_id:
                    if not db.execute('SELECT id FROM category_tags WHERE id = ?', (tag_id,)).fetchone():
                        abort(404)
                    # Clear selections if a tag moves to another category.
                    db.execute('DELETE FROM deal_tags WHERE tag_id = ? AND deal_id IN (SELECT id FROM deals WHERE category <> (SELECT name FROM categories WHERE id = ?))', (tag_id, category_id))
                    db.execute('UPDATE category_tags SET category_id=?, name=?, keywords=? WHERE id=?', (category_id, name, keywords, tag_id))
                else:
                    tag_id = str(uuid4())
                    db.execute('INSERT INTO category_tags(id,category_id,name,keywords) VALUES (?,?,?,?)', (tag_id,category_id,name,keywords))
                audit('save_category_tag', 'category', category_id, name)
                flash('Search tag saved.', 'success')
                return redirect(url_for('admin_tags'))
        return render_template('admin_tags.html', categories=get_db().execute('SELECT * FROM categories ORDER BY LOWER(name)').fetchall())

    @app.post('/admin/tags/<string:tag_id>/delete')
    @roles_required('admin')
    def delete_tag(tag_id):
        get_db().execute('DELETE FROM category_tags WHERE id = ?', (tag_id,))
        audit('delete_category_tag', 'tag', tag_id, 'Removed search tag')
        flash('Search tag removed.', 'success')
        return redirect(url_for('admin_tags'))

    return admin_can, PERMISSIONS, save_permissions, save_tags, tag_query_matches
