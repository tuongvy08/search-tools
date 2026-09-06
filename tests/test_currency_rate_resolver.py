"""Focused tests for Phase 6B2B2: Central Currency Rates.

Covers:
1. migration_018 schema/constraints (idempotent seed, VND pinned to 1,
   rate must be positive, only 5 allowed currencies, ordering after
   migration_017).
2. `currency_rates.CurrencyRateResolver`: brand -> currency -> rate
   resolution, fail-closed for unknown brand / missing currency rate,
   no silent 1.0 fallback, updating one currency's rate propagates to
   every brand using it without touching `products`.
3. Admin update helpers (`apply_currency_rate_update`,
   `apply_brand_currency_update`): SELECT ... FOR UPDATE + history audit,
   VND immutability, allowlist enforcement, non-positive rate rejection.
4. Legacy/back-compat: resolver transparently falls back to the exact
   `search._exchange_rate_map` overlay when `brand_master`/`currency_rates`
   do not exist (pre-migration-017/018 schema), so all pre-existing
   pricing tests remain unaffected.
5. Phase 6B2B2-R (independent review) additions -- the full 4-state
   migration matrix:
   - State 1 (no migration_017): covered by
     `CurrencyRateResolverUnitTests` (`test_legacy_schema_*`) above.
   - State 2 (migration_017 applied, migration_018 NOT applied yet):
     `Migration017OnlyPartialStateTests` below.
   - State 3 (017+018 both applied): covered by
     `CurrencyRateMigrationAndResolverPgTests` above.
   - State 4 (018 present, but a DB error occurs loading brand_master/
     currency_rates content -- permission error, dropped connection,
     UndefinedTable from a half-applied migration, etc.):
     `ResolverLoadErrorFailsClosedTests` below. Regression test for a
     confirmed bug where the resolver silently reinterpreted ANY load
     failure (even with both tables confirmed present) as "legacy schema"
     and fell back to `.get(brand, 1.0)` -- i.e. every non-VND brand would
     have silently priced at rate=1.0 during a transient DB error.
6. Phase 6B2B2-R2 (remove all silent rate defaults) additions:
   - The legacy path (State 1) no longer defaults an unmapped brand to
     rate=1.0 -- see `test_legacy_schema_fails_closed_for_unmapped_brand_*`
     and `test_legacy_schema_with_no_loader_or_map_fails_closed_*` above.
   - State 2 (migration_017-only) is now a HARD fail-closed
     `CURRENCY_SCHEMA_INCOMPLETE` state, not a "legacy bridge" -- see the
     rewritten `Migration017OnlyPartialStateTests` below.
   - `SchemaIncompleteUnitTests` below: pure in-memory (no Postgres)
     coverage of the symmetric partial-migration case (only
     `currency_rates` exists, not `brand_master`) plus proof the legacy
     loader is never invoked in either partial state.
"""

from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from dotenv import load_dotenv
from psycopg2 import errors as pg_errors

import search
from currency_rates import (
    SEEDED_CURRENCIES,
    CurrencyRateError,
    CurrencyRateResolver,
    apply_brand_currency_update,
    apply_currency_rate_update,
    fetch_brand_currency_rows,
    fetch_currency_rate_rows,
    load_currency_rate_resolver,
)
from pg_temp_db import (
    apply_brand_master_and_currency_migrations,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)

load_dotenv(dotenv_path=".env")

_ROOT = Path(__file__).resolve().parents[1]


