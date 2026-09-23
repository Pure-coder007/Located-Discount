"""Small DB-API compatibility layer for SQLite development and Neon Postgres."""

from __future__ import annotations

import sqlite3
import re
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row


INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
DATABASE_ERRORS = (sqlite3.Error, psycopg.Error)


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('consumer','business','admin')),
  name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  created_by_admin UUID REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (LOWER(email));
CREATE TABLE IF NOT EXISTS businesses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL UNIQUE REFERENCES users(id),
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  address TEXT NOT NULL,
  city TEXT NOT NULL,
  wallet_balance INTEGER NOT NULL DEFAULT 0,
  low_balance_threshold INTEGER NOT NULL DEFAULT 2000,
  needs_top_up INTEGER NOT NULL DEFAULT 0,
  is_approved INTEGER NOT NULL DEFAULT 0,
  is_blocked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  redemption_fee INTEGER,
  opening_hours TEXT NOT NULL DEFAULT '',
  low_balance_alerted_at TEXT
);
CREATE TABLE IF NOT EXISTS consumer_profiles (
  user_id UUID PRIMARY KEY REFERENCES users(id),
  area TEXT,
  favorite_categories TEXT NOT NULL DEFAULT '',
  notifications_enabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumer_devices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS deals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES businesses(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  terms TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  redemption_limit INTEGER NOT NULL CHECK(redemption_limit > 0),
  redemption_count INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  regular_price_kobo INTEGER NOT NULL DEFAULT 0,
  discount_price_kobo INTEGER NOT NULL DEFAULT 0,
  daily_voucher_limit INTEGER NOT NULL DEFAULT 5,
  max_vouchers_per_customer INTEGER NOT NULL DEFAULT 1,
  is_approved INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT,
  approved_by UUID REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS favorites (
  user_id UUID NOT NULL REFERENCES users(id),
  deal_id UUID NOT NULL REFERENCES deals(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, deal_id)
);
CREATE TABLE IF NOT EXISTS codes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_id UUID NOT NULL REFERENCES deals(id),
  user_id UUID NOT NULL REFERENCES users(id),
  value TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('active','redeemed','expired')) DEFAULT 'active',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  redeemed_at TEXT,
  redeemed_by UUID REFERENCES users(id),
  device_id UUID REFERENCES consumer_devices(id),
  claimed_area TEXT,
  user_agent TEXT,
  ip_address TEXT,
  quantity INTEGER NOT NULL DEFAULT 1,
  unit_price_kobo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS device_deal_claims (
  deal_id UUID NOT NULL REFERENCES deals(id),
  device_id UUID NOT NULL REFERENCES consumer_devices(id),
  code_id UUID REFERENCES codes(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (deal_id, device_id)
);
CREATE TABLE IF NOT EXISTS deal_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  secure_url TEXT,
  public_id TEXT,
  storage_provider TEXT NOT NULL DEFAULT 'local',
  sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
  created_at TEXT NOT NULL,
  UNIQUE(deal_id, sort_order)
);
CREATE TABLE IF NOT EXISTS products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES businesses(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  price_kobo INTEGER,
  product_url TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  secure_url TEXT,
  public_id TEXT,
  storage_provider TEXT NOT NULL DEFAULT 'local',
  sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
  created_at TEXT NOT NULL,
  UNIQUE(product_id, sort_order)
);
CREATE TABLE IF NOT EXISTS wallet_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES businesses(id),
  amount INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('topup','redemption','adjustment')),
  status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','posted')),
  reference TEXT NOT NULL UNIQUE,
  note TEXT,
  created_at TEXT NOT NULL,
  approved_at TEXT,
  approved_by UUID REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS ledger_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES businesses(id),
  deal_id UUID NOT NULL REFERENCES deals(id),
  code_id UUID NOT NULL UNIQUE REFERENCES codes(id),
  fee_charged INTEGER NOT NULL,
  wallet_balance_after INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_settings (
  setting_key TEXT PRIMARY KEY,
  integer_value INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by UUID REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS admin_audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_id UUID NOT NULL REFERENCES users(id),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  details TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  image_file_name TEXT,
  image_secure_url TEXT,
  image_public_id TEXT,
  image_storage_provider TEXT,
  created_at TEXT NOT NULL,
  created_by UUID REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS categories_name_lower_idx ON categories (LOWER(name));
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
  attempt_key TEXT PRIMARY KEY,
  failures INTEGER NOT NULL DEFAULT 0,
  blocked_until TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_active_idx ON deals(is_active, expires_at);
CREATE INDEX IF NOT EXISTS codes_value_idx ON codes(value);
CREATE INDEX IF NOT EXISTS codes_user_status_idx ON codes(user_id, status, created_at);
CREATE INDEX IF NOT EXISTS codes_device_status_idx ON codes(device_id, status, created_at);
CREATE INDEX IF NOT EXISTS device_deal_claims_code_idx ON device_deal_claims(code_id);
CREATE INDEX IF NOT EXISTS products_business_active_idx ON products(business_id, is_active);
CREATE INDEX IF NOT EXISTS product_images_product_idx ON product_images(product_id, sort_order);
CREATE INDEX IF NOT EXISTS deal_images_deal_idx ON deal_images(deal_id, sort_order);
CREATE INDEX IF NOT EXISTS password_reset_user_idx ON password_reset_tokens(user_id, expires_at);
"""


POSTGRES_MIGRATIONS = """
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS opening_hours TEXT NOT NULL DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS low_balance_alerted_at TEXT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS regular_price_kobo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS discount_price_kobo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS daily_voucher_limit INTEGER NOT NULL DEFAULT 5;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS max_vouchers_per_customer INTEGER NOT NULL DEFAULT 1;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS is_approved INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS approved_at TEXT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id);
ALTER TABLE codes ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1;
ALTER TABLE codes ADD COLUMN IF NOT EXISTS unit_price_kobo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE codes ADD COLUMN IF NOT EXISTS claimed_area TEXT;
ALTER TABLE codes ADD COLUMN IF NOT EXISTS user_agent TEXT;
ALTER TABLE codes ADD COLUMN IF NOT EXISTS ip_address TEXT;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS image_file_name TEXT;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS image_secure_url TEXT;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS image_public_id TEXT;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS image_storage_provider TEXT;
CREATE TABLE IF NOT EXISTS deal_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
  file_name TEXT NOT NULL,
  secure_url TEXT,
  public_id TEXT,
  storage_provider TEXT NOT NULL DEFAULT 'local',
  sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
  created_at TEXT NOT NULL,
  UNIQUE(deal_id, sort_order)
);
CREATE TABLE IF NOT EXISTS device_deal_claims (
  deal_id UUID NOT NULL REFERENCES deals(id),
  device_id UUID NOT NULL REFERENCES consumer_devices(id),
  code_id UUID REFERENCES codes(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (deal_id, device_id)
);
CREATE INDEX IF NOT EXISTS deal_images_deal_idx ON deal_images(deal_id, sort_order);
CREATE INDEX IF NOT EXISTS device_deal_claims_code_idx ON device_deal_claims(code_id);
"""


UUID_ID_TABLES = {
    "users", "businesses", "consumer_devices", "deals", "codes", "deal_images",
    "products", "product_images", "wallet_transactions", "ledger_entries",
    "admin_audit_logs", "categories", "password_reset_tokens",
}


class SQLiteCursor:
    """Cursor adapter that returns the generated UUID as ``lastrowid``."""

    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        return self._lastrowid if self._lastrowid is not None else self._cursor.lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class SQLiteDatabase:
    """SQLite compatibility layer that assigns UUIDs for application records."""

    _insert_re = re.compile(
        r"\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([a-z_]+)\s*\(([^)]*)\)", re.IGNORECASE
    )

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=None):
        match = self._insert_re.match(query)
        table = match.group(1).lower() if match else ""
        columns = {column.strip().strip('"`[]').lower() for column in match.group(2).split(",")} if match else set()
        if table in UUID_ID_TABLES and "id" not in columns:
            values_match = re.search(r"\bVALUES\s*\(", query[match.end():], re.IGNORECASE)
            if not values_match:
                raise sqlite3.ProgrammingError("UUID-backed inserts must use a VALUES clause")
            generated_id = str(uuid4())
            query = query[:match.start(2)] + "id, " + query[match.start(2):]
            values_start = match.end() + values_match.end() + len("id, ")
            query = query[:values_start] + "?, " + query[values_start:]
            bound_params = (generated_id, *(params or ()))
            return SQLiteCursor(self._connection.execute(query, bound_params), generated_id)
        return SQLiteCursor(self._connection.execute(query, params or ()))

    def executescript(self, script):
        return self._connection.executescript(script)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


class PostgresCursor:
    """Expose the small sqlite cursor surface the application already uses."""

    def __init__(self, cursor, connection, lastrowid=None):
        self._cursor = cursor
        self._connection = connection
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return self._connection.execute("SELECT LASTVAL() AS id").fetchone()["id"]

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PostgresDatabase:
    """Translate SQLite qmark placeholders for the existing query layer."""

    def __init__(self, database_url):
        self._database_url = database_url
        self._connection = self._connect()

    def _connect(self):
        return psycopg.connect(
            self._database_url,
            autocommit=True,
            connect_timeout=10,
            row_factory=dict_row,
        )

    def _reconnect(self):
        try:
            self._connection.close()
        except psycopg.Error:
            pass
        self._connection = self._connect()

    def execute(self, query, params=None):
        # psycopg treats every percent sign in the SQL text as the start of a
        # parameter placeholder. Escape literal percent signs first (such as
        # ``LIKE 'PSTK-%'``), then translate the application's SQLite qmarks.
        query = query.replace("BEGIN IMMEDIATE", "BEGIN").replace("%", "%%").replace("?", "%s")
        table_match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)\s*\(", query, re.IGNORECASE)
        table = table_match.group(1).lower() if table_match else ""
        def run():
            if table in UUID_ID_TABLES and " RETURNING " not in query.upper():
                cursor = self._connection.execute(query + " RETURNING id", params)
                inserted_id = cursor.fetchone()["id"]
                # Flask's signed cookie session cannot serialize UUID objects.
                if isinstance(inserted_id, UUID):
                    inserted_id = str(inserted_id)
                return PostgresCursor(cursor, self._connection, inserted_id)
            cursor = self._connection.execute(query, params)
            return PostgresCursor(cursor, self._connection)

        try:
            return run()
        except psycopg.OperationalError:
            # A pooled Neon connection can be dropped between request setup and
            # a read. Retrying a SELECT once is safe; writes deliberately fail
            # rather than risking a duplicate transaction.
            if not query.lstrip().upper().startswith("SELECT"):
                raise
            self._reconnect()
            return run()

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        try:
            self._connection.close()
        except psycopg.Error:
            pass
