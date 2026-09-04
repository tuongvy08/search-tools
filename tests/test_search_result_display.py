"""Tests for /search table display fields and Copy Selected column output."""

import os
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search  # noqa: E402
from auth_test_helpers import start_auth_db_patch  # noqa: E402
from compliance_resolver import resolve_compliance_precedence  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_JS = ROOT / "static" / "script.js"
INDEX_HTML = ROOT / "templates" / "index.html"


def _local_dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    host = urlparse(dsn).hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return dsn


def _product_note(product):
    return product.get("Note") or product.get("note") or ""


def _product_compliance(product):
    return product.get("compliance") or product.get("Compliance_Status") or ""


def _product_compliance_note(product):
    return product.get("compliance_note") or product.get("Compliance_Note") or ""


def _excel_safe_cell(value):
    s = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    if s and s[0] in "=+-@":
        return "'" + s
    return s


def _product_row_key(product):
    return "\x1f".join(
        [
            product.get("Code") or "",
            product.get("Brand") or "",
            product.get("Cas") or "",
            product.get("Size") or "",
            product.get("Name") or "",
        ]
    )


def build_copy_row(product):
    columns = [
        ("Name", product.get("Name", "")),
        ("Code", product.get("Code", "")),
        ("Cas", product.get("Cas", "")),
        ("Brand", product.get("Brand", "")),
        ("Size", product.get("Size", "")),
        ("Unit_Price", product.get("Unit_Price", "")),
        ("Note", _product_note(product)),
        ("Compliance", _product_compliance(product)),
        ("Compliance_Note", _product_compliance_note(product)),
    ]
    return "\t".join(_excel_safe_cell(value) for _label, value in columns)


def build_copy_payload(displayed_products, selected_keys):
    products = [p for p in displayed_products if _product_row_key(p) in selected_keys]
    if not products:
        return ""
    return "\n".join(build_copy_row(product) for product in products)


COPY_HEADER_LINE = "\t".join(
    ["Name", "Code", "Cas", "Brand", "Size", "Unit_Price", "Note", "Compliance", "Compliance_Note"]
)


