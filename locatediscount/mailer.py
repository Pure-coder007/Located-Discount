"""Shared transactional email design and Resend delivery. Never sends in tests."""
import hashlib
import requests
from flask import current_app, render_template

EMAIL_EVENTS = {
    'verification': ('Verify your email address', 'Confirm your email', 'Verify email'),
    'password_reset': ('Reset your Locatediscount password', 'A fresh start, securely.', 'Reset password'),
    'password_changed': ('Your Locatediscount password was changed', 'Your password has changed.', 'Review your account'),
    'admin_invitation': ('Your Locatediscount admin account is ready', 'Welcome to the team.', 'Set your password'),
    'low_wallet': ('Your Locatediscount wallet is running low', 'Keep your offers available.', 'Fund your wallet'),
    'wallet_topup': ('Your Locatediscount wallet has been credited', 'Payment confirmed.', 'View your wallet'),
    'business_status': ('Your business account has an update', 'An update for your business.', 'View your dashboard'),
    'deal_submitted': ('Your deal is awaiting review', 'Your offer is on its way.', 'Manage your deals'),
    'deal_approved': ('Your deal has been approved', 'Your offer is approved.', 'Manage your deals'),
    'deal_review': ('Your deal needs attention', 'Let’s get your offer ready.', 'Review your deal'),
    'deal_archived': ('Your deal has been archived', 'Your catalogue is up to date.', 'Manage your deals'),
    'deal_removed': ('Your deal has been removed', 'An update to your catalogue.', 'Manage your deals'),
    'deal_fee': ('Your deal redemption fee has changed', 'Your redemption fee was updated.', 'Manage your deals'),
}


def render_email(event, *, name='there', message='', action_url='', details=None):
    subject, title, button = EMAIL_EVENTS[event]
    data = dict(event=event, subject=subject, title=title, button=button, name=name,
                message=message, action_url=action_url, details=details or [],
                support_email=current_app.config.get('MAIL_REPLY_TO', ''),
                site_url=current_app.config.get('PUBLIC_BASE_URL', '').rstrip('/'))
    return {'subject': subject, 'html': render_template('emails/transactional.html', **data),
            'text': render_template('emails/transactional.txt', **data)}


def send_email(event, recipient, *, event_id=None, **context):
    app = current_app
    payload = dict(render_email(event, **context), to=[recipient], from_=app.config['MAIL_FROM'])
    payload['from'] = payload.pop('from_')
    if app.config.get('MAIL_REPLY_TO'):
        payload['reply_to'] = app.config['MAIL_REPLY_TO']
    if app.testing:
        app.extensions.setdefault('mail_outbox', []).append(dict(payload, event=event))
        return True
    if not app.config.get('RESEND_API_KEY'):
        app.logger.warning('Email delivery disabled: RESEND_API_KEY is not configured (event=%s).', event)
        return False
    if not app.config.get('PUBLIC_BASE_URL') or '.local' in app.config['MAIL_FROM']:
        app.logger.error('Set PUBLIC_BASE_URL and a verified-domain MAIL_FROM before enabling email delivery.')
        return False
    headers = {'Authorization': f"Bearer {app.config['RESEND_API_KEY']}", 'Content-Type': 'application/json'}
    if event_id:
        headers['Idempotency-Key'] = hashlib.sha256(f'{event}:{event_id}'.encode()).hexdigest()
    try:
        response = requests.post('https://api.resend.com/emails', json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        if not response.json().get('id'):
            raise ValueError('Missing delivery ID')
        return True
    except (requests.RequestException, ValueError):
        # Do not log response bodies, recipients, credentials or security URLs.
        app.logger.error('Resend did not accept email (event=%s).', event)
        return False
