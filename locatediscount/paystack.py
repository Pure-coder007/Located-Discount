"""Small server-side Paystack client for wallet funding."""

from __future__ import annotations

import json
from urllib.parse import quote

import requests


class PaystackError(RuntimeError):
    """Raised when Paystack cannot initialize or verify a payment."""


def _request(secret_key, url, *, payload=None, timeout=12):
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
        "User-Agent": "Locatediscount/1.0",
    }
    try:
        response = requests.request(
            "POST" if payload is not None else "GET",
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as error:
        raise PaystackError("Paystack could not be reached. Please try again shortly.") from error
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise PaystackError("Paystack returned an unreadable response. Please try again shortly.") from error
    if not isinstance(result, dict):
        raise PaystackError("Paystack returned an unreadable response. Please try again shortly.")
    if not response.ok:
        message = result.get("message") if isinstance(result, dict) else None
        raise PaystackError(message or "Paystack rejected the payment request.")
    if not result.get("status") or not isinstance(result.get("data"), dict):
        raise PaystackError(result.get("message") or "Paystack rejected the payment request.")
    return result["data"]


def initialize_transaction(secret_key, api_base, *, email, amount_kobo, reference, callback_url, business_id):
    return _request(
        secret_key,
        f"{api_base.rstrip('/')}/transaction/initialize",
        payload={
            "email": email,
            "amount": str(amount_kobo),
            "currency": "NGN",
            "reference": reference,
            "callback_url": callback_url,
            "channels": ["card", "bank", "ussd", "bank_transfer"],
            "metadata": json.dumps({"purpose": "wallet_topup", "business_id": business_id}),
        },
    )


def verify_transaction(secret_key, api_base, reference):
    return _request(secret_key, f"{api_base.rstrip('/')}/transaction/verify/{quote(reference, safe='')}")
