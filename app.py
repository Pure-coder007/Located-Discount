import hmac
import hashlib
import os
import secrets
import smtplib
import sqlite3
import math
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from email.message import EmailMessage

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from jinja2 import ChoiceLoader, FileSystemLoader
from markupsafe import Markup
import qrcode
import qrcode.image.svg
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge

from locatediscount.billing import redeem_code
from locatediscount.database import (
    DATABASE_ERRORS,
    INTEGRITY_ERRORS,
    POSTGRES_SCHEMA,
    PostgresDatabase,
)
from locatediscount.media import MediaStorageError, destroy_product_image, upload_product_image
from locatediscount.paystack import PaystackError, initialize_transaction, verify_transaction
from locatediscount.validators import CODE_RE, EMAIL_RE, PHONE_RE, REFERENCE_RE, valid_password


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# DATABASE_URL selects Neon/Postgres. DATABASE_PATH remains a convenient
# SQLite fallback for local development and the isolated test suite.
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "instance" / "locatediscount.sqlite3"))
CATEGORIES = ("Food & Drink", "Beauty", "Auto", "Home Services", "Shopping", "Health")
DEFAULT_REDEMPTION_FEE = 150
MAX_PRODUCT_IMAGES = 5
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024
PASSWORD_RESET_TTL = timedelta(minutes=30)


class ConsumerProfileConflict(ValueError):
    """A phone already belongs to an account that this browser cannot claim."""


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value=None):
    return (value or utcnow()).isoformat(timespec="seconds")


def parse_timestamp(value):
    return datetime.fromisoformat(value)


