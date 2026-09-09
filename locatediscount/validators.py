"""Reusable validation rules for account and payment input."""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
CODE_RE = re.compile(r"LD-[A-F0-9]{8}")
REFERENCE_RE = re.compile(r"[A-Z0-9-]{6,50}")


def valid_password(password):
    return len(password) >= 12 and bool(re.search(r"[A-Za-z]", password)) and bool(re.search(r"\d", password))
