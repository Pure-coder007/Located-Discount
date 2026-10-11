# Locatediscount email delivery

There are **13 email types**, sent at the events below. All use the same white, navy and red design with solid colours, inline email-compatible styles, no gradients, and an equivalent plain-text message.

| Email | Trigger | Recipient |
|---|---|---|
| Email verification | Business registration; signed-in user requests another verification link | Business owner; admin when requested |
| Password recovery | Active business/admin requests a password reset | Account holder |
| Password changed | Successful password reset or signed-in password change | Account holder |
| Admin invitation | Primary admin creates a sub-admin account | New sub-admin; secure password setup link, never their password |
| Low wallet | Redemption reduces wallet below the owner's chosen alert level | Business owner; once per low-balance period |
| Wallet credit receipt | Verified Paystack credit or approval of a historical manual transfer | Business owner; duplicate callbacks do not create a second receipt |
| Business status | Admin approves, blocks or restores a business | Business owner |
| Deal submitted | Vendor creates, edits/resubmits or renews a deal for review | Business owner |
| Deal approved | Admin approves a deal or publishes a renewal | Business owner |
| Deal review required | Admin rejects a deal or requests corrections | Business owner; includes the review reason |
| Deal archived | Vendor or admin archives a deal | Business owner |
| Deal removed | Vendor removes/deletes a deal | Business owner |
| Deal fee updated | Admin changes the redemption fee for a deal | Business owner |

Customers currently claim with a phone number and no email address, so vouchers and ratings do not send customer email. Chat remains in-app. There are no scheduled promotional or expiry email campaigns. Business registration verification is separate from business approval, and does not prevent access to the pending vendor dashboard. Existing accounts are not locked out; they can verify through **Email verification** in the sidebar.

Low-wallet alert status is recorded only after the provider accepts the message. Funding resets the alert period. There is no automatic background retry queue: failed emails are logged safely, recovery/verification can be requested again, and routine event emails require the event to be retriggered. A provider acceptance means queued by Resend, not proof of inbox delivery.

## Activate Resend later

Set these environment variables (see [.env.example](../.env.example)):

```
RESEND_API_KEY=re_your_key
MAIL_FROM=Locatediscount <hello@your-verified-domain.com>
MAIL_REPLY_TO=support@your-domain.com
PUBLIC_BASE_URL=https://your-domain.com
```

Verify the sending domain in Resend first, including its required DNS records. `PUBLIC_BASE_URL` supplies trusted account links; use the actual HTTPS website, without a trailing path. `MAIL_REPLY_TO` is the existing support inbox that should receive replies. Keys stay server-side. Sending uses Resend's HTTPS API, not the old SMTP configuration. No external emails are sent while keys are absent or in automated tests.

This implements outgoing transactional mail. Replies go to the configured support inbox; inbound mail is not ingested into the app or vendor/admin chat. Resend inbound webhooks are not configured.

Verification links expire in 24 hours and can only be used once. Resending invalidates the prior link and has a one-minute cooldown. Password reset links retain the existing 30-minute expiry and single-use behaviour. Emails contain no passwords or API keys. Resend idempotency headers are supplied for payment receipts and other identifiable events.

## Preview

Open [the standalone gallery](../previews/emails/index.html) to switch between all email designs and desktop/mobile widths. It uses sample data and never sends mail. The primary admin can also open **Email previews** from the dashboard sidebar (`/admin/email-previews`). These previews render the same template used in delivery.

To regenerate the standalone files (the script creates its own temporary SQLite database):

```
.venv/bin/python scripts/export_email_previews.py
```

API reference: https://resend.com/docs/api-reference/emails/send-email
