"""Wallet funding reports: totals, filters, daily trend, CSV and balance checks.

Money received means confirmed top-ups only (Paystack or manual, status
'approved'). Every stored time is UTC ISO text; day and month boundaries
follow Lagos time (UTC+1), matching the rest of the platform.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

from flask import Response, abort, render_template, request

LAGOS = timezone(timedelta(hours=1))
PER_PAGE = 25
STATUSES = {"approved": "Confirmed", "pending": "Not yet confirmed", "rejected": "Failed", "all": "All statuses"}
METHODS = {"all": "All methods", "paystack": "Paystack", "manual": "Manual"}
RANGES = {
    "today": "Today", "yesterday": "Yesterday", "7d": "Last 7 days", "30d": "Last 30 days",
    "month": "This month", "last_month": "Last month", "all": "All time", "custom": "Custom dates",
}
# Pending Paystack checkouts older than this were almost certainly abandoned.
STALE_PENDING = timedelta(hours=1)
EVENT_TIME = "COALESCE(wallet_transactions.approved_at, wallet_transactions.created_at)"
PAYSTACK = "wallet_transactions.reference LIKE 'PSTK-%'"


def lagos_midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=LAGOS)


def utc_text(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def month_start(day):
    return day.replace(day=1)


def period_bounds(today):
    """Named reporting periods as [start, end) Lagos dates; None means open."""
    first = month_start(today)
    previous_first = month_start(first - timedelta(days=1))
    return {
        "today": (today, today + timedelta(days=1)),
        "yesterday": (today - timedelta(days=1), today),
        "7d": (today - timedelta(days=6), today + timedelta(days=1)),
        "30d": (today - timedelta(days=29), today + timedelta(days=1)),
        "month": (first, today + timedelta(days=1)),
        "last_month": (previous_first, first),
        "all": (None, None),
    }


def parse_day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def read_filters(args, today):
    """Validate report filters; anything unrecognised falls back to a default."""
    status = args.get("status", "approved")
    method = args.get("method", "all")
    chosen = args.get("range", "month")
    start, end = parse_day(args.get("start", "")), parse_day(args.get("end", ""))
    if start or end:
        chosen = "custom"
        if start and end and end < start:
            start, end = end, start
        # Custom end dates are inclusive for the person choosing them.
        bounds = (start, end + timedelta(days=1) if end else None)
    else:
        chosen = chosen if chosen in RANGES and chosen != "custom" else "month"
        bounds = period_bounds(today)[chosen]
    return {
        "status": status if status in STATUSES else "approved",
        "method": method if method in METHODS else "all",
        "range": chosen,
        "start": bounds[0],
        "end": bounds[1],
        # What the date inputs show: the inclusive custom range only.
        "start_input": start.isoformat() if chosen == "custom" and start else "",
        "end_input": end.isoformat() if chosen == "custom" and end else "",
        "business": args.get("business", "").strip()[:64],
        "q": " ".join(args.get("q", "").split())[:100],
    }


def filter_sql(filters):
    where, params = ["wallet_transactions.kind = 'topup'"], []
    if filters["status"] != "all":
        where.append("wallet_transactions.status = ?")
        params.append(filters["status"])
    if filters["method"] == "paystack":
        where.append(PAYSTACK)
    elif filters["method"] == "manual":
        where.append(f"NOT ({PAYSTACK})")
    if filters["start"]:
        where.append(f"{EVENT_TIME} >= ?")
        params.append(utc_text(lagos_midnight(filters["start"])))
    if filters["end"]:
        where.append(f"{EVENT_TIME} < ?")
        params.append(utc_text(lagos_midnight(filters["end"])))
    if filters["business"]:
        where.append("CAST(wallet_transactions.business_id AS TEXT) = ?")
        params.append(filters["business"])
    if filters["q"]:
        where.append("(LOWER(businesses.name) LIKE ? OR LOWER(wallet_transactions.reference) LIKE ?)")
        params.extend([f"%{filters['q'].lower()}%"] * 2)
    return " AND ".join(where), params


def funding_totals(db, now=None):
    """Confirmed funding for the standard periods plus the pending queue."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(LAGOS).date()
    columns, params = [], []
    for key, (start, end) in period_bounds(today).items():
        condition, bounds = ["status = 'approved'"], []
        if start:
            condition.append("approved_at >= ?")
            bounds.append(utc_text(lagos_midnight(start)))
        if end:
            condition.append("approved_at < ?")
            bounds.append(utc_text(lagos_midnight(end)))
        test = " AND ".join(condition)
        params.extend(bounds * 2)  # once for the total, once for the count
        # Quoted aliases: period keys such as "7d" cannot start a bare identifier.
        columns.append(f'COALESCE(SUM(CASE WHEN {test} THEN amount ELSE 0 END), 0) AS "{key}_total"')
        columns.append(f'COALESCE(SUM(CASE WHEN {test} THEN 1 ELSE 0 END), 0) AS "{key}_count"')
    stale = utc_text(now - STALE_PENDING)
    row = db.execute(
        f"""SELECT {", ".join(columns)},
                   COALESCE(SUM(CASE WHEN status = 'pending' THEN amount ELSE 0 END), 0) AS pending_total,
                   COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                   COALESCE(SUM(CASE WHEN status = 'pending' AND created_at < ? THEN 1 ELSE 0 END), 0) AS stale_count,
                   COUNT(DISTINCT CASE WHEN status = 'approved' THEN CAST(business_id AS TEXT) END) AS paying_businesses
            FROM wallet_transactions WHERE kind = 'topup'""",
        (*params, stale),
    ).fetchone()
    totals = {key: row[key] for key in row.keys()}
    totals["average"] = round(totals["all_total"] / totals["all_count"]) if totals["all_count"] else 0
    return totals