class CurrencyRateResolverUnitTests(unittest.TestCase):
    """Pure in-memory tests -- no DB required."""

    def _manual_resolver(self, brand_currency, currency_rate, static_fallback=None):
        r = CurrencyRateResolver()
        r.schema_ready = True
        r.brand_currency = dict(brand_currency)
        r.currency_rate = {k: Decimal(v) for k, v in currency_rate.items()}
        r.static_fallback = {k: Decimal(v) for k, v in (static_fallback or {}).items()}
        return r

    def test_known_brand_currency_rate_resolves_correctly(self):
        r = self._manual_resolver({"Sigma": "USD"}, {"USD": "26500"})
        res = r.resolve("Sigma")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.currency_code, "USD")
        self.assertEqual(res.rate, Decimal("26500"))

    def test_vnd_always_multiplies_by_one(self):
        r = self._manual_resolver({"NIFC (Việt Nam)": "VND"}, {"VND": "1"})
        res = r.resolve("NIFC (Việt Nam)")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.rate, Decimal("1"))

    def test_unknown_brand_fails_closed(self):
        r = self._manual_resolver({"Sigma": "USD"}, {"USD": "26500"})
        res = r.resolve("Không Tồn Tại Brand XYZ")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "BRAND_UNKNOWN")

    def test_currency_missing_rate_fails_closed_no_static_fallback(self):
        r = self._manual_resolver({"A2S": "EUR"}, {"USD": "26500"})  # no EUR row at all
        res = r.resolve("A2S")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "RATE_MISSING")

    def test_static_json_is_never_used_when_db_row_missing(self):
        r = self._manual_resolver({"A2S": "EUR"}, {"USD": "26500"}, static_fallback={"EUR": "31500"})
        res = r.resolve("A2S")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "RATE_MISSING")
        self.assertFalse(r.warnings)

    def test_zero_or_negative_rate_never_used_even_if_present_in_memory(self):
        r = self._manual_resolver({"A2S": "EUR"}, {"EUR": "0"})
        res = r.resolve("A2S")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)

    def test_non_vnd_currency_rate_one_is_valid_positive_rate(self):
        r = self._manual_resolver({"Sigma": "USD"}, {"USD": "1"})
        res = r.resolve("Sigma")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.rate, Decimal("1"))

    def test_get_convenience_accessor_returns_none_not_one(self):
        r = self._manual_resolver({"Sigma": "USD"}, {"USD": "26500"})
        self.assertIsNone(r.get("Unmapped Brand"))
        self.assertEqual(r.get("Sigma"), Decimal("26500"))

    def test_legacy_schema_resolves_known_brand_from_map(self):
        """When brand_master/currency_rates don't exist, a brand present in
        the supplied legacy overlay map still resolves normally (this is
        the deliberate backward-compat path for pre-migration-017/018
        schemas -- see currency_rates.py module docstring)."""
        r = CurrencyRateResolver()
        r.schema_ready = False
        r.legacy_rate_map = {"Sigma": 26500.0}
        res_known = r.resolve("Sigma")
        self.assertTrue(res_known.is_valid)
        self.assertEqual(res_known.rate, Decimal("26500.0"))
        self.assertEqual(res_known.status, "LEGACY_SCHEMA")

    def test_legacy_schema_fails_closed_for_unmapped_brand_never_silently_1_0(self):
        """Phase 6B2B2-R2: a brand missing from the legacy overlay map must
        fail closed (`LEGACY_RATE_MISSING`, rate=None) -- the old silent
        default of rate=1.0 for any unmapped brand is a confirmed bug and
        must never resurface."""
        r = CurrencyRateResolver()
        r.schema_ready = False
        r.legacy_rate_map = {"Sigma": 26500.0}
        res_unknown = r.resolve("Completely Unmapped Brand")
        self.assertFalse(res_unknown.is_valid)
        self.assertIsNone(res_unknown.rate)
        self.assertEqual(res_unknown.status, "LEGACY_RATE_MISSING")

    def test_legacy_schema_fails_closed_for_zero_or_negative_or_unparseable_rate(self):
        r = CurrencyRateResolver()
        r.schema_ready = False
        r.legacy_rate_map = {"Zero Brand": 0.0, "Negative Brand": -5.0, "Bad Brand": "not-a-number"}
        for brand in ("Zero Brand", "Negative Brand", "Bad Brand"):
            res = r.resolve(brand)
            self.assertFalse(res.is_valid, f"{brand} must fail closed")
            self.assertIsNone(res.rate)
            self.assertEqual(res.status, "LEGACY_RATE_MISSING")

    def test_legacy_schema_with_no_loader_or_map_fails_closed_for_every_brand(self):
        """Phase 6B2B2-R2: `load_currency_rate_resolver()` called without a
        `legacy_rate_map`/`legacy_rate_map_loader` must NOT silently create
        an empty map that then defaults every lookup to rate=1.0 -- it must
        fail closed for every single brand instead."""
        r = CurrencyRateResolver()
        r.schema_ready = False
        r.legacy_rate_map = {}
        for brand in ("Sigma", "USD Brand", "EUR Brand", "AUD Brand", "GBP Brand", ""):
            res = r.resolve(brand)
            self.assertFalse(res.is_valid, f"{brand!r} must fail closed with no legacy data available")
            self.assertIsNone(res.rate)
            self.assertEqual(res.status, "LEGACY_RATE_MISSING")

    def test_non_vnd_currencies_missing_legacy_rate_never_resolve_to_one(self):
        """Sweep for USD/EUR/AUD/GBP specifically (the 4 approved non-VND
        currencies) missing from the legacy overlay -- none may ever
        resolve to rate=1 (that value is reserved for VND)."""
        r = CurrencyRateResolver()
        r.schema_ready = False
        r.legacy_rate_map = {}
        for currency_brand in ("USD Brand", "EUR Brand", "AUD Brand", "GBP Brand"):
            res = r.resolve(currency_brand)
            self.assertFalse(res.is_valid)
            self.assertIsNone(res.rate)
            self.assertNotEqual(res.rate, Decimal("1"))


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class CurrencyRateMigrationAndResolverPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)
            # A dummy admin actor for FK-friendly audit rows.
            cur.execute(
                "INSERT INTO app_users (username, password_hash, is_admin) "
                "VALUES ('cur_test_admin', 'x', TRUE) RETURNING id"
            )
            cls.actor_id = cur.fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def setUp(self):
        # Reset currency_rates back to the approved seed before every test so
        # tests that mutate a rate don't leak into each other.
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE currency_rates SET rate_vnd = CASE currency_code
                    WHEN 'VND' THEN 1 WHEN 'AUD' THEN 17200 WHEN 'USD' THEN 26500
                    WHEN 'EUR' THEN 31500 WHEN 'GBP' THEN 35500 END,
                    updated_by = NULL, update_source = 'TEST_RESET'
                """
            )
            cur.execute("DELETE FROM currency_rate_history WHERE source = 'ADMIN_UI'")
            cur.execute("DELETE FROM brand_currency_history")
            cur.execute("UPDATE brand_master SET currency_code = 'EUR' WHERE name = 'A2S'")

    # -- Migration 018 shape / constraints -----------------------------------

    def test_seed_has_exactly_5_currencies_with_approved_rates(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code, rate_vnd FROM currency_rates ORDER BY currency_code")
            rows = dict(cur.fetchall())
        self.assertEqual(set(rows.keys()), set(SEEDED_CURRENCIES))
        self.assertEqual(rows["VND"], 1)
        self.assertEqual(rows["AUD"], 17200)
        self.assertEqual(rows["USD"], 26500)
        self.assertEqual(rows["EUR"], 31500)
        self.assertEqual(rows["GBP"], 35500)

    def test_migration_018_is_idempotent_and_does_not_clobber_admin_edits(self):
        migration_018_path = _ROOT / "sql" / "migration_018_currency_rates.sql"
        with self.conn.cursor() as cur:
            cur.execute("UPDATE currency_rates SET rate_vnd = 99999 WHERE currency_code = 'USD'")
            with open(migration_018_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'USD'")
            self.assertEqual(cur.fetchone()[0], 99999, "Re-running migration_018 must not overwrite an admin-set rate")
            cur.execute("SELECT COUNT(*) FROM currency_rates")
            self.assertEqual(cur.fetchone()[0], 5)

    def test_vnd_rate_cannot_be_changed_at_db_level(self):
        with self.conn.cursor() as cur:
            with self.assertRaises(pg_errors.CheckViolation):
                cur.execute("UPDATE currency_rates SET rate_vnd = 2 WHERE currency_code = 'VND'")

    def test_zero_or_negative_rate_rejected_at_db_level(self):
        for bad in (0, -5):
            with self.conn.cursor() as cur:
                with self.assertRaises(pg_errors.CheckViolation):
                    cur.execute("UPDATE currency_rates SET rate_vnd = %s WHERE currency_code = 'USD'", (bad,))

    def test_unapproved_currency_code_rejected_at_db_level(self):
        with self.conn.cursor() as cur:
            with self.assertRaises(pg_errors.CheckViolation):
                cur.execute("INSERT INTO currency_rates (currency_code, rate_vnd) VALUES ('JPY', 180)")

    def test_migration_017_must_precede_018_brand_currency_history_fk(self):
        """brand_currency_history.brand_id FKs to brand_master -- migration
        018 cannot be meaningfully applied/used without 017 already present.
        We assert the dependency by checking the FK exists and rejects an
        orphan brand_id."""
        with self.conn.cursor() as cur:
            with self.assertRaises(pg_errors.ForeignKeyViolation):
                cur.execute(
                    "INSERT INTO brand_currency_history (brand_id, new_currency_code) VALUES (999999, 'USD')"
                )

    # -- Resolver against the real 35-brand/5-currency dataset ---------------

    def test_all_35_canonical_brands_resolve_to_a_valid_positive_rate(self):
        resolver = load_currency_rate_resolver(self.conn, str(_ROOT))
        self.assertTrue(resolver.schema_ready)
        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM brand_master WHERE is_active = TRUE")
            names = [r[0] for r in cur.fetchall()]
        self.assertEqual(len(names), 35)
        for name in names:
            res = resolver.resolve(name)
            self.assertTrue(res.is_valid, f"{name} should resolve to a valid rate")
            self.assertGreater(res.rate, 0)
            if res.currency_code != "VND":
                self.assertNotEqual(res.rate, Decimal("1"), f"{name} (non-VND) must never resolve to rate=1")

    def test_updating_eur_rate_propagates_to_every_eur_brand_without_touching_products(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            products_before = cur.fetchone()[0]
            cur.execute("SELECT name FROM brand_master WHERE currency_code = 'EUR' AND is_active = TRUE")
            eur_brands = [r[0] for r in cur.fetchall()]
        self.assertGreater(len(eur_brands), 1, "need multiple EUR brands for this test to be meaningful")

        with self.conn:
            apply_currency_rate_update(self.conn, "EUR", Decimal("32000"), self.actor_id)

        resolver = load_currency_rate_resolver(self.conn, str(_ROOT))
        for brand in eur_brands:
            res = resolver.resolve(brand)
            self.assertTrue(res.is_valid)
            self.assertEqual(res.rate, Decimal("32000"))

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            self.assertEqual(cur.fetchone()[0], products_before, "products table must not be touched by a rate update")

    def test_apply_currency_rate_update_writes_history_with_actor_and_old_new_rate(self):
        with self.conn:
            apply_currency_rate_update(self.conn, "GBP", Decimal("36000"), self.actor_id, source="ADMIN_UI")
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT old_rate, new_rate, actor_user_id, source FROM currency_rate_history "
                "WHERE currency_code = 'GBP' ORDER BY id DESC LIMIT 1"
            )
            old_rate, new_rate, actor, source = cur.fetchone()
        self.assertEqual(old_rate, Decimal("35500"))
        self.assertEqual(new_rate, Decimal("36000"))
        self.assertEqual(actor, self.actor_id)
        self.assertEqual(source, "ADMIN_UI")

    def test_apply_currency_rate_update_rejects_vnd(self):
        with self.assertRaises(CurrencyRateError):
            with self.conn:
                apply_currency_rate_update(self.conn, "VND", Decimal("2"), self.actor_id)

    def test_apply_currency_rate_update_rejects_non_positive(self):
        for bad in (Decimal("0"), Decimal("-1")):
            with self.assertRaises(CurrencyRateError):
                with self.conn:
                    apply_currency_rate_update(self.conn, "USD", bad, self.actor_id)

    def test_apply_currency_rate_update_rejects_unapproved_currency(self):
        with self.assertRaises(CurrencyRateError):
            with self.conn:
                apply_currency_rate_update(self.conn, "JPY", Decimal("180"), self.actor_id)

    def test_apply_brand_currency_update_changes_mapping_and_writes_history(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, currency_code FROM brand_master WHERE name = 'A2S'")
            brand_id, old_currency = cur.fetchone()
        self.assertEqual(old_currency, "EUR")

        with self.conn:
            apply_brand_currency_update(self.conn, brand_id, "USD", self.actor_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE id = %s", (brand_id,))
            self.assertEqual(cur.fetchone()[0], "USD")
            cur.execute(
                "SELECT old_currency_code, new_currency_code, actor_user_id FROM brand_currency_history "
                "WHERE brand_id = %s ORDER BY id DESC LIMIT 1",
                (brand_id,),
            )
            old_c, new_c, actor = cur.fetchone()
        self.assertEqual((old_c, new_c, actor), ("EUR", "USD", self.actor_id))

        # Effective immediately for the very next resolve -- no product touched.
        resolver = load_currency_rate_resolver(self.conn, str(_ROOT))
        res = resolver.resolve("A2S")
        self.assertEqual(res.currency_code, "USD")
        self.assertEqual(res.rate, Decimal("26500"))

    def test_apply_brand_currency_update_rejects_unapproved_currency(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM brand_master WHERE name = 'A2S'")
            brand_id = cur.fetchone()[0]
        with self.assertRaises(CurrencyRateError):
            with self.conn:
                apply_brand_currency_update(self.conn, brand_id, "JPY", self.actor_id)

    def test_fetch_currency_rate_rows_includes_brand_counts(self):
        rows = fetch_currency_rate_rows(self.conn)
        self.assertEqual(len(rows), 5)
        by_code = {r["currency_code"]: r for r in rows}
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM brand_master WHERE currency_code = 'USD' AND is_active = TRUE")
            expected_usd_count = cur.fetchone()[0]
        self.assertEqual(by_code["USD"]["brand_count"], expected_usd_count)

    def test_fetch_brand_currency_rows_search_and_filter(self):
        all_rows = fetch_brand_currency_rows(self.conn)
        self.assertEqual(len(all_rows), 35)
        filtered = fetch_brand_currency_rows(self.conn, search_query="sigma")
        self.assertTrue(all("sigma" in r["name"].lower() for r in filtered))
        eur_rows = fetch_brand_currency_rows(self.conn, currency_filter="EUR")
        self.assertTrue(all(r["currency_code"] == "EUR" for r in eur_rows))

    def test_brand_unknown_in_master_fails_closed(self):
        resolver = load_currency_rate_resolver(self.conn, str(_ROOT))
        res = resolver.resolve("Brand Không Tồn Tại Trong Master")
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, "BRAND_UNKNOWN")

    def test_currency_rate_missing_fails_closed(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM currency_rates WHERE currency_code = 'GBP'")
        try:
            resolver = load_currency_rate_resolver(self.conn, "/nonexistent-root-no-json-fallback")
            res = resolver.resolve("BP")  # BP is the canonical GBP brand
            self.assertFalse(res.is_valid)
            self.assertEqual(res.status, "RATE_MISSING")
        finally:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO currency_rates (currency_code, rate_vnd) VALUES ('GBP', 35500) "
                    "ON CONFLICT (currency_code) DO UPDATE SET rate_vnd = EXCLUDED.rate_vnd"
                )

    def test_search_pricing_uses_resolver_and_matches_expected_amount(self):
        """Integration check for the Search pricing formula through the real
        `search._compute_unit_price` helper, using the live currency_rates
        table (not a hand-built resolver)."""
        resolver = search._load_pricing_resolver(self.conn)
        unit_price, display, valid = search._compute_unit_price("100", "1.1", "Sigma", resolver)
        self.assertTrue(valid)
        self.assertEqual(unit_price, round(100 * 1.1 * 26500, -3))
        self.assertNotIn("không khả dụng", display.lower())

    def test_search_pricing_shows_unavailable_for_unknown_brand(self):
        resolver = search._load_pricing_resolver(self.conn)
        unit_price, display, valid = search._compute_unit_price("100", "1.1", "Brand Lạ Không Tồn Tại", resolver)
        self.assertFalse(valid)
        self.assertEqual(unit_price, 0.0)
        self.assertEqual(display, search.UNAVAILABLE_PRICE_LABEL)

    def test_quick_quote_unit_price_reports_currency_status_and_stays_no_valid_price(self):
        resolver = search._load_pricing_resolver(self.conn)
        unit_price, status = search._quote_unit_price_value("1", "100", "Brand Lạ Không Tồn Tại", resolver)
        self.assertEqual(unit_price, 0.0)
        self.assertEqual(status, "BRAND_UNKNOWN")

        unit_price_ok, status_ok = search._quote_unit_price_value("1", "100", "Sigma", resolver)
        self.assertGreater(unit_price_ok, 0)
        self.assertIsNone(status_ok)

    def _quote_candidate_row(self, brand, ship, price):
        return (
            1, 999, "Test Product", "T-001", "0-00-0", brand, "1g",
            ship, price, "", None, False, "", False, "", "",
        )

    def test_quick_quote_candidate_never_shows_digit_zero_for_missing_rate(self):
        """Phase 6B2B2-R2 item 3: a candidate whose rate could not be
        resolved must display the Vietnamese unavailable label, never the
        digit '0' as if it were a real price."""
        resolver = search._load_pricing_resolver(self.conn)
        row = self._quote_candidate_row("Brand Lạ Không Tồn Tại", "1.1", "100")
        candidate = search._quote_candidate_from_row(row, resolver)
        self.assertEqual(candidate["Unit_Price"], search.UNAVAILABLE_PRICE_LABEL)
        self.assertNotEqual(candidate["Unit_Price"], "0")
        self.assertEqual(candidate["Unit_Price_Value"], 0.0)
        self.assertFalse(candidate["eligible"])
        self.assertEqual(candidate["ineligible_reason"], "NO_VALID_PRICE")
        self.assertEqual(candidate["currency_rate_status"], "BRAND_UNKNOWN")
        self.assertEqual(candidate["currency_rate_message"], search.UNAVAILABLE_PRICE_LABEL)

    def test_quick_quote_candidate_shows_real_zero_only_when_rate_is_valid(self):
        """A genuinely zero computed amount (valid rate, but ship=0 in the
        product data) is distinct from a missing rate and MAY display '0'
        -- this must not regress into always hiding zero."""
        resolver = search._load_pricing_resolver(self.conn)
        row = self._quote_candidate_row("Sigma", "0", "100")
        candidate = search._quote_candidate_from_row(row, resolver)
        self.assertEqual(candidate["Unit_Price"], "0")
        self.assertEqual(candidate["Unit_Price_Value"], 0.0)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class Migration017OnlyPartialStateTests(unittest.TestCase):
    """State 2 of the Phase 6B2B2-R2 migration matrix: migration_017
    applied, migration_018 NOT applied yet.

    Phase 6B2B2-R2 change: this state is now a HARD fail-closed state
    (`CURRENCY_SCHEMA_INCOMPLETE`), not the `LEGACY_SCHEMA` "bridge" it used
    to be. Even though migration_017's Section 7 happens to rewrite the
    legacy `exchange_rates` table with correct canonical-brand rates (which
    made the old bridge behavior empirically safe), relying on that as a
    resolver-level guarantee was fragile. The required deployment order
    already wraps the 017-then-018 window in maintenance mode (code
    checkpoint -> backup -> maintenance -> 017 -> 018 -> restart), so
    Search/Quick Quote are not expected to serve live pricing traffic
    during that window -- the resolver must not read `exchange_rates` or
    the static JSON in this state, and must not resolve any rate.
    """

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            migration_017_path = _ROOT / "sql" / "migration_017_brand_master.sql"
            cur.execute(migration_017_path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def test_currency_rates_table_does_not_exist_yet(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT to_regclass('currency_rates')")
            self.assertIsNone(cur.fetchone()[0])

    def _resolver(self, loader_calls=None):
        # Mirrors exactly how `search._load_pricing_resolver` wires the
        # legacy overlay loader in production -- `load_currency_rate_resolver`
        # itself never queries `exchange_rates` directly, the caller supplies
        # it lazily. In this partial-migration state it must NEVER be
        # invoked at all.
        def _loader():
            if loader_calls is not None:
                loader_calls.append(1)
            return search._exchange_rate_map(self.conn)

        return load_currency_rate_resolver(self.conn, str(_ROOT), legacy_rate_map_loader=_loader)

    def test_resolver_reports_schema_incomplete_not_schema_ready(self):
        resolver = self._resolver()
        self.assertFalse(resolver.schema_ready)
        self.assertTrue(resolver.schema_incomplete)

    def test_resolve_fails_closed_with_currency_schema_incomplete_status(self):
        resolver = self._resolver()
        res = resolver.resolve("Sigma")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "CURRENCY_SCHEMA_INCOMPLETE")
        self.assertNotEqual(res.status, "LEGACY_SCHEMA")

    def test_legacy_exchange_rates_loader_is_never_invoked(self):
        loader_calls: list = []
        self._resolver(loader_calls=loader_calls)
        self.assertEqual(
            loader_calls, [],
            "Partial-migration state must NEVER read the legacy exchange_rates "
            "overlay -- that would silently resurrect the old 'bridge' behavior "
            "this phase deliberately removed.",
        )

    def test_partial_migration_state_is_logged_as_a_warning(self):
        resolver = self._resolver()
        self.assertTrue(
            any("PARTIAL" in w and "migration" in w for w in resolver.warnings),
            "Partial-migration state (017 without 018) must be surfaced as a "
            "loud operational warning, not pass completely silently.",
        )

    def test_all_35_canonical_brands_fail_closed_not_just_some(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM brand_master WHERE is_active = TRUE")
            names = [r[0] for r in cur.fetchall()]
        self.assertEqual(len(names), 35)
        resolver = self._resolver()
        for name in names:
            res = resolver.resolve(name)
            self.assertFalse(res.is_valid, f"{name} must fail closed during partial migration")
            self.assertIsNone(res.rate)

    def test_search_pricing_shows_unavailable_not_a_computed_price_in_partial_migration_state(self):
        resolver = search._load_pricing_resolver(self.conn)
        unit_price, display, valid = search._compute_unit_price("100", "1.1", "Sigma", resolver)
        self.assertFalse(valid)
        self.assertEqual(unit_price, 0.0)
        self.assertEqual(display, search.UNAVAILABLE_PRICE_LABEL)


class _FakeExistenceCursor:
    """Fake cursor that answers `to_regclass()` existence-check queries with
    a canned result, without needing a real Postgres connection. Used to
    unit-test the partial-migration detection branch in `load()` in
    isolation for both existence combinations."""

    def __init__(self, to_regclass_result):
        self._result = to_regclass_result

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _FakeExistenceConn:
    def __init__(self, to_regclass_result):
        self._result = to_regclass_result

    def cursor(self, *args, **kwargs):
        return _FakeExistenceCursor(self._result)


class SchemaIncompleteUnitTests(unittest.TestCase):
    """Pure in-memory (no Postgres required) coverage of BOTH partial-
    migration combinations, per Phase 6B2B2-R2 item 2: "Nếu chỉ một trong
    hai bảng tồn tại, luôn fail closed" -- not just the
    brand_master-exists-only direction covered by the Postgres-backed
    `Migration017OnlyPartialStateTests` above.
    """

    def test_only_brand_master_exists_fails_closed(self):
        conn = _FakeExistenceConn(("brand_master", None))
        resolver = CurrencyRateResolver().load(conn, str(_ROOT))
        self.assertFalse(resolver.schema_ready)
        self.assertTrue(resolver.schema_incomplete)
        res = resolver.resolve("Sigma")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "CURRENCY_SCHEMA_INCOMPLETE")

    def test_only_currency_rates_exists_fails_closed(self):
        conn = _FakeExistenceConn((None, "currency_rates"))
        resolver = CurrencyRateResolver().load(conn, str(_ROOT))
        self.assertFalse(resolver.schema_ready)
        self.assertTrue(resolver.schema_incomplete)
        res = resolver.resolve("Sigma")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "CURRENCY_SCHEMA_INCOMPLETE")

    def test_neither_table_exists_is_legacy_not_schema_incomplete(self):
        conn = _FakeExistenceConn((None, None))
        resolver = CurrencyRateResolver().load(conn, str(_ROOT), legacy_rate_map={"Sigma": 26500.0})
        self.assertFalse(resolver.schema_ready)
        self.assertFalse(resolver.schema_incomplete)
        res = resolver.resolve("Sigma")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, "LEGACY_SCHEMA")

    def test_both_tables_exist_is_neither_legacy_nor_schema_incomplete(self):
        conn = _FakeExistenceConn(("brand_master", "currency_rates"))
        resolver = CurrencyRateResolver().load(conn, str(_ROOT))
        self.assertTrue(resolver.schema_ready)
        self.assertFalse(resolver.schema_incomplete)

    def test_partial_migration_never_calls_legacy_loader_either_direction(self):
        for to_regclass_result in (("brand_master", None), (None, "currency_rates")):
            calls: list = []

            def _loader():
                calls.append(1)
                return {"Sigma": 26500.0}

            conn = _FakeExistenceConn(to_regclass_result)
            CurrencyRateResolver().load(conn, str(_ROOT), legacy_rate_map_loader=_loader)
            self.assertEqual(calls, [], f"loader must not be called for to_regclass={to_regclass_result}")


class _FlakyCursor:
    """Wraps a real cursor; raises on the first `execute()` whose SQL
    contains `fail_marker`, to simulate a permission error/dropped
    connection/UndefinedTable happening AFTER `to_regclass()` has already
    confirmed both tables exist."""

    def __init__(self, real_cursor, fail_marker):
        self._c = real_cursor
        self._fail_marker = fail_marker

    def execute(self, sql, params=None):
        if self._fail_marker in sql:
            raise RuntimeError(f"simulated DB error: {self._fail_marker}")
        return self._c.execute(sql) if params is None else self._c.execute(sql, params)

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _FlakyConnWrapper:
    def __init__(self, real_conn, fail_marker):
        self._conn = real_conn
        self._fail_marker = fail_marker

    def cursor(self, *args, **kwargs):
        return _FlakyCursor(self._conn.cursor(*args, **kwargs), self._fail_marker)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class ResolverLoadErrorFailsClosedTests(unittest.TestCase):
    """State 4 of the Phase 6B2B2-R migration matrix: `brand_master` and
    `currency_rates` both CONFIRMED to exist (`to_regclass` succeeds), but a
    subsequent query against one of them fails (permission error, dropped
    connection, UndefinedTable from a half-applied migration, etc.).

    Regression test: the resolver must fail closed for EVERY brand in this
    case (a new `RESOLVER_LOAD_ERROR` status, `is_valid=False`,
    `rate=None`), never reinterpret the failure as "pre-migration-017/018
    legacy schema" and fall back to `.get(brand, 1.0)` -- that would have
    silently quoted every non-VND brand at rate=1.0 on a fully-migrated
    production database during a transient DB error.
    """

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def test_db_error_reading_currency_rates_fails_closed_not_legacy_1_0(self):
        flaky = _FlakyConnWrapper(self.conn, fail_marker="FROM currency_rates")
        resolver = CurrencyRateResolver().load(flaky, str(_ROOT))
        self.assertTrue(resolver.schema_ready, "tables ARE confirmed to exist -- must not report not-ready")
        self.assertIsNotNone(resolver.load_error)

        res = resolver.resolve("Sigma")
        self.assertFalse(res.is_valid, "must fail closed, never silently resolve at an implicit rate")
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "RESOLVER_LOAD_ERROR")
        self.assertNotEqual(res.status, "LEGACY_SCHEMA")

    def test_db_error_reading_brand_master_fails_closed_not_legacy_1_0(self):
        flaky = _FlakyConnWrapper(self.conn, fail_marker="FROM brand_master")
        resolver = CurrencyRateResolver().load(flaky, str(_ROOT))
        self.assertTrue(resolver.schema_ready)
        res = resolver.resolve("Sigma")
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.rate)
        self.assertEqual(res.status, "RESOLVER_LOAD_ERROR")

    def test_load_error_is_logged_as_a_warning(self):
        flaky = _FlakyConnWrapper(self.conn, fail_marker="FROM currency_rates")
        resolver = CurrencyRateResolver().load(flaky, str(_ROOT))
        self.assertTrue(any("failed to load their contents" in w for w in resolver.warnings))

    def test_search_pricing_shows_unavailable_not_wrong_price_on_load_error(self):
        flaky = _FlakyConnWrapper(self.conn, fail_marker="FROM currency_rates")
        resolver = CurrencyRateResolver().load(flaky, str(_ROOT))
        unit_price, display, valid = search._compute_unit_price("100", "1.1", "Sigma", resolver)
        self.assertFalse(valid)
        self.assertEqual(unit_price, 0.0)
        self.assertEqual(display, search.UNAVAILABLE_PRICE_LABEL)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class AdminMutationAtomicityTests(unittest.TestCase):
    """Phase 6B2B2-R: confirms the admin update helpers roll back the rate/
    brand mutation itself when the audit history INSERT in the same
    transaction fails (e.g. an actor_user_id that doesn't exist -- a FK
    violation on `currency_rate_history.actor_user_id` /
    `brand_currency_history.actor_user_id`), instead of leaving a mutated
    row with no corresponding audit trail.
    """

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def test_currency_rate_update_rolls_back_if_history_insert_violates_fk(self):
        nonexistent_actor_id = 987654321
        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'USD'")
            rate_before = cur.fetchone()[0]

        with self.assertRaises(pg_errors.ForeignKeyViolation):
            with self.conn:
                apply_currency_rate_update(self.conn, "USD", Decimal("99999"), nonexistent_actor_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'USD'")
            self.assertEqual(
                cur.fetchone()[0], rate_before,
                "rate UPDATE must be rolled back when the audit history INSERT fails",
            )
            cur.execute(
                "SELECT COUNT(*) FROM currency_rate_history WHERE currency_code = 'USD' AND new_rate = 99999"
            )
            self.assertEqual(cur.fetchone()[0], 0, "no history row when the mutation was rolled back")

    def test_brand_currency_update_rolls_back_if_history_insert_violates_fk(self):
        nonexistent_actor_id = 987654321
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, currency_code FROM brand_master WHERE name = 'A2S'")
            brand_id, currency_before = cur.fetchone()

        with self.assertRaises(pg_errors.ForeignKeyViolation):
            with self.conn:
                apply_brand_currency_update(self.conn, brand_id, "GBP", nonexistent_actor_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE id = %s", (brand_id,))
            self.assertEqual(
                cur.fetchone()[0], currency_before,
                "brand_master.currency_code must be rolled back when the audit history INSERT fails",
            )
            cur.execute("SELECT COUNT(*) FROM brand_currency_history WHERE brand_id = %s", (brand_id,))
            self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
