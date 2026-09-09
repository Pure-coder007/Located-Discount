"""Wallet and redemption operations kept separate from HTTP route handling."""

from __future__ import annotations

import secrets


def redeem_code(db, business, code_value, redeemed_by, now, fee):
    """Redeem one code and record every financial side effect atomically.

    The caller must convert ``ValueError`` into a safe user-facing response.
    """
    db.execute("BEGIN IMMEDIATE")
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business["id"],)).fetchone()
    if not business or not business["is_approved"] or business["is_blocked"]:
        raise ValueError("This business is not approved to validate customer codes.")
    if business["needs_top_up"]:
        raise ValueError("Your wallet needs a top-up before another code can be redeemed.")

    code = db.execute(
        """SELECT codes.*, deals.business_id, deals.is_active, deals.expires_at deal_expiry,
                  deals.id deal_id
           FROM codes JOIN deals ON deals.id = codes.deal_id WHERE codes.value = ?""",
        (code_value,),
    ).fetchone()
    if not code or code["business_id"] != business["id"]:
        raise ValueError("Code not found for this business.")
    if (code["status"] != "active" or code["expires_at"] <= now or
            code["deal_expiry"] <= now or not code["is_active"]):
        raise ValueError("This code is expired or already redeemed.")

    balance_after = business["wallet_balance"] - fee
    update = db.execute(
        """UPDATE codes SET status = 'redeemed', redeemed_at = ?, redeemed_by = ?
           WHERE id = ? AND status = 'active'""",
        (now, redeemed_by, code["id"]),
    )
    if update.rowcount != 1:
        raise ValueError("This code was just redeemed by another staff member.")

    db.execute(
        "UPDATE businesses SET wallet_balance = ?, needs_top_up = ? WHERE id = ?",
        (balance_after, int(balance_after < business["low_balance_threshold"]), business["id"]),
    )
    db.execute(
        """UPDATE deals SET redemption_count = redemption_count + 1,
           is_active = CASE WHEN redemption_count + 1 >= redemption_limit THEN 0 ELSE is_active END
           WHERE id = ?""",
        (code["deal_id"],),
    )
    reference = "RED-" + secrets.token_hex(8).upper()
    db.execute(
        """INSERT INTO wallet_transactions
           (business_id, amount, kind, status, reference, note, created_at)
           VALUES (?, ?, 'redemption', 'posted', ?, ?, ?)""",
        (business["id"], -fee, reference, code_value, now),
    )
    db.execute(
        """INSERT INTO ledger_entries
           (business_id, deal_id, code_id, fee_charged, wallet_balance_after, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (business["id"], code["deal_id"], code["id"], fee, balance_after, now),
    )
    db.commit()
    return balance_after