def balance_checks(db):
    """Businesses whose wallet differs from the sum of their recorded money movements.

    Confirmed top-ups, redemption fees and adjustments are all logged in
    wallet_transactions, so any difference means a balance changed without a
    matching record (for example a top-up credited twice).
    """
    rows = db.execute(
        """SELECT businesses.id, businesses.name, businesses.wallet_balance,
                  COALESCE(SUM(CASE WHEN wallet_transactions.kind = 'topup' AND wallet_transactions.status = 'approved' THEN wallet_transactions.amount
                                    WHEN wallet_transactions.kind IN ('redemption', 'adjustment') THEN wallet_transactions.amount
                                    ELSE 0 END), 0) AS expected
           FROM businesses LEFT JOIN wallet_transactions ON wallet_transactions.business_id = businesses.id
           GROUP BY businesses.id, businesses.name, businesses.wallet_balance
           ORDER BY LOWER(businesses.name)"""
    ).fetchall()
    return [
        {"id": row["id"], "name": row["name"], "balance": row["wallet_balance"], "expected": row["expected"],
         "difference": row["wallet_balance"] - row["expected"]}
        for row in rows if row["wallet_balance"] != row["expected"]
    ]


def to_lagos(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LAGOS)


def daily_series(db, where, params, filters, today):
    """Confirmed amount per Lagos day across the selected range (max 92 days)."""
    if filters["status"] not in ("approved", "all"):
        return []
    end = min(filters["end"] or today + timedelta(days=1), today + timedelta(days=1))
    start = filters["start"]
    if not start:
        first = db.execute(f"SELECT MIN({EVENT_TIME}) AS first FROM wallet_transactions JOIN businesses ON businesses.id = wallet_transactions.business_id WHERE {where}", params).fetchone()["first"]
        first_moment = to_lagos(first)
        start = first_moment.date() if first_moment else end - timedelta(days=30)
    start = max(start, end - timedelta(days=92))
    if start >= end:
        return []
    totals = {start + timedelta(days=offset): 0 for offset in range((end - start).days)}
    rows = db.execute(
        f"""SELECT wallet_transactions.approved_at, wallet_transactions.amount
            FROM wallet_transactions JOIN businesses ON businesses.id = wallet_transactions.business_id
            WHERE {where} AND wallet_transactions.status = 'approved'""",
        params,
    ).fetchall()
    for row in rows:
        moment = to_lagos(row["approved_at"])
        if moment and moment.date() in totals:
            totals[moment.date()] += row["amount"]
    peak = max(totals.values(), default=0)
    # Bar heights are 5% steps (CSS classes), since the CSP blocks inline styles.
    return [{"day": day, "total": total, "level": max(1, round(total / peak * 20)) if total else 0} for day, total in totals.items()]


