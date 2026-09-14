"""End-to-end tests for the public workflows and immutable billing path."""

import sqlite3
import tempfile
import unittest
import hashlib
import hmac
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app, timestamp


class LocatediscountWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": self.database,
            "UPLOAD_FOLDER": str(Path(self.temp_dir.name) / "product-uploads"),
        })
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf(self):
        self.client.get("/")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def post(self, path, data, **kwargs):
        return self.client.post(path, data={"csrf_token": self.csrf(), **data}, **kwargs)

    def logout(self):
        return self.post("/logout", {})

    def login(self, email, password):
        return self.post("/login", {"email": email, "password": password}, follow_redirects=True)

    def create_admin(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            """INSERT INTO users (email, phone, password_hash, role, name, created_at)
               VALUES (?, ?, ?, 'admin', ?, ?)""",
            ("admin@example.com", "+2348011111111", generate_password_hash("AdminPassword123"), "Admin", timestamp()),
        )
        connection.commit()
        connection.close()

    def test_registration_topup_claim_and_redemption_are_consistent(self):
        response = self.post(
            "/register",
            {
                "role": "business", "name": "Merchant Owner", "email": "merchant@example.com",
                "phone": "+2348012345678", "password": "MerchantPassword123", "business_name": "Fresh Bowl",
                "category": "Food & Drink", "address": "12 Allen Avenue", "city": "Lagos",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Fresh Bowl", response.data)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE businesses SET is_approved = 1 WHERE name = 'Fresh Bowl'")
        connection.commit()
        connection.close()

        response = self.post(
            "/business/products",
            {
                "name": "Lunch bowl", "description": "A complete lunch bowl with rice, protein, and vegetables.",
                "price": "4500.00", "product_url": "",
                "images": (BytesIO(b"\x89PNG\r\n\x1a\nproduct-image"), "lunch.png"),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Product published", response.data)
        connection = sqlite3.connect(self.database)
        product_id = connection.execute("SELECT id FROM products WHERE name = 'Lunch bowl'").fetchone()[0]
        image_name = connection.execute("SELECT file_name FROM product_images WHERE product_id = ?", (product_id,)).fetchone()[0]
        connection.close()
        self.assertTrue((Path(self.app.config["UPLOAD_FOLDER"]) / image_name).is_file())
        product_redirect = self.client.get(f"/products/{product_id}")
        self.assertEqual(product_redirect.status_code, 302)
        suggestions = self.client.get("/search/suggestions?q=clothes").get_json()["suggestions"]
        self.assertIn({"label": "Clothes & fashion", "kind": "category", "value": "Shopping", "detail": "Category"}, suggestions)

        response = self.post(
            "/business/deals/new",
            {
                "title": "20% off lunch", "category": "Food & Drink", "expires_at": "2030-12-31T17:00",
                "description": "Twenty percent off any weekday lunch order.", "redemption_limit": "10",
                "terms": "Valid Monday to Friday only.", "regular_price": "2500", "discount_price": "2000",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Deal submitted for admin approval", response.data)
        connection = sqlite3.connect(self.database)
        created_deal_id = connection.execute("SELECT id FROM deals WHERE title = '20% off lunch'").fetchone()[0]
        connection.close()
        response = self.post("/business/wallet", {"amount": "5000", "reference": "BANK-TEST-001", "note": "Pilot funding"})
        self.assertEqual(response.status_code, 302)
        self.logout()

        self.create_admin()
        self.login("admin@example.com", "AdminPassword123")
        connection = sqlite3.connect(self.database)
        transaction_id = connection.execute("SELECT id FROM wallet_transactions WHERE reference = 'BANK-TEST-001'").fetchone()[0]
        connection.close()
        response = self.post(f"/admin/topups/{transaction_id}/approve", {}, follow_redirects=True)
        self.assertIn(b"Top-up approved", response.data)
        response = self.post(f"/admin/deals/{created_deal_id}/approve", {}, follow_redirects=True)
        self.assertIn(b"Deal approved and live", response.data)
        self.logout()

        home_search = self.client.get("/?q=lunch&area=Lagos")
        self.assertIn(b"20% off lunch", home_search.data)
        self.assertNotIn(b"Lunch bowl", home_search.data)
        empty_category_page = self.client.get("/deals?category=Beauty")
        self.assertEqual(empty_category_page.status_code, 200)
        self.assertIn(b"No deals in Beauty yet", empty_category_page.data)
        suggestions = self.client.get("/search/suggestions?q=Lunch").get_json()["suggestions"]
        self.assertTrue(any(item["value"] == "20% off lunch" and item["detail"] == "Deal" for item in suggestions))
        deal_page = self.client.get(f"/deals/{created_deal_id}")
        self.assertIn(b"Get your voucher", deal_page.data)
        conflicting_phone = self.post(
            f"/deals/{created_deal_id}/claim",
            {"name": "Merchant Owner", "phone": "+2348012345678", "area": "Ikeja"},
        )
        self.assertEqual(conflicting_phone.status_code, 409)
        self.assertIn(b"belongs to a business or administrator account", conflicting_phone.data)
        self.assertIn(b'value="+2348012345678"', conflicting_phone.data)

        connection = sqlite3.connect(self.database)
        deal_id = connection.execute("SELECT id FROM deals").fetchone()[0]
        connection.close()
        response = self.client.get(f"/deals/{deal_id}")
        self.assertIn(b"Claim this deal", response.data)
        response = self.client.get(f"/deals/{deal_id}/claim")
        self.assertIn(b"A few details first", response.data)
        response = self.post(
            f"/deals/{deal_id}/claim",
            {"name": "Test Customer", "phone": "+2348099999999", "area": "Ikeja"},
            follow_redirects=True,
        )
        self.assertIn(b"Show this code to the business", response.data)
        self.assertIn(b"<svg", response.data)

        response = self.client.get("/my-codes")
        self.assertIn(b"Your local savings", response.data)
        self.assertIn(b"Test Customer", response.data)

        connection = sqlite3.connect(self.database)
        code_value = connection.execute("SELECT value FROM codes").fetchone()[0]
        code_owner = connection.execute("SELECT users.name FROM codes JOIN users ON users.id = codes.user_id").fetchone()[0]
        connection.close()
        self.assertEqual(code_owner, "Test Customer")

        # A device may claim different deals, but can never generate a second
        # voucher for the same deal (including after its first code is redeemed).
        connection = sqlite3.connect(self.database)
        business_id = connection.execute("SELECT id FROM businesses WHERE name = 'Fresh Bowl'").fetchone()[0]
        connection.execute(
            """INSERT INTO deals (business_id, title, description, category, terms, expires_at,
               redemption_limit, is_active, is_approved, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)""",
            (business_id, "Free drink with dinner", "A free drink with every dinner order this week.",
             "Food & Drink", "One drink per qualifying dinner order.", "2030-12-31T17:00:00+00:00", 10, timestamp()),
        )
        second_deal_id = connection.execute(
            "SELECT id FROM deals WHERE title = 'Free drink with dinner'"
        ).fetchone()[0]
        connection.commit()
        connection.close()
        response = self.post(f"/deals/{second_deal_id}/claim", {}, follow_redirects=True)
        self.assertIn(b"Show this code to the business", response.data)
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM codes").fetchone()[0], 2)
        connection.close()
        response = self.post(f"/deals/{deal_id}/claim", {}, follow_redirects=True)
        self.assertIn(b"already generated a voucher for this deal", response.data)

        self.post(
            "/register",
            {
                "name": "Other Owner", "email": "other@example.com", "phone": "+2348012345677",
                "password": "OtherPassword123", "business_name": "Other Shop", "category": "Shopping",
                "address": "24 Allen Avenue", "city": "Lagos",
            },
            follow_redirects=True,
        )
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE businesses SET is_approved = 1 WHERE name = 'Other Shop'")
        connection.commit()
        connection.close()
        wrong_business = self.post("/business/redeem", {"code": code_value}, follow_redirects=True)
        self.assertIn(b"Code not found for this business", wrong_business.data)
        self.logout()
        self.login("merchant@example.com", "MerchantPassword123")
        self.assertIn(b"Business analytics", self.client.get("/business/analytics").data)
        response = self.post("/business/redeem", {"code": code_value}, follow_redirects=True)
        self.assertIn(b"Voucher validated", response.data)

        connection = sqlite3.connect(self.database)
        code_status = connection.execute("SELECT status FROM codes WHERE value = ?", (code_value,)).fetchone()[0]
        balance = connection.execute("SELECT wallet_balance FROM businesses").fetchone()[0]
        ledger_count = connection.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        platform_revenue = connection.execute("SELECT COALESCE(SUM(fee_charged), 0) FROM ledger_entries").fetchone()[0]
        wallet_entry_count = connection.execute("SELECT COUNT(*) FROM wallet_transactions WHERE kind = 'redemption'").fetchone()[0]
        connection.close()
        self.assertEqual(code_status, "redeemed")
        self.assertEqual(balance, 4500)
        self.assertEqual(ledger_count, 1)
        self.assertEqual(platform_revenue, 500)
        self.assertEqual(wallet_entry_count, 1)

        duplicate = self.post("/business/redeem", {"code": code_value}, follow_redirects=True)
        self.assertIn(b"expired or already redeemed", duplicate.data)
        self.assertEqual(self.client.get(f"/business/deals/{created_deal_id}/edit").status_code, 200)
        response = self.post(
            f"/business/deals/{created_deal_id}/edit",
            {
                "title": "20% off lunch", "category": "Food & Drink", "expires_at": "2030-12-31T17:00",
                "description": "An updated weekday lunch offer with rice, protein, and vegetables.",
                "redemption_limit": "10", "daily_voucher_limit": "5", "max_vouchers_per_customer": "1",
                "terms": "Valid Monday to Friday only.", "regular_price": "2500", "discount_price": "2000",
            },
            follow_redirects=True,
        )
        self.assertIn(b"submitted for admin approval again", response.data)
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT is_active, is_approved FROM deals WHERE id = ?", (created_deal_id,)).fetchone(), (0, 0))
        connection.close()

    def test_post_without_csrf_is_rejected(self):
        response = self.client.post("/login", data={"email": "any@example.com", "password": "not-used"})
        self.assertEqual(response.status_code, 400)

    def test_deleted_category_stays_deleted_after_database_reinitialization(self):
        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM categories WHERE name = 'Beauty'")
        connection.commit()
        connection.close()

        result = self.app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        connection = sqlite3.connect(self.database)
        self.assertIsNone(connection.execute("SELECT 1 FROM categories WHERE name = 'Beauty'").fetchone())
        connection.close()

    def test_demo_seed_is_disabled_without_explicit_opt_in(self):
        result = self.app.test_cli_runner().invoke(args=["seed-demo"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Demo seeding is disabled", result.output)

    def test_admin_can_remove_an_unused_pending_business(self):
        self.post(
            "/register",
            {
                "name": "Unverified Owner", "email": "unverified@example.com",
                "phone": "+2348012345699", "password": "UnverifiedPassword123",
                "business_name": "Unverified Shop", "category": "Shopping",
                "address": "10 Review Street", "city": "Lagos",
            },
        )
        connection = sqlite3.connect(self.database)
        business_id, owner_id = connection.execute(
            "SELECT id, owner_id FROM businesses WHERE name = 'Unverified Shop'"
        ).fetchone()
        connection.close()
        self.create_admin()
        self.logout()
        self.login("admin@example.com", "AdminPassword123")

        response = self.post(f"/admin/businesses/{business_id}/delete", {}, follow_redirects=True)
        self.assertIn(b"Unverified business registration removed", response.data)
        connection = sqlite3.connect(self.database)
        self.assertIsNone(connection.execute("SELECT 1 FROM businesses WHERE id = ?", (business_id,)).fetchone())
        self.assertIsNone(connection.execute("SELECT 1 FROM users WHERE id = ?", (owner_id,)).fetchone())
        connection.close()

    def test_admin_can_purge_legacy_demo_catalogue_without_activity(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            """INSERT INTO users (email, phone, password_hash, role, name, created_at)
               VALUES (?, ?, ?, 'business', ?, ?)""",
            ("demo-vendor-1@locatediscount.invalid", "+2348011111199",
             generate_password_hash("DemoVendorPassword123"), "Demo Owner", timestamp()),
        )
        owner_id = connection.execute(
            "SELECT id FROM users WHERE email = 'demo-vendor-1@locatediscount.invalid'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO businesses (owner_id, name, category, address, city, is_approved, created_at)
               VALUES (?, 'Legacy Demo Shop', 'Shopping', '12 Test Street', 'Lagos', 1, ?)""",
            (owner_id, timestamp()),
        )
        business_id = connection.execute(
            "SELECT id FROM businesses WHERE owner_id = ?", (owner_id,)
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO deals (business_id, title, description, category, terms, expires_at,
               redemption_limit, created_at) VALUES (?, 'Legacy deal', 'Demo description', 'Shopping',
               'Demo terms', '2030-12-31T17:00:00+00:00', 10, ?)""",
            (business_id, timestamp()),
        )
        connection.commit()
        connection.close()
        self.create_admin()
        self.login("admin@example.com", "AdminPassword123")

        response = self.post(f"/admin/businesses/{business_id}/purge-demo", {}, follow_redirects=True)
        self.assertIn(b"Legacy demo business and its catalogue removed", response.data)
        connection = sqlite3.connect(self.database)
        self.assertIsNone(connection.execute("SELECT 1 FROM businesses WHERE id = ?", (business_id,)).fetchone())
        self.assertIsNone(connection.execute("SELECT 1 FROM deals WHERE business_id = ?", (business_id,)).fetchone())
        connection.close()

    def test_pending_business_can_view_dashboard_but_cannot_operate(self):
        response = self.post(
            "/register",
            {
                "name": "Pending Owner", "email": "pending@example.com",
                "phone": "+2348012345600", "password": "PendingPassword123",
                "business_name": "Pending Shop", "category": "Shopping",
                "address": "10 Review Street", "city": "Lagos",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your business is under review", response.data)
        self.assertIn(b"Pending Shop", response.data)

        dashboard = self.client.get("/business")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Awaiting approval", dashboard.data)

        legacy_dashboard = self.client.get("/dashboard")
        self.assertEqual(legacy_dashboard.status_code, 200)
        self.assertIn(b"Your business is under review", legacy_dashboard.data)

        blocked_action = self.client.get("/business/deals/new", follow_redirects=True)
        self.assertEqual(blocked_action.status_code, 200)
        self.assertIn(b"Publishing, wallet, and redemption are disabled", blocked_action.data)
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0], 0)
        connection.close()

    def test_product_images_use_cloudinary_urls_when_configured(self):
        self.post(
            "/register",
            {
                "name": "Cloud Owner", "email": "cloud@example.com",
                "phone": "+2348012345622", "password": "CloudPassword123",
                "business_name": "Cloud Shop", "category": "Shopping",
                "address": "21 Media Avenue", "city": "Lagos",
            },
        )
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE businesses SET is_approved = 1 WHERE name = 'Cloud Shop'")
        connection.commit()
        connection.close()
        self.app.config.update(
            MEDIA_STORAGE="cloudinary",
            CLOUDINARY_CLOUD_NAME="test-cloud",
            CLOUDINARY_API_KEY="test-key",
            CLOUDINARY_API_SECRET="test-secret",
        )
        cloudinary_result = {
            "public_id": "locatediscount/products/1/cloud-image",
            "secure_url": "https://res.cloudinary.com/test-cloud/image/upload/cloud-image.png",
        }
        with patch("app.upload_product_image", return_value=cloudinary_result) as upload:
            response = self.post(
                "/business/products",
                {
                    "name": "Cloud product",
                    "description": "A product image stored safely using Cloudinary media storage.",
                    "price": "2500.00", "product_url": "",
                    "images": (BytesIO(b"\x89PNG\r\n\x1a\ncloud-image"), "cloud.png"),
                },
                follow_redirects=True,
            )
        self.assertIn(b"Product published", response.data)
        upload.assert_called_once()

        connection = sqlite3.connect(self.database)
        row = connection.execute(
            "SELECT secure_url, public_id, storage_provider FROM product_images"
        ).fetchone()
        product_id = connection.execute("SELECT id FROM products WHERE name = 'Cloud product'").fetchone()[0]
        connection.close()
        self.assertEqual(row, (cloudinary_result["secure_url"], cloudinary_result["public_id"], "cloudinary"))
        detail = self.client.get(f"/products/{product_id}")
        self.assertEqual(detail.status_code, 302)

    def test_paystack_topup_is_verified_and_credited_only_once(self):
        self.post(
            "/register",
            {
                "name": "Paystack Owner", "email": "paystack@example.com",
                "phone": "+2348012345611", "password": "PaystackPassword123",
                "business_name": "Paystack Shop", "category": "Shopping",
                "address": "15 Payment Avenue", "city": "Lagos",
            },
        )
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE businesses SET is_approved = 1 WHERE name = 'Paystack Shop'")
        connection.commit()
        connection.close()
        self.app.config["PAYSTACK_SECRET_KEY"] = "sk_test_unit_test"

        with patch("app.initialize_transaction") as initialize:
            initialize.return_value = {
                "authorization_url": "https://checkout.paystack.com/test-access",
                "reference": None,
            }
            # Paystack normally echoes the supplied reference; make the mock do the same.
            initialize.side_effect = lambda *args, **kwargs: {
                "authorization_url": "https://checkout.paystack.com/test-access",
                "reference": kwargs["reference"],
            }
            response = self.post("/business/wallet/paystack", {"amount": "5000"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://checkout.paystack.com/test-access")

        connection = sqlite3.connect(self.database)
        reference, business_id = connection.execute(
            "SELECT reference, business_id FROM wallet_transactions WHERE reference LIKE 'PSTK-%'"
        ).fetchone()
        connection.close()
        verified = {"reference": reference, "amount": 500000, "currency": "NGN", "status": "success"}
        with patch("app.verify_transaction", return_value=verified):
            self.client.get(f"/payments/paystack/callback?reference={reference}")
            self.client.get(f"/payments/paystack/callback?reference={reference}")

        connection = sqlite3.connect(self.database)
        balance = connection.execute("SELECT wallet_balance FROM businesses WHERE id = ?", (business_id,)).fetchone()[0]
        status = connection.execute("SELECT status FROM wallet_transactions WHERE reference = ?", (reference,)).fetchone()[0]
        connection.close()
        self.assertEqual(balance, 5000)
        self.assertEqual(status, "approved")

        # A correctly signed webhook is also idempotent and must not double-credit.
        payload = json.dumps({"event": "charge.success", "data": verified}, separators=(",", ":")).encode()
        signature = hmac.new(b"sk_test_unit_test", payload, hashlib.sha512).hexdigest()
        response = self.client.post(
            "/payments/paystack/webhook", data=payload, content_type="application/json",
            headers={"x-paystack-signature": signature},
        )
        self.assertEqual(response.status_code, 200)
        connection = sqlite3.connect(self.database)
        balance = connection.execute("SELECT wallet_balance FROM businesses WHERE id = ?", (business_id,)).fetchone()[0]
        connection.close()
        self.assertEqual(balance, 5000)

    def test_admin_tools_categories_audit_and_password_recovery(self):
        self.create_admin()
        response = self.login("admin@example.com", "AdminPassword123")
        self.assertIn(b"Platform performance snapshot", response.data)
        self.assertEqual(self.client.get("/account/password").status_code, 200)

        response = self.post("/admin/categories", {"name": "Events & Experiences"}, follow_redirects=True)
        self.assertIn(b"Category created", response.data)
        self.assertIn(b"Events &amp; Experiences", response.data)
        connection = sqlite3.connect(self.database)
        event_category_id = connection.execute(
            "SELECT id FROM categories WHERE name = 'Events & Experiences'"
        ).fetchone()[0]
        connection.close()
        response = self.post(f"/admin/categories/{event_category_id}/edit", {"name": "Events"}, follow_redirects=True)
        self.assertIn(b"Category updated", response.data)
        self.assertIn(b"Events", response.data)
        self.assertEqual(self.client.get("/policies/terms").status_code, 200)
        pdf_response = self.client.get("/policies/privacy/document")
        self.assertEqual(pdf_response.status_code, 200)
        pdf_response.close()

        response = self.post(
            "/admin/categories",
            {"name": "Dining", "image": (BytesIO(b"\x89PNG\r\n\x1a\nthumbnail"), "dining.png")},
            follow_redirects=True,
        )
        self.assertIn(b"Category created", response.data)
        connection = sqlite3.connect(self.database)
        category_id, image_name = connection.execute(
            "SELECT id, image_file_name FROM categories WHERE name = 'Dining'"
        ).fetchone()
        connection.close()
        self.assertTrue((Path(self.app.config["UPLOAD_FOLDER"]) / image_name).is_file())
        response = self.post(f"/admin/categories/{category_id}/delete", {}, follow_redirects=True)
        self.assertIn(b"Category deleted", response.data)
        self.assertFalse((Path(self.app.config["UPLOAD_FOLDER"]) / image_name).exists())

        response = self.post(
            "/admin/team",
            {
                "name": "Operations Admin", "email": "ops@example.com",
                "phone": "+2348022222222", "password": "TemporaryAdmin123",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Sub-admin account created", response.data)
        self.assertIn(b"ops@example.com", response.data)
        self.assertEqual(self.client.get("/admin/analytics").status_code, 200)
        audit_response = self.client.get("/admin/audit-logs")
        self.assertIn(b"create_sub_admin", audit_response.data)
        self.assertIn(b"create_category", audit_response.data)

        connection = sqlite3.connect(self.database)
        for index in range(6):
            connection.execute(
                """INSERT INTO users (email, phone, password_hash, role, name, created_at)
                   VALUES (?, ?, ?, 'business', ?, ?)""",
                (f"page-{index}@example.com", f"+23480300000{index:02d}",
                 generate_password_hash("BusinessPassword123"), f"Owner {index}", timestamp()),
            )
            owner_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", (f"page-{index}@example.com",)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO businesses (owner_id, name, category, address, city, created_at)
                   VALUES (?, ?, 'Shopping', '12 Test Street', 'Lagos', ?)""",
                (owner_id, f"Pagination Business {index}", timestamp()),
            )
        connection.commit()
        connection.close()
        first_page = self.client.get("/admin/businesses")
        second_page = self.client.get("/admin/businesses?page=2")
        self.assertEqual(first_page.data.count(b'data-business-id="'), 5)
        self.assertGreaterEqual(second_page.data.count(b'data-business-id="'), 1)

        self.logout()
        response = self.post("/forgot-password", {"email": "admin@example.com"}, follow_redirects=True)
        self.assertIn(b"password reset link has been sent", response.data)
        reset_url = self.app.extensions["password_reset_links"][-1][1]
        reset_path = reset_url.split("localhost", 1)[-1]
        response = self.post(
            reset_path,
            {"password": "ACompletelyNewPassword123", "password_confirmation": "ACompletelyNewPassword123"},
            follow_redirects=True,
        )
        self.assertIn(b"password has been reset", response.data)
        response = self.login("admin@example.com", "ACompletelyNewPassword123")
        self.assertIn(b"Platform performance snapshot", response.data)


if __name__ == "__main__":
    unittest.main()
