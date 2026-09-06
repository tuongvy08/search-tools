"""Tests for Admin Currency Rates (/admin/exchange-rates) — Phase 6B2B2 redesign.

Phase 6B2B2 replaces the old per-brand exchange rate admin UI with:
  - a currency rate table (VND/AUD/USD/EUR/GBP, VND fixed at 1), and
  - a brand -> currency mapping table (35 canonical brands from brand_master).

- Isolated temporary Postgres database via `pg_temp_db` (never touches products_local),
  with migration_017 (Brand Master) + migration_018 (currency_rates) applied.
- Admin GET & POST tests (`update_rate`, `update_brand_currency`).
- Security guards: Anonymous redirected to login, Staff blocked with 403, CSRF blocked with 400.
- Safe validation: rejects NaN, Infinity, negative/zero, VND immutability, unapproved currency codes.
- Row-level locking (`SELECT ... FOR UPDATE`) + audit history on every successful update.
- Price calculation integration: updated currency rate immediately changes resolver output.
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
from currency_rates import load_currency_rate_resolver
from pg_temp_db import (
    apply_brand_master_and_currency_migrations,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)


class ParseExchangeRateUnitTests(unittest.TestCase):
    """Unit tests for search.parse_exchange_rate (unchanged by Phase 6B2B2:
    still the shared validator for currency rate input in the new admin route)."""

    def test_valid_standard_numbers(self):
        self.assertEqual(search.parse_exchange_rate("26000"), Decimal("26000"))
        self.assertEqual(search.parse_exchange_rate("26000.5"), Decimal("26000.5"))
        self.assertEqual(search.parse_exchange_rate("1.25"), Decimal("1.25"))
        self.assertEqual(search.parse_exchange_rate("1"), Decimal("1"))
        self.assertEqual(search.parse_exchange_rate(26000), Decimal("26000"))
        self.assertEqual(search.parse_exchange_rate(Decimal("29300.75")), Decimal("29300.75"))

    def test_valid_decimal_comma(self):
        self.assertEqual(search.parse_exchange_rate("1,25"), Decimal("1.25"))
        self.assertEqual(search.parse_exchange_rate("0,75"), Decimal("0.75"))
        self.assertEqual(search.parse_exchange_rate("26000,50"), Decimal("26000.50"))

    def test_valid_thousands_and_decimal_combinations(self):
        self.assertEqual(search.parse_exchange_rate("26,000.50"), Decimal("26000.50"))
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
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1000000001")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("0.00000001")

    def test_reject_ambiguous_thousands_vs_decimals(self):
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("26,000")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("26.000")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1,000,000")
        with self.assertRaises(ValueError):
            search.parse_exchange_rate("1.000.000")


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class AdminCurrencyRatesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        try:
            cls._env_patch = mock.patch.dict("os.environ", {"DATABASE_URL": cls.dsn})
            cls._env_patch.start()
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True
            with cls.conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                cur.execute(
                    "INSERT INTO teams (name, ip_policy) VALUES ('Currency Rates Test Team', 'INHERIT') RETURNING id"
                )
                cls.team_id = cur.fetchone()[0]
                cur.execute("SELECT id FROM brand_master WHERE name = 'A2S'")
                cls.a2s_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO app_users (username, password_hash, is_admin) "
                    "VALUES ('admin_exchange_rates_test_user', 'x', TRUE) RETURNING id"
                )
                cls.admin_user_id = cur.fetchone()[0]
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
            cur.execute(
                """
                UPDATE currency_rates SET rate_vnd = CASE currency_code
                    WHEN 'VND' THEN 1 WHEN 'AUD' THEN 17200 WHEN 'USD' THEN 26500
                    WHEN 'EUR' THEN 31500 WHEN 'GBP' THEN 35500 END
                """
            )
            cur.execute("UPDATE brand_master SET currency_code = 'EUR' WHERE name = 'A2S'")

    def _auth_session(self, is_admin=True):
        csrf = "test-csrf-token"
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = is_admin
            sess["role"] = "admin" if is_admin else "staff"
            sess["user_id"] = self.admin_user_id if is_admin else 1
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
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "USD", "rate": "27000"},
        )
        self.assertEqual(resp.status_code, 302)

    def test_staff_user_get_forbidden(self):
        self._auth_session(is_admin=False)
        resp = self.client.get("/admin/exchange-rates")
        self.assertEqual(resp.status_code, 403)

    def test_staff_user_post_forbidden(self):
        csrf = self._auth_session(is_admin=False)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "USD", "rate": "27000", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 403)
        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'USD'")
            self.assertEqual(cur.fetchone()[0], Decimal("26500"), "Staff POST must not mutate the rate")

    def test_post_without_csrf_token_rejected_400(self):
        self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "USD", "rate": "27000"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_with_wrong_csrf_token_rejected_400(self):
        self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "USD", "rate": "27000", "csrf_token": "invalid-token"},
        )
        self.assertEqual(resp.status_code, 400)

    # --- Admin GET Tests ---

    def test_admin_get_success_shows_currency_and_brand_tables(self):
        self._auth_session(is_admin=True)
        resp = self.client.get("/admin/exchange-rates")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Quản lý Tỷ giá Trung tâm", html)
        self.assertIn("VND", html)
        self.assertIn("CỐ ĐỊNH", html)  # VND immutability marker
        self.assertIn("A2S", html)  # canonical brand mapping table
        self.assertIn('name="csrf_token"', html)
        self.assertIn('id="brandFilter"', html)
        self.assertIn('id="currencyFilter"', html)

    # --- update_rate Tests ---

    def test_update_rate_success_and_history_recorded(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "USD", "rate": "27500", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'USD'")
            self.assertEqual(cur.fetchone()[0], Decimal("27500"))
            cur.execute(
                "SELECT old_rate, new_rate, source FROM currency_rate_history "
                "WHERE currency_code = 'USD' ORDER BY id DESC LIMIT 1"
            )
            old_rate, new_rate, source = cur.fetchone()
            self.assertEqual(old_rate, Decimal("26500"))
            self.assertEqual(new_rate, Decimal("27500"))
            self.assertEqual(source, "ADMIN_UI")

    def test_update_rate_ajax_returns_json(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "EUR", "rate": "32000", "csrf_token": csrf},
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("EUR", data.get("message", ""))

    def test_update_rate_rejects_vnd_change(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "VND", "rate": "2", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("VND", html)
        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'VND'")
            self.assertEqual(cur.fetchone()[0], Decimal("1"), "VND must remain 1")

    def test_update_rate_rejects_unapproved_currency(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={"action": "update_rate", "currency_code": "JPY", "rate": "180", "csrf_token": csrf},
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM currency_rates WHERE currency_code = 'JPY'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_update_rate_validation_errors_do_not_mutate_db(self):
        csrf = self._auth_session(is_admin=True)
        for bad_rate in ("abc", "-500", "0", "NaN", "26,000"):
            resp = self.client.post(
                "/admin/exchange-rates",
                data={"action": "update_rate", "currency_code": "GBP", "rate": bad_rate, "csrf_token": csrf},
            )
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn("statusAlert", html)
        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'GBP'")
            self.assertEqual(cur.fetchone()[0], Decimal("35500"), "Invalid input must never mutate the rate")

    # --- update_brand_currency Tests ---

    def test_update_brand_currency_success_and_history_recorded(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={
                "action": "update_brand_currency",
                "brand_id": str(self.a2s_id),
                "currency_code": "USD",
                "csrf_token": csrf,
            },
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE id = %s", (self.a2s_id,))
            self.assertEqual(cur.fetchone()[0], "USD")
            cur.execute(
                "SELECT old_currency_code, new_currency_code FROM brand_currency_history "
                "WHERE brand_id = %s ORDER BY id DESC LIMIT 1",
                (self.a2s_id,),
            )
            self.assertEqual(cur.fetchone(), ("EUR", "USD"))

    def test_update_brand_currency_rejects_unapproved_currency(self):
        csrf = self._auth_session(is_admin=True)
        resp = self.client.post(
            "/admin/exchange-rates",
            data={
                "action": "update_brand_currency",
                "brand_id": str(self.a2s_id),
                "currency_code": "JPY",
                "csrf_token": csrf,
            },
        )
        self.assertEqual(resp.status_code, 200)
        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE id = %s", (self.a2s_id,))
            self.assertEqual(cur.fetchone()[0], "EUR", "Unapproved currency must not be applied")

    def test_update_brand_currency_takes_effect_immediately_for_next_resolve(self):
        csrf = self._auth_session(is_admin=True)
        self.client.post(
            "/admin/exchange-rates",
            data={
                "action": "update_brand_currency",
                "brand_id": str(self.a2s_id),
                "currency_code": "GBP",
                "csrf_token": csrf,
            },
        )
        resolver = load_currency_rate_resolver(self.conn, search.app.root_path)
        res = resolver.resolve("A2S")
        self.assertEqual(res.currency_code, "GBP")
        self.assertEqual(res.rate, Decimal("35500"))


if __name__ == "__main__":
    unittest.main()
