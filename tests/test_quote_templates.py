import io
import json
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import search
from auth_test_helpers import start_auth_db_patch
from quote_workbook_export import WorkbookExportError
try:
    from tests.test_quote_workbook_export import make_workbook, product
except ModuleNotFoundError:
    from test_quote_workbook_export import make_workbook, product


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migration_013_quote_templates.sql"


class QuoteTemplateMigrationTests(unittest.TestCase):
    def test_migration_creates_table_checks_and_partial_active_index(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS quote_templates", sql)
        for col in [
            "id BIGSERIAL PRIMARY KEY",
            "filename TEXT NOT NULL",
            "content BYTEA NOT NULL",
            "content_sha256 TEXT NOT NULL",
            "content_size INTEGER NOT NULL",
            "profile_version TEXT NOT NULL",
            "mapping_json JSONB NOT NULL",
            "is_active BOOLEAN NOT NULL DEFAULT FALSE",
            "uploaded_by TEXT",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "activated_at TIMESTAMPTZ",
        ]:
            self.assertIn(col, sql)
        self.assertIn("quote_templates_content_size_check", sql)
        self.assertIn("content_size <= 10485760", sql)
        self.assertIn("quote_templates_content_sha256_check", sql)
        self.assertIn("^[0-9a-f]{64}$", sql)
        self.assertIn("quote_templates_profile_version_check", sql)
        self.assertIn("profile_version = 'BG_V1'", sql)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS quote_templates_one_active_idx", sql)
        self.assertIn("WHERE is_active = TRUE", sql)
        self.assertNotIn("From_BG_V2.xlsx", sql)


class FakeTemplateCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, query, params=None):
        self.conn.queries.append((query, params or ()))
        q = " ".join(query.split())
        if q.startswith("SELECT id, filename, content_sha256"):
            self.rows = [
                (
                    t["id"],
                    t["filename"],
                    t["content_sha256"],
                    t["content_size"],
                    t["profile_version"],
                    t["is_active"],
                    t["uploaded_by"],
                    t["created_at"],
                    t["activated_at"],
                )
                for t in sorted(self.conn.templates, key=lambda item: item["id"], reverse=True)
            ]
            return
        if q.startswith("UPDATE quote_templates SET is_active = FALSE"):
            for t in self.conn.templates:
                t["is_active"] = False
            return
        if q.startswith("INSERT INTO quote_templates"):
            next_id = self.conn.next_id
            self.conn.next_id += 1
            filename, _content, digest, size, profile, mapping_json, active, uploaded_by, _active_again = params
            template = {
                "id": next_id,
                "filename": filename,
                "content_sha256": digest,
                "content_size": size,
                "profile_version": profile,
                "mapping_json": json.loads(mapping_json),
                "is_active": bool(active),
                "uploaded_by": uploaded_by,
                "created_at": "2026-08-27T10:00:00+00:00",
                "activated_at": "2026-08-27T10:00:01+00:00" if active else None,
            }
            self.conn.templates.append(template)
            self.row = (
                template["id"],
                template["filename"],
                template["content_sha256"],
                template["content_size"],
                template["profile_version"],
                template["is_active"],
                template["uploaded_by"],
                template["created_at"],
                template["activated_at"],
            )
            return
        if q.startswith("SELECT id FROM quote_templates"):
            template_id = params[0]
            self.row = (template_id,) if any(t["id"] == template_id for t in self.conn.templates) else None
            return
        if q.startswith("UPDATE quote_templates SET is_active = TRUE"):
            template_id = params[0]
            for t in self.conn.templates:
                if t["id"] == template_id:
                    t["is_active"] = True
                    t["activated_at"] = "2026-08-27T11:00:00+00:00"
                    self.row = (
                        t["id"],
                        t["filename"],
                        t["content_sha256"],
                        t["content_size"],
                        t["profile_version"],
                        t["is_active"],
                        t["uploaded_by"],
                        t["created_at"],
                        t["activated_at"],
                    )
                    return
            self.row = None
            return
        if q.startswith("SELECT filename, content FROM quote_templates"):
            template_id = params[0]
            for t in self.conn.templates:
                if t["id"] == template_id:
                    self.row = (t["filename"], t.get("content", b"template-bytes"))
                    return
            self.row = None
            return
        if q.startswith("SELECT id, filename, profile_version, content_size, created_at, activated_at, content"):
            active = next((t for t in self.conn.templates if t["is_active"]), None)
            self.row = None if active is None else (
                active["id"],
                active["filename"],
                active["profile_version"],
                active["content_size"],
                active["created_at"],
                active["activated_at"],
                active.get("content", make_workbook()),
            )
            return
        if q.startswith("SELECT id, filename, profile_version, content_size, created_at, activated_at"):
            active = next((t for t in self.conn.templates if t["is_active"]), None)
            self.row = None if active is None else (
                active["id"],
                active["filename"],
                active["profile_version"],
                active["content_size"],
                active["created_at"],
                active["activated_at"],
            )

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeTemplateConnection:
    def __init__(self, templates=None):
        self.templates = list(templates or [])
        self.next_id = max([t["id"] for t in self.templates], default=0) + 1
        self.queries = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def cursor(self, *args, **kwargs):
        return FakeTemplateCursor(self)

    def close(self):
        self.closed = True


