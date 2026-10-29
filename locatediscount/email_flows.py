"""Verification links and administrator-only previews of real email templates."""
import hashlib
import secrets
from datetime import timedelta
from flask import abort, flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash
from .database import INTEGRITY_ERRORS
from .mailer import EMAIL_EVENTS, render_email, send_email
from .validators import EMAIL_RE, email_typo_suggestion


def register_email_flows(app, get_db, current_user, roles_required, timestamp, utcnow, admin_can):
    key_type = 'UUID' if app.config['DATABASE_URL'] else 'TEXT'
    with app.app_context():
        get_db().execute(f'''CREATE TABLE IF NOT EXISTS email_verifications (
          user_id {key_type} PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
          requested_at TEXT NOT NULL, verified_at TEXT)''')

    def public_url(endpoint, **kwargs):
        path = url_for(endpoint, **kwargs)
        base = app.config.get('PUBLIC_BASE_URL', '').rstrip('/')
        return base + path if base else url_for(endpoint, _external=True, **kwargs)

    def issue_verification(user):
        token = secrets.token_urlsafe(32)
        get_db().execute('''INSERT INTO email_verifications(user_id,token_hash,expires_at,requested_at)
          VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash,
          expires_at=excluded.expires_at,requested_at=excluded.requested_at''',
          (user['id'], hashlib.sha256(token.encode()).hexdigest(), timestamp(utcnow()+timedelta(hours=24)), timestamp()))
        return send_email('verification', user['email'], name=user['name'],
            message='Please confirm this email address for your Locatediscount account. Business approval is reviewed separately by our team.',
            action_url=public_url('verify_email', token=token), event_id=token)

    @app.route('/account/verify-email', methods=['GET','POST'])
    @roles_required('business','admin')
    def email_verification():
        user = current_user()
        state = get_db().execute('SELECT * FROM email_verifications WHERE user_id=?', (user['id'],)).fetchone()
        if request.method == 'POST' and not (state and state['verified_at']):
            if state and state['requested_at'] > timestamp(utcnow()-timedelta(minutes=1)):
                flash('Please wait one minute before requesting another verification email.', 'info')
            elif issue_verification(user):
                flash('Your verification email has been sent.', 'success')
            else:
                flash('Email delivery is not available yet. Please try again later.', 'warning')
            return redirect(url_for('email_verification'))
        return render_template('email_verification.html', verification=state)

    @app.post('/account/verify-email/change-address')
    @roles_required('business','admin')
    def change_unverified_email():
        """Correct a mistyped address so the verification email can arrive.

        Only unverified addresses can be changed here; the current password is
        required so an unattended session cannot redirect the account's email.
        """
        user = current_user()
        db = get_db()
        state = db.execute('SELECT * FROM email_verifications WHERE user_id=?', (user['id'],)).fetchone()
        if state and state['verified_at']:
            abort(403)
        email = request.form.get('email', '').strip().lower()
        if not check_password_hash(user['password_hash'], request.form.get('password', '')):
            flash('Your current password is incorrect.', 'danger')
        elif not EMAIL_RE.match(email):
            flash('Enter a valid email address.', 'danger')
        elif email_typo_suggestion(email):
            flash(f'Please check your email address. Did you mean {email_typo_suggestion(email)}?', 'danger')
        elif email == user['email'].lower():
            flash('That is already your email address.', 'info')
        elif state and state['requested_at'] > timestamp(utcnow()-timedelta(minutes=1)):
            flash('Please wait one minute before requesting another verification email.', 'info')
        else:
            try:
                db.execute('UPDATE users SET email=? WHERE id=?', (email, user['id']))
            except INTEGRITY_ERRORS:
                flash('That email address is already registered.', 'danger')
            else:
                if issue_verification(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()):
                    flash(f'Your email address was updated and a verification link was sent to {email}.', 'success')
                else:
                    flash('Your email address was updated, but the verification email could not be sent. Please try again shortly.', 'warning')
        return redirect(url_for('email_verification'))

    @app.get('/verify-email/<token>')
    def verify_email(token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        result = get_db().execute('''UPDATE email_verifications SET verified_at=?
           WHERE token_hash=? AND verified_at IS NULL AND expires_at>?''', (timestamp(), digest, timestamp()))
        flash('Your email address is verified.' if result.rowcount else 'That verification link is invalid, expired, or already used.', 'success' if result.rowcount else 'warning')
        return redirect(url_for('email_verification') if current_user() else url_for('login'))

    @app.context_processor
    def verification_context():
        user = current_user()
        state = get_db().execute('SELECT verified_at FROM email_verifications WHERE user_id=?', (user['id'],)).fetchone() if user else None
        return {'email_verification_pending': bool(state and not state['verified_at'])}

    def samples():
        data = {}
        for event in EMAIL_EVENTS:
            message = {
                'verification': 'Please confirm your email address to keep your Locatediscount account secure.',
                'password_reset': 'We received a request to reset your password. Choose a new password using the secure link below.',
                'password_changed': 'The password for your Locatediscount account has been updated. You can sign in with your new password.',
                'admin_invitation': 'Your administrator account is ready. Set your own password to access the tools assigned to you.',
                'low_wallet': 'Your Palm Kitchen wallet balance is below your chosen alert level. Deals pause if the balance cannot cover their redemption fee.',
                'wallet_topup': 'Your payment has been verified and added to your Palm Kitchen wallet. Thank you.',
                'business_status': 'Palm Kitchen has been approved. You can now submit deals and fund your wallet.',
                'deal_submitted': 'Your Weekday lunch offer has been submitted. Our team will review it before it appears on the platform.',
                'deal_approved': 'Your Weekday lunch offer has been approved. It can appear on the platform while it is active, unexpired, and funded.',
                'deal_review': 'Your Weekday lunch offer needs a small update. Please clarify which items are included in the offer before resubmitting.',
                'deal_archived': 'Your Weekday lunch offer has been archived. Its voucher and financial history remain available.',
                'deal_removed': 'Your Weekday lunch offer has been removed from the catalogue.',
                'deal_fee': 'The redemption fee for your Weekday lunch offer has been updated. Check your deal settings for the current fee.',
            }[event]
            details = [('Business','Palm Kitchen'),('Amount credited','₦10,000'),('Reference','PSTK-DEMO-0001')] if event=='wallet_topup' else [('Current balance','₦1,500'),('Alert level','₦2,000')] if event=='low_wallet' else []
            data[event] = render_email(event, name='Ada', message=message, details=details, action_url='https://example.com/email-preview')
        return data

    @app.get('/admin/email-previews')
    @roles_required('admin')
    def email_previews():
        if not admin_can('team'): abort(403)
        return render_template('email_previews.html', previews=samples())

    @app.get('/admin/email-previews/<event>')
    @roles_required('admin')
    def email_preview(event):
        if not admin_can('team'): abort(403)
        if event not in EMAIL_EVENTS: abort(404)
        return samples()[event]['html']

    return issue_verification, public_url, samples
