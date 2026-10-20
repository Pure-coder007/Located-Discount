"""Reusable validation rules for account and payment input."""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
# Nigerian mobile numbers: 0803..., 234803... or +234803... (business sign-ups only).
NG_PHONE_RE = re.compile(r"^(?:\+?234|0)[789][01][0-9]{8}$")
CODE_RE = re.compile(r"LD-[A-F0-9]{8}")
REFERENCE_RE = re.compile(r"[A-Z0-9-]{6,50}")


def valid_password(password):
    return len(password) >= 12 and bool(re.search(r"[A-Za-z]", password)) and bool(re.search(r"\d", password))