def _template_row(template_id=1, filename="From_BG_V2.xlsx", active=True, content=b"template-bytes"):
    return {
        "id": template_id,
        "filename": filename,
        "content_sha256": "a" * 64,
        "content_size": len(content),
        "profile_version": "BG_V1",
        "mapping_json": search._quote_template_mapping_snapshot(),
        "is_active": active,
        "uploaded_by": "user:1",
        "created_at": "2026-08-27T10:00:00+00:00",
        "activated_at": "2026-08-27T10:00:01+00:00" if active else None,
        "content": content,
    }


class QuoteTemplateHelperTests(unittest.TestCase):
    def test_bg_v1_mapping_snapshot_matches_exporter_profile(self):
        mapping = search._validate_bg_v1_template(make_workbook())
        self.assertEqual(mapping["profile_version"], "BG_V1")
        self.assertEqual(mapping["sheet"], "BG")
        self.assertEqual(mapping["header_row"], 16)
        self.assertEqual(mapping["product_start_row"], 17)
        self.assertEqual(mapping["total_label"], "Tổng giá")
        self.assertEqual(
            mapping["mapping"],
            {
                "sequence": "A",
                "Name": "B",
                "Code": "C",
                "Cas": "D",
                "Brand": "E",
                "Size": "F",
                "Note": "M",
                "Compliance_Combined": "N",
                "Unit_Price_Value": "P",
            },
        )

    def test_filename_basename_and_extension_rules(self):
        self.assertEqual(search._safe_uploaded_xlsx_filename(r"C:\tmp\From_BG_V2.xlsx"), "From_BG_V2.xlsx")
        self.assertEqual(search._safe_uploaded_xlsx_filename("../../From_BG_V2.xlsx"), "From_BG_V2.xlsx")
        for bad in ("bad.xls", "bad.xlsm", "bad.csv", ""):
            with self.assertRaises(ValueError):
                search._safe_uploaded_xlsx_filename(bad)

    def test_bounded_read_rejects_more_than_max_plus_one(self):
        fake = MagicMock()
        fake.read.return_value = b"x" * 6
        with patch.object(search, "MAX_XLSX_BYTES", 5):
            with self.assertRaises(OverflowError):
                search._read_bounded_workbook_upload(fake)
        fake.read.assert_called_once_with(6)

    def test_insert_records_sha256_size_profile_and_mapping(self):
        raw = make_workbook()
        conn = FakeTemplateConnection()
        meta = search._insert_quote_template(
            conn,
            filename="From_BG_V2.xlsx",
            raw=raw,
            mapping=search._quote_template_mapping_snapshot(),
            activate=True,
            uploaded_by="user:99",
        )
        self.assertTrue(meta["is_active"])
        self.assertEqual(meta["content_size"], len(raw))
        self.assertEqual(meta["content_sha256"], search.hashlib.sha256(raw).hexdigest())
        self.assertEqual(conn.templates[0]["mapping_json"]["profile_version"], "BG_V1")


class QuoteTemplateAdminApiTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        # Phase 5D2A: `enforce_session_validity` needs `user_id` + matching
        # `auth_version`; stub the DB it checks with an in-memory fake (no
        # real Postgres touched) for every test in this class.
        start_auth_db_patch(self)
        # Phase 6A-UAT gap fix: this file only exercises the quote-template
        # admin API's own 401/403 auth checks and mocks `search.get_connection`
        # for template rows -- it never wires a fake for the Fix1 IP/team
        # policy middleware, which now issues a REAL query
        # (`SELECT ip_policy FROM teams WHERE id = %s`) against whatever
        # `DATABASE_URL` this process has. Disabling it here (same pattern
        # already used by test_admin_teams.py / test_admin_google_users.py)
        # keeps this file testing what it says it tests instead of failing
        # with an unrelated 503 whenever a non-admin session is used.
        self._disable_ip_patch = mock.patch.dict(
            "os.environ", {"DISABLE_IP_ALLOWLIST": "1"}
        )
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)

    def _auth(self, admin=True):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user_id"] = 1
            sess["auth_version"] = 1
            sess["is_admin"] = admin
            sess["role"] = "admin" if admin else "user"
            if not admin:
                sess["team_id"] = 1
            sess["csrf_token"] = "the-real-token"
        self.client.environ_base["HTTP_X_CSRF_TOKEN"] = "the-real-token"

    def test_mutations_reject_missing_or_wrong_csrf_before_db(self):
        self._auth(admin=True)
        self.client.environ_base.pop("HTTP_X_CSRF_TOKEN", None)
        with patch.object(search, "get_connection", side_effect=AssertionError("db should not open")):
            missing = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(make_workbook()), "template.xlsx")},
                content_type="multipart/form-data",
            )
            wrong = self.client.post(
                "/api/admin/quote-templates/1/activate",
                headers={"X-CSRF-Token": "wrong"},
            )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(wrong.status_code, 400)

    def test_admin_auth_401_403(self):
        self.assertEqual(self.client.get("/api/admin/quote-templates").status_code, 401)
        self._auth(admin=False)
        self.assertEqual(self.client.get("/api/admin/quote-templates").status_code, 403)

    def test_valid_upload_defaults_active_and_strips_filename_path(self):
        self._auth(admin=True)
        conn = FakeTemplateConnection()
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(make_workbook()), "../../From_BG_V2.xlsx")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 201)
        data = response.get_json()["template"]
        self.assertEqual(data["filename"], "From_BG_V2.xlsx")
        self.assertTrue(data["is_active"])
        self.assertEqual(data["profile_version"], "BG_V1")
        # Phase 5D2A: a real session always carries `user_id`, so
        # `_current_actor()` now attributes uploads to `user:<id>` (more
        # precise than the old role-string fallback, which only ever
        # applies to anonymous/legacy break-glass sessions without a
        # `user_id`).
        self.assertEqual(conn.templates[0]["uploaded_by"], "user:1")

    def test_invalid_uploads_rejected_before_db_write(self):
        self._auth(admin=True)
        with patch.object(search, "get_connection", side_effect=AssertionError("db should not open")):
            bad_ext = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(b"x"), "bad.xlsm")},
                content_type="multipart/form-data",
            )
            self.assertEqual(bad_ext.status_code, 400)

            bad_ooxml = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(b"PK bad"), "bad.xlsx")},
                content_type="multipart/form-data",
            )
            self.assertEqual(bad_ooxml.status_code, 400)

            macro = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(make_workbook(macro=True)), "macro.xlsx")},
                content_type="multipart/form-data",
            )
            self.assertEqual(macro.status_code, 400)

        with patch.object(search, "MAX_XLSX_BYTES", 5), patch.object(
            search, "get_connection", side_effect=AssertionError("db should not open")
        ):
            large = self.client.post(
                "/api/admin/quote-templates",
                data={"workbook": (io.BytesIO(b"x" * 6), "large.xlsx")},
                content_type="multipart/form-data",
            )
            self.assertEqual(large.status_code, 413)

        with patch.object(search, "inspect_bg_template", side_effect=WorkbookExportError("zip bomb")):
            with patch.object(search, "get_connection", side_effect=AssertionError("db should not open")):
                bomb = self.client.post(
                    "/api/admin/quote-templates",
                    data={"workbook": (io.BytesIO(make_workbook()), "bomb.xlsx")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(bomb.status_code, 400)

    def test_upload_activate_false_then_activate_old_version(self):
        self._auth(admin=True)
        conn = FakeTemplateConnection([_template_row(1, "old.xlsx", active=True)])
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.post(
                "/api/admin/quote-templates",
                data={"activate": "false", "workbook": (io.BytesIO(make_workbook()), "new.xlsx")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201)
            self.assertFalse(response.get_json()["template"]["is_active"])
            self.assertEqual([t["id"] for t in conn.templates if t["is_active"]], [1])

            activated = self.client.post("/api/admin/quote-templates/2/activate")
            self.assertEqual(activated.status_code, 200)
            self.assertEqual(activated.get_json()["template"]["id"], 2)
            self.assertEqual([t["id"] for t in conn.templates if t["is_active"]], [2])

            rollback = self.client.post("/api/admin/quote-templates/1/activate")
            self.assertEqual(rollback.status_code, 200)
            self.assertEqual([t["id"] for t in conn.templates if t["is_active"]], [1])

    def test_list_does_not_return_content_or_mapping(self):
        self._auth(admin=True)
        conn = FakeTemplateConnection([_template_row()])
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.get("/api/admin/quote-templates")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["templates"][0]
        self.assertNotIn("content", item)
        self.assertNotIn("mapping_json", item)

    def test_download_admin_only_and_returns_binary(self):
        conn = FakeTemplateConnection([_template_row(content=b"xlsx-bytes")])
        self.assertEqual(self.client.get("/api/admin/quote-templates/1/download").status_code, 401)
        self._auth(admin=True)
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.get("/api/admin/quote-templates/1/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), b"xlsx-bytes")
        self.assertIn("From_BG_V2.xlsx", response.headers["Content-Disposition"])


class QuoteTemplateAssistantApiTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        start_auth_db_patch(self)
        # Phase 6A-UAT gap fix: same reason as QuoteTemplateAdminApiTests
        # above -- every test in this class uses a non-admin, team_id=123
        # session, which now makes the real Fix1 IP/team-policy middleware
        # query `teams.ip_policy` for real instead of being exempt. Nothing
        # here is testing IP policy, so disable it explicitly.
        self._disable_ip_patch = mock.patch.dict(
            "os.environ", {"DISABLE_IP_ALLOWLIST": "1"}
        )
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user_id"] = 1
            sess["auth_version"] = 1
            sess["is_admin"] = False
            sess["team_id"] = 123

    def test_active_metadata_does_not_expose_binary_mapping_or_uploaded_by(self):
        conn = FakeTemplateConnection([_template_row()])
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.get("/api/quote-assistant/workbook/template")
        self.assertEqual(response.status_code, 200)
        template = response.get_json()["template"]
        self.assertEqual(template["profile_version"], "BG_V1")
        for hidden in ("content", "mapping_json", "uploaded_by", "content_sha256"):
            self.assertNotIn(hidden, template)

    def test_no_active_template_returns_409(self):
        conn = FakeTemplateConnection([])
        with patch.object(search, "get_connection", return_value=conn):
            response = self.client.get("/api/quote-assistant/workbook/template")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Chưa có mẫu báo giá active", response.get_json()["error"])

    def test_export_without_workbook_uses_active_template(self):
        with patch.object(
            search,
            "_get_active_quote_template",
            return_value={
                "id": 1,
                "filename": "From_BG_V2.xlsx",
                "profile_version": "BG_V1",
                "content_size": len(make_workbook()),
                "created_at": "2026-08-27T10:00:00+00:00",
                "activated_at": "2026-08-27T10:00:01+00:00",
                "content": make_workbook(),
            },
        ) as active_mock, patch.object(
            search, "_quote_export_products", return_value=[product(1)]
        ) as products_mock, patch.object(
            search, "export_quick_quote_workbook", return_value=b"exported"
        ) as export_mock, patch.object(
            search, "get_connection", return_value=FakeTemplateConnection()
        ):
            response = self.client.post(
                "/api/quote-assistant/workbook/export",
                data={"selections": json.dumps([{"product_id": 42}])},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        active_mock.assert_called_once()
        self.assertEqual(products_mock.call_args.args[1], [{"ord": 1, "product_id": 42}])
        self.assertEqual(export_mock.call_args.args[0][:2], b"PK")
        self.assertIn("From_BG_V2_draft.xlsx", response.headers["Content-Disposition"])

    def test_export_without_active_template_returns_409(self):
        with patch.object(search, "_get_active_quote_template", side_effect=search.QuoteTemplateError("Chưa có mẫu báo giá active.")):
            with patch.object(search, "get_connection", return_value=FakeTemplateConnection()):
                response = self.client.post(
                    "/api/quote-assistant/workbook/export",
                    data={"selections": json.dumps([{"product_id": 42}])},
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 409)

    def test_legacy_export_with_workbook_does_not_load_active_template(self):
        with patch.object(search, "_get_active_quote_template", side_effect=AssertionError("should not load active")):
            with patch.object(search, "_quote_export_products", return_value=[product(1)]), patch.object(
                search, "export_quick_quote_workbook", return_value=b"exported"
            ), patch.object(search, "get_connection", return_value=FakeTemplateConnection()):
                response = self.client.post(
                    "/api/quote-assistant/workbook/export",
                    data={
                        "workbook": (io.BytesIO(make_workbook()), "legacy.xlsx"),
                        "selections": json.dumps([{"product_id": 42}]),
                    },
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 200)
        self.assertIn("legacy_draft.xlsx", response.headers["Content-Disposition"])

    def test_template_metadata_when_table_missing_returns_503(self):
        with patch.object(
            search,
            "_get_active_quote_template",
            side_effect=search.UndefinedTable('relation "quote_templates" does not exist'),
        ), patch.object(search, "get_connection", return_value=FakeTemplateConnection()):
            response = self.client.get("/api/quote-assistant/workbook/template")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Hệ thống quản lý mẫu báo giá chưa sẵn sàng", response.get_json()["error"])

    def test_template_export_when_table_missing_returns_503(self):
        with patch.object(
            search,
            "_get_active_quote_template",
            side_effect=search.UndefinedTable('relation "quote_templates" does not exist'),
        ), patch.object(search, "get_connection", return_value=FakeTemplateConnection()):
            response = self.client.post(
                "/api/quote-assistant/workbook/export",
                data={"selections": json.dumps([{"product_id": 42}])},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Hệ thống quản lý mẫu báo giá chưa sẵn sàng", response.get_json()["error"])

    def test_unrelated_db_error_is_not_masked_as_503(self):
        import psycopg2
        with patch.object(
            search,
            "_get_active_quote_template",
            side_effect=psycopg2.OperationalError("connection lost"),
        ), patch.object(search, "get_connection", return_value=FakeTemplateConnection()):
            with self.assertRaises(psycopg2.OperationalError):
                self.client.get("/api/quote-assistant/workbook/template")


if __name__ == "__main__":
    unittest.main()
