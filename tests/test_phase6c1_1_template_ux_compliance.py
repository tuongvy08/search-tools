import hashlib
import json
import os
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import psycopg2

import search
from auth_test_helpers import start_auth_db_patch
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable
from test_quote_workbook_export import make_workbook


ROOT = Path(__file__).resolve().parents[1]


class _ValidRates:
    def resolve(self, _brand):
        return SimpleNamespace(is_valid=True, rate=Decimal("1"), status=None)


def _candidate_row(compliance):
    return (
        1, 101, "Sản phẩm", "CODE-1", "50-00-0", "Hãng A", "1g",
        Decimal("1"), Decimal("1000"), "", "NEAT", compliance, "",
        True, None, None,
    )


class ComplianceDecisionTests(unittest.TestCase):
    def test_unknown_is_warning_but_selectable_and_banned_import_is_blocked(self):
        unknown = search._quote_candidate_from_row(_candidate_row("Chưa xác định"), _ValidRates())
        self.assertTrue(unknown["eligible"])
        self.assertEqual(unknown["ineligible_reason"], "")
        self.assertIn("Chưa xác định", unknown["warnings"])
        selected, _status, _reason = search._quote_select_candidate(
            [unknown], search.QUOTE_SELECTION_LOWEST_OVERALL
        )
        self.assertEqual(selected["product_id"], 101)

        banned = search._quote_candidate_from_row(_candidate_row("CẤM NHẬP"), _ValidRates())
        self.assertFalse(banned["eligible"])
        self.assertEqual(banned["ineligible_reason"], search.REASON_COMPLIANCE_BLOCKED)

    def test_search_and_quick_quote_export_unknown_without_oracle(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [_candidate_row("Chưa xác định")]
        context = {
            "is_admin": True,
            "team_id": None,
            "template_id": None,
            "grants": frozenset({"VIEW_PRICE"}),
        }
        with mock.patch.object(search, "_load_pricing_resolver", return_value=_ValidRates()):
            products = search._quote_export_products(
                conn, [{"ord": 1, "product_id": 101}], context
            )
        self.assertEqual(products[0]["Compliance"], "Chưa xác định")

    def test_banned_import_message_is_vietnamese_and_permission_safe(self):
        def call(grants):
            conn = mock.MagicMock()
            conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [
                _candidate_row("CẤM NHẬP")
            ]
            context = {"is_admin": True, "team_id": None, "template_id": None,
                       "grants": frozenset(grants)}
            with mock.patch.object(search, "_load_pricing_resolver", return_value=_ValidRates()):
                return search._quote_export_products(
                    conn, [{"ord": 1, "product_id": 101}], context
                )

        with self.assertRaisesRegex(
            ValueError,
            r"Dòng 1 không thể xuất báo giá: Sản phẩm thuộc diện CẤM NHẬP\.",
        ):
            call({"VIEW_PRICE", "VIEW_COMPLIANCE"})
        with self.assertRaisesRegex(ValueError, r"^Dòng 1 không thể xuất báo giá\.$"):
            call({"VIEW_PRICE"})


class VietnameseTemplateUiTests(unittest.TestCase):
    def test_required_labels_and_no_raw_sales_labels(self):
        admin_html = (ROOT / "templates" / "admin_quote_templates.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "static" / "admin_quote_templates.js").read_text(encoding="utf-8")
        search_html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        quick_html = (ROOT / "templates" / "quick_quote.html").read_text(encoding="utf-8")
        quick_js = (ROOT / "static" / "quick_quote.js").read_text(encoding="utf-8")

        combined = "\n".join((admin_html, admin_js, search_html, quick_html, quick_js))
        for label in (
            "Mẫu mặc định toàn hệ thống",
            "Sẵn sàng gán cho team",
            "Đặt làm mẫu mặc định",
            "Dùng mẫu mặc định",
            "Tình trạng quản lý",
            "Ghi chú quản lý",
            "Team đang dùng",
            "Lưu trữ",
        ):
            self.assertIn(label, combined)
        for raw_label in (
            ">Compliance<", ">Compliance Note<", "'Inactive'",
            "'Fallback global active'", ">Kích hoạt<",
        ):
            self.assertNotIn(raw_label, combined)
        self.assertNotIn("'Chưa xác định'", quick_js.split("const QQ_BLOCKED_COMPLIANCE", 1)[1].split(";", 1)[0])

    def test_mobile_desktop_layout_and_font_awesome_5_icons(self):
        html = (ROOT / "templates" / "admin_quote_templates.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", html)
        self.assertIn("max-width: 1100px", html)
        self.assertIn("font-awesome/5.15.2", html)
        self.assertIn("fa-archive", html)
        self.assertIn(".assignment-row > * { min-width:0; width:100%; }", html)
        self.assertNotIn('class="card', html)

    def test_migration_is_additive_idempotent_and_rejects_partial_archive_state(self):
        sql = (ROOT / "sql" / "migration_023_quote_template_archiving.sql").read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS archived_at", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS archived_by", sql)
        self.assertIn("quote_templates_archived_not_active_check", sql)
        self.assertIn("COALESCE(length(trim(archived_by)) > 0, FALSE)", sql)
        self.assertNotIn("DROP ", sql.upper())


class ArchiveApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        start_auth_db_patch(self)
        self.env = mock.patch.dict(os.environ, DISABLE_IP_ALLOWLIST="1")
        self.env.start()
        self.addCleanup(self.env.stop)

    def _auth(self, admin=True):
        with self.client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=1, auth_version=1,
                        is_admin=admin, team_id=None if admin else 1,
                        csrf_token="csrf-test")

    def test_archive_is_admin_post_with_csrf(self):
        self.assertEqual(self.client.post("/api/admin/quote-templates/9/archive").status_code, 401)
        self._auth(admin=False)
        self.assertEqual(self.client.post(
            "/api/admin/quote-templates/9/archive",
            headers={"X-CSRF-Token": "csrf-test"},
        ).status_code, 403)

        self._auth(admin=True)
        self.assertEqual(self.client.post("/api/admin/quote-templates/9/archive").status_code, 400)
        conn = mock.MagicMock()
        archived = {"id": 9, "archived_at": "2026-09-08T00:00:00+00:00"}
        with mock.patch.object(search, "get_connection", return_value=conn), \
             mock.patch.object(search, "_archive_quote_template", return_value=archived) as action:
            response = self.client.post(
                "/api/admin/quote-templates/9/archive",
                headers={"X-CSRF-Token": "csrf-test"},
            )
        self.assertEqual(response.status_code, 200)
        action.assert_called_once_with(conn, 9)