def create_app(test_config=None):
    app = Flask(
        __name__,
        template_folder="Located Folder/templates",
        static_folder="Located Folder/assets",
    )
    # The original supplied landing page remains the actual home template.
    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(BASE_DIR / "Located Folder"),
        FileSystemLoader(BASE_DIR / "Located Folder/templates"),
    ])
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-only-change-me"),
        DATABASE_URL=os.environ.get("DATABASE_URL", "").strip(),
        DATABASE=str(DATABASE_PATH),
        UPLOAD_FOLDER=str(BASE_DIR / "Located Folder" / "assets" / "uploads" / "products"),
        MAX_CONTENT_LENGTH=MAX_PRODUCT_IMAGES * MAX_PRODUCT_IMAGE_BYTES + 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_USE_TLS=os.environ.get("SMTP_USE_TLS", "1") == "1",
        MAIL_FROM=os.environ.get("MAIL_FROM", "no-reply@locatediscount.local"),
        PAYSTACK_SECRET_KEY=(os.environ.get("PAYSTACK_SECRET_KEY") or os.environ.get("PAYSTACK_TEST_SECRET", "")),
        PAYSTACK_API_BASE="https://api.paystack.co",
        PAYSTACK_CALLBACK_URL=os.environ.get("PAYSTACK_CALLBACK_URL", ""),
        CLOUDINARY_CLOUD_NAME=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        CLOUDINARY_API_KEY=os.environ.get("CLOUDINARY_API_KEY", ""),
        CLOUDINARY_API_SECRET=(os.environ.get("CLOUDINARY_API_SECRET") or os.environ.get("CLOUDINARY_SECRET_KEY", "")),
        MEDIA_STORAGE="cloudinary",
    )
    if test_config:
        app.config.update(test_config)
    if app.testing and (not test_config or "MEDIA_STORAGE" not in test_config):
        app.config["MEDIA_STORAGE"] = "local"
    if os.environ.get("FLASK_ENV") == "production" and app.config["SECRET_KEY"] == "development-only-change-me":
        raise RuntimeError("Set a strong SECRET_KEY before running in production.")
    if os.environ.get("FLASK_ENV") == "production" and not app.config["DATABASE_URL"]:
        raise RuntimeError("Set DATABASE_URL to the Neon Postgres connection string before running in production.")
    if not app.config["DATABASE_URL"]:
        Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    def get_db():
        if "db" not in g:
            if app.config["DATABASE_URL"]:
                g.db = PostgresDatabase(app.config["DATABASE_URL"])
            else:
                g.db = sqlite3.connect(app.config["DATABASE"], isolation_level=None)
                g.db.row_factory = sqlite3.Row
                g.db.execute("PRAGMA foreign_keys = ON")
                g.db.execute("PRAGMA journal_mode = WAL")
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        db = get_db()
        if app.config["DATABASE_URL"]:
            for statement in POSTGRES_SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute(
                """INSERT INTO platform_settings (setting_key, integer_value, updated_at)
                   VALUES (?, ?, ?) ON CONFLICT (setting_key) DO NOTHING""",
                ("redemption_fee", DEFAULT_REDEMPTION_FEE, timestamp()),
            )
            for category_name in CATEGORIES:
                db.execute(
                    """INSERT INTO categories (name, created_at) VALUES (?, ?)
                       ON CONFLICT DO NOTHING""",
                    (category_name, timestamp()),
                )
            return
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL UNIQUE COLLATE NOCASE,
              phone TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('consumer','business','admin')),
              name TEXT NOT NULL,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS businesses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              owner_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
              name TEXT NOT NULL,
              category TEXT NOT NULL,
              address TEXT NOT NULL,
              city TEXT NOT NULL,
              wallet_balance INTEGER NOT NULL DEFAULT 0,
              low_balance_threshold INTEGER NOT NULL DEFAULT 2000,
              needs_top_up INTEGER NOT NULL DEFAULT 0,
              is_approved INTEGER NOT NULL DEFAULT 0,
              is_blocked INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS consumer_profiles (
              user_id INTEGER PRIMARY KEY REFERENCES users(id),
              area TEXT,
              favorite_categories TEXT NOT NULL DEFAULT '',
              notifications_enabled INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favorites (
              user_id INTEGER NOT NULL REFERENCES users(id),
              deal_id INTEGER NOT NULL REFERENCES deals(id),
              created_at TEXT NOT NULL,
              PRIMARY KEY (user_id, deal_id)
            );
            CREATE TABLE IF NOT EXISTS consumer_devices (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id),
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS deals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              business_id INTEGER NOT NULL REFERENCES businesses(id),
              title TEXT NOT NULL,
              description TEXT NOT NULL,
              category TEXT NOT NULL,
              terms TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              redemption_limit INTEGER NOT NULL CHECK(redemption_limit > 0),
              redemption_count INTEGER NOT NULL DEFAULT 0,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS codes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              deal_id INTEGER NOT NULL REFERENCES deals(id),
              user_id INTEGER NOT NULL REFERENCES users(id),
              value TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('active','redeemed','expired')) DEFAULT 'active',
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              redeemed_at TEXT,
              redeemed_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS products (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              business_id INTEGER NOT NULL REFERENCES businesses(id),
              name TEXT NOT NULL,
              description TEXT NOT NULL,
              price_kobo INTEGER,
              product_url TEXT,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_images (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
              file_name TEXT NOT NULL,
              secure_url TEXT,
              public_id TEXT,
              storage_provider TEXT NOT NULL DEFAULT 'local',
              sort_order INTEGER NOT NULL CHECK(sort_order BETWEEN 1 AND 5),
              created_at TEXT NOT NULL,
              UNIQUE(product_id, sort_order)
            );
            DROP INDEX IF EXISTS one_active_code_per_deal_user;
            CREATE TABLE IF NOT EXISTS wallet_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              business_id INTEGER NOT NULL REFERENCES businesses(id),
              amount INTEGER NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('topup','redemption','adjustment')),
              status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','posted')),
              reference TEXT NOT NULL UNIQUE,
              note TEXT,
              created_at TEXT NOT NULL,
              approved_at TEXT,
              approved_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS ledger_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              business_id INTEGER NOT NULL REFERENCES businesses(id),
              deal_id INTEGER NOT NULL REFERENCES deals(id),
              code_id INTEGER NOT NULL UNIQUE REFERENCES codes(id),
              fee_charged INTEGER NOT NULL,
              wallet_balance_after INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_settings (
              setting_key TEXT PRIMARY KEY,
              integer_value INTEGER NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              admin_id INTEGER NOT NULL REFERENCES users(id),
              action TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_id INTEGER NOT NULL,
              details TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS categories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE COLLATE NOCASE,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              created_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
            CREATE INDEX IF NOT EXISTS products_business_active_idx ON products(business_id, is_active);
            CREATE INDEX IF NOT EXISTS product_images_product_idx ON product_images(product_id, sort_order);
            CREATE INDEX IF NOT EXISTS password_reset_user_idx ON password_reset_tokens(user_id, expires_at);
            INSERT OR IGNORE INTO platform_settings (setting_key, integer_value, updated_at)
              VALUES ('redemption_fee', 150, datetime('now'));
            """
        )
        # SQLite migrations for databases created before the current pilot schema.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(businesses)").fetchall()}
        if "redemption_fee" not in columns:
            db.execute("ALTER TABLE businesses ADD COLUMN redemption_fee INTEGER")
        if "is_approved" not in columns:
            db.execute("ALTER TABLE businesses ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 0")
        if "is_blocked" not in columns:
            db.execute("ALTER TABLE businesses ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "created_by_admin" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN created_by_admin INTEGER REFERENCES users(id)")
        code_columns = {row["name"] for row in db.execute("PRAGMA table_info(codes)").fetchall()}
        if "device_id" not in code_columns:
            db.execute("ALTER TABLE codes ADD COLUMN device_id INTEGER REFERENCES consumer_devices(id)")
        image_columns = {row["name"] for row in db.execute("PRAGMA table_info(product_images)").fetchall()}
        if "secure_url" not in image_columns:
            db.execute("ALTER TABLE product_images ADD COLUMN secure_url TEXT")
        if "public_id" not in image_columns:
            db.execute("ALTER TABLE product_images ADD COLUMN public_id TEXT")
        if "storage_provider" not in image_columns:
            db.execute("ALTER TABLE product_images ADD COLUMN storage_provider TEXT NOT NULL DEFAULT 'local'")
        db.execute("CREATE INDEX IF NOT EXISTS codes_device_status_idx ON codes(device_id, status, created_at)")
        for category_name in CATEGORIES:
            db.execute(
                "INSERT OR IGNORE INTO categories (name, created_at) VALUES (?, ?)",
                (category_name, timestamp()),
            )

    def expire_codes():
        """Persist expiry so code history and active-code uniqueness stay accurate."""
        get_db().execute(
            "UPDATE codes SET status = 'expired' WHERE status = 'active' AND expires_at <= ?",
            (timestamp(),),
        )

    @app.cli.command("init-db")
    def init_db_command():
        """Create or update the configured database tables."""
        init_db()
        click.echo("Database initialized.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--phone", prompt=True)
    @click.option("--name", prompt=True)
    @click.password_option()
    def create_admin(email, phone, name, password):
        """Create the first platform administrator."""
        if not EMAIL_RE.match(email.strip().lower()) or not PHONE_RE.match(phone.strip()):
            raise click.UsageError("Provide a valid email and phone number.")
        if not 2 <= len(name.strip()) <= 80:
            raise click.UsageError("Admin name must be between 2 and 80 characters.")
        if not valid_password(password):
            raise click.UsageError("Admin passwords must be at least 12 characters and include a letter and number.")
        try:
            get_db().execute(
                "INSERT INTO users (email, phone, password_hash, role, name, created_at) VALUES (?, ?, ?, 'admin', ?, ?)",
                (email.strip().lower(), phone.strip(), generate_password_hash(password), name.strip(), timestamp()),
            )
        except INTEGRITY_ERRORS as error:
            raise click.UsageError("That email or phone number is already registered.") from error
        click.echo("Admin account created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create an idempotent public catalogue for local testing."""
        vendors = (
            ("Lagos Lunch Club", "Food & Drink", "12 Allen Avenue", "Ikeja", "Jollof lunch bowl", "Grilled chicken combo", "Smoothie pack", "Peppered fish platter", "Weekend brunch box"),
            ("Glow House Studio", "Beauty", "18 Admiralty Way", "Lekki", "Signature facial", "Protective style session", "Manicure and pedicure", "Bridal beauty package", "Natural hair treatment"),
            ("DriveCare Garage", "Auto", "4 Opebi Road", "Ikeja", "Engine oil service", "Wheel alignment", "Car wash bundle", "Brake inspection", "Battery replacement"),
            ("HomeFix Collective", "Home Services", "7 Bode Thomas Street", "Surulere", "Deep cleaning visit", "Plumbing call-out", "AC servicing", "Electrical safety check", "Furniture assembly"),
            ("Market Square Finds", "Shopping", "22 Marina Road", "Lagos Island", "Everyday grocery basket", "Household essentials pack", "Fresh produce box", "Pantry restock bundle", "Family value hamper"),
            ("Wellness Corner", "Health", "5 Adeniran Ogunsanya", "Surulere", "Health screening bundle", "Wellness consultation", "Fitness assessment", "Vitamin starter pack", "Massage therapy session"),
            ("Urban Thread", "Shopping", "31 Awolowo Road", "Ikoyi", "Ankara statement shirt", "Weekend linen set", "Handmade tote bag", "Classic sandal pair", "Tailored trouser fit"),
            ("Brew & Bites", "Food & Drink", "9 Freedom Way", "Lekki", "Iced coffee pair", "Breakfast pastry box", "Gourmet burger meal", "Tea-time snack set", "Cold brew bottle"),
            ("Fresh Start Pharmacy", "Health", "14 Toyin Street", "Ikeja", "First-aid kit", "Baby care bundle", "Daily wellness pack", "Skincare essentials", "Travel health pouch"),
            ("AutoShine Detailers", "Auto", "3 Airport Road", "Ikeja", "Interior detailing", "Exterior polish", "Headlight restoration", "Premium wash plan", "Tyre care service"),
        )
        db = get_db()
        created_vendors = created_products = created_deals = 0
        expires = timestamp(utcnow() + timedelta(days=45))
        for index, vendor in enumerate(vendors, start=1):
            name, category, address, city, *product_names = vendor
            email = f"demo-vendor-{index}@locatediscount.invalid"
            owner = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if owner:
                owner_id = owner["id"]
            else:
                cursor = db.execute(
                    """INSERT INTO users (email, phone, password_hash, role, name, created_at)
                       VALUES (?, ?, ?, 'business', ?, ?)""",
                    (email, f"+2348091000{index:03d}", generate_password_hash(secrets.token_urlsafe(24)),
                     f"{name} Owner", timestamp()),
                )
                owner_id = cursor.lastrowid
            business = db.execute("SELECT id FROM businesses WHERE owner_id = ?", (owner_id,)).fetchone()
            if business:
                business_id = business["id"]
                db.execute("UPDATE businesses SET is_approved = 1, is_blocked = 0, wallet_balance = CASE WHEN wallet_balance < 25000 THEN 25000 ELSE wallet_balance END WHERE id = ?", (business_id,))
            else:
                cursor = db.execute(
                    """INSERT INTO businesses
                       (owner_id, name, category, address, city, wallet_balance, is_approved, created_at)
                       VALUES (?, ?, ?, ?, ?, 25000, 1, ?)""",
                    (owner_id, name, category, address, city, timestamp()),
                )
                business_id = cursor.lastrowid
                created_vendors += 1
            for product_index, product_name in enumerate(product_names, start=1):
                exists = db.execute(
                    "SELECT 1 FROM products WHERE business_id = ? AND name = ?", (business_id, product_name)
                ).fetchone()
                if not exists:
                    db.execute(
                        """INSERT INTO products (business_id, name, description, price_kobo, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (business_id, product_name,
                         f"A quality {product_name.lower()} from {name}, available for local discovery and in-store purchase.",
                         (2500 + index * 430 + product_index * 275) * 100, timestamp()),
                    )
                    created_products += 1
            deal_title = f"{name}: 15% off selected items"
            if not db.execute("SELECT 1 FROM deals WHERE business_id = ? AND title = ?", (business_id, deal_title)).fetchone():
                db.execute(
                    """INSERT INTO deals
                       (business_id, title, description, category, terms, expires_at, redemption_limit, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 100, ?)""",
                    (business_id, deal_title, f"Save 15% on selected products and services at {name}.",
                     category, "One redemption per device. Valid while stock lasts. Present your Locatediscount code before payment.",
                     expires, timestamp()),
                )
                created_deals += 1
        click.echo(f"Demo data ready: {created_vendors} vendors, {created_products} products, {created_deals} deals added.")

    def current_user():
        user_id = session.get("user_id")
        if not user_id:
            return None
        return get_db().execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()

    @app.context_processor
    def inject_globals():
        def pagination_url(page, key="page"):
            args = request.args.to_dict()
            args[key] = page
            return url_for(request.endpoint, **(request.view_args or {}), **args)

        def product_image_url(image):
            secure_url = image["secure_url"] if "secure_url" in image.keys() else None
            return secure_url or url_for("static", filename="uploads/products/" + image["file_name"])

        return {
            "current_user": current_user(),
            "csrf_token": session.get("csrf_token"),
            "pagination_url": pagination_url,
            "product_image_url": product_image_url,
        }

    @app.template_filter("qr_svg")
    def qr_svg(value):
        """Render a self-contained SVG QR code; no third-party tracking or API is used."""
        image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage, border=2)
        return Markup(image.to_string(encoding="unicode"))

    def require_csrf():
        token = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(token, expected):
            abort(400, "Invalid or missing CSRF token.")

    @app.before_request
    def protect_requests():
        session.permanent = True
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        expire_codes()
        if request.method == "POST" and request.endpoint != "paystack_webhook":
            require_csrf()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(self)"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https://res.cloudinary.com; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self';"
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith(("/consumer", "/business", "/admin", "/login", "/register", "/my-codes", "/codes/")) or request.path.endswith("/claim"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user():
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def roles_required(*roles):
        def decorator(view):
            @wraps(view)
            @login_required
            def wrapped(*args, **kwargs):
                if current_user()["role"] not in roles:
                    abort(403)
                return view(*args, **kwargs)
            return wrapped
        return decorator

    def business_for_user(user_id):
        return get_db().execute("SELECT * FROM businesses WHERE owner_id = ?", (user_id,)).fetchone()

    def approved_business_required(view):
        """Require a reviewed, unblocked business for financial and publishing actions."""
        @wraps(view)
        @roles_required("business")
        def wrapped(*args, **kwargs):
            business = business_for_user(current_user()["id"])
            if not business or business["is_blocked"]:
                abort(403)
            if not business["is_approved"]:
                flash("Your business is awaiting admin approval. Publishing, wallet, and redemption are disabled until review is complete.", "warning")
                return redirect(url_for("business_dashboard"))
            return view(*args, **kwargs)
        return wrapped

    def current_consumer_profile():
        """A customer session is separate from staff authentication."""
        consumer_id = session.get("consumer_id")
        if not isinstance(consumer_id, int):
            return None
        return get_db().execute(
            """SELECT users.id, users.name, users.phone, consumer_profiles.area
               FROM users JOIN consumer_profiles ON consumer_profiles.user_id = users.id
               WHERE users.id = ? AND users.role = 'consumer' AND users.is_active = 1""",
            (consumer_id,),
        ).fetchone()

    def device_token_hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def ensure_consumer_device(db, consumer_id):
        """Bind generated codes to one persistent browser/device installation."""
        token = session.get("consumer_device_token")
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            session["consumer_device_token"] = token
        row = db.execute(
            """SELECT id FROM consumer_devices
               WHERE user_id = ? AND token_hash = ? AND revoked_at IS NULL""",
            (consumer_id, device_token_hash(token)),
        ).fetchone()
        if row:
            db.execute("UPDATE consumer_devices SET last_seen_at = ? WHERE id = ?", (timestamp(), row["id"]))
            session["consumer_device_id"] = row["id"]
            return row["id"]
        cursor = db.execute(
            """INSERT INTO consumer_devices (user_id, token_hash, created_at, last_seen_at)
               VALUES (?, ?, ?, ?)""",
            (consumer_id, device_token_hash(token), timestamp(), timestamp()),
        )
        session["consumer_device_id"] = cursor.lastrowid
        return cursor.lastrowid

    def create_consumer_profile(db, name, phone, area):
        """Create the minimal, non-login customer record used to own redemption codes."""
        existing = db.execute(
            "SELECT id, role FROM users WHERE phone = ?", (phone,)
        ).fetchone()
        if existing:
            # Phone ownership must be verified before cross-browser profile recovery.
            if existing["role"] == "consumer":
                raise ConsumerProfileConflict(
                    "This phone number already has a code profile. Open it from the browser where you first claimed a code."
                )
            raise ConsumerProfileConflict(
                "This phone number belongs to a business or administrator account. Use the customer's own phone number to generate their code."
            )
        email = f"consumer-{secrets.token_urlsafe(18).lower()}@locatediscount.invalid"
        cursor = db.execute(
            """INSERT INTO users (email, phone, password_hash, role, name, created_at)
               VALUES (?, ?, ?, 'consumer', ?, ?)""",
            (email, phone, generate_password_hash(secrets.token_urlsafe(32)), name, timestamp()),
        )
        db.execute(
            "INSERT INTO consumer_profiles (user_id, area, created_at) VALUES (?, ?, ?)",
            (cursor.lastrowid, area or None, timestamp()),
        )
        return cursor.lastrowid

    def safe_next_url(value):
        return value if value and value.startswith("/") and not value.startswith("//") else None

    def code_value():
        return "LD-" + secrets.token_hex(4).upper()

    def valid_product_url(value):
        if not value:
            return True
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.netloc) and len(value) <= 500

    def product_image_extension(upload):
        """Accept only common raster image signatures, never client-supplied extensions."""
        header = upload.stream.read(16)
        upload.stream.seek(0)
        if header.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "webp"
        return None

    def upload_size(upload):
        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        return size

    def save_product_images(uploads, business_id):
        uploads = [upload for upload in uploads if upload and upload.filename]
        if len(uploads) > MAX_PRODUCT_IMAGES:
            raise ValueError(f"Upload no more than {MAX_PRODUCT_IMAGES} product images.")
        saved = []
        target_dir = Path(app.config["UPLOAD_FOLDER"]) / str(business_id)
        cloudinary_options = {
            "cloud_name": app.config["CLOUDINARY_CLOUD_NAME"],
            "api_key": app.config["CLOUDINARY_API_KEY"],
            "api_secret": app.config["CLOUDINARY_API_SECRET"],
        }
        if app.config["MEDIA_STORAGE"] == "cloudinary" and not all(cloudinary_options.values()):
            raise ValueError("Cloudinary is not configured. Add the cloud name, API key, and API secret.")
        try:
            for upload in uploads:
                extension = product_image_extension(upload)
                size = upload_size(upload)
                if not extension:
                    raise ValueError("Product images must be valid JPEG, PNG, or WebP files.")
                if not 0 < size <= MAX_PRODUCT_IMAGE_BYTES:
                    raise ValueError("Each product image must be 5 MB or smaller.")
                token = secrets.token_hex(16)
                if app.config["MEDIA_STORAGE"] == "cloudinary":
                    public_id = f"locatediscount/products/{business_id}/{token}"
                    result = upload_product_image(upload.stream, public_id=public_id, **cloudinary_options)
                    saved.append({
                        "file_name": result["public_id"],
                        "public_id": result["public_id"],
                        "secure_url": result["secure_url"],
                        "storage_provider": "cloudinary",
                    })
                else:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    file_name = f"{business_id}/{token}.{extension}"
                    upload.save(Path(app.config["UPLOAD_FOLDER"]) / file_name)
                    saved.append({
                        "file_name": file_name,
                        "public_id": None,
                        "secure_url": None,
                        "storage_provider": "local",
                    })
        except (OSError, ValueError, MediaStorageError):
            cleanup_product_images(saved)
            raise
        return saved

    def cleanup_product_images(images):
        cloudinary_options = {
            "cloud_name": app.config["CLOUDINARY_CLOUD_NAME"],
            "api_key": app.config["CLOUDINARY_API_KEY"],
            "api_secret": app.config["CLOUDINARY_API_SECRET"],
        }
        for image in images:
            try:
                if image["storage_provider"] == "cloudinary" and image["public_id"]:
                    destroy_product_image(public_id=image["public_id"], **cloudinary_options)
                elif image["storage_provider"] == "local":
                    (Path(app.config["UPLOAD_FOLDER"]) / image["file_name"]).unlink(missing_ok=True)
            except Exception:
                app.logger.exception("Could not remove newly uploaded product image")

    def redemption_fee(db=None, business=None):
        db = db or get_db()
        if business and business["redemption_fee"] is not None:
            return business["redemption_fee"]
        setting = db.execute(
            "SELECT integer_value FROM platform_settings WHERE setting_key = 'redemption_fee'"
        ).fetchone()
        return setting["integer_value"] if setting else DEFAULT_REDEMPTION_FEE

    def apply_paystack_topup(payment):
        """Credit a matching local payment once after trusted Paystack confirmation."""
        if not isinstance(payment, dict):
            return False
        reference = payment.get("reference")
        amount_kobo = payment.get("amount")
        if (not isinstance(reference, str) or not reference.startswith("PSTK-") or
                payment.get("status") != "success" or payment.get("currency") != "NGN" or
                not isinstance(amount_kobo, int)):
            return False
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            transaction = db.execute(
                "SELECT * FROM wallet_transactions WHERE reference = ? AND kind = 'topup'",
                (reference,),
            ).fetchone()
            if not transaction:
                db.rollback()
                return False
            if transaction["status"] == "approved":
                db.rollback()
                return True
            if transaction["status"] != "pending" or amount_kobo != transaction["amount"] * 100:
                db.rollback()
                return False
            db.execute(
                """UPDATE businesses
                   SET wallet_balance = wallet_balance + ?,
                       needs_top_up = CASE WHEN wallet_balance + ? < low_balance_threshold THEN 1 ELSE 0 END
                   WHERE id = ?""",
                (transaction["amount"], transaction["amount"], transaction["business_id"]),
            )
            db.execute(
                "UPDATE wallet_transactions SET status = 'approved', approved_at = ? WHERE id = ?",
                (timestamp(), transaction["id"]),
            )
            db.commit()
            return True
        except DATABASE_ERRORS:
            db.rollback()
            raise

    def category_names(include_inactive=False):
        sql = "SELECT name FROM categories"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY LOWER(name)"
        return tuple(row["name"] for row in get_db().execute(sql).fetchall())

    def page_window(total, key="page", per_page=5):
        total_pages = max(1, math.ceil(total / per_page))
        page = min(max(request.args.get(key, 1, type=int) or 1, 1), total_pages)
        return page, total_pages, per_page, (page - 1) * per_page

    def daily_redemption_series(db, business_id=None, days=7):
        start = (utcnow() - timedelta(days=days - 1)).date()
        params = [start.isoformat()]
        condition = "created_at >= ?"
        if business_id is not None:
            condition += " AND business_id = ?"
            params.append(business_id)
        rows = db.execute(
            f"SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS total FROM ledger_entries WHERE {condition} GROUP BY day",
            params,
        ).fetchall()
        totals = {row["day"]: row["total"] for row in rows}
        values = [{"day": (start + timedelta(days=index)).isoformat(), "total": totals.get((start + timedelta(days=index)).isoformat(), 0)} for index in range(days)]
        peak = max((item["total"] for item in values), default=0)
        for item in values:
            item["level"] = math.ceil(item["total"] / peak * 10) if peak else 0
        return values

    def record_admin_action(action, target_type, target_id, details):
        get_db().execute(
            "INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (current_user()["id"], action, target_type, target_id, details[:1000], timestamp()),
        )

    def reset_token_hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def send_password_reset(user, reset_url):
        """Send through configured SMTP; tests retain the URL without sending mail."""
        if app.testing:
            app.extensions.setdefault("password_reset_links", []).append((user["email"], reset_url))
            return True
        if not app.config["SMTP_HOST"]:
            app.logger.warning("Password reset requested for user %s, but SMTP is not configured.", user["id"])
            return False
        message = EmailMessage()
        message["Subject"] = "Reset your Locatediscount password"
        message["From"] = app.config["MAIL_FROM"]
        message["To"] = user["email"]
        message.set_content(
            f"Hello {user['name']},\n\nUse this secure link within 30 minutes to reset your password:\n{reset_url}\n\nIf you did not request this, ignore this email."
        )
        with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=10) as smtp:
            if app.config["SMTP_USE_TLS"]:
                smtp.starttls()
            if app.config["SMTP_USERNAME"]:
                smtp.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
            smtp.send_message(message)
        return True

    def login_attempt_key(email):
        # Do not retain raw login identifiers in the rate-limit table.
        value = f"{request.remote_addr or ''}:{email}".encode()
        return hashlib.sha256(value).hexdigest()

    def login_is_blocked(key):
        row = get_db().execute(
            "SELECT blocked_until FROM login_attempts WHERE attempt_key = ?", (key,)
        ).fetchone()
        return bool(row and row["blocked_until"] and parse_timestamp(row["blocked_until"]) > utcnow())

    def record_failed_login(key):
        db = get_db()
        row = db.execute(
            "SELECT failures FROM login_attempts WHERE attempt_key = ?", (key,)
        ).fetchone()
        failures = (row["failures"] if row else 0) + 1
        blocked_until = timestamp(utcnow() + timedelta(minutes=15)) if failures >= 5 else None
        db.execute(
            """INSERT INTO login_attempts (attempt_key, failures, blocked_until, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(attempt_key) DO UPDATE SET failures = excluded.failures,
               blocked_until = excluded.blocked_until, updated_at = excluded.updated_at""",
            (key, failures, blocked_until, timestamp()),
        )

    def clear_login_attempts(key):
        get_db().execute("DELETE FROM login_attempts WHERE attempt_key = ?", (key,))

    @app.route("/")
    def home():
        category = request.args.get("category", "")
        query = request.args.get("q", "").strip()
        area = request.args.get("area", "").strip()
        sort = request.args.get("sort", "newest")
        sql = """SELECT deals.*, businesses.name business_name, businesses.city, businesses.address
                 FROM deals JOIN businesses ON businesses.id = deals.business_id
                 WHERE deals.is_active = 1 AND deals.expires_at > ?
                   AND businesses.is_approved = 1 AND businesses.is_blocked = 0"""
        params = [timestamp()]
        categories = category_names()
        if category in categories:
            sql += " AND deals.category = ?"
            params.append(category)
        if query:
            sql += " AND (deals.title LIKE ? OR deals.description LIKE ? OR businesses.name LIKE ? OR businesses.city LIKE ?)"
            params.extend([f"%{query}%"] * 4)
        if area:
            sql += " AND (businesses.city LIKE ? OR businesses.address LIKE ?)"
            params.extend([f"%{area}%", f"%{area}%"])
        order_by = {
            "expiring": "deals.expires_at ASC",
            "popular": "deals.redemption_count DESC, deals.created_at DESC",
        }.get(sort, "deals.created_at DESC")
        db = get_db()
        count_sql = f"SELECT COUNT(*) total FROM ({sql}) filtered_deals"
        total = db.execute(count_sql, params).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        sql += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
        deals = db.execute(sql, [*params, per_page, offset]).fetchall()
        product_sql = """SELECT products.*, businesses.name business_name, businesses.city,
                                businesses.category, cover.file_name, cover.secure_url,
                                (SELECT COUNT(*) FROM deals product_deals
                                  WHERE product_deals.business_id = products.business_id
                                    AND product_deals.is_active = 1 AND product_deals.expires_at > ?) live_deal_count
                         FROM products JOIN businesses ON businesses.id = products.business_id
                         LEFT JOIN product_images cover ON cover.id = (
                           SELECT image.id FROM product_images image
                           WHERE image.product_id = products.id ORDER BY image.sort_order LIMIT 1
                         )
                         WHERE products.is_active = 1
                           AND businesses.is_approved = 1 AND businesses.is_blocked = 0"""
        product_params = [timestamp()]
        if category in categories:
            product_sql += " AND businesses.category = ?"
            product_params.append(category)
        if query:
            product_sql += """ AND (products.name LIKE ? OR products.description LIKE ?
                                  OR businesses.name LIKE ? OR businesses.category LIKE ?
                                  OR businesses.city LIKE ?)"""
            product_params.extend([f"%{query}%"] * 5)
        if area:
            product_sql += " AND (businesses.city LIKE ? OR businesses.address LIKE ?)"
            product_params.extend([f"%{area}%", f"%{area}%"])
        product_total = db.execute(
            f"SELECT COUNT(*) total FROM ({product_sql}) filtered_products", product_params
        ).fetchone()["total"]
        homepage_products = db.execute(
            product_sql + " ORDER BY products.created_at DESC LIMIT 5", product_params
        ).fetchall()
        return render_template(
            "index.html", deals=deals, categories=categories, query=query, area=area,
            selected_category=category, selected_sort=sort, consumer=current_consumer_profile(),
            page=page, total_pages=total_pages, total=total,
            homepage_products=homepage_products, product_total=product_total,
            deal_images=(
                "deals/local-meal.jpg", "deals/fried-chicken.jpg", "deals/market-offer.jpg",
                "deals/clothing-sale.jpg", "deals/sneaker-deal.jpg", "deals/boutique-style.png",
            ),
        )

    @app.route("/register", methods=("GET", "POST"))
    def register():
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")
            role = "business"
            errors = []
            if not EMAIL_RE.match(email): errors.append("Enter a valid email address.")
            if not PHONE_RE.match(phone): errors.append("Enter a valid phone number with 10 to 15 digits.")
            if not 2 <= len(name) <= 80: errors.append("Name must be between 2 and 80 characters.")
            if not valid_password(password): errors.append("Password must be at least 12 characters and include a letter and number.")
            business_name = request.form.get("business_name", "").strip()
            category = request.form.get("category", "")
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            if not 2 <= len(business_name) <= 120: errors.append("Business name must be between 2 and 120 characters.")
            if category not in category_names(): errors.append("Choose a business category.")
            if not 5 <= len(address) <= 200 or not 2 <= len(city) <= 80: errors.append("Enter a valid business address and city.")
            if errors:
                for error in errors: flash(error, "danger")
            else:
                db = get_db()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    cursor = db.execute("INSERT INTO users (email, phone, password_hash, role, name, created_at) VALUES (?, ?, ?, ?, ?, ?)", (email, phone, generate_password_hash(password), role, name, timestamp()))
                    db.execute("INSERT INTO businesses (owner_id, name, category, address, city, created_at) VALUES (?, ?, ?, ?, ?, ?)", (cursor.lastrowid, business_name, category, address, city, timestamp()))
                    db.commit()
                except INTEGRITY_ERRORS:
                    db.rollback(); flash("That email or phone number is already registered.", "danger")
                else:
                    session.clear(); session["user_id"] = cursor.lastrowid; session["csrf_token"] = secrets.token_urlsafe(32)
                    flash("Your account has been created and is pending admin approval.", "success")
                    return redirect(url_for("dashboard"))
        return render_template("register.html", categories=category_names())

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if current_user(): return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            attempt_key = login_attempt_key(email)
            if login_is_blocked(attempt_key):
                flash("Too many sign-in attempts. Please wait 15 minutes and try again.", "danger")
                return render_template("login.html"), 429
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user or not user["is_active"] or not check_password_hash(user["password_hash"], password):
                record_failed_login(attempt_key)
                flash("Invalid email or password.", "danger")
            else:
                clear_login_attempts(attempt_key)
                session.clear(); session["user_id"] = user["id"]; session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(safe_next_url(request.args.get("next")) or url_for("dashboard"))
        return render_template("login.html")

    @app.route("/forgot-password", methods=("GET", "POST"))
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = get_db().execute(
                "SELECT * FROM users WHERE email = ? AND role IN ('business', 'admin') AND is_active = 1",
                (email,),
            ).fetchone()
            if user:
                token = secrets.token_urlsafe(32)
                get_db().execute(
                    "UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                    (timestamp(), user["id"]),
                )
                get_db().execute(
                    "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (user["id"], reset_token_hash(token), timestamp(utcnow() + PASSWORD_RESET_TTL), timestamp()),
                )
                try:
                    send_password_reset(user, url_for("reset_password", token=token, _external=True))
                except (OSError, smtplib.SMTPException):
                    app.logger.exception("Could not send password reset email for user %s", user["id"])
            flash("If an active account matches that email, a password reset link has been sent.", "success")
            return redirect(url_for("login"))
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=("GET", "POST"))
    def reset_password(token):
        token_row = get_db().execute(
            """SELECT password_reset_tokens.*, users.email
               FROM password_reset_tokens JOIN users ON users.id = password_reset_tokens.user_id
               WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? AND users.is_active = 1""",
            (reset_token_hash(token), timestamp()),
        ).fetchone()
        if not token_row:
            flash("That reset link is invalid or has expired. Please request a new one.", "danger")
            return redirect(url_for("forgot_password"))
        if request.method == "POST":
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            if password != confirmation:
                flash("The password confirmation does not match.", "danger")
            elif not valid_password(password):
                flash("Use at least 12 characters including a letter and number.", "danger")
            else:
                db = get_db()
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), token_row["user_id"]))
                db.execute("UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (timestamp(), token_row["user_id"]))
                db.commit()
                session.clear()
                flash("Your password has been reset. You can now sign in.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html", account_email=token_row["email"])

    @app.route("/account/password", methods=("GET", "POST"))
    @login_required
    def change_password():
        if request.method == "POST":
            user = current_user()
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirmation = request.form.get("password_confirmation", "")
            if not check_password_hash(user["password_hash"], current_password):
                flash("Your current password is incorrect.", "danger")
            elif new_password != confirmation:
                flash("The new password confirmation does not match.", "danger")
            elif not valid_password(new_password):
                flash("Use at least 12 characters including a letter and number.", "danger")
            elif check_password_hash(user["password_hash"], new_password):
                flash("Choose a password different from your current password.", "danger")
            else:
                get_db().execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user["id"]))
                get_db().execute("UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (timestamp(), user["id"]))
                if user["role"] == "admin":
                    record_admin_action("change_password", "user", user["id"], "Administrator changed their password")
                session.clear()
                flash("Password changed. Please sign in again.", "success")
                return redirect(url_for("login"))
        return render_template("change_password.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear(); flash("You have signed out.", "success")
        return redirect(url_for("home"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        role = current_user()["role"]
        return redirect(url_for({"consumer": "home", "business": "business_dashboard", "admin": "admin_dashboard"}[role]))

    @app.route("/deals/<int:deal_id>")
    def deal_detail(deal_id):
        deal = get_db().execute("SELECT deals.*, businesses.name business_name, businesses.address, businesses.city FROM deals JOIN businesses ON businesses.id = deals.business_id WHERE deals.id = ? AND deals.is_active = 1 AND deals.expires_at > ? AND businesses.is_approved = 1 AND businesses.is_blocked = 0", (deal_id, timestamp())).fetchone()
        if not deal: abort(404)
        consumer = current_consumer_profile()
        is_favorite = bool(consumer and get_db().execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND deal_id = ?", (consumer["id"], deal_id)
        ).fetchone())
        return render_template("deal_detail.html", deal=deal, consumer=consumer, is_favorite=is_favorite)

    @app.route("/consumer")
    @roles_required("consumer")
    def consumer_dashboard():
        return redirect(url_for("home"))

    @app.route("/consumer/codes")
    def legacy_consumer_codes():
        """Keep old bookmarks safe while public claiming no longer needs an account."""
        return redirect(url_for("home"))

    @app.route("/consumer/deals")
    @roles_required("consumer")
    def legacy_consumer_deals():
        category = request.args.get("category", "")
        query = request.args.get("q", "").strip()
        sql = "SELECT deals.*, businesses.name business_name, businesses.city FROM deals JOIN businesses ON businesses.id = deals.business_id WHERE deals.is_active = 1 AND deals.expires_at > ?"
        params = [timestamp()]
        categories = category_names()
        if category in categories: sql += " AND deals.category = ?"; params.append(category)
        if query: sql += " AND (deals.title LIKE ? OR businesses.name LIKE ?)"; params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY deals.created_at DESC"
        deals = get_db().execute(sql, params).fetchall()
        codes = get_db().execute("SELECT codes.*, deals.title, businesses.name business_name FROM codes JOIN deals ON deals.id = codes.deal_id JOIN businesses ON businesses.id = deals.business_id WHERE codes.user_id = ? ORDER BY codes.created_at DESC", (current_user()["id"],)).fetchall()
        return render_template("consumer_dashboard.html", deals=deals, codes=codes, categories=categories, selected_category=category, query=query)

    @app.route("/deals/<int:deal_id>/claim", methods=("GET", "POST"))
    def claim_code(deal_id):
        db = get_db()
        deal = db.execute(
            """SELECT deals.*, businesses.name business_name, businesses.city, businesses.address
               FROM deals JOIN businesses ON businesses.id = deals.business_id
               WHERE deals.id = ? AND deals.is_active = 1 AND deals.expires_at > ?
                 AND businesses.is_approved = 1 AND businesses.is_blocked = 0""",
            (deal_id, timestamp()),
        ).fetchone()
        if not deal or deal["redemption_count"] >= deal["redemption_limit"]:
            flash("This deal is no longer available.", "danger")
            return redirect(url_for("home"))
        consumer = current_consumer_profile()
        if request.method == "GET":
            return render_template("claim_code.html", deal=deal, consumer=consumer)

        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        area = request.form.get("area", "").strip()
        if not consumer:
            if not 2 <= len(name) <= 80 or not PHONE_RE.match(phone) or len(area) > 80:
                flash("Enter your name, a valid phone number, and an optional area up to 80 characters.", "danger")
                return render_template("claim_code.html", deal=deal, consumer=None), 400
        try:
            db.execute("BEGIN IMMEDIATE")
            deal = db.execute(
                """SELECT deals.* FROM deals JOIN businesses ON businesses.id = deals.business_id
                   WHERE deals.id = ? AND deals.is_active = 1 AND deals.expires_at > ?
                     AND businesses.is_approved = 1 AND businesses.is_blocked = 0""",
                (deal_id, timestamp()),
            ).fetchone()
            if not deal or deal["redemption_count"] >= deal["redemption_limit"]:
                raise ValueError("This deal is no longer available.")
            consumer_id = consumer["id"] if consumer else create_consumer_profile(db, name, phone, area)
            device_id = ensure_consumer_device(db, consumer_id)
            existing = db.execute(
                """SELECT codes.id FROM codes JOIN deals ON deals.id = codes.deal_id
                   WHERE codes.device_id = ? AND deals.business_id = ? AND codes.status = 'active'""",
                (device_id, deal["business_id"]),
            ).fetchone()
            if existing:
                db.commit()
                session["consumer_id"] = consumer_id
                flash("This device already has an active code for this business. Redeem it before claiming another.", "info")
                return redirect(url_for("code_detail", code_id=existing["id"]))
            expires = min(parse_timestamp(deal["expires_at"]), utcnow() + timedelta(days=7))
            for _ in range(5):
                value = code_value()
                try:
                    cursor = db.execute("INSERT INTO codes (deal_id, user_id, device_id, value, status, expires_at, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)", (deal_id, consumer_id, device_id, value, timestamp(expires), timestamp()))
                    db.commit()
                    session["consumer_id"] = consumer_id
                    flash("Your redemption code is ready. Show it to this business in-store.", "success")
                    return redirect(url_for("code_detail", code_id=cursor.lastrowid))
                except INTEGRITY_ERRORS:
                    continue
            raise ValueError("Could not generate a secure code. Please retry.")
        except ConsumerProfileConflict as error:
            db.rollback()
            flash(str(error), "danger")
            return render_template("claim_code.html", deal=deal, consumer=None), 409
        except INTEGRITY_ERRORS:
            db.rollback()
            app.logger.exception("Could not create the customer code profile because its details already exist")
            flash("That phone number is already in use. Use the customer's own phone number or reopen their original code browser.", "danger")
            return render_template("claim_code.html", deal=deal, consumer=None), 409
        except ValueError as error:
            db.rollback(); flash(str(error), "danger")
        return redirect(url_for("deal_detail", deal_id=deal_id))

    @app.route("/codes/<int:code_id>")
    def code_detail(code_id):
        consumer = current_consumer_profile()
        if not consumer:
            abort(404)
        code = get_db().execute(
            """SELECT codes.*, deals.title, deals.terms, businesses.name business_name,
                      businesses.address, businesses.city
               FROM codes
               JOIN deals ON deals.id = codes.deal_id
               JOIN businesses ON businesses.id = deals.business_id
               WHERE codes.id = ? AND codes.user_id = ?""",
            (code_id, consumer["id"]),
        ).fetchone()
        if not code:
            abort(404)
        return render_template("code_detail.html", code=code)

    @app.route("/my-codes")
    def my_codes():
        consumer = current_consumer_profile()
        if not consumer:
            flash("Claim a deal first to create your private code wallet.", "info")
            return redirect(url_for("home"))
        active_tab = request.args.get("status", "active")
        if active_tab not in {"active", "redeemed", "expired"}:
            active_tab = "active"
        db = get_db()
        total = db.execute(
            "SELECT COUNT(*) total FROM codes WHERE user_id = ? AND status = ?",
            (consumer["id"], active_tab),
        ).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        codes = db.execute(
            """SELECT codes.*, deals.title, deals.description, businesses.name business_name, businesses.city
               FROM codes JOIN deals ON deals.id = codes.deal_id
               JOIN businesses ON businesses.id = deals.business_id
               WHERE codes.user_id = ? AND codes.status = ? ORDER BY codes.created_at DESC
               LIMIT ? OFFSET ?""",
            (consumer["id"], active_tab, per_page, offset),
        ).fetchall()
        counts = db.execute(
            "SELECT status, COUNT(*) total FROM codes WHERE user_id = ? GROUP BY status", (consumer["id"],)
        ).fetchall()
        return render_template("my_codes.html", consumer=consumer, codes=codes, active_tab=active_tab,
                               counts={row["status"]: row["total"] for row in counts},
                               page=page, total_pages=total_pages, total=total)

    @app.post("/deals/<int:deal_id>/favorite")
    def toggle_favorite(deal_id):
        consumer = current_consumer_profile()
        if not consumer:
            flash("Claim a deal first to create your private customer profile.", "info")
            return redirect(url_for("claim_code", deal_id=deal_id))
        db = get_db()
        exists = db.execute("SELECT 1 FROM favorites WHERE user_id = ? AND deal_id = ?", (consumer["id"], deal_id)).fetchone()
        if exists:
            db.execute("DELETE FROM favorites WHERE user_id = ? AND deal_id = ?", (consumer["id"], deal_id))
            flash("Removed from your saved deals.", "info")
        else:
            db.execute("INSERT INTO favorites (user_id, deal_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (consumer["id"], deal_id, timestamp()))
            flash("Saved to your private deal list.", "success")
        return redirect(url_for("deal_detail", deal_id=deal_id))

    @app.route("/vendors")
    def vendors():
        query = request.args.get("q", "").strip()
        area = request.args.get("area", "").strip()
        sql = """SELECT businesses.*, COUNT(DISTINCT deals.id) live_deal_count,
                         COUNT(DISTINCT products.id) product_count
                  FROM businesses
                  LEFT JOIN deals ON deals.business_id = businesses.id
                     AND deals.is_active = 1 AND deals.expires_at > ?
                  LEFT JOIN products ON products.business_id = businesses.id AND products.is_active = 1
                  WHERE businesses.is_approved = 1 AND businesses.is_blocked = 0"""
        params = [timestamp()]
        if query:
            sql += " AND (businesses.name LIKE ? OR businesses.category LIKE ? OR businesses.city LIKE ?)"
            params.extend([f"%{query}%"] * 3)
        if area:
            sql += " AND (businesses.city LIKE ? OR businesses.address LIKE ?)"
            params.extend([f"%{area}%"] * 2)
        grouped_sql = sql + " GROUP BY businesses.id"
        db = get_db()
        total = db.execute(f"SELECT COUNT(*) total FROM ({grouped_sql}) filtered_vendors", params).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        sql = grouped_sql + " ORDER BY businesses.created_at DESC LIMIT ? OFFSET ?"
        return render_template("vendors.html", vendors=db.execute(sql, [*params, per_page, offset]).fetchall(),
                               query=query, area=area, page=page, total_pages=total_pages, total=total)

    @app.route("/vendors/<int:business_id>")
    def vendor_detail(business_id):
        db = get_db()
        business = db.execute(
            "SELECT * FROM businesses WHERE id = ? AND is_approved = 1 AND is_blocked = 0", (business_id,)
        ).fetchone()
        if not business:
            abort(404)
        deal_total = db.execute("SELECT COUNT(*) total FROM deals WHERE business_id = ? AND is_active = 1 AND expires_at > ?", (business_id, timestamp())).fetchone()["total"]
        deal_page, deal_pages, per_page, deal_offset = page_window(deal_total, "deal_page")
        deals = db.execute(
            "SELECT * FROM deals WHERE business_id = ? AND is_active = 1 AND expires_at > ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (business_id, timestamp(), per_page, deal_offset),
        ).fetchall()
        product_total = db.execute("SELECT COUNT(*) total FROM products WHERE business_id = ? AND is_active = 1", (business_id,)).fetchone()["total"]
        product_page, product_pages, per_page, product_offset = page_window(product_total, "product_page")
        products = db.execute(
            "SELECT * FROM products WHERE business_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (business_id, per_page, product_offset),
        ).fetchall()
        return render_template("vendor_detail.html", business=business, deals=deals, products=products,
                               deal_page=deal_page, deal_pages=deal_pages, deal_total=deal_total,
                               product_page=product_page, product_pages=product_pages, product_total=product_total)

    @app.route("/products")
    def products():
        query = request.args.get("q", "").strip()
        area = request.args.get("area", "").strip()
        sql = """SELECT products.*, businesses.name business_name, businesses.city, businesses.category,
                         cover.file_name, cover.secure_url
                  FROM products JOIN businesses ON businesses.id = products.business_id
                  LEFT JOIN product_images cover ON cover.id = (
                    SELECT image.id FROM product_images image
                    WHERE image.product_id = products.id ORDER BY image.sort_order LIMIT 1
                  )
                  WHERE products.is_active = 1 AND businesses.is_approved = 1 AND businesses.is_blocked = 0"""
        params = []
        if query:
            sql += """ AND (products.name LIKE ? OR products.description LIKE ? OR businesses.name LIKE ?
                           OR businesses.category LIKE ? OR businesses.city LIKE ?)"""
            params.extend([f"%{query}%"] * 5)
        if area:
            sql += " AND (businesses.city LIKE ? OR businesses.address LIKE ?)"
            params.extend([f"%{area}%"] * 2)
        db = get_db()
        total = db.execute(f"SELECT COUNT(*) total FROM ({sql}) filtered_products", params).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        sql += " ORDER BY products.created_at DESC LIMIT ? OFFSET ?"
        return render_template("products.html", products=db.execute(sql, [*params, per_page, offset]).fetchall(),
                               query=query, area=area, page=page, total_pages=total_pages, total=total)

    @app.route("/products/<int:product_id>")
    def product_detail(product_id):
        db = get_db()
        product = db.execute(
            """SELECT products.*, businesses.name business_name, businesses.city, businesses.address
               FROM products JOIN businesses ON businesses.id = products.business_id
               WHERE products.id = ? AND products.is_active = 1
                 AND businesses.is_approved = 1 AND businesses.is_blocked = 0""",
            (product_id,),
        ).fetchone()
        if not product:
            abort(404)
        images = db.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order", (product_id,)
        ).fetchall()
        deals = db.execute(
            """SELECT id, title, terms, expires_at FROM deals
               WHERE business_id = ? AND is_active = 1 AND expires_at > ? ORDER BY created_at DESC LIMIT 3""",
            (product["business_id"], timestamp()),
        ).fetchall()
        return render_template("product_detail.html", product=product, images=images, deals=deals)

    @app.route("/business")
    @roles_required("business")
    def business_dashboard():
        business = business_for_user(current_user()["id"])
        db = get_db()
        deal_total = db.execute("SELECT COUNT(*) total FROM deals WHERE business_id = ?", (business["id"],)).fetchone()["total"]
        deal_page, deal_pages, limit, deal_offset = page_window(deal_total, "deal_page")
        deals = db.execute("SELECT * FROM deals WHERE business_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (business["id"], limit, deal_offset)).fetchall()
        ledger_total = db.execute("SELECT COUNT(*) total FROM ledger_entries WHERE business_id = ?", (business["id"],)).fetchone()["total"]
        activity_page, activity_pages, limit, activity_offset = page_window(ledger_total, "activity_page")
        ledger = db.execute("SELECT ledger_entries.*, codes.value code, deals.title FROM ledger_entries JOIN codes ON codes.id = ledger_entries.code_id JOIN deals ON deals.id = ledger_entries.deal_id WHERE ledger_entries.business_id = ? ORDER BY ledger_entries.created_at DESC LIMIT ? OFFSET ?", (business["id"], limit, activity_offset)).fetchall()
        active_deal_count = db.execute("SELECT COUNT(*) total FROM deals WHERE business_id = ? AND is_active = 1 AND expires_at > ?", (business["id"], timestamp())).fetchone()["total"]
        return render_template("business_dashboard.html", business=business, deals=deals, ledger=ledger,
                               fee=redemption_fee(db, business), active_deal_count=active_deal_count,
                               deal_page=deal_page, deal_pages=deal_pages, activity_page=activity_page,
                               activity_pages=activity_pages, chart=daily_redemption_series(db, business["id"]))

    @app.route("/business/analytics")
    @roles_required("business")
    def business_analytics():
        business = business_for_user(current_user()["id"])
        db = get_db()
        summary = db.execute(
            """SELECT COUNT(*) redemptions, COALESCE(SUM(fee_charged), 0) fees,
                      COUNT(DISTINCT deal_id) converting_deals
               FROM ledger_entries WHERE business_id = ?""",
            (business["id"],),
        ).fetchone()
        deal_total = db.execute("SELECT COUNT(*) total FROM deals WHERE business_id = ?", (business["id"],)).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(deal_total)
        deal_performance = db.execute(
            """SELECT deals.id, deals.title, deals.redemption_count, deals.redemption_limit,
                      deals.is_active, deals.expires_at, COUNT(codes.id) claims,
                      SUM(CASE WHEN codes.status = 'redeemed' THEN 1 ELSE 0 END) redemptions
               FROM deals LEFT JOIN codes ON codes.deal_id = deals.id
               WHERE deals.business_id = ? GROUP BY deals.id ORDER BY redemptions DESC, claims DESC
               LIMIT ? OFFSET ?""",
            (business["id"], per_page, offset),
        ).fetchall()
        daily_total = db.execute(
            "SELECT COUNT(DISTINCT substr(created_at, 1, 10)) total FROM ledger_entries WHERE business_id = ? AND created_at >= ?",
            (business["id"], timestamp(utcnow() - timedelta(days=30))),
        ).fetchone()["total"]
        daily_page, daily_pages, daily_limit, daily_offset = page_window(daily_total, "daily_page")
        daily = db.execute(
            """SELECT substr(created_at, 1, 10) day, COUNT(*) redemptions, SUM(fee_charged) fees
               FROM ledger_entries WHERE business_id = ? AND created_at >= ?
               GROUP BY day ORDER BY day DESC LIMIT ? OFFSET ?""",
            (business["id"], timestamp(utcnow() - timedelta(days=30)), daily_limit, daily_offset),
        ).fetchall()
        claims = db.execute(
            "SELECT COUNT(*) total FROM codes JOIN deals ON deals.id = codes.deal_id WHERE deals.business_id = ?",
            (business["id"],),
        ).fetchone()["total"]
        conversion_rate = round((summary["redemptions"] / claims * 100), 1) if claims else 0
        return render_template("business_analytics.html", business=business, summary=summary,
                               deals=deal_performance, daily=daily, claims=claims,
                               conversion_rate=conversion_rate, page=page,
                               total_pages=total_pages, total=deal_total,
                               daily_page=daily_page, daily_pages=daily_pages, daily_total=daily_total)

    @app.route("/business/products", methods=("GET", "POST"))
    @approved_business_required
    def business_products():
        business = business_for_user(current_user()["id"])
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            product_url = request.form.get("product_url", "").strip()
            price = request.form.get("price", "").strip()
            try:
                price_kobo = int((Decimal(price).quantize(Decimal("0.01"))) * 100) if price else None
            except (InvalidOperation, ValueError, OverflowError):
                price_kobo = -1
            if not (2 <= len(name) <= 120 and 10 <= len(description) <= 1200 and
                    price_kobo is not None and 0 <= price_kobo <= 1_000_000_000 and valid_product_url(product_url)):
                flash("Enter a product name, description, non-negative price, and an optional valid product link.", "danger")
            else:
                db = get_db()
                images = []
                try:
                    images = save_product_images(request.files.getlist("images"), business["id"])
                    db.execute("BEGIN IMMEDIATE")
                    cursor = db.execute(
                        "INSERT INTO products (business_id, name, description, price_kobo, product_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (business["id"], name, description, price_kobo, product_url or None, timestamp()),
                    )
                    for order, image in enumerate(images, start=1):
                        db.execute(
                            """INSERT INTO product_images
                               (product_id, file_name, secure_url, public_id, storage_provider, sort_order, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (cursor.lastrowid, image["file_name"], image["secure_url"], image["public_id"],
                             image["storage_provider"], order, timestamp()),
                        )
                    db.commit()
                except (OSError, ValueError) + DATABASE_ERRORS as error:
                    db.rollback()
                    cleanup_product_images(images)
                    flash(str(error) or "Could not save your product images.", "danger")
                else:
                    flash("Product published.", "success")
                    return redirect(url_for("business_products"))
        db = get_db()
        total = db.execute("SELECT COUNT(*) total FROM products WHERE business_id = ?", (business["id"],)).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        products = db.execute(
            "SELECT * FROM products WHERE business_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (business["id"], per_page, offset),
        ).fetchall()
        return render_template("business_products.html", business=business, products=products,
                               page=page, total_pages=total_pages, total=total)

    @app.route("/business/deals/new", methods=("GET", "POST"))
    @approved_business_required
    def create_deal():
        business = business_for_user(current_user()["id"])
        if request.method == "POST":
            title = request.form.get("title", "").strip(); description = request.form.get("description", "").strip(); terms = request.form.get("terms", "").strip(); category = request.form.get("category", ""); expiry = request.form.get("expires_at", ""); limit = request.form.get("redemption_limit", "")
            try: limit = int(limit)
            except ValueError: limit = 0
            try: expires_at = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
            except ValueError: expires_at = None
            if not (3 <= len(title) <= 120 and 10 <= len(description) <= 1200 and 5 <= len(terms) <= 1200 and category in category_names() and 1 <= limit <= 100000 and expires_at and expires_at > utcnow()):
                flash("Check all deal details: use valid text, a future expiry date, category, and redemption limit.", "danger")
            else:
                get_db().execute("INSERT INTO deals (business_id, title, description, category, terms, expires_at, redemption_limit, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (business["id"], title, description, category, terms, timestamp(expires_at), limit, timestamp()))
                flash("Deal published.", "success"); return redirect(url_for("business_dashboard"))
        return render_template("create_deal.html", business=business, categories=category_names(), fee=redemption_fee(business=business))

    @app.route("/business/wallet", methods=("GET", "POST"))
    @approved_business_required
    def wallet():
        business = business_for_user(current_user()["id"])
        if request.method == "POST":
            threshold = request.form.get("low_balance_threshold", "")
            if threshold:
                try: threshold_value = int(threshold)
                except ValueError: threshold_value = None
                if threshold_value is None or not 0 <= threshold_value <= 10_000_000:
                    flash("Low-balance alert must be between ₦0 and ₦10,000,000.", "danger")
                else:
                    get_db().execute("UPDATE businesses SET low_balance_threshold = ? WHERE id = ?", (threshold_value, business["id"]))
                    flash("Low-balance alert updated.", "success")
                    return redirect(url_for("wallet"))
            try: amount = int(request.form.get("amount", "0"))
            except ValueError: amount = 0
            reference = request.form.get("reference", "").strip().upper()
            if not 500 <= amount <= 10_000_000 or not REFERENCE_RE.fullmatch(reference):
                flash("Enter a top-up amount from ₦500 and a valid bank transfer reference.", "danger")
            else:
                try:
                    get_db().execute("INSERT INTO wallet_transactions (business_id, amount, kind, status, reference, note, created_at) VALUES (?, ?, 'topup', 'pending', ?, ?, ?)", (business["id"], amount, reference, request.form.get("note", "").strip()[:250], timestamp()))
                    flash("Top-up request submitted. Your wallet will update after payment verification.", "success")
                except INTEGRITY_ERRORS: flash("That transfer reference has already been submitted.", "danger")
                return redirect(url_for("wallet"))
        db = get_db()
        total = db.execute("SELECT COUNT(*) total FROM wallet_transactions WHERE business_id = ?", (business["id"],)).fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        transactions = db.execute("SELECT * FROM wallet_transactions WHERE business_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (business["id"], per_page, offset)).fetchall()
        return render_template("wallet.html", business=business, transactions=transactions,
                               page=page, total_pages=total_pages, total=total,
                               paystack_enabled=bool(app.config["PAYSTACK_SECRET_KEY"]))

    @app.post("/business/wallet/paystack")
    @approved_business_required
    def initialize_paystack_topup():
        business = business_for_user(current_user()["id"])
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        if not 500 <= amount <= 10_000_000:
            flash("Enter a Paystack top-up amount between ₦500 and ₦10,000,000.", "danger")
            return redirect(url_for("wallet"))
        secret_key = app.config["PAYSTACK_SECRET_KEY"]
        if not secret_key:
            flash("Paystack is not configured yet. Add the Paystack secret key to the server environment.", "warning")
            return redirect(url_for("wallet"))
        reference = f"PSTK-{business['id']}-{secrets.token_hex(10).upper()}"
        db = get_db()
        db.execute(
            """INSERT INTO wallet_transactions
               (business_id, amount, kind, status, reference, note, created_at)
               VALUES (?, ?, 'topup', 'pending', ?, 'Paystack wallet top-up', ?)""",
            (business["id"], amount, reference, timestamp()),
        )
        callback_url = app.config["PAYSTACK_CALLBACK_URL"] or url_for("paystack_callback", _external=True)
        try:
            result = initialize_transaction(
                secret_key,
                app.config["PAYSTACK_API_BASE"],
                email=current_user()["email"],
                amount_kobo=amount * 100,
                reference=reference,
                callback_url=callback_url,
                business_id=business["id"],
            )
            authorization_url = result.get("authorization_url", "")
            returned_reference = result.get("reference", reference)
            parsed_url = urlparse(authorization_url)
            if returned_reference != reference or parsed_url.scheme != "https" or parsed_url.hostname != "checkout.paystack.com":
                raise PaystackError("Paystack returned an invalid checkout response.")
        except PaystackError as error:
            db.execute("UPDATE wallet_transactions SET status = 'rejected' WHERE reference = ?", (reference,))
            flash(str(error), "danger")
            return redirect(url_for("wallet"))
        return redirect(authorization_url)

    @app.get("/payments/paystack/callback")
    @roles_required("business")
    def paystack_callback():
        reference = request.args.get("reference", "").strip()
        transaction = get_db().execute(
            "SELECT * FROM wallet_transactions WHERE reference = ? AND business_id = ? AND kind = 'topup'",
            (reference, business_for_user(current_user()["id"])["id"]),
        ).fetchone()
        if not transaction or not reference.startswith("PSTK-"):
            flash("That Paystack payment reference is not valid for this wallet.", "danger")
            return redirect(url_for("wallet"))
        try:
            payment = verify_transaction(app.config["PAYSTACK_SECRET_KEY"], app.config["PAYSTACK_API_BASE"], reference)
        except PaystackError as error:
            flash(str(error), "warning")
            return redirect(url_for("wallet"))
        if apply_paystack_topup(payment):
            flash("Payment verified. Your wallet has been credited.", "success")
        else:
            flash("The payment is not successful yet or its details do not match this top-up.", "warning")
        return redirect(url_for("wallet"))

    @app.post("/payments/paystack/webhook")
    def paystack_webhook():
        secret_key = app.config["PAYSTACK_SECRET_KEY"]
        raw_body = request.get_data(cache=True)
        received_signature = request.headers.get("x-paystack-signature", "")
        expected_signature = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest() if secret_key else ""
        if not expected_signature or not hmac.compare_digest(received_signature, expected_signature):
            abort(401)
        event = request.get_json(silent=True) or {}
        if event.get("event") == "charge.success":
            apply_paystack_topup(event.get("data"))
        return "", 200

    @app.route("/business/redeem", methods=("GET", "POST"))
    @approved_business_required
    def redeem():
        business = business_for_user(current_user()["id"])
        if request.method == "POST":
            value = request.form.get("code", "").strip().upper()
            if not CODE_RE.fullmatch(value):
                flash("Enter a valid Locatediscount code.", "danger")
                return render_template("redeem.html", business=business, fee=redemption_fee(business=business))
            db = get_db()
            try:
                fee = redemption_fee(db, business)
                balance_after = redeem_code(db, business, value, current_user()["id"], timestamp(), fee)
                flash(f"Code validated. ₦{fee:,} deducted. New wallet balance: ₦{balance_after:,}.", "success")
            except ValueError as error:
                db.rollback(); flash(str(error), "danger")
        return render_template("redeem.html", business=business, fee=redemption_fee(business=business))

    @app.route("/admin")
    @roles_required("admin")
    def admin_dashboard():
        db = get_db()
        ledger_total = db.execute("SELECT COUNT(*) total FROM ledger_entries").fetchone()["total"]
        ledger_page, ledger_pages, limit, ledger_offset = page_window(ledger_total, "ledger_page")
        ledger = db.execute("SELECT ledger_entries.*, businesses.name business_name, codes.value code, deals.title FROM ledger_entries JOIN businesses ON businesses.id = ledger_entries.business_id JOIN codes ON codes.id = ledger_entries.code_id JOIN deals ON deals.id = ledger_entries.deal_id ORDER BY ledger_entries.created_at DESC LIMIT ? OFFSET ?", (limit, ledger_offset)).fetchall()
        topup_total = db.execute("SELECT COUNT(*) total FROM wallet_transactions WHERE status = 'pending' AND reference NOT LIKE 'PSTK-%'").fetchone()["total"]
        topup_page, topup_pages, limit, topup_offset = page_window(topup_total, "topup_page")
        topups = db.execute("SELECT wallet_transactions.*, businesses.name business_name FROM wallet_transactions JOIN businesses ON businesses.id = wallet_transactions.business_id WHERE status = 'pending' AND reference NOT LIKE 'PSTK-%' ORDER BY created_at ASC LIMIT ? OFFSET ?", (limit, topup_offset)).fetchall()
        business_total = db.execute("SELECT COUNT(*) total FROM businesses").fetchone()["total"]
        wallet_page, wallet_pages, limit, wallet_offset = page_window(business_total, "wallet_page")
        businesses = db.execute("SELECT * FROM businesses ORDER BY wallet_balance ASC LIMIT ? OFFSET ?", (limit, wallet_offset)).fetchall()
        pending_business_count = db.execute(
            "SELECT COUNT(*) total FROM businesses WHERE is_approved = 0 AND is_blocked = 0"
        ).fetchone()["total"]
        revenue = db.execute("SELECT COALESCE(SUM(fee_charged), 0) total FROM ledger_entries").fetchone()["total"]
        redemption_count = db.execute("SELECT COUNT(*) total FROM ledger_entries").fetchone()["total"]
        return render_template("admin_dashboard.html", ledger=ledger, topups=topups, businesses=businesses,
                               revenue=revenue, redemption_count=redemption_count, fee=redemption_fee(db),
                               pending_business_count=pending_business_count, business_total=business_total,
                               ledger_page=ledger_page, ledger_pages=ledger_pages,
                               topup_page=topup_page, topup_pages=topup_pages,
                               wallet_page=wallet_page, wallet_pages=wallet_pages,
                               chart=daily_redemption_series(db))

    @app.route("/admin/businesses")
    @roles_required("admin")
    def admin_businesses():
        per_page = 5
        db = get_db()
        query = request.args.get("q", "").strip()
        status = request.args.get("status", "all")
        if status not in {"all", "pending", "approved", "blocked"}:
            status = "all"
        where = ["1 = 1"]
        params = []
        if query:
            where.append("(businesses.name LIKE ? OR businesses.city LIKE ? OR businesses.category LIKE ? OR users.email LIKE ?)")
            params.extend([f"%{query}%"] * 4)
        if status == "pending": where.append("businesses.is_approved = 0 AND businesses.is_blocked = 0")
        elif status == "approved": where.append("businesses.is_approved = 1 AND businesses.is_blocked = 0")
        elif status == "blocked": where.append("businesses.is_blocked = 1")
        where_sql = " AND ".join(where)
        total_businesses = db.execute(
            f"SELECT COUNT(*) total FROM businesses JOIN users ON users.id = businesses.owner_id WHERE {where_sql}", params
        ).fetchone()["total"]
        total_pages = max(1, (total_businesses + per_page - 1) // per_page)
        page = max(request.args.get("page", 1, type=int) or 1, 1)
        page = min(page, total_pages)
        businesses = db.execute(
            f"""SELECT businesses.*, users.email owner_email, users.name owner_name, users.phone owner_phone,
                      (SELECT COUNT(*) FROM deals WHERE deals.business_id = businesses.id) deal_count,
                      (SELECT COUNT(*) FROM products WHERE products.business_id = businesses.id) product_count,
                      (SELECT COUNT(*) FROM ledger_entries WHERE ledger_entries.business_id = businesses.id) redemption_count
               FROM businesses JOIN users ON users.id = businesses.owner_id
               WHERE {where_sql}
               ORDER BY businesses.is_blocked ASC, businesses.is_approved ASC, businesses.created_at DESC LIMIT ? OFFSET ?""",
            (*params, per_page, (page - 1) * per_page),
        ).fetchall()
        status_counts = db.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN is_approved = 0 AND is_blocked = 0 THEN 1 ELSE 0 END) pending,
                      SUM(CASE WHEN is_approved = 1 AND is_blocked = 0 THEN 1 ELSE 0 END) approved,
                      SUM(CASE WHEN is_blocked = 1 THEN 1 ELSE 0 END) blocked
               FROM businesses"""
        ).fetchone()
        return render_template("admin_businesses.html", businesses=businesses, default_fee=redemption_fee(),
                               page=page, total_pages=total_pages, total_businesses=total_businesses,
                               status_counts=status_counts, query=query, status=status)

    @app.route("/admin/analytics")
    @roles_required("admin")
    def admin_analytics():
        db = get_db()
        summary = db.execute(
            """SELECT (SELECT COUNT(*) FROM users WHERE is_active = 1) active_users,
                      (SELECT COUNT(*) FROM businesses WHERE is_approved = 1 AND is_blocked = 0) approved_businesses,
                      (SELECT COUNT(*) FROM deals WHERE is_active = 1 AND expires_at > ?) live_deals,
                      (SELECT COUNT(*) FROM ledger_entries) redemptions,
                      (SELECT COALESCE(SUM(fee_charged), 0) FROM ledger_entries) revenue""",
            (timestamp(),),
        ).fetchone()
        daily_total = db.execute(
            "SELECT COUNT(DISTINCT substr(created_at, 1, 10)) total FROM ledger_entries WHERE created_at >= ?",
            (timestamp(utcnow() - timedelta(days=30)),),
        ).fetchone()["total"]
        daily_page, daily_pages, per_page, daily_offset = page_window(daily_total, "daily_page")
        daily = db.execute(
            """SELECT substr(created_at, 1, 10) day, COUNT(*) redemptions, SUM(fee_charged) revenue
               FROM ledger_entries WHERE created_at >= ? GROUP BY day ORDER BY day DESC LIMIT ? OFFSET ?""",
            (timestamp(utcnow() - timedelta(days=30)), per_page, daily_offset),
        ).fetchall()
        top_total = db.execute("SELECT COUNT(*) total FROM businesses").fetchone()["total"]
        top_page, top_pages, per_page, top_offset = page_window(top_total, "top_page")
        top_businesses = db.execute(
            """SELECT businesses.name, businesses.city, COUNT(ledger_entries.id) redemptions,
                      COALESCE(SUM(ledger_entries.fee_charged), 0) revenue
               FROM businesses LEFT JOIN ledger_entries ON ledger_entries.business_id = businesses.id
               GROUP BY businesses.id ORDER BY redemptions DESC, revenue DESC LIMIT ? OFFSET ?""",
            (per_page, top_offset),
        ).fetchall()
        category_total = db.execute("SELECT COUNT(DISTINCT category) total FROM deals").fetchone()["total"]
        category_page, category_pages, per_page, category_offset = page_window(category_total, "category_page")
        category_performance = db.execute(
            """SELECT deals.category, COUNT(DISTINCT deals.id) deals,
                      COUNT(ledger_entries.id) redemptions
               FROM deals LEFT JOIN ledger_entries ON ledger_entries.deal_id = deals.id
               GROUP BY deals.category ORDER BY redemptions DESC, deals DESC LIMIT ? OFFSET ?""",
            (per_page, category_offset),
        ).fetchall()
        return render_template("admin_analytics.html", summary=summary, daily=daily,
                               top_businesses=top_businesses, category_performance=category_performance,
                               daily_page=daily_page, daily_pages=daily_pages, daily_total=daily_total,
                               top_page=top_page, top_pages=top_pages, top_total=top_total,
                               category_page=category_page, category_pages=category_pages,
                               category_total=category_total)

    @app.route("/admin/categories", methods=("GET", "POST"))
    @roles_required("admin")
    def admin_categories():
        db = get_db()
        if request.method == "POST":
            name = " ".join(request.form.get("name", "").strip().split())
            if not 2 <= len(name) <= 60:
                flash("Category names must be between 2 and 60 characters.", "danger")
            else:
                try:
                    cursor = db.execute(
                        "INSERT INTO categories (name, created_at, created_by) VALUES (?, ?, ?)",
                        (name, timestamp(), current_user()["id"]),
                    )
                    record_admin_action("create_category", "category", cursor.lastrowid, name)
                    flash("Category created and available to businesses.", "success")
                    return redirect(url_for("admin_categories"))
                except INTEGRITY_ERRORS:
                    flash("That category already exists.", "danger")
        total = db.execute("SELECT COUNT(*) total FROM categories").fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        categories = db.execute(
            """SELECT categories.*, users.name creator_name,
                      (SELECT COUNT(*) FROM businesses WHERE businesses.category = categories.name) business_count,
                      (SELECT COUNT(*) FROM deals WHERE deals.category = categories.name) deal_count
               FROM categories LEFT JOIN users ON users.id = categories.created_by
               ORDER BY categories.is_active DESC, LOWER(categories.name) LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        return render_template("admin_categories.html", categories=categories, page=page,
                               total_pages=total_pages, total=total)

    @app.post("/admin/categories/<int:category_id>/toggle")
    @roles_required("admin")
    def toggle_category(category_id):
        db = get_db()
        category = db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        if not category:
            abort(404)
        new_state = int(not category["is_active"])
        db.execute("UPDATE categories SET is_active = ? WHERE id = ?", (new_state, category_id))
        record_admin_action("activate_category" if new_state else "archive_category", "category", category_id, category["name"])
        flash("Category availability updated.", "success")
        return redirect(url_for("admin_categories"))

    @app.route("/admin/team", methods=("GET", "POST"))
    @roles_required("admin")
    def admin_team():
        db = get_db()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            errors = []
            if not 2 <= len(name) <= 80: errors.append("Name must be between 2 and 80 characters.")
            if not EMAIL_RE.match(email): errors.append("Enter a valid email address.")
            if not PHONE_RE.match(phone): errors.append("Enter a valid phone number.")
            if not valid_password(password): errors.append("Temporary password must be at least 12 characters with a letter and number.")
            if errors:
                for error in errors: flash(error, "danger")
            else:
                try:
                    cursor = db.execute(
                        """INSERT INTO users (email, phone, password_hash, role, name, is_active, created_at, created_by_admin)
                           VALUES (?, ?, ?, 'admin', ?, 1, ?, ?)""",
                        (email, phone, generate_password_hash(password), name, timestamp(), current_user()["id"]),
                    )
                    record_admin_action("create_sub_admin", "user", cursor.lastrowid, f"Created administrator {email}")
                    flash("Sub-admin account created.", "success")
                    return redirect(url_for("admin_team"))
                except INTEGRITY_ERRORS:
                    flash("That email or phone number is already registered.", "danger")
        total = db.execute("SELECT COUNT(*) total FROM users WHERE role = 'admin'").fetchone()["total"]
        page, total_pages, per_page, offset = page_window(total)
        admins = db.execute(
            """SELECT users.*, creator.name creator_name FROM users
               LEFT JOIN users creator ON creator.id = users.created_by_admin
               WHERE users.role = 'admin' ORDER BY users.created_at ASC LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()
        return render_template("admin_team.html", admins=admins, page=page,
                               total_pages=total_pages, total=total)

    @app.post("/admin/team/<int:user_id>/toggle")
    @roles_required("admin")
    def toggle_admin(user_id):
        if user_id == current_user()["id"]:
            flash("You cannot deactivate your own account.", "danger")
            return redirect(url_for("admin_team"))
        db = get_db()
        admin = db.execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if not admin:
            abort(404)
        if not admin["created_by_admin"]:
            flash("Primary administrator accounts cannot be deactivated here.", "danger")
            return redirect(url_for("admin_team"))
        new_state = int(not admin["is_active"])
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
        record_admin_action("activate_sub_admin" if new_state else "deactivate_sub_admin", "user", user_id, admin["email"])
        flash("Administrator access updated.", "success")
        return redirect(url_for("admin_team"))

    @app.route("/admin/audit-logs")
    @roles_required("admin")
    def admin_audit_logs():
        page = max(request.args.get("page", 1, type=int) or 1, 1)
        per_page = 5
        db = get_db()
        total = db.execute("SELECT COUNT(*) total FROM admin_audit_logs").fetchone()["total"]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        logs = db.execute(
            """SELECT admin_audit_logs.*, users.name admin_name, users.email admin_email
               FROM admin_audit_logs JOIN users ON users.id = admin_audit_logs.admin_id
               ORDER BY admin_audit_logs.created_at DESC LIMIT ? OFFSET ?""",
            (per_page, (page - 1) * per_page),
        ).fetchall()
        return render_template("admin_audit_logs.html", logs=logs, page=page,
                               total_pages=total_pages, total=total)

    @app.post("/admin/businesses/<int:business_id>/status")
    @roles_required("admin")
    def update_business_status(business_id):
        action = request.form.get("action", "")
        states = {"approve": (1, 0), "block": (0, 1), "restore": (1, 0)}
        if action not in states:
            abort(400)
        db = get_db()
        business = db.execute("SELECT id FROM businesses WHERE id = ?", (business_id,)).fetchone()
        if not business:
            abort(404)
        approved, blocked = states[action]
        db.execute("UPDATE businesses SET is_approved = ?, is_blocked = ? WHERE id = ?", (approved, blocked, business_id))
        db.execute(
            "INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, ?, 'business', ?, ?, ?)",
            (current_user()["id"], "business_" + action, business_id, action, timestamp()),
        )
        flash("Business access updated.", "success")
        return redirect(url_for("admin_businesses"))

    @app.post("/admin/topups/<int:transaction_id>/approve")
    @roles_required("admin")
    def approve_topup(transaction_id):
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            transaction = db.execute("SELECT * FROM wallet_transactions WHERE id = ? AND kind = 'topup' AND status = 'pending' AND reference NOT LIKE 'PSTK-%'", (transaction_id,)).fetchone()
            if not transaction: raise ValueError("This top-up is no longer pending.")
            db.execute("UPDATE wallet_transactions SET status = 'approved', approved_at = ?, approved_by = ? WHERE id = ?", (timestamp(), current_user()["id"], transaction_id))
            db.execute("UPDATE businesses SET wallet_balance = wallet_balance + ?, needs_top_up = 0 WHERE id = ?", (transaction["amount"], transaction["business_id"]))
            db.execute("INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, 'approve_topup', 'wallet_transaction', ?, ?, ?)", (current_user()["id"], transaction_id, transaction["reference"], timestamp()))
            db.commit(); flash("Top-up approved and wallet credited.", "success")
        except ValueError as error:
            db.rollback(); flash(str(error), "danger")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/fee-rule")
    @roles_required("admin")
    def update_fee_rule():
        try:
            fee = int(request.form.get("fee", "0"))
        except ValueError:
            fee = 0
        if not 1 <= fee <= 100_000:
            flash("Redemption fee must be between ₦1 and ₦100,000.", "danger")
            return redirect(url_for("admin_dashboard"))
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE platform_settings SET integer_value = ?, updated_at = ?, updated_by = ? WHERE setting_key = 'redemption_fee'", (fee, timestamp(), current_user()["id"]))
        db.execute("INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, 'update_fee', 'platform_setting', 0, ?, ?)", (current_user()["id"], str(fee), timestamp()))
        db.commit()
        flash("Default redemption fee updated. New redemptions will use this amount.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/businesses/<int:business_id>/adjust-wallet")
    @roles_required("admin")
    def adjust_wallet(business_id):
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        note = request.form.get("note", "").strip()
        if not -10_000_000 <= amount <= 10_000_000 or amount == 0 or not 5 <= len(note) <= 250:
            flash("Enter a non-zero adjustment and a 5 to 250 character reason.", "danger")
            return redirect(url_for("admin_dashboard"))
        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
            if not business:
                raise ValueError("Business not found.")
            balance_after = business["wallet_balance"] + amount
            db.execute("UPDATE businesses SET wallet_balance = ?, needs_top_up = ? WHERE id = ?", (balance_after, int(balance_after < business["low_balance_threshold"]), business_id))
            reference = "ADJ-" + secrets.token_hex(8).upper()
            db.execute("INSERT INTO wallet_transactions (business_id, amount, kind, status, reference, note, created_at, approved_at, approved_by) VALUES (?, ?, 'adjustment', 'posted', ?, ?, ?, ?, ?)", (business_id, amount, reference, note, timestamp(), timestamp(), current_user()["id"]))
            db.execute("INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, 'adjust_wallet', 'business', ?, ?, ?)", (current_user()["id"], business_id, f"{amount}: {note}", timestamp()))
            db.commit()
            flash("Wallet adjustment recorded.", "success")
        except ValueError as error:
            db.rollback(); flash(str(error), "danger")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/businesses/<int:business_id>/fee")
    @roles_required("admin")
    def update_business_fee(business_id):
        raw_fee = request.form.get("fee", "").strip()
        try:
            fee = int(raw_fee) if raw_fee else None
        except ValueError:
            fee = -1
        if fee is not None and not 1 <= fee <= 100_000:
            flash("Business fee must be between ₦1 and ₦100,000, or blank to use the default.", "danger")
            return redirect(url_for("admin_businesses"))
        db = get_db()
        if not db.execute("SELECT 1 FROM businesses WHERE id = ?", (business_id,)).fetchone():
            abort(404)
        db.execute("UPDATE businesses SET redemption_fee = ? WHERE id = ?", (fee, business_id))
        db.execute(
            "INSERT INTO admin_audit_logs (admin_id, action, target_type, target_id, details, created_at) VALUES (?, 'update_business_fee', 'business', ?, ?, ?)",
            (current_user()["id"], business_id, str(fee) if fee is not None else "default", timestamp()),
        )
        flash("Business fee rule updated. It applies to future redemptions.", "success")
        return redirect(url_for("admin_businesses"))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def error_page(error):
        return render_template("error.html", error=error), error.code

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(error):
        return render_template("error.html", error=error), 413

    # Apply additive, idempotent schema upgrades automatically on startup.
    with app.app_context():
        init_db()

    return app


app = create_app()