class SearchDisplayStaticTests(unittest.TestCase):
    def test_index_table_has_separate_compliance_note_column(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('name="viewport"', html)
        self.assertIn("results-table-wrap", html)
        self.assertIn("<th>Compliance Note</th>", html)
        self.assertIn("overflow-x", (ROOT / "static" / "styles.css").read_text(encoding="utf-8"))

    def test_script_has_compliance_classes_and_copy_columns(self):
        js = SCRIPT_JS.read_text(encoding="utf-8")
        self.assertIn("'Được bán': 'warning-duoc-ban'", js)
        self.assertIn("'Chưa xác định': 'warning-chua-xac-dinh'", js)
        self.assertIn("'Không phát hiện hạn chế': 'warning-khong-phat-hien'", js)
        self.assertIn("label: 'Compliance_Note'", js)
        self.assertIn("cell.textContent", js)
        self.assertNotIn("button-brand", js)


class SearchDisplayResolverTests(unittest.TestCase):
    def test_manual_duoc_ban_badge_class(self):
        resolved = resolve_compliance_precedence(
            brand_manual_enabled=True,
            manual_compliance="Được bán",
            manual_compliance_note="ok",
            legacy_compliance="CẤM NHẬP",
            legacy_compliance_note="legacy",
            cas="123-45-6",
        )
        self.assertEqual(resolved["compliance"], "Được bán")
        self.assertEqual(resolved["compliance_css"], "warning-duoc-ban")
        self.assertEqual(resolved["compliance_note"], "ok")

    def test_legacy_category_and_note(self):
        resolved = resolve_compliance_precedence(
            brand_manual_enabled=False,
            manual_compliance="",
            manual_compliance_note="",
            legacy_compliance="Phụ lục II",
            legacy_compliance_note="legacy note",
            cas="123-45-6",
        )
        self.assertEqual(resolved["compliance"], "Phụ lục II")
        self.assertEqual(resolved["compliance_note"], "legacy note")
        self.assertEqual(resolved["compliance_css"], "warning-phu-luc-ii")

    def test_missing_cas_is_amber_not_green(self):
        resolved = resolve_compliance_precedence(
            brand_manual_enabled=False,
            manual_compliance="",
            manual_compliance_note="",
            legacy_compliance="",
            legacy_compliance_note="",
            cas="",
        )
        self.assertEqual(resolved["compliance"], "Chưa xác định")
        self.assertEqual(resolved["compliance_css"], "warning-chua-xac-dinh")

    def test_nonmatching_cas_is_neutral_not_approval(self):
        resolved = resolve_compliance_precedence(
            brand_manual_enabled=False,
            manual_compliance="",
            manual_compliance_note="",
            legacy_compliance="",
            legacy_compliance_note="",
            cas="999-99-9",
        )
        self.assertEqual(resolved["compliance"], "Không phát hiện hạn chế")
        self.assertEqual(resolved["compliance_css"], "warning-khong-phat-hien")


class SearchDisplayCopyTests(unittest.TestCase):
    def test_product_and_compliance_notes_remain_separate(self):
        product = {
            "Name": "A",
            "Code": "C1",
            "Cas": "1-1-1",
            "Brand": "B",
            "Size": "1g",
            "Unit_Price": "1,000",
            "Note": "product only",
            "compliance": "Phụ lục II",
            "compliance_note": "reg note",
        }
        row = build_copy_row(product)
        self.assertIn("product only", row)
        self.assertIn("reg note", row)
        self.assertNotIn("Compliance:", row)
        self.assertNotIn("|", row)

    def test_empty_values_and_backward_compatible_keys(self):
        legacy = {
            "Name": "Legacy",
            "Code": "L1",
            "Cas": "",
            "Brand": "X",
            "Size": "1g",
            "Unit_Price": "100",
            "Note": "",
            "Compliance_Status": "CẤM NHẬP",
        }
        row = build_copy_row(legacy)
        self.assertTrue(row.endswith("\tCẤM NHẬP\t"))
        self.assertEqual(_product_compliance_note(legacy), "")

    def test_long_text_is_preserved_as_plain_text(self):
        long_note = "x" * 120
        long_compliance_note = "y" * 120
        product = {
            "Name": "Long",
            "Code": "L2",
            "Cas": "2-2-2",
            "Brand": "Z",
            "Size": "1g",
            "Unit_Price": "100",
            "Note": long_note,
            "compliance": "Được bán",
            "compliance_note": long_compliance_note,
        }
        row = build_copy_row(product)
        self.assertIn(long_note, row)
        self.assertIn(long_compliance_note, row)
        self.assertNotRegex(row, re.compile(r"<[^>]+>"))


class SearchDisplayCopySelectionTests(unittest.TestCase):
    @staticmethod
    def _sample_products():
        return [
            {
                "Name": "First",
                "Code": "C1",
                "Cas": "1-1-1",
                "Brand": "B1",
                "Size": "1g",
                "Unit_Price": "100",
                "Note": "note one",
                "compliance": "Được bán",
                "compliance_note": "ok one",
            },
            {
                "Name": "Second",
                "Code": "C2",
                "Cas": "2-2-2",
                "Brand": "B2",
                "Size": "2g",
                "Unit_Price": "200",
                "Note": "note two",
                "compliance": "Phụ lục II",
                "compliance_note": "ok two",
            },
            {
                "Name": "Third",
                "Code": "C3",
                "Cas": "3-3-3",
                "Brand": "B3",
                "Size": "3g",
                "Unit_Price": "300",
                "Note": "note three",
                "compliance": "CẤM NHẬP",
                "compliance_note": "ok three",
            },
        ]

    def test_one_selected_row_copies_one_line_without_header(self):
        products = self._sample_products()
        payload = build_copy_payload(products, {_product_row_key(products[0])})
        self.assertEqual(payload, build_copy_row(products[0]))
        self.assertNotIn("\n", payload)
        self.assertNotIn(COPY_HEADER_LINE, payload)

    def test_multiple_selected_rows_match_display_order(self):
        products = self._sample_products()
        selected = {_product_row_key(products[0]), _product_row_key(products[2])}
        payload = build_copy_payload(products, selected)
        lines = payload.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], build_copy_row(products[0]))
        self.assertEqual(lines[1], build_copy_row(products[2]))

    def test_header_labels_absent_from_clipboard_payload(self):
        products = self._sample_products()
        payload = build_copy_payload(products, {_product_row_key(p) for p in products})
        self.assertNotIn(COPY_HEADER_LINE, payload)
        for label in ("Name", "Unit_Price", "Compliance_Note"):
            self.assertFalse(payload.startswith(f"{label}\t"))

    def test_zero_selection_yields_empty_payload(self):
        products = self._sample_products()
        self.assertEqual(build_copy_payload(products, set()), "")

    def test_script_copy_selected_uses_displayed_products_without_header(self):
        js = SCRIPT_JS.read_text(encoding="utf-8")
        fn_start = js.index("function copySelectedRows()")
        fn_end = js.index("function _excelSafeCell", fn_start)
        block = js[fn_start:fn_end]
        self.assertIn("displayedProducts.filter", block)
        self.assertNotIn("headers.join", block)
        self.assertNotIn("col.label", block)

    def test_copy_button_disabled_when_no_selection(self):
        js = SCRIPT_JS.read_text(encoding="utf-8")
        ui_start = js.index("function updateSelectionUI()")
        ui_end = js.index("function copySelectedRows()", ui_start)
        block = js[ui_start:ui_end]
        self.assertIn("btnCopySelected", block)
        self.assertIn("disabled = count === 0", block)

    def test_select_all_and_clear_selection_handlers_remain(self):
        js = SCRIPT_JS.read_text(encoding="utf-8")
        self.assertIn("selectAllRows", js)
        self.assertIn("btnClearSelection", js)
        self.assertIn("clearRowSelection", js)
        self.assertIn("indeterminate", js)
        self.assertIn("Bỏ chọn tất cả", INDEX_HTML.read_text(encoding="utf-8"))


