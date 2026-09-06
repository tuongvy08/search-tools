"""Phase 6B1: template/UI-shape tests for the Team page's brand picker
(search + checkbox + "select/deselect filtered" + "clear all" + live
counter) added on top of the existing Phase 6A preview/confirm workflow.

Two groups, same isolation contract as the rest of this test suite:

1. `BrandPickerStaticTests` -- no DB, no Flask app at all: reads
   `templates/admin_teams.html`'s raw source and asserts the behavior
   CONTRACT the interactive picker must satisfy (a JS engine isn't
   available in this test runner, so runtime click-by-click behavior is
   instead verified via real-browser smoke per the Phase 6B1 task, and via
   the structural/source assertions here -- e.g. "select/deselect filtered
   only ever touches visible rows", "the search filter itself never
   toggles `.checked`", "clear-all always confirms first", "never
   innerHTML, never a per-checkbox network request").

2. `BrandPickerRenderTests` -- REAL, temporary Postgres (created via
   `tests/pg_temp_db.py`'s helper, same one `test_admin_brand_compliance.py`
   / `test_batch_queries.py` etc. already use; NEVER `products_local`,
   dropped in `tearDownClass` even on failure). Renders the real
   `GET /admin/teams` route as an authenticated admin against a seeded set
   of 8 distinct brands + one team with 2 pre-assigned brands, and checks
   the actual server-rendered markup: exactly one create-picker and one
   edit-picker, no brand duplicated beyond the expected "once per picker",
   and the edit-picker's checkboxes carry `checked` for exactly the
   pre-assigned brands (proving `_validate_brands`'s source list and the
   macro wiring are unchanged/correct after the Phase 6B1 markup rework).
   Also re-proves (independently of `tests/test_admin_teams.py`) that a
   staff session gets a plain 403 with no picker markup at all.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # noqa: E402

import search  # noqa: E402
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADMIN_TEAMS_HTML = ROOT / "templates" / "admin_teams.html"


class BrandPickerStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = ADMIN_TEAMS_HTML.read_text(encoding="utf-8")

    def test_macro_defined_once_and_used_for_both_create_and_edit_pickers(self):
        self.assertEqual(self.html.count("{% macro brand_picker("), 1)
        self.assertIn("brand_picker('create', [])", self.html)
        self.assertIn("brand_picker('edit-' ~ t.id, t.brands)", self.html)

    def test_toolbar_has_required_bulk_actions_with_unambiguous_labels(self):
        self.assertIn('data-action="select-filtered"', self.html)
        self.assertIn('data-action="deselect-filtered"', self.html)
        self.assertIn('data-action="clear-all"', self.html)
        self.assertIn("Chọn tất cả kết quả đang lọc", self.html)
        self.assertIn("Bỏ chọn kết quả đang lọc", self.html)
        self.assertIn("Xóa toàn bộ lựa chọn", self.html)
        # UX safety requirement: never a vague "Chọn All" label.
        self.assertNotIn("Chọn All", self.html)

    def test_counter_and_live_status_region_present(self):
        self.assertIn('data-role="count"', self.html)
        self.assertIn("Đã chọn 0 brand", self.html)  # server-rendered placeholder, JS updates on load
        self.assertIn('aria-live="polite"', self.html)

    def test_clear_all_confirms_and_states_the_impacted_count_before_clearing(self):
        self.assertIn("window.confirm(", self.html)
        self.assertIn("totalChecked", self.html)
        # Cancelling the confirm must not touch any checkbox.
        self.assertIn("if (!confirmed) return;", self.html)

    def test_search_filter_never_toggles_checked_state(self):
        # `applyFilter` (empty search term == "toàn bộ danh sách") is only
        # allowed to write `row.style.display` -- never `.checked` -- so a
        # brand hidden by a search term keeps whatever selection state it
        # already had.
        start = self.html.index("function applyFilter")
        end = self.html.index("if (input) input.addEventListener")
        body = self.html[start:end]
        self.assertIn("row.style.display", body)
        self.assertNotIn(".checked", body)

    def test_bulk_select_and_deselect_only_touch_currently_visible_rows(self):
        self.assertIn("isRowVisible(row)", self.html)
        start = self.html.index("action === 'select-filtered'")
        end = self.html.index("if (action === 'clear-all')")
        body = self.html[start:end]
        self.assertIn("if (!isRowVisible(row)) return;", body)

    def test_no_innerhtml_and_no_network_request_from_picker_js(self):
        script_start = self.html.index("<script>", self.html.index("brand-picker"))
        script = self.html[script_start:]
        self.assertNotIn(".innerHTML", script)  # actual property writes, not the word in a comment
        self.assertNotIn("fetch(", script)
        self.assertNotIn("XMLHttpRequest", script)
        self.assertIn("textContent", script)

    def test_picker_container_radius_is_8px_and_not_a_second_card(self):
        self.assertIn(
            ".brand-picker { border: 1px solid #e5e7eb; border-radius: 8px;",
            self.html,
        )
        self.assertNotIn('class="brand-picker card"', self.html)
        self.assertNotIn('class="card brand-picker"', self.html)

    def test_bulk_actions_never_submit_the_form_themselves(self):
        # Every `.brand-action` button must be type="button" (never
        # type="submit") so select/deselect/clear never auto-saves or
        # auto-confirms the surrounding preview/create form.
        import re
        for match in re.finditer(r'<button[^>]*class="brand-action[^"]*"[^>]*>', self.html):
            self.assertIn('type="button"', match.group(0))

    def test_shared_nav_included_exactly_once_and_no_legacy_duplicate_block(self):
        self.assertEqual(self.html.count('{% include "_user_nav.html" %}'), 1)
        self.assertNotIn('class="nav"', self.html)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres (DATABASE_URL) not reachable")
class BrandPickerRenderTests(unittest.TestCase):
    BRANDS = [f"Brand{i:02d}" for i in range(1, 9)]
    PRESET_BRANDS = ["Brand01", "Brand02"]

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()

    @classmethod
    def tearDownClass(cls):
        drop_temp_db(cls.db_name)

    def setUp(self):
        self._env_patch = mock.patch.dict(
            os.environ, {"DATABASE_URL": self.dsn, "DISABLE_IP_ALLOWLIST": "1"}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        search.app.testing = True
        self.client = search.app.test_client()
        self._seed()

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def _seed(self):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "TRUNCATE team_brands, app_users, teams, products RESTART IDENTITY CASCADE"
                    )
                    for i, brand in enumerate(self.BRANDS):
                        cur.execute(
                            "INSERT INTO products (name, code, cas, brand, size, ship, price, note) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            (f"Product {brand}", f"C{i}", None, brand, "1L", "1", "1000", None),
                        )
                    cur.execute(
                        "INSERT INTO teams (name, ip_policy) VALUES ('Team Smoke', 'INHERIT') RETURNING id"
                    )
                    (self.team_id,) = cur.fetchone()
                    for brand in self.PRESET_BRANDS:
                        cur.execute(
                            "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s)",
                            (self.team_id, brand),
                        )
                    cur.execute(
                        "INSERT INTO app_users "
                        "(username, password_hash, team_id, is_admin, account_status, auth_version, auth_provider) "
                        "VALUES ('admin1', 'x', NULL, TRUE, 'ACTIVE', 1, 'LOCAL') RETURNING id"
                    )
                    (self.admin_id,) = cur.fetchone()
                    cur.execute(
                        "INSERT INTO app_users "
                        "(username, password_hash, team_id, is_admin, account_status, auth_version, auth_provider) "
                        "VALUES ('staff1', 'x', %s, FALSE, 'ACTIVE', 1, 'LOCAL') RETURNING id",
                        (self.team_id,),
                    )
                    (self.staff_id,) = cur.fetchone()
        finally:
            conn.close()

    def _admin_session(self):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=self.admin_id, auth_version=1,
                        is_admin=True, role="admin", username="admin1")
            sess["csrf_token"] = "the-real-token"

    def _staff_session(self):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=self.staff_id, auth_version=1,
                        is_admin=False, team_id=self.team_id, role="staff", username="staff1")
            sess["csrf_token"] = "the-real-token"

    def test_one_create_and_one_edit_picker_no_duplicate_brand_checkboxes(self):
        self._admin_session()
        resp = self.client.get("/admin/teams")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertEqual(body.count('data-picker="create"'), 1)
        self.assertEqual(body.count(f'data-picker="edit-{self.team_id}"'), 1)
        for brand in self.BRANDS:
            # Exactly two checkboxes for this brand across the whole page
            # (one in the create-picker, one in the edit-picker) -- never
            # duplicated, never missing.
            self.assertEqual(body.count(f'value="{brand}"'), 2, brand)

    def test_edit_picker_checks_exactly_the_preassigned_brands(self):
        self._admin_session()
        resp = self.client.get("/admin/teams")
        body = resp.get_data(as_text=True)
        edit_start = body.index(f'data-picker="edit-{self.team_id}"')
        edit_end = body.index("</details>", edit_start)
        edit_block = body[edit_start:edit_end]
        for brand in self.PRESET_BRANDS:
            self.assertIn(f'value="{brand}" checked', edit_block)
        for brand in [b for b in self.BRANDS if b not in self.PRESET_BRANDS]:
            self.assertNotIn(f'value="{brand}" checked', edit_block)

    def test_bulk_toolbar_rendered_once_per_picker(self):
        self._admin_session()
        resp = self.client.get("/admin/teams")
        body = resp.get_data(as_text=True)
        # Only count within the rendered markup, not the shared <script>
        # block (whose comments/confirm-dialog text legitimately reuse
        # these same Vietnamese phrases). The page has an earlier
        # <script> too (the shared nav's mobile-toggle, from
        # `_user_nav.html`) -- the picker's own script is the LAST one.
        markup = body[: body.rindex("<script>")]
        self.assertEqual(markup.count("Chọn tất cả kết quả đang lọc"), 2)
        self.assertEqual(markup.count("Bỏ chọn kết quả đang lọc"), 2)
        self.assertEqual(markup.count("Xóa toàn bộ lựa chọn"), 2)

    def test_admin_nav_shows_team_link_as_active_exactly_once(self):
        self._admin_session()
        resp = self.client.get("/admin/teams")
        body = resp.get_data(as_text=True)
        self.assertIn("is-active", body)
        self.assertEqual(body.count('href="/admin/teams"'), 1)

    def test_staff_blocked_by_backend_and_sees_no_picker_markup(self):
        self._staff_session()
        resp = self.client.get("/admin/teams")
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(b"brand-picker", resp.data)
        self.assertNotIn(b"Team & qu", resp.data)


if __name__ == "__main__":
    unittest.main()
