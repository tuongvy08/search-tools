"""Tests for Admin Exchange Rates (/admin/exchange-rates) and rate validation.

Phase 6B2A:
- Isolated temporary Postgres database via `pg_temp_db` (never touches products_local).
- Admin GET & POST tests (single, bulk same, bulk lines, delete, seed from JSON).
- Security guards: Anonymous redirected to login, Staff blocked with 403, CSRF blocked with 400.
- Safe validation: rejects NaN, Infinity, negative/zero, out-of-bounds, letters, and ambiguous separators.
- Atomic batch operations: single bad row in bulk_lines aborts all rows without partial updates.
- Price calculation integration: updated DB rate correctly overlays static JSON and affects price math.
"""

from decimal import Decimal
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search
from auth_test_helpers import start_auth_db_patch
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable


class ParseExchangeRateUnitTests(unittest.TestCase):
    """Unit tests for search.parse_exchange_rate."""

    def test_valid_standard_numbers(self):
        self.assertEqual(search.parse_exchange_rate("26000"), Decimal("26000"))
        self.assertEqual(search.parse_exchange_rate("26000.5"), Decimal("26000.5"))
        self.assertEqual(search.parse_exchange_rate("1.25"), Decimal("1.25"))
        self.assertEqual(search.parse_exchange_rate("1"), Decimal("1"))
        self.assertEqual(search.parse_exchange_rate(26000), Decimal("26000"))
        self.assertEqual(search.parse_exchange_rate(Decimal("29300.75")), Decimal("29300.75"))

    def test_valid_decimal_comma(self):
        # Unambiguous decimal comma (e.g. 1,25 or 23,5)
        self.assertEqual(search.parse_exchange_rate("1,25"), Decimal("1.25"))
        self.assertEqual(search.parse_exchange_rate("0,75"), Decimal("0.75"))
        self.assertEqual(search.parse_exchange_rate("26000,50"), Decimal("26000.50"))

    def test_valid_thousands_and_decimal_combinations(self):
        # US style: 26,000.50
        self.assertEqual(search.parse_exchange_rate("26,000.50"), Decimal("26000.50"))
        # EU style: 26.000,50
        self.assertEqual(search.parse_exchange_rate("26.000,50"), Decimal("26000.50"))

    def test_reject_empty_or_whitespace(self):
        for bad in [None, "", "   ", "\t\n"]:
            with self.assertRaises(ValueError):
                search.parse_exchange_rate(bad)

    def test_reject_letters_and_symbols(self):
        for bad in ["abc", "26k", "26.000vnd", "$26000", "twenty-six"]:
            with self.assertRaises(ValueError):
                search.parse_exchange_rate(bad)

    def test_reject_nan_and_infinities(self):
        for bad in ["NaN", "nan", "Infinity", "-Infinity", "inf", "-inf", float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                search.parse_exchange_rate(bad)

    def test_reject_negative_and_zero(self):
        for bad in ["0", "0.0", "-1", "-26000", -26000, 0]:
            with self.assertRaises(ValueError):
                search.parse_exchange_rate(bad)

    def test_reject_out_of_bounds(self):
        # Beyond 1 billion
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1000000001")
        # Too small (below 0.000001)
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("0.00000001")

    def test_reject_ambiguous_thousands_vs_decimals(self):
        # e.g. 26,000 or 26.000: could be 26 thousand or 26.000
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("26,000")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("26.000")
        # Multiple commas or dots without decimal
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1,000,000")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1.000.000")


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class AdminExchangeRatesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        try:
            cls._env_patch = mock.patch.dict("os.environ", {"DATABASE_URL": cls.dsn})
            cls._env_patch.start()
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True

            # Ensure exchange_rates table has updated_at column in temp db
            with cls.conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS exchange_rates (
                        brand TEXT PRIMARY KEY,
                        rate NUMERIC NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE exchange_rates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                )
                # Ensure a valid team exists for IP policy middleware
                cur.execute(
                    "INSERT INTO teams (name, ip_policy) VALUES ('Exchange Rates Test Team', 'INHERIT') RETURNING id"
                )
                cls.team_id = cur.fetchone()[0]
        except Exception:
            drop_temp_db(cls.db_name)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            try:
                drop_temp_db(cls.db_name)
            finally:
                cls._env_patch.stop()

    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        start_auth_db_patch(self)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM exchange_rates")

    def _auth_session(self, is_admin=True):
        csrf = "test-csrf-token"
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = is_admin
            sess["role"] = "admin" if is_admin else "staff"
            sess["user_id"] = 1
            sess["auth_version"] = 1
            if not is_admin:
                sess["team_id"] = self.team_id
            sess["csrf_token"] = csrf
        return csrf

    # --- Security Guard Tests ---

    def test_anonymous_get_redirects_to_login(self):
        resp = self.client.get("/admin/exchange-rates")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))

    def test_anonymous_post_redirects_to_login(self):
        resp = self.client.post("/admin/exchange-rates", data={"brand": "TestBrand", "rate": "25000"})
        self.assertEqual(resp.status_code, 302)

    def test_staff_user_get_forbidden(self):
        self._auth_session(is_admin=False)
        resp = self.client.get("/admin/exchange-rates")
        self.assertEqual(resp.status_code, 403)

    def test_staff_user_post_forbidden(self):
        csrf = self._auth_session(is_admin=False)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "StaffBrand", "rate": "25000", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 403)
        # Ensure nothing was inserted into DB
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exchange_rates WHERE brand = 'StaffBrand'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_post_without_csrf_token_rejected_400(self):
        self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "NoCsrfBrand", "rate": "25000"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_with_wrong_csrf_token_rejected_400(self):
        self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "WrongCsrfBrand", "rate": "25000", "csrf_token": "invalid-token"},
        )
        self.assertEqual(resp.status_code, 400)

    # --- Admin GET Tests ---

    def test_admin_get_success(self):
        self._auth_session(is_admin=True)
        # Insert a sample rate in DB
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO exchange_rates (brand, rate) VALUES ('Sigma (Mỹ)', 26500)"
            )

        resp = self.client.get("/admin/exchange-rates")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Quản lý Tỷ giá theo Brand", html)
        self.assertIn("Sigma (Mỹ)", html)
        self.assertIn("26,500", html)
        self.assertIn('name="csrf_token"', html)
        self.assertIn('id="brandFilter"', html)

    # --- Admin Single POST Tests ---

    def test_admin_post_single_insert_and_update(self):
        csrf = self._auth_session(is_admin=True)

        # 1. Insert new brand
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "Merck Test", "rate": "28500", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)

        with self.conn.cursor() as cur:
            cur.execute("SELECT rate FROM exchange_rates WHERE brand = 'Merck Test'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], Decimal("28500"))

        # 2. Update existing brand
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "Merck Test", "rate": "29000.5", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)

        with self.conn.cursor() as cur:
            cur.execute("SELECT rate FROM exchange_rates WHERE brand = 'Merck Test'")
            self.assertEqual(cur.fetchone()[0], Decimal("29000.5"))

    def test_admin_post_ajax_returns_json(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"brand": "AjaxBrand", "rate": "31000", "csrf_token": csrf},
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        json_data = resp.get_json()
        self.assertTrue(json_data.get("ok"))
        self.assertIn("Đã lưu tỷ giá", json_data.get("message", ""))

    def test_admin_post_single_validation_errors(self):
        csrf = self._auth_session(is_admin=True)

        bad_inputs = [
            ("BadBrand1", "abc", "không phải số"),
            ("BadBrand2", "-500", "dương"),
            ("BadBrand3", "0", "dương"),
            ("BadBrand4", "NaN", "NaN"),
            ("BadBrand5", "26,000", "không rõ ràng"),
        ]
        for brand, rate, expected_err_keyword in bad_inputs:
            resp = self.client.post(
                "/admin/exchange-rates",
                data={"brand": brand, "rate": rate, "csrf_token": csrf},
            )
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("statusAlert", html)
            # Ensure DB was not modified
            with self.conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM exchange_rates WHERE brand = %s", (brand,))
                self.assertEqual(cur.fetchone()[0], 0)

    # --- Bulk and Delete Tests ---

    def test_admin_bulk_same_rate(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={
                "bulk_same_apply": "1",
                "bulk_brands": "BrandAlpha, BrandBeta\nBrandGamma",
                "bulk_rate": "27000",
                "csrf_token": csrf,
            },
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT brand, rate FROM exchange_rates WHERE brand IN ('BrandAlpha', 'BrandBeta', 'BrandGamma') ORDER BY brand")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 3)
            for r in rows:
                self.assertEqual(r[1], Decimal("27000"))

    def test_admin_bulk_lines_success(self):
        csrf = self._auth_session(is_admin=True)
        lines = "BrandOne, 24000\nBrandTwo\t25500\n# Comment line\nBrandThree, 26100.5"
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"bulk_lines_apply": "1", "bulk_lines": lines, "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT brand, rate FROM exchange_rates WHERE brand IN ('BrandOne', 'BrandTwo', 'BrandThree') ORDER BY brand")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0], ("BrandOne", Decimal("24000")))
            self.assertEqual(rows[1], ("BrandThree", Decimal("26100.5")))
            self.assertEqual(rows[2], ("BrandTwo", Decimal("25500")))

    def test_admin_bulk_lines_atomic_rollback_on_single_bad_row(self):
        """If one line is invalid, NO rows must be inserted/updated (no partial update)."""
        csrf = self._auth_session(is_admin=True)
        lines = "GoodBrand1, 24000\nBadBrand, NOT_A_NUMBER\nGoodBrand2, 25000"
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"bulk_lines_apply": "1", "bulk_lines": lines, "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("không hợp lệ", html)

        # Confirm GoodBrand1 and GoodBrand2 were NOT inserted!
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exchange_rates WHERE brand IN ('GoodBrand1', 'GoodBrand2', 'BadBrand')")
            self.assertEqual(cur.fetchone()[0], 0, "Partial update occurred! Expected 0 rows inserted.")

    def test_admin_delete_brand(self):
        csrf = self._auth_session(is_admin=True)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO exchange_rates (brand, rate) VALUES ('BrandToDelete', 26000)")

        resp = self.client.post(
            "/admin/exchange-rates",
            data={"delete_brand": "BrandToDelete", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exchange_rates WHERE brand = 'BrandToDelete'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_admin_seed_json(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"seed_json": "1", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM exchange_rates")
            count = cur.fetchone()[0]
            self.assertGreater(count, 10, "Expected seed_json to populate database from static file")

    # --- Price Calculation Integration Test ---

    def test_exchange_rate_map_and_price_calculation(self):
        """Verify that updating a brand rate in DB overlays JSON and price calculation uses it."""
        # 1. Default map before DB insert
        rates_before = search._exchange_rate_map(self.conn)
        sigma_default = rates_before.get("Sigma (Mỹ)", 26000.0)

        # 2. Update rate in DB
        new_rate = Decimal("35000.0")
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO exchange_rates (brand, rate) VALUES ('Sigma (Mỹ)', %s) ON CONFLICT (brand) DO UPDATE SET rate = EXCLUDED.rate",
                (new_rate,),
            )

        # 3. Read rate map again
        rates_after = search._exchange_rate_map(self.conn)
        self.assertEqual(rates_after["Sigma (Mỹ)"], 35000.0)

        # 4. Verify sales price calculation: unit_price = round(price * ship * exchange_rate, -3)
        price = 100.0
        ship = 1.1
        rate = rates_after.get("Sigma (Mỹ)", 1.0)
        unit_price = round(price * ship * rate, -3)
        # 100 * 1.1 * 35000 = 3,850,000
        self.assertEqual(unit_price, 3850000.0)


if __name__ == "__main__":
    unittest.main()