@unittest.skipUnless(_local_dsn(), "local DATABASE_URL required")
class SearchDisplayIntegrationTests(unittest.TestCase):
    PREFIX = "CURSOR_DISPLAY"
    BRAND = "CURSOR_DISPLAY_BRAND"
    CODE = "CURSOR-DISPLAY-CODE"
    CAS = "CURSOR-DISPLAY-CAS"

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(_local_dsn())
        cls.conn.autocommit = True
        cls._ensure_schema()
        cls._reset_fixture()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._cleanup_fixture()
        finally:
            cls.conn.close()

    @classmethod
    def _ensure_schema(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'brand_compliance_settings'
                """
            )
            if cur.fetchone() is None:
                raise unittest.SkipTest("Run sql/migration_011_manual_compliance.sql on local DB first.")

    @classmethod
    def _cleanup_fixture(cls):
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))", (cls.CODE,))
            cur.execute("DELETE FROM brand_compliance_settings WHERE brand_norm = %s", (cls.BRAND.upper(),))

    @classmethod
    def _reset_fixture(cls):
        cls._cleanup_fixture()
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"{cls.PREFIX} product",
                    cls.CODE,
                    cls.CAS,
                    cls.BRAND,
                    "1g",
                    "1",
                    "100",
                    "product note separate",
                    "Được bán",
                    "manual compliance note",
                ),
            )
            cur.execute(
                """
                INSERT INTO brand_compliance_settings (brand_norm, manual_compliance_priority)
                VALUES (%s, TRUE)
                ON CONFLICT (brand_norm) DO UPDATE
                SET manual_compliance_priority = TRUE, updated_at = NOW()
                """,
                (cls.BRAND.upper(),),
            )

    def setUp(self):
        self._reset_fixture()
        # Phase 5D2A: stub the per-request session-liveness DB check with an
        # in-memory fake (no real Postgres touched) for every test here.
        start_auth_db_patch(self)

    def tearDown(self):
        self._cleanup_fixture()

    def _search_row(self):
        search.app.testing = True
        with search.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user_id"] = 1
                sess["auth_version"] = 1
                sess["is_admin"] = True
            response = client.get("/search", query_string={"query": self.CODE})
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["results"]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_search_api_returns_separated_fields_for_table(self):
        row = self._search_row()
        self.assertEqual(row["Note"], "product note separate")
        self.assertEqual(row["note"], "product note separate")
        self.assertEqual(row["compliance"], "Được bán")
        self.assertEqual(row["compliance_note"], "manual compliance note")
        self.assertEqual(row["Compliance_Css"], "warning-duoc-ban")
        self.assertNotEqual(row["Note"], row["compliance_note"])

    def test_copy_selected_output_from_search_row(self):
        row = self._search_row()
        copied = build_copy_row(row)
        self.assertIn("product note separate", copied)
        self.assertIn("manual compliance note", copied)
        self.assertIn("Được bán", copied)


if __name__ == "__main__":
    unittest.main()