def csv_cell(value):
    """Stop spreadsheet apps treating vendor-supplied text as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def register_funding(app, get_db, roles_required, admin_can):
    def report_rows(filters, limit=None, offset=0):
        where, params = filter_sql(filters)
        sql = f"""SELECT wallet_transactions.*, businesses.name AS business_name, users.name AS approver_name,
                         {EVENT_TIME} AS event_at,
                         CASE WHEN {PAYSTACK} THEN 'Paystack' ELSE 'Manual' END AS method
                  FROM wallet_transactions
                  JOIN businesses ON businesses.id = wallet_transactions.business_id
                  LEFT JOIN users ON users.id = wallet_transactions.approved_by
                  WHERE {where} ORDER BY {EVENT_TIME} DESC, wallet_transactions.reference"""
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, limit, offset]
        return get_db().execute(sql, params).fetchall()

    def require_finance():
        if not admin_can("finance"):
            abort(403)

    @app.get("/admin/topups/report")
    @roles_required("admin")
    def admin_funding_report():
        require_finance()
        db = get_db()
        today = datetime.now(LAGOS).date()
        filters = read_filters(request.args, today)
        where, params = filter_sql(filters)
        joined = "FROM wallet_transactions JOIN businesses ON businesses.id = wallet_transactions.business_id"
        summary = db.execute(
            f"""SELECT COUNT(*) AS count, COALESCE(SUM(wallet_transactions.amount), 0) AS total,
                       COALESCE(SUM(CASE WHEN {PAYSTACK} THEN wallet_transactions.amount ELSE 0 END), 0) AS paystack,
                       COALESCE(SUM(CASE WHEN {PAYSTACK} THEN 0 ELSE wallet_transactions.amount END), 0) AS manual,
                       COALESCE(MAX(wallet_transactions.amount), 0) AS largest,
                       COUNT(DISTINCT CAST(wallet_transactions.business_id AS TEXT)) AS businesses
                {joined} WHERE {where}""",
            params,
        ).fetchone()
        top_businesses = db.execute(
            f"""SELECT businesses.id, businesses.name, COUNT(*) AS count, COALESCE(SUM(wallet_transactions.amount), 0) AS total,
                       MAX({EVENT_TIME}) AS last_at
                {joined} WHERE {where}
                GROUP BY businesses.id, businesses.name ORDER BY total DESC, LOWER(businesses.name) LIMIT 10""",
            params,
        ).fetchall()
        pages = max(1, (summary["count"] + PER_PAGE - 1) // PER_PAGE)
        page = min(max(request.args.get("page", 1, type=int) or 1, 1), pages)
        businesses = db.execute("SELECT id, name FROM businesses ORDER BY LOWER(name)").fetchall()
        return render_template(
            "admin_funding_report.html",
            filters=filters, statuses=STATUSES, methods=METHODS, ranges=RANGES,
            totals=funding_totals(db), summary=summary, top_businesses=top_businesses,
            rows=report_rows(filters, PER_PAGE, (page - 1) * PER_PAGE), page=page, pages=pages,
            chart=daily_series(db, where, params, filters, today), checks=balance_checks(db),
            businesses=businesses, to_lagos=to_lagos, today=today,
            export_args={key: value for key, value in request.args.items() if key != "page"},
        )

    @app.get("/admin/topups/report.csv")
    @roles_required("admin")
    def admin_funding_report_csv():
        require_finance()
        filters = read_filters(request.args, datetime.now(LAGOS).date())
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Date (Lagos)", "Business", "Reference", "Method", "Amount (NGN)", "Status", "Confirmed by", "Note"])
        for row in report_rows(filters):
            moment = to_lagos(row["event_at"])
            writer.writerow([
                moment.strftime("%Y-%m-%d %H:%M") if moment else "", csv_cell(row["business_name"]), row["reference"],
                row["method"], row["amount"], STATUSES.get(row["status"], row["status"]),
                csv_cell(row["approver_name"] or ("Paystack" if row["method"] == "Paystack" and row["status"] == "approved" else "")),
                csv_cell(row["note"]),
            ])
        label = filters["range"] if filters["range"] != "custom" else f"{filters['start_input'] or 'start'}_{filters['end_input'] or 'now'}"
        return Response(
            "﻿" + buffer.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="wallet-funding-{label}.csv"'},
        )

    return funding_totals
