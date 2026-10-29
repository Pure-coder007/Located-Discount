"""Reusable validation rules for account and payment input."""

import difflib
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
# Nigerian mobile numbers: 0803..., 234803... or +234803... (business sign-ups only).
NG_PHONE_RE = re.compile(r"^(?:\+?234|0)[789][01][0-9]{8}$")
CODE_RE = re.compile(r"LD-[A-F0-9]{8}")
REFERENCE_RE = re.compile(r"[A-Z0-9-]{6,50}")
# Real mailbox providers. A domain one or two letters away from one of these
# (gmail.con, gmial.com, yahoo.co) is almost always a typo that would bounce.
KNOWN_EMAIL_DOMAINS = (
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "yahoo.co.uk",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "icloud.com",
    "me.com", "aol.com", "mail.com", "email.com", "gmx.com", "proton.me",
    "protonmail.com", "zoho.com",
)


def valid_password(password):
    return len(password) >= 12 and bool(re.search(r"[A-Za-z]", password)) and bool(re.search(r"\d", password))


def email_typo_suggestion(email):
    """Return the likely intended address when the domain looks mistyped."""
    local, _, domain = email.rpartition("@")
    if not local or domain in KNOWN_EMAIL_DOMAINS:
        return None
    match = difflib.get_close_matches(domain, KNOWN_EMAIL_DOMAINS, n=1, cutoff=0.85)
    return f"{local}@{match[0]}" if match else None
