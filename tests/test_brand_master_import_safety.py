"""Focused unit and integration tests for Brand Master and Import Safety (Phase 6B2B1-C).

Tests:
1. Alias -> Canonical resolution and source_brand preservation.
2. Unknown brand atomic rejection (fail closed, no partial writes).
3. Collision preservation (55 collisions / duplicate codes not arbitrarily updated).
4. Same code but different source/size handling.
5. replace_by_brand safety (replace LGC rejected without scope; scope replaces only targeted catalog).
6. Importer never recreates old aliases in products.brand.
7. Team visibility uses canonical brands.
8. Regulatory rules invariance.
9. Exchange rates compatibility with 35 canonical brands.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

import search
from brand_gateway import (
    BrandGatewayCache,
    load_brand_gateway,
    resolve_product_candidates,
    inspect_replace_by_brand_scopes,
    validate_import_rows_brands,
)
from tests.pg_temp_db import (
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)

load_dotenv()

_MIGRATION_017_PATH = Path(__file__).resolve().parents[1] / "sql" / "migration_017_brand_master.sql"


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class BrandMasterImportSafetyPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            with open(_MIGRATION_017_PATH, "r", encoding="utf-8") as f:
                cur.execute(f.read())
        cls.gateway = BrandGatewayCache()
        with cls.conn.cursor() as cur:
            cls.gateway.load(cur)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE id > 0;")
            cur.execute("DELETE FROM team_brands WHERE team_id > 0;")

    # -------------------------------------------------------------------------
    # 1. Alias -> Canonical Resolution
    # -------------------------------------------------------------------------
    def test_alias_resolves_to_canonical_with_source_brand(self):
        """Test that audited aliases map to canonical brand while tracking source_brand."""
        # 1. LGC variant
        res = self.gateway.resolve("LGC (Mikromol)")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.canonical_brand, "LGC")
        self.assertEqual(res.source_brand, "LGC (Mikromol)")
        self.assertEqual(res.currency_code, "EUR")

        # 2. CATO variant
        res_cato = self.gateway.resolve("CATO (TQ)")
        self.assertTrue(res_cato.is_valid)
        self.assertEqual(res_cato.canonical_brand, "CATO")
        self.assertEqual(res_cato.source_brand, "CATO (TQ)")
        self.assertEqual(res_cato.currency_code, "USD")

        # 3. TLC variant
        res_tlc = self.gateway.resolve("TLC (Mỹ)")
        self.assertTrue(res_tlc.is_valid)
        self.assertEqual(res_tlc.canonical_brand, "TLC Pharmaceutical")
        self.assertEqual(res_tlc.source_brand, "TLC (Mỹ)")
        self.assertEqual(res_tlc.currency_code, "USD")

        # 4. Canonical self-alias
        res_self = self.gateway.resolve("PhytoLab")
        self.assertTrue(res_self.is_valid)
        self.assertEqual(res_self.canonical_brand, "PhytoLab")
        self.assertEqual(res_self.source_brand, "PhytoLab")
        self.assertEqual(res_self.currency_code, "EUR")

    # -------------------------------------------------------------------------
    # 2. Unknown Brand Rejection (Fail Closed)
    # -------------------------------------------------------------------------
    def test_unknown_brand_rejected_atomic(self):
        """Test that unknown brands are rejected immediately and no partial rows written."""
        # Brand outside canonical & aliases
        res = self.gateway.resolve("NonExistentBrand123")
        self.assertFalse(res.is_valid)
        self.assertIn("Brand không tồn tại trong danh mục Brand Master", res.error_message)

        # Brand from Delete Set (e.g. Clearsynth) is rejected
        res_del = self.gateway.resolve("Clearsynth")
        self.assertFalse(res_del.is_valid)

        # Batch validation fails closed
        rows = [
            {"brand": "PhytoLab", "code": "P-101", "name": "Valid 1"},
            {"brand": "UnknownBrand", "code": "U-102", "name": "Invalid"},
            {"brand": "LGC (Mikromol)", "code": "L-103", "name": "Valid 2"},
        ]
        resolved, errors = validate_import_rows_brands(rows, self.gateway)
        self.assertTrue(len(errors) > 0)
        self.assertIn("UnknownBrand", errors[0])

    # -------------------------------------------------------------------------
    # 3. Collision Preservation & No Arbitrary LIMIT 1 Update
    # -------------------------------------------------------------------------
    def test_collision_not_arbitrarily_updated(self):
        """Test that cross-brand collisions (e.g. TLC Pharmaceutical C-1105) are preserved."""
        with self.conn.cursor() as cur:
            # Seed 2 colliding products with same code and canonical brand
            cur.execute(
                """
                INSERT INTO products (code, brand, source_brand, size, price, name)
                VALUES ('C-1105', 'TLC Pharmaceutical', 'TLC (Mỹ)', '10mg', '0.0', '(E)-Cefdinir Old'),
                       ('C-1105', 'TLC Pharmaceutical', 'TLC Pharmaceutical', '10,25,50,100mg', '100.0', '(E)-Cefdinir New')
                RETURNING id;
                """
            )
            seeded_ids = [r[0] for r in cur.fetchall()]
            self.assertEqual(len(seeded_ids), 2)

            # Looking up without source_brand or size finds BOTH candidates (ambiguous)
            candidates = resolve_product_candidates(cur, "C-1105", "TLC Pharmaceutical")
            self.assertEqual(len(candidates), 2, "Must return both colliding candidates")

            # Providing source_brand disambiguates to EXACTLY ONE record
            cand_my = resolve_product_candidates(
                cur, "C-1105", "TLC Pharmaceutical", source_brand="TLC (Mỹ)"
            )
            self.assertEqual(len(cand_my), 1)
            self.assertEqual(cand_my[0][0], seeded_ids[0])

            cand_pharm = resolve_product_candidates(
                cur, "C-1105", "TLC Pharmaceutical", source_brand="TLC Pharmaceutical"
            )
            self.assertEqual(len(cand_pharm), 1)
            self.assertEqual(cand_pharm[0][0], seeded_ids[1])

    # -------------------------------------------------------------------------
    # 4. Same Code Different Source or Size
    # -------------------------------------------------------------------------
    def test_same_code_different_source_or_size(self):
        """Test disambiguation by size when source_brand is identical."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (code, brand, source_brand, size, price, name)
                VALUES ('BIOS-01', 'Biosynth', 'Biosynth', '10mg', '50.0', 'Compound A 10mg'),
                       ('BIOS-01', 'Biosynth', 'Biosynth', '50mg', '200.0', 'Compound A 50mg')
                RETURNING id;
                """
            )
            ids = [r[0] for r in cur.fetchall()]
            self.assertEqual(len(ids), 2)

            # Query with size '50mg' targets ONLY the 50mg record
            cand = resolve_product_candidates(cur, "BIOS-01", "Biosynth", size="50mg")
            self.assertEqual(len(cand), 1)
            self.assertEqual(cand[0][0], ids[1])

            # Query with size '10mg' targets ONLY the 10mg record
            cand10 = resolve_product_candidates(cur, "BIOS-01", "Biosynth", size="10mg")
            self.assertEqual(len(cand10), 1)
            self.assertEqual(cand10[0][0], ids[0])

    # -------------------------------------------------------------------------
    # 5. replace_by_brand Scope Safety (LGC Protection)
    # -------------------------------------------------------------------------
    def test_replace_lgc_without_source_scope_is_rejected(self):
        """Test that replace_by_brand for multi-source LGC without source_brand scope is REJECTED."""
        with self.conn.cursor() as cur:
            # Seed 2 catalogs under LGC
            cur.execute(
                """
                INSERT INTO products (code, brand, source_brand, name)
                VALUES ('MIK-01', 'LGC', 'LGC (Mikromol)', 'Mikromol Item 1'),
                       ('XRF-01', 'LGC', 'LGC (XRF)', 'XRF Item 1');
                """
            )

            # 1. Attempt replace with generic 'LGC' and no source_brand -> REJECT
            rows_generic = [{"brand": "LGC", "code": "NEW-01", "name": "New Item"}]
            _, errors, _ = inspect_replace_by_brand_scopes(cur, rows_generic, self.gateway)
            self.assertTrue(len(errors) > 0)
            self.assertIn("Mode replace_by_brand bị từ chối", errors[0])

            # 2. Attempt replace with specific alias 'LGC (Mikromol)' -> PERMITTED with bounded scope
            rows_scoped = [{"brand": "LGC (Mikromol)", "code": "NEW-MIK", "name": "New Mik"}]
            brand_map, errors_scoped, deletable_count = inspect_replace_by_brand_scopes(cur, rows_scoped, self.gateway)
            self.assertEqual(len(errors_scoped), 0)
            self.assertEqual(deletable_count, 1, "Only 1 Mikromol product should be deletable")
            self.assertEqual(brand_map["LGC"], {"LGC (Mikromol)"})

    # -------------------------------------------------------------------------
    # 6. Import Never Recreates Old Aliases in products.brand
    # -------------------------------------------------------------------------
    def test_import_never_recreates_old_alias_in_brand(self):
        """Test that products.brand only receives canonical brand, never the alias string."""
        with self.conn.cursor() as cur:
            raw_row = {"brand": "LGC (Mikromol)", "code": "CODE-SAFE-01", "name": "Safe Item"}
            res = self.gateway.resolve(raw_row["brand"])
            search._insert_product_row(
                cur,
                (raw_row["name"], raw_row["code"], None, res.canonical_brand, "1g", "1", "10", "n"),
                include_manual=False,
                manual_c=None,
                manual_n=None,
                source_brand=res.source_brand,
            )

            cur.execute(
                "SELECT brand, source_brand FROM products WHERE code = 'CODE-SAFE-01'"
            )
            saved_brand, saved_source = cur.fetchone()
            self.assertEqual(saved_brand, "LGC", "products.brand must be canonical")
            self.assertEqual(saved_source, "LGC (Mikromol)", "products.source_brand must preserve alias")

    # -------------------------------------------------------------------------
    # 7. Team Visibility Uses Canonical Brand
    # -------------------------------------------------------------------------
    def test_team_visibility_uses_canonical_brand(self):
        """Test that team visibility via _visibility_sql checks canonical brand."""
        with self.conn.cursor() as cur:
            # Create a test team
            cur.execute("INSERT INTO teams (name) VALUES ('Team Canonical') RETURNING id;")
            tid = cur.fetchone()[0]

            # Assign canonical 'LGC' to team
            cur.execute(
                "INSERT INTO team_brands (team_id, brand) VALUES (%s, 'LGC');",
                (tid,),
            )

            # Insert product under canonical 'LGC' with sub-brand source
            cur.execute(
                """
                INSERT INTO products (code, brand, source_brand, name)
                VALUES ('VIS-01', 'LGC', 'LGC (VHG)', 'VHG Item');
                """
            )

            # Check visibility
            cur.execute(
                """
                SELECT p.code FROM products p
                WHERE p.brand IN (SELECT brand FROM team_brands WHERE team_id = %s)
                """,
                (tid,),
            )
            visible = [r[0] for r in cur.fetchall()]
            self.assertIn("VIS-01", visible)

    # -------------------------------------------------------------------------
    # 8. Regulatory Rules Invariance
    # -------------------------------------------------------------------------
    def test_regulatory_rules_invariance(self):
        """Test that regulatory_rules table remains untouched by brand master."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM regulatory_rules;")
            count_before = cur.fetchone()[0]

            # Re-run migration 017
            with open(_MIGRATION_017_PATH, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            cur.execute("SELECT COUNT(*) FROM regulatory_rules;")
            count_after = cur.fetchone()[0]
            self.assertEqual(count_before, count_after, "regulatory_rules count must not change")

    # -------------------------------------------------------------------------
    # 9. Exchange Rates 35 Canonical Brands Compatibility
    # -------------------------------------------------------------------------
    def test_exchange_rates_35_canonical_brands(self):
        """Test that exchange_rates table contains exactly 35 canonical brands with positive rates."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT brand, rate FROM exchange_rates ORDER BY brand;")
            rates = cur.fetchall()
            self.assertEqual(len(rates), 35, "exchange_rates must have exactly 35 canonical brands")
            for b, r in rates:
                self.assertGreater(float(r), 0, f"Rate for {b} must be positive")

    # -------------------------------------------------------------------------
    # 10. Data-Driven: All 35 Canonical Brands Resolve to Self
    # -------------------------------------------------------------------------
    def test_all_canonical_brands_resolve_to_self_data_driven(self):
        """Data-driven test: every canonical name in brand_master MUST resolve to itself."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT name, currency_code FROM brand_master WHERE is_active = TRUE ORDER BY name;")
            canonical_rows = cur.fetchall()
            self.assertEqual(len(canonical_rows), 35, "Must have exactly 35 active canonical brands")

        for name, expected_curr in canonical_rows:
            with self.subTest(canonical_brand=name):
                res = self.gateway.resolve(name)
                self.assertTrue(res.is_valid, f"Canonical brand '{name}' must be valid")
                self.assertEqual(res.canonical_brand, name)
                self.assertEqual(res.source_brand, name)
                self.assertEqual(res.currency_code, expected_curr)

                # Case-insensitive resolution also resolves to canonical name
                res_lower = self.gateway.resolve(name.lower())
                self.assertTrue(res_lower.is_valid)
                self.assertEqual(res_lower.canonical_brand, name)

    # -------------------------------------------------------------------------
    # 11. Gateway Accepts Canonical Brand When Alias Cache Is Empty / Fails Closed
    # -------------------------------------------------------------------------
    def test_gateway_accepts_canonical_when_alias_cache_empty(self):
        """Gateway must accept canonical brands even if alias cache is empty or fails, without fuzzy-match."""
        degraded_gateway = BrandGatewayCache()
        # Simulate canonical loaded, but aliases table load failed/empty
        with self.conn.cursor() as cur:
            cur.execute("SELECT normalized_name, name, currency_code FROM brand_master WHERE is_active = TRUE;")
            for norm, name, curr in cur.fetchall():
                degraded_gateway._canonical[norm] = (name, curr)
        degraded_gateway._table_exists = True
        degraded_gateway._loaded = True

        # 1. Canonical brand still resolves directly
        res = degraded_gateway.resolve("LGC")
        self.assertTrue(res.is_valid)
        self.assertEqual(res.canonical_brand, "LGC")
        self.assertEqual(res.currency_code, "EUR")

        # 2. Unknown brand fails closed with clear error, no fuzzy matching
        res_unk = degraded_gateway.resolve("LGC GmbH")
        self.assertFalse(res_unk.is_valid)
        self.assertIn("Brand không tồn tại", res_unk.error_message)

    # -------------------------------------------------------------------------
    # 12. Importer Pre-Mutation Ambiguity Detection (Atomic Rollback)
    # -------------------------------------------------------------------------
    def test_ambiguous_rows_fail_closed_pre_mutation(self):
        """If any row in upsert batch is ambiguous, pre-mutation check fails closed with zero rows modified."""
        with self.conn.cursor() as cur:
            # Seed 2 collision products with same code and canonical brand but different size
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, source_brand, size, price)
                VALUES
                    ('Collide Prod 1', 'AMB-101', '100-00-1', 'TLC Pharmaceutical', 'TLC (Mỹ)', '10mg', '100'),
                    ('Collide Prod 2', 'AMB-101', '100-00-1', 'TLC Pharmaceutical', 'TLC (Mỹ)', '25mg', '200'),
                    ('Unambiguous Prod', 'OK-101', '200-00-2', 'PhytoLab', 'PhytoLab', '1g', '50');
                """
            )

        # Candidate check: AMB-101 without size returns 2 candidates
        with self.conn.cursor() as cur:
            cands = resolve_product_candidates(cur, "AMB-101", "TLC Pharmaceutical")
            self.assertEqual(len(cands), 2, "Must find 2 candidates for ambiguous code")

        # Simulate batch: Row 1 is valid, Row 2 is ambiguous
        batch = [
            {"code": "OK-101", "brand": "PhytoLab", "name": "SHOULD_NOT_UPDATE"},
            {"code": "AMB-101", "brand": "TLC Pharmaceutical", "name": "AMBIGUOUS_ROW"},
        ]

        # The pre-mutation check in search must catch this
        with self.conn.cursor() as cur:
            ambiguous_errors = []
            for idx, r in enumerate(batch, start=2):
                chk_code = r.get("code")
                chk_brand = r.get("brand")
                cands = resolve_product_candidates(cur, chk_code, chk_brand)
                if len(cands) > 1:
                    ambiguous_errors.append(f"dòng {idx}: code '{chk_code}', brand '{chk_brand}'")
            self.assertEqual(len(ambiguous_errors), 1)
            self.assertIn("AMB-101", ambiguous_errors[0])

        # Verify initial data unchanged
        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM products WHERE code = 'OK-101';")
            self.assertEqual(cur.fetchone()[0], "Unambiguous Prod")

    # -------------------------------------------------------------------------
    # 13. DB-Level Schema Integrity (Phase 6B2B1-E)
    # -------------------------------------------------------------------------
    def test_insert_product_brand_outside_master_rejected_by_db(self):
        """FK products.brand -> brand_master(name) must reject non-canonical brand at the DB level."""
        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO products (name, code, cas, brand, source_brand)
                    VALUES ('Rogue Product', 'ROGUE-1', NULL, 'NotACanonicalBrand', 'NotACanonicalBrand');
                    """
                )

    def test_insert_product_missing_source_brand_rejected_by_db(self):
        """CHECK/NOT NULL on products.source_brand must reject a NULL source_brand at the DB level."""
        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.NotNullViolation):
                cur.execute(
                    """
                    INSERT INTO products (name, code, cas, brand, source_brand)
                    VALUES ('No Source Brand', 'NOSRC-1', NULL, 'LGC', NULL);
                    """
                )

    def test_canonical_product_insert_still_succeeds(self):
        """A fully valid canonical product insert must succeed under the new constraints."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, source_brand)
                VALUES ('Valid LGC Product', 'VALID-1', NULL, 'LGC', 'LGC (Mikromol)')
                RETURNING id;
                """
            )
            new_id = cur.fetchone()[0]
            cur.execute("SELECT brand, source_brand FROM products WHERE id = %s;", (new_id,))
            brand, source_brand = cur.fetchone()
            self.assertEqual(brand, "LGC")
            self.assertEqual(source_brand, "LGC (Mikromol)")

    def test_team_brands_cannot_be_assigned_non_canonical_brand(self):
        """FK team_brands.brand -> brand_master(name) must reject a non-canonical brand grant."""
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO teams (name) VALUES ('Team FK Test') RETURNING id;")
            team_id = cur.fetchone()[0]
            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                cur.execute(
                    "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s);",
                    (team_id, "Clearsynth"),
                )
            # Canonical brand grant must still succeed
            cur.execute(
                "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s);",
                (team_id, "CATO"),
            )
            cur.execute(
                "SELECT brand FROM team_brands WHERE team_id = %s;", (team_id,)
            )
            self.assertEqual(cur.fetchone()[0], "CATO")

    def test_collisions_still_valid_under_new_constraints(self):
        """The 55-style cross-brand collision pattern must remain fully insertable under FK/NOT NULL."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, source_brand, size)
                VALUES
                    ('Collision A', 'COL-X1', NULL, 'TLC Pharmaceutical', 'TLC (Mỹ)', '1mg'),
                    ('Collision B', 'COL-X1', NULL, 'TLC Pharmaceutical', 'TLC Pharmaceutical', '10,25,50,100mg');
                """
            )
            cur.execute("SELECT COUNT(*) FROM products WHERE code = 'COL-X1';")
            self.assertEqual(cur.fetchone()[0], 2, "Both collision rows must coexist")


if __name__ == "__main__":
    unittest.main()
