"""Tests for optional Compliance / Compliance_Note product import (Phase 008A).

Phase 6A -- Local Release Gate: `ManualComplianceImportIntegrationTests`
used to connect straight to whatever `DATABASE_URL` pointed at (in practice
`products_local`, per `.env`); it now creates its own throwaway,
uniquely-named database (see `tests/pg_temp_db.py`) and patches
`DATABASE_URL` for the whole class, never touching `products_local`.
"""

import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import psycopg2
from dotenv import load_dotenv
from openpyxl import Workbook

load_dotenv(dotenv_path=".env")

import search  # noqa: E402
from auth_test_helpers import auth_db_patch  # noqa: E402
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable  # noqa: E402
from product_import_manual import (  # noqa: E402
    HEADER_COMPLIANCE,
    HEADER_MODE_ABSENT,
    HEADER_NOTE,
    HEADER_PREPARATION_TYPE,
    classify_manual_compliance_headers,
    fetch_manual_compliance_snapshot,
    fetch_preparation_type_snapshot,
    normalize_manual_compliance_value,
    normalize_preparation_type_value,
    resolve_manual_fields_for_write,
    resolve_preparation_type_for_write,
    validate_product_import_rows,
)

ROOT = Path(__file__).resolve().parents[1]


def _xlsx_bytes(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


class ManualComplianceValidationTests(unittest.TestCase):
    def test_preparation_type_migration_is_nullable_and_canonical(self):
        sql = (ROOT / "sql" / "migration_012_product_preparation_type.sql").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS preparation_type TEXT NULL", sql)
        self.assertIn("products_preparation_type_check", sql)
        for value in ("NEAT", "SOLUTION", "MIXTURE", "OTHER"):
            self.assertIn(value, sql)
        self.assertNotIn("UPDATE PRODUCTS", sql.upper())

    def test_partial_header_rejected(self):
        with self.assertRaisesRegex(ValueError, "Thiếu: Compliance_Note"):
            validate_product_import_rows([], {HEADER_COMPLIANCE})

    def test_note_without_compliance_rejected_with_row(self):
        rows = [{HEADER_COMPLIANCE: "", HEADER_NOTE: "needs license"}]
        with self.assertRaisesRegex(ValueError, "Dòng 2"):
            validate_product_import_rows(rows, {HEADER_COMPLIANCE, HEADER_NOTE})

    def test_unknown_compliance_rejected_with_row(self):
        rows = [{HEADER_COMPLIANCE: "Banned forever", HEADER_NOTE: ""}]
        with self.assertRaisesRegex(ValueError, "Dòng 2"):
            validate_product_import_rows(rows, {HEADER_COMPLIANCE, HEADER_NOTE})

    def test_canonical_normalization(self):
        self.assertEqual(normalize_manual_compliance_value("  cấm nhập "), "Cấm nhập")
        self.assertEqual(normalize_manual_compliance_value("phụ lục ii"), "Phụ lục II")

    def test_manual_compliance_without_code_rejected_in_validation(self):
        rows = [{HEADER_COMPLIANCE: "Được bán", HEADER_NOTE: "", "code": ""}]
        with self.assertRaisesRegex(ValueError, "Dòng 2: Cần Code"):
            validate_product_import_rows(rows, {HEADER_COMPLIANCE, HEADER_NOTE})

    def test_absent_headers_skip_validation(self):
        validate_product_import_rows([{"brand": "Sigma"}], {"brand"})

    def test_preparation_type_alias_normalization(self):
        self.assertEqual(normalize_preparation_type_value(" pure "), "NEAT")
        self.assertEqual(normalize_preparation_type_value("Nguyên chất"), "NEAT")
        self.assertEqual(normalize_preparation_type_value("Dung dịch"), "SOLUTION")
        self.assertEqual(normalize_preparation_type_value("mix"), "MIXTURE")
        self.assertEqual(normalize_preparation_type_value("khác"), "OTHER")

    def test_liquid_is_not_solution_alias(self):
        with self.assertRaisesRegex(ValueError, "Preparation_Type"):
            normalize_preparation_type_value("LIQUID")

    def test_preparation_type_without_code_rejected(self):
        rows = [{HEADER_PREPARATION_TYPE: "NEAT", "code": ""}]
        with self.assertRaisesRegex(ValueError, "Dòng 2: Cần Code"):
            validate_product_import_rows(rows, {"brand", "code", HEADER_PREPARATION_TYPE})

    def test_invalid_preparation_type_reports_row(self):
        rows = [{HEADER_PREPARATION_TYPE: "LIQUID", "code": "P1"}]
        with self.assertRaisesRegex(ValueError, "Dòng 2"):
            validate_product_import_rows(rows, {"brand", "code", HEADER_PREPARATION_TYPE})


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class ManualComplianceImportIntegrationTests(unittest.TestCase):
    BRAND_A = "CURSOR_MANUAL_BRAND_A"
    BRAND_B = "CURSOR_MANUAL_BRAND_B"
    CODE_SHARED = "CURSOR-MANUAL-SHARED"

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        # unittest does NOT call tearDownClass if setUpClass raises --
        # anything below that fails would otherwise leak this temp DB
        # forever, so clean up ourselves on any exception past this point.
        try:
            # `test_preview_rejects_partial_headers` hits a real Flask
            # route (`/admin/imports/preview`) via `search.get_connection()`
            # == `db.get_connection()`, which reads `DATABASE_URL` fresh on
            # every call -- patch it for the whole class.
            cls._env_patch = mock.patch.dict("os.environ", {"DATABASE_URL": cls.dsn})
            cls._env_patch.start()
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True
            cls._reset_rows()
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

    @classmethod
    def _reset_rows(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM products
                WHERE UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
                   OR UPPER(TRIM(code)) = UPPER(TRIM(%s))
                """,
                ([cls.BRAND_A.upper(), cls.BRAND_B.upper()], cls.CODE_SHARED),
            )

    def _seed_product(self, *, brand, code, manual_c=None, manual_n=None, name="Seed", preparation_type=None):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note, preparation_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (name, code, "111-11-1", brand, "1g", "1", "100", "base note", manual_c, manual_n, preparation_type),
            )
            return cur.fetchone()[0]

    def _fetch_manual(self, product_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT manual_compliance, manual_compliance_note FROM products WHERE id = %s",
                (product_id,),
            )
            return cur.fetchone()

    def _fetch_preparation(self, product_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT preparation_type FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def _apply_rows(self, rows, mode="upsert", header_cols=None):
        header_cols = header_cols or {"brand", "code", "name", "cas", "size", "ship", "price", "note"}
        validate_product_import_rows(rows, header_cols)
        manual_header_mode = classify_manual_compliance_headers(header_cols)
        manual_snapshot = {}
        preparation_snapshot = {}
        with self.conn.cursor() as cur:
            if mode == "replace_by_brand":
                brands_norm = sorted(
                    {search._norm(r.get("brand")).strip().upper() for r in rows if search._norm(r.get("brand"))}
                )
                if manual_header_mode == HEADER_MODE_ABSENT:
                    manual_snapshot = fetch_manual_compliance_snapshot(cur, brands_norm)
                if HEADER_PREPARATION_TYPE not in header_cols:
                    preparation_snapshot = fetch_preparation_type_snapshot(cur, brands_norm)
                cur.execute(
                    """
                    DELETE FROM products
                    WHERE UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
                    """,
                    (brands_norm,),
                )
            for r in rows:
                vals = (
                    search._norm(r.get("name")),
                    search._norm(r.get("code")),
                    search._norm(r.get("cas")),
                    search._norm(r.get("brand")),
                    search._norm(r.get("size")),
                    search._norm(r.get("ship")),
                    search._norm(r.get("price")),
                    search._norm(r.get("note")),
                )
                code, brand = vals[1], vals[3]
                include_manual, manual_c, manual_n = resolve_manual_fields_for_write(
                    header_mode=manual_header_mode,
                    row=r,
                    code=code,
                    brand=brand,
                    snapshot=manual_snapshot,
                )
                include_preparation, preparation_type = resolve_preparation_type_for_write(
                    header_cols=header_cols,
                    row=r,
                    code=code,
                    brand=brand,
                    snapshot=preparation_snapshot,
                )
                cur.execute(
                    """
                    SELECT id FROM products
                    WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                      AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                    LIMIT 1
                    """,
                    (code, brand),
                )
                existing = cur.fetchone()
                if existing:
                    search._update_product_row(
                        cur,
                        vals,
                        existing[0],
                        include_manual,
                        manual_c,
                        manual_n,
                        include_preparation,
                        preparation_type,
                    )
                else:
                    search._insert_product_row(
                        cur,
                        vals,
                        include_manual,
                        manual_c,
                        manual_n,
                        include_preparation,
                        preparation_type,
                    )
        self.conn.commit()

    def test_upsert_without_new_columns_preserves_manual_values(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-KEEP-MANUAL",
            manual_c="Cấm nhập",
            manual_n="keep me",
        )
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-KEEP-MANUAL",
                    "name": "Updated name",
                    "cas": "111-11-1",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "updated note",
                }
            ]
        )
        manual_c, manual_n = self._fetch_manual(product_id)
        self.assertEqual(manual_c, "Cấm nhập")
        self.assertEqual(manual_n, "keep me")

    def test_valid_import_updates_and_normalizes(self):
        product_id = self._seed_product(brand=self.BRAND_A, code="CURSOR-SET-MANUAL")
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-SET-MANUAL",
                    "name": "Item",
                    "cas": "222-22-2",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_COMPLIANCE: "  được bán ",
                    HEADER_NOTE: " ok ",
                }
            ],
            header_cols={
                "brand",
                "code",
                "name",
                "cas",
                "size",
                "ship",
                "price",
                "note",
                HEADER_COMPLIANCE,
                HEADER_NOTE,
            },
        )
        manual_c, manual_n = self._fetch_manual(product_id)
        self.assertEqual(manual_c, "Được bán")
        self.assertEqual(manual_n, "ok")

    def test_blank_compliance_clears_override(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-CLEAR-MANUAL",
            manual_c="Phụ lục II",
            manual_n="old",
        )
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-CLEAR-MANUAL",
                    "name": "Item",
                    "cas": "333-33-3",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_COMPLIANCE: "",
                    HEADER_NOTE: "",
                }
            ],
            header_cols={
                "brand",
                "code",
                "name",
                "cas",
                "size",
                "ship",
                "price",
                "note",
                HEADER_COMPLIANCE,
                HEADER_NOTE,
            },
        )
        self.assertEqual(self._fetch_manual(product_id), (None, None))

    def test_atomic_reject_note_only(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-NO-NOTE-ONLY",
            manual_c="Chưa xác định",
            manual_n="stay",
        )
        with self.assertRaises(ValueError):
            self._apply_rows(
                [
                    {
                        "brand": self.BRAND_A,
                        "code": "CURSOR-NO-NOTE-ONLY",
                        "name": "Item",
                        "cas": "444-44-4",
                        "size": "1g",
                        "ship": "1",
                        "price": "100",
                        "note": "n",
                        HEADER_COMPLIANCE: "",
                        HEADER_NOTE: "orphan note",
                    }
                ],
                header_cols={
                    "brand",
                    "code",
                    "name",
                    "cas",
                    "size",
                    "ship",
                    "price",
                    "note",
                    HEADER_COMPLIANCE,
                    HEADER_NOTE,
                },
            )
        self.assertEqual(self._fetch_manual(product_id), ("Chưa xác định", "stay"))

    def test_same_code_different_brands_stay_separate(self):
        id_a = self._seed_product(brand=self.BRAND_A, code=self.CODE_SHARED, manual_c="Cấm nhập", manual_n="A")
        id_b = self._seed_product(brand=self.BRAND_B, code=self.CODE_SHARED, manual_c="Được bán", manual_n="B")
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": self.CODE_SHARED,
                    "name": "A",
                    "cas": "555-55-5",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_COMPLIANCE: "Phụ lục III",
                    HEADER_NOTE: "only A",
                }
            ],
            header_cols={
                "brand",
                "code",
                "name",
                "cas",
                "size",
                "ship",
                "price",
                "note",
                HEADER_COMPLIANCE,
                HEADER_NOTE,
            },
        )
        self.assertEqual(self._fetch_manual(id_a), ("Phụ lục III", "only A"))
        self.assertEqual(self._fetch_manual(id_b), ("Được bán", "B"))

    def test_manual_compliance_without_code_rejects_atomically(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-NO-CODE-MANUAL",
            manual_c="Cấm nhập",
            manual_n="stay",
        )
        with self.assertRaisesRegex(ValueError, "Dòng 2: Cần Code"):
            self._apply_rows(
                [
                    {
                        "brand": self.BRAND_A,
                        "code": "",
                        "name": "Legacy row",
                        "cas": "777-77-7",
                        "size": "1g",
                        "ship": "1",
                        "price": "100",
                        "note": "n",
                        HEADER_COMPLIANCE: "Được bán",
                        HEADER_NOTE: "no code",
                    }
                ],
                header_cols={
                    "brand",
                    "code",
                    "name",
                    "cas",
                    "size",
                    "ship",
                    "price",
                    "note",
                    HEADER_COMPLIANCE,
                    HEADER_NOTE,
                },
            )
        self.assertEqual(self._fetch_manual(product_id), ("Cấm nhập", "stay"))

    def test_no_cas_with_code_and_manual_compliance_succeeds(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                ("No CAS", "CURSOR-NO-CAS-MANUAL", "", self.BRAND_A, "1g", "1", "100", "n"),
            )
            product_id = cur.fetchone()[0]
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-NO-CAS-MANUAL",
                    "name": "No CAS",
                    "cas": "",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_COMPLIANCE: "Chưa xác định",
                    HEADER_NOTE: "missing cas ok",
                }
            ],
            header_cols={
                "brand",
                "code",
                "name",
                "cas",
                "size",
                "ship",
                "price",
                "note",
                HEADER_COMPLIANCE,
                HEADER_NOTE,
            },
        )
        manual_c, manual_n = self._fetch_manual(product_id)
        self.assertEqual(manual_c, "Chưa xác định")
        self.assertEqual(manual_n, "missing cas ok")

    def test_no_code_without_manual_fields_keeps_legacy_insert(self):
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "",
                    "name": "Legacy no code",
                    "cas": "888-88-8",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "legacy",
                }
            ],
            header_cols={
                "brand",
                "code",
                "name",
                "cas",
                "size",
                "ship",
                "price",
                "note",
                HEADER_COMPLIANCE,
                HEADER_NOTE,
            },
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM products
                WHERE brand = %s
                  AND name = %s
                  AND NULLIF(TRIM(code), '') IS NULL
                """,
                (self.BRAND_A, "Legacy no code"),
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_normalized_code_brand_restores_manual_snapshot(self):
        self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-NORM-RESTORE",
            manual_c="Phụ lục II",
            manual_n="normalized",
        )
        self._apply_rows(
            [
                {
                    "brand": f"  {self.BRAND_A.lower()}  ",
                    "code": "  cursor-norm-restore ",
                    "name": "Reloaded norm",
                    "cas": "",
                    "size": "2g",
                    "ship": "1",
                    "price": "200",
                    "note": "new note",
                }
            ],
            mode="replace_by_brand",
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT manual_compliance, manual_compliance_note, name
                FROM products
                WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                """,
                ("cursor-norm-restore", self.BRAND_A),
            )
            manual_c, manual_n, name = cur.fetchone()
        self.assertEqual((manual_c, manual_n), ("Phụ lục II", "normalized"))
        self.assertEqual(name, "Reloaded norm")

    def test_replace_by_brand_without_columns_restores_manual_snapshot(self):
        self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-REPLACE-KEEP",
            manual_c="Cần giấy phép",
            manual_n="restore",
        )
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-REPLACE-KEEP",
                    "name": "Reloaded",
                    "cas": "666-66-6",
                    "size": "2g",
                    "ship": "1",
                    "price": "200",
                    "note": "new note",
                }
            ],
            mode="replace_by_brand",
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT manual_compliance, manual_compliance_note, name, note
                FROM products
                WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                """,
                ("CURSOR-REPLACE-KEEP", self.BRAND_A),
            )
            manual_c, manual_n, name, note = cur.fetchone()
        self.assertEqual((manual_c, manual_n), ("Cần giấy phép", "restore"))
        self.assertEqual(name, "Reloaded")
        self.assertEqual(note, "new note")

    def test_preparation_header_absent_preserves_existing_value(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-KEEP-PREP",
            preparation_type="SOLUTION",
        )
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-KEEP-PREP",
                    "name": "Updated prep keep",
                    "cas": "101-01-1",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                }
            ]
        )
        self.assertEqual(self._fetch_preparation(product_id), "SOLUTION")

    def test_preparation_header_updates_and_blank_clears(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-SET-PREP",
            preparation_type="NEAT",
        )
        header_cols = {"brand", "code", "name", "cas", "size", "ship", "price", "note", HEADER_PREPARATION_TYPE}
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-SET-PREP",
                    "name": "Updated prep",
                    "cas": "202-02-2",
                    "size": "100mg",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_PREPARATION_TYPE: "dung dịch",
                }
            ],
            header_cols=header_cols,
        )
        self.assertEqual(self._fetch_preparation(product_id), "SOLUTION")

        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "CURSOR-SET-PREP",
                    "name": "Cleared prep",
                    "cas": "202-02-2",
                    "size": "100mg",
                    "ship": "1",
                    "price": "100",
                    "note": "n",
                    HEADER_PREPARATION_TYPE: "",
                }
            ],
            header_cols=header_cols,
        )
        self.assertIsNone(self._fetch_preparation(product_id))

    def test_invalid_preparation_rejects_atomically(self):
        product_id = self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-BAD-PREP",
            preparation_type="NEAT",
        )
        with self.assertRaisesRegex(ValueError, "Dòng 2"):
            self._apply_rows(
                [
                    {
                        "brand": self.BRAND_A,
                        "code": "CURSOR-BAD-PREP",
                        "name": "Bad prep",
                        "cas": "303-03-3",
                        "size": "1g",
                        "ship": "1",
                        "price": "100",
                        "note": "n",
                        HEADER_PREPARATION_TYPE: "LIQUID",
                    }
                ],
                header_cols={"brand", "code", "name", "cas", "size", "ship", "price", "note", HEADER_PREPARATION_TYPE},
            )
        self.assertEqual(self._fetch_preparation(product_id), "NEAT")

    def test_no_code_with_blank_preparation_keeps_legacy_insert(self):
        self._apply_rows(
            [
                {
                    "brand": self.BRAND_A,
                    "code": "",
                    "name": "Legacy no code blank prep",
                    "cas": "404-04-4",
                    "size": "1g",
                    "ship": "1",
                    "price": "100",
                    "note": "legacy",
                    HEADER_PREPARATION_TYPE: "",
                }
            ],
            header_cols={"brand", "code", "name", "cas", "size", "ship", "price", "note", HEADER_PREPARATION_TYPE},
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM products
                WHERE brand = %s
                  AND name = %s
                  AND NULLIF(TRIM(code), '') IS NULL
                """,
                (self.BRAND_A, "Legacy no code blank prep"),
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_replace_by_brand_without_preparation_header_restores_snapshot(self):
        self._seed_product(
            brand=self.BRAND_A,
            code="CURSOR-REPLACE-PREP",
            preparation_type="MIXTURE",
        )
        self._apply_rows(
            [
                {
                    "brand": f" {self.BRAND_A.lower()} ",
                    "code": " cursor-replace-prep ",
                    "name": "Reloaded prep",
                    "cas": "505-05-5",
                    "size": "2g",
                    "ship": "1",
                    "price": "200",
                    "note": "new note",
                }
            ],
            mode="replace_by_brand",
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT preparation_type, name
                FROM products
                WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                """,
                ("CURSOR-REPLACE-PREP", self.BRAND_A),
            )
            preparation_type, name = cur.fetchone()
        self.assertEqual(preparation_type, "MIXTURE")
        self.assertEqual(name, "Reloaded prep")

    def test_preview_rejects_partial_headers(self):
        bio = _xlsx_bytes(
            ["brand", "code", "Compliance"],
            [[self.BRAND_A, "CURSOR-PARTIAL", "Được bán"]],
        )
        search.app.testing = True
        with search.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user_id"] = 1
                sess["auth_version"] = 1
                sess["is_admin"] = True
            # Phase 5D2A: stub the per-request session-liveness DB check
            # with an in-memory fake (no real Postgres touched).
            with auth_db_patch(user_id=1, auth_version=1):
                response = client.post(
                    "/admin/imports/preview",
                    data={
                        "dataset": "products",
                        "mode": "upsert",
                        "file": (bio, "partial.xlsx"),
                    },
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 302)
        self.assertIn("Compliance_Note", response.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()
