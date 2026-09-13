"""One-time, data-preserving PostgreSQL primary-key migration to UUIDs.

Run from the repository root after stopping every app process:
    python3 migrate_postgres_ids_to_uuid.py

The migration requires the existing `uuid_migration_backup` schema, created
beforehand as a data-preservation snapshot. PostgreSQL DDL is transactional,
so any error rolls back the complete conversion.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ.get("DATABASE_URL")

PRIMARY_KEY_TABLES = (
    "users", "businesses", "consumer_devices", "deals", "codes",
    "deal_images", "products", "product_images", "wallet_transactions",
    "ledger_entries", "admin_audit_logs", "categories",
    "password_reset_tokens",
)

# (child table, child FK column, parent table). Every old integer reference is
# first mapped through a temporary UUID column while the old keys still exist.
FOREIGN_KEYS = (
    ("users", "created_by_admin", "users"),
    ("businesses", "owner_id", "users"),
    ("consumer_profiles", "user_id", "users"),
    ("consumer_devices", "user_id", "users"),
    ("deals", "business_id", "businesses"),
    ("deals", "approved_by", "users"),
    ("favorites", "user_id", "users"),
    ("favorites", "deal_id", "deals"),
    ("codes", "deal_id", "deals"),
    ("codes", "user_id", "users"),
    ("codes", "redeemed_by", "users"),
    ("codes", "device_id", "consumer_devices"),
    ("deal_images", "deal_id", "deals"),
    ("products", "business_id", "businesses"),
    ("product_images", "product_id", "products"),
    ("wallet_transactions", "business_id", "businesses"),
    ("wallet_transactions", "approved_by", "users"),
    ("ledger_entries", "business_id", "businesses"),
    ("ledger_entries", "deal_id", "deals"),
    ("ledger_entries", "code_id", "codes"),
    ("platform_settings", "updated_by", "users"),
    ("admin_audit_logs", "admin_id", "users"),
    ("categories", "created_by", "users"),
    ("password_reset_tokens", "user_id", "users"),
)


def q(identifier: str) -> str:
    """Quote a known, internal SQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def execute(cur, sql: str, params=None):
    cur.execute(sql, params) if params else cur.execute(sql)


def execute_batch(cur, statements: list[str]) -> None:
    """Send one conversion phase in a single database request."""
    cur.execute(";\n".join(statement.rstrip(";") for statement in statements))
    while cur.nextset():
        pass


def main() -> None:
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not configured in .env")

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cur:
            execute(cur, "SELECT to_regnamespace('uuid_migration_backup')")
            if cur.fetchone()[0] is None:
                raise SystemExit(
                    "Refusing to migrate: uuid_migration_backup is missing. "
                    "Create the preservation snapshot first."
                )

            execute(cur, "SELECT gen_random_uuid()")
            print("UUID support and preservation snapshot verified.", flush=True)

            # Make UUID counterparts for old primary keys and map every FK to
            # the corresponding new value while the old keys still exist.
            primary_mapping: list[str] = []
            for table in PRIMARY_KEY_TABLES:
                primary_mapping.extend((
                    f"ALTER TABLE {q(table)} ADD COLUMN __uuid_id UUID",
                    f"UPDATE {q(table)} SET __uuid_id = gen_random_uuid() WHERE __uuid_id IS NULL",
                    f"ALTER TABLE {q(table)} ALTER COLUMN __uuid_id SET NOT NULL",
                ))
            execute_batch(cur, primary_mapping)
            print("Primary-key UUID mappings prepared.", flush=True)

            foreign_mapping: list[str] = []
            for child, column, parent in FOREIGN_KEYS:
                temporary = f"__uuid_{column}"
                foreign_mapping.extend((
                    f"ALTER TABLE {q(child)} ADD COLUMN {q(temporary)} UUID",
                    f"UPDATE {q(child)} AS child SET {q(temporary)} = parent.__uuid_id "
                    f"FROM {q(parent)} AS parent WHERE child.{q(column)} = parent.id",
                    f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {q(child)} "
                    f"WHERE {q(column)} IS NOT NULL AND {q(temporary)} IS NULL) "
                    f"THEN RAISE EXCEPTION 'Unable to map a legacy reference to UUID'; END IF; END $$",
                ))
            execute_batch(cur, foreign_mapping)
            print("Foreign-key UUID mappings prepared.", flush=True)

            # Existing FKs prevent changing either side's type. Rebuild them
            # after conversion, preserving the schema's original cascade rules.
            execute(cur, """DO $$ DECLARE item RECORD; BEGIN
                FOR item IN SELECT conrelid::regclass AS table_name, conname
                    FROM pg_constraint
                    WHERE contype = 'f' AND connamespace = 'public'::regnamespace
                LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', item.table_name, item.conname);
                END LOOP;
            END $$""")
            print("Legacy foreign-key constraints detached.", flush=True)

            primary_conversion = [
                "ALTER TABLE admin_audit_logs ALTER COLUMN target_id TYPE TEXT USING target_id::text"
            ]
            for table in PRIMARY_KEY_TABLES:
                primary_conversion.extend((
                    f"ALTER TABLE {q(table)} ALTER COLUMN id DROP IDENTITY IF EXISTS",
                    f"ALTER TABLE {q(table)} ALTER COLUMN id TYPE UUID USING __uuid_id",
                    f"ALTER TABLE {q(table)} DROP COLUMN __uuid_id",
                    f"ALTER TABLE {q(table)} ALTER COLUMN id SET DEFAULT gen_random_uuid()",
                ))
            execute_batch(cur, primary_conversion)
            print("Primary keys converted to UUID.", flush=True)

            foreign_conversion: list[str] = []
            for child, column, _parent in FOREIGN_KEYS:
                temporary = f"__uuid_{column}"
                foreign_conversion.extend((
                    f"ALTER TABLE {q(child)} ALTER COLUMN {q(column)} TYPE UUID USING {q(temporary)}",
                    f"ALTER TABLE {q(child)} DROP COLUMN {q(temporary)}",
                ))
            execute_batch(cur, foreign_conversion)
            print("Foreign-key columns converted to UUID.", flush=True)

            rebuilt_foreign_keys: list[str] = []
            for child, column, parent in FOREIGN_KEYS:
                constraint = f"{child}_{column}_fkey"
                on_delete = " ON DELETE CASCADE" if (child, column) in {
                    ("deal_images", "deal_id"),
                    ("product_images", "product_id"),
                    ("password_reset_tokens", "user_id"),
                } else ""
                rebuilt_foreign_keys.append(
                    f"ALTER TABLE {q(child)} ADD CONSTRAINT {q(constraint)} "
                    f"FOREIGN KEY ({q(column)}) REFERENCES {q(parent)}(id){on_delete}"
                )
            execute_batch(cur, rebuilt_foreign_keys)

        connection.commit()
    print("UUID migration completed successfully. Existing data was preserved in uuid_migration_backup.", flush=True)


if __name__ == "__main__":
    main()
