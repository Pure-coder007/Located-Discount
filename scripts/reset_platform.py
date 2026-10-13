"""Reset the Postgres database to a fresh platform, keeping admin accounts.

Removes every deal, voucher, business, product, wallet record, customer,
chat and log. Keeps admin users (and their permissions), categories,
category tags and platform settings.

Runs in a single transaction. Without --yes it only reports what would be
deleted and rolls back. Take a backup first.

    python scripts/reset_platform.py          # dry run
    python scripts/reset_platform.py --yes    # apply
"""
import os
import sys

import psycopg

KEEP = {"users", "admin_permissions", "categories", "category_tags", "platform_settings"}


def count(conn, table):
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def main():
    apply = "--yes" in sys.argv[1:]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")]
        wipe = [t for t in tables if t not in KEEP]

        print("Before:")
        for t in tables:
            print(f"  {t:28} {count(conn, t):>7}{'   (kept)' if t in KEEP else ''}")
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        others = conn.execute("SELECT COUNT(*) FROM users WHERE role <> 'admin'").fetchone()[0]
        print(f"  users: {admins} admin (kept), {others} non-admin (deleted)")
        if not admins:
            sys.exit("Aborting: no admin account exists, refusing to reset.")

        # No CASCADE: if a kept table still referenced a wiped one, this fails
        # and the whole transaction rolls back instead of deleting kept data.
        conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in wipe) + " RESTART IDENTITY")
        conn.execute("DELETE FROM users WHERE role <> 'admin'")

        after_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if after_admins != admins:
            conn.rollback()
            sys.exit("Aborting: admin count changed; rolled back.")

        print("\nAfter:")
        for t in tables:
            print(f"  {t:28} {count(conn, t):>7}")

        if apply:
            conn.commit()
            print(f"\nCommitted. {after_admins} admin account(s) kept.")
        else:
            conn.rollback()
            print("\nDry run only - rolled back. Re-run with --yes to apply.")


if __name__ == "__main__":
    main()