@unittest.skipUnless(probe_postgres_reachable(), "local PostgreSQL required")
class ArchivePgIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dbname, cls.dsn = create_full_schema_temp_db()

    @classmethod
    def tearDownClass(cls):
        drop_temp_db(cls.dbname)

    def _seed(self):
        suffix = uuid4().hex[:8]
        raw = make_workbook()
        mapping = json.dumps(search._quote_template_mapping_snapshot(), ensure_ascii=False)
        conn = psycopg2.connect(self.dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("UPDATE quote_templates SET is_active=FALSE WHERE is_active=TRUE")
                cur.execute(
                    "INSERT INTO teams(name) VALUES (%s),(%s),(%s) RETURNING id",
                    (f"Team A {suffix}", f"Team B {suffix}", f"Team C {suffix}"),
                )
                teams = [row[0] for row in cur.fetchall()]
                ids = []
                for filename, active in ((f"global-{suffix}.xlsx", True),
                                         (f"team-{suffix}.xlsx", False),
                                         (f"idle-{suffix}.xlsx", False)):
                    cur.execute(
                        """
                        INSERT INTO quote_templates(
                            filename, content, content_sha256, content_size, profile_version,
                            mapping_json, mapping_v2_json, is_active, uploaded_by
                        ) VALUES (%s,%s,%s,%s,'BG_V1',%s::jsonb,%s::jsonb,%s,'test')
                        RETURNING id
                        """,
                        (filename, psycopg2.Binary(raw), hashlib.sha256(raw).hexdigest(),
                         len(raw), mapping, mapping, active),
                    )
                    ids.append(cur.fetchone()[0])
                cur.execute(
                    "INSERT INTO team_quote_templates(team_id,template_id,assigned_by) VALUES (%s,%s,'test'),(%s,%s,'test')",
                    (teams[0], ids[1], teams[1], ids[1]),
                )
            return teams, ids
        finally:
            conn.close()

    def test_multiple_templates_team_usage_and_archive_guards(self):
        teams, ids = self._seed()
        conn = psycopg2.connect(self.dsn)
        try:
            self.assertEqual(search._get_active_quote_template(conn, team_id=teams[0])["source"], "team")
            self.assertEqual(search._get_active_quote_template(conn, team_id=teams[2])["source"], "global")
            listed = {item["id"]: item for item in search._list_quote_templates(conn)}
            self.assertEqual(len(listed[ids[1]]["team_usage"]), 2)
            with self.assertRaisesRegex(search.QuoteTemplateError, "mặc định toàn hệ thống"):
                search._archive_quote_template(conn, ids[0])
            with self.assertRaisesRegex(search.QuoteTemplateError, "đang được gán"):
                search._archive_quote_template(conn, ids[1])

            with mock.patch.object(search, "_current_actor", return_value="test:phase6c1.1"):
                archived = search._archive_quote_template(conn, ids[2])
            self.assertIsNotNone(archived["archived_at"])
            filename, downloaded = search._download_quote_template(conn, ids[2])
            self.assertTrue(filename.endswith(".xlsx"))
            self.assertTrue(downloaded.startswith(b"PK"))
            with self.assertRaises(search.QuoteTemplateError):
                search._activate_quote_template(conn, ids[2])
            with self.assertRaises(search.QuoteTemplateError):
                search._assign_quote_template(conn, teams[2], ids[2])
            with self.assertRaises(search.QuoteTemplateError):
                search._get_active_quote_template(conn, template_id=ids[2], admin_override=True)
        finally:
            conn.close()

    def test_assignment_waits_for_template_lock_then_rejects_archived_row(self):
        teams, ids = self._seed()
        locker = psycopg2.connect(self.dsn)
        result = {}
        try:
            with locker.cursor() as cur:
                cur.execute("SELECT id FROM quote_templates WHERE id=%s FOR UPDATE", (ids[2],))

            started = threading.Event()

            def assign():
                conn = psycopg2.connect(self.dsn)
                try:
                    started.set()
                    search._assign_quote_template(conn, teams[2], ids[2])
                    result["ok"] = True
                except Exception as exc:  # captured for the main test thread
                    result["error"] = exc
                finally:
                    conn.close()

            worker = threading.Thread(target=assign, daemon=True)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.1)
            self.assertTrue(worker.is_alive(), "assignment must wait on the template row lock")

            with locker.cursor() as cur:
                cur.execute(
                    "UPDATE quote_templates SET archived_at=NOW(), archived_by='race-test' WHERE id=%s",
                    (ids[2],),
                )
            locker.commit()
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(result.get("error"), search.QuoteTemplateError)
            self.assertNotIn("ok", result)
        finally:
            locker.close()

    def test_migration_023_is_idempotent(self):
        sql = (ROOT / "sql" / "migration_023_quote_template_archiving.sql").read_text(encoding="utf-8")
        conn = psycopg2.connect(self.dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(sql)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
