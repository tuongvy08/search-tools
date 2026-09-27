"""Phase 6D9 Product Search workbench presentation gates.

The page remains permission-driven and uses the existing interaction IDs; this
module protects the new information hierarchy without requiring PostgreSQL.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import search
import team_permissions
from auth_test_helpers import set_authenticated_session, start_auth_db_patch


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "templates" / "index.html"
WORKSPACE_CSS = ROOT / "static" / "search_workspace.css"
VISUAL_FIXTURE = ROOT / "tests" / "phase6d9_visual_fixture.py"


class SearchWorkspaceStaticTests(unittest.TestCase):
    def test_page_only_asset_loads_after_shared_search_assets(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        styles = html.index("filename='styles.css'")
        nav = html.index("filename='admin_nav.css'")
        suggestions = html.index("filename='search_suggestions.css'")
        workspace = html.index("filename='search_workspace.css'")
        self.assertLess(styles, nav)
        self.assertLess(nav, suggestions)
        self.assertLess(suggestions, workspace)
        self.assertEqual(html.count("filename='search_workspace.css'"), 1)
        for other in (ROOT / "templates").glob("*.html"):
            if other == INDEX_HTML:
                continue
            self.assertNotIn("search_workspace.css", other.read_text(encoding="utf-8"), other.name)

    def test_existing_interaction_ids_remain_unique(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for ident in (
            "searchQuery", "searchSuggestions", "searchSuggestStatus", "inStockOnly",
            "inStockOnlyHint", "btnCheckLicense", "btnFindCode", "btnAdvancedSearch",
            "btnCopySelected", "btnExportSelected", "quoteExportContext", "selectionBar",
            "selectionCount", "brandCheckboxes", "sizeCheckboxes", "results",
        ):
            self.assertEqual(html.count(f'id="{ident}"'), 1, ident)
        self.assertIn('class="search-container"', html)
        self.assertIn('class="search-card filter-container"', html)

    def test_stock_filter_stays_inside_intentional_filter_group(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        group_start = html.index('class="search-filter-group search-filter-stock"')
        group_end = html.index("</fieldset>", group_start)
        group = html[group_start:group_end]
        self.assertIn('for="inStockOnly"', group)
        self.assertIn('id="inStockOnly"', group)
        self.assertIn('aria-describedby="inStockOnlyHint"', group)
        self.assertIn('id="inStockOnlyHint"', group)

    def test_result_actions_are_not_nested_in_hidden_selection_bar(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        actions = html.index('class="results-actions"')
        selection = html.index('id="selectionBar"')
        self.assertLess(actions, selection)
        self.assertNotIn("btnCopySelected", html[selection:html.index("</div>", selection)])
        self.assertNotIn("btnExportSelected", html[selection:html.index("</div>", selection)])
        self.assertIn("Ngữ cảnh xuất báo giá", html)
        self.assertIn("không lọc kết quả", html)

    def test_filters_have_robust_initial_empty_state_and_semantic_columns(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('<div id="brandCheckboxes" aria-label="Lọc theo brand"></div>', html)
        self.assertIn('<div id="sizeCheckboxes" aria-label="Lọc theo quy cách"></div>', html)
        self.assertIn("<colgroup>", html)
        for name in (
            "col-name", "col-code", "col-cas", "col-brand", "col-size", "col-price",
            "col-note", "col-compliance", "col-compliance-note", "col-stock",
        ):
            self.assertIn(f'class="{name}"', html, name)

    def test_workspace_css_is_scoped_and_avoids_dynamic_column_positions(self):
        css = WORKSPACE_CSS.read_text(encoding="utf-8")
        self.assertIn("--sw-primary", css)
        self.assertIn(".search-page .search-workspace", css)
        self.assertIn("overflow-x: auto", css)
        self.assertNotRegex(css, r"#results[^\{]*:(?:nth|first|last)-child")
        self.assertNotIn("display: none !important", css)
        self.assertNotIn("display: flex !important", css)
        table_cells = css[css.index(".search-page #results th,"):css.index(".search-page #results th {", css.index(".search-page #results th,"))]
        self.assertNotIn("overflow-wrap: anywhere", table_cells)
        self.assertIn("white-space: nowrap", css)
        selector_lines = [
            line.strip() for line in css.splitlines()
            if line.strip().endswith("{") and not line.strip().startswith(("@", "/*"))
        ]
        for selector in selector_lines:
            self.assertTrue(selector.startswith(".search-page"), selector)

    def test_mobile_touch_targets_are_at_least_44px(self):
        css = WORKSPACE_CSS.read_text(encoding="utf-8")
        mobile = css[css.index("@media (max-width: 767.98px)"):css.index("@media (max-width: 479.98px)")]
        phone = css[css.index("@media (max-width: 479.98px)"):css.index("@media (forced-colors: active)")]
        self.assertIn(".search-page .nav-button {\n    min-height: 44px;", mobile)
        self.assertIn(".search-page .input-box {\n    height: 54px;", mobile)
        self.assertIn(".search-page .input-box .search-button {\n    min-height: 44px;", mobile)
        self.assertIn(".search-page .input-box {\n    height: 54px;", phone)
        self.assertIn(".search-page .input-box .search-button {\n    min-height: 44px;", phone)

    def test_visual_fixture_uses_real_result_wire_fields(self):
        fixture = VISUAL_FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(fixture.count('"product_id":'), 3)
        self.assertEqual(fixture.count('"product_id": None'), 1)
        self.assertIn('"product_id": 101', fixture)
        self.assertIn('"product_id": 102', fixture)
        self.assertNotIn('"product_id": 103', fixture)
        self.assertNotIn('"Stock_State": "available"', fixture)
        for state in ("current", "near_expiry", "missing"):
            self.assertIn(f'"Stock_State": "{state}"', fixture)
        self.assertIn('"Stock_Match": "exact_code"', fixture)
        self.assertIn('"Stock_Match": "same_cas"', fixture)
        self.assertIn('"Stock_Match": "name"', fixture)
        self.assertIn('"Compliance_Status":', fixture)
        self.assertIn('"Compliance_Css": "regulatory-color-custom"', fixture)
        self.assertIn('"Compliance_Bg": "#EDE9FE"', fixture)
        self.assertIn('"Compliance_Fg": "#5B21B6"', fixture)


class SearchWorkspacePermissionRenderTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        start_auth_db_patch(self)
        self.env = mock.patch.dict("os.environ", {"DISABLE_IP_ALLOWLIST": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def render(self, grants, *, admin=False):
        with self.client.session_transaction() as sess:
            sess.clear()
            set_authenticated_session(
                sess,
                is_admin=admin,
                team_id=None if admin else 1,
                username="synthetic@example.test",
                csrf_token="csrf-test",
            )
        admin_context = {
            "keys": frozenset(),
            "is_super_admin": False,
            "is_admin": admin,
        }
        with mock.patch.object(team_permissions, "current_permissions", return_value=frozenset(grants)), \
             mock.patch("admin_permissions.current_permissions", return_value=admin_context):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_restricted_staff_sees_only_granted_controls_and_columns(self):
        body = self.render({"SEARCH", "VIEW_CODE"})
        self.assertIn('id="searchQuery"', body)
        self.assertIn('id="inStockOnly"', body)
        self.assertIn("<th>Code</th>", body)
        self.assertNotIn("<th>Name</th>", body)
        self.assertIn('<col class="col-code">', body)
        self.assertNotIn('<col class="col-name">', body)
        self.assertNotIn('id="btnCopySelected"', body)
        self.assertNotIn('id="btnExportSelected"', body)
        self.assertNotIn('id="quoteExportContext"', body)

    def test_no_search_staff_keeps_granted_utility_without_stock_control(self):
        body = self.render({"FIND_CODE", "VIEW_CODE"})
        self.assertNotIn('id="searchQuery"', body)
        self.assertNotIn('id="inStockOnly"', body)
        self.assertIn('id="btnFindCode"', body)
        self.assertNotIn('id="btnCheckLicense"', body)
        self.assertIn('id="brandCheckboxes"', body)
        self.assertIn('id="sizeCheckboxes"', body)

    def test_copy_export_controls_follow_permissions(self):
        copy_only = self.render({"SEARCH", "COPY", "VIEW_NAME"})
        self.assertIn('id="btnCopySelected"', copy_only)
        self.assertNotIn('id="btnExportSelected"', copy_only)
        export_only = self.render({"SEARCH", "EXPORT", "VIEW_COMPLIANCE"})
        self.assertNotIn('id="btnCopySelected"', export_only)
        self.assertIn('id="btnExportSelected"', export_only)


if __name__ == "__main__":
    unittest.main()
