import unittest
from pathlib import Path

import search


ROOT = Path(__file__).resolve().parents[1]
ADMIN_QUOTE_HTML = ROOT / "templates" / "admin_quote_templates.html"
ADMIN_QUOTE_JS = ROOT / "static" / "admin_quote_templates.js"
INDEX_HTML = ROOT / "templates" / "index.html"
QUICK_QUOTE_HTML = ROOT / "templates" / "quick_quote.html"
QUICK_QUOTE_JS = ROOT / "static" / "quick_quote.js"
ADMIN_TEMPLATES = [
    ROOT / "templates" / "admin_imports.html",
    ROOT / "templates" / "admin_exchange_rates.html",
    ROOT / "templates" / "admin_network.html",
    ROOT / "templates" / "admin_users.html",
    ROOT / "templates" / "admin_brand_compliance.html",
    ADMIN_QUOTE_HTML,
]


class AdminQuoteTemplatesRouteTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()

    def _auth(self, admin=True):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["is_admin"] = admin
            if not admin:
                sess["team_id"] = 1

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get("/admin/quote-templates")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))

    def test_non_admin_forbidden(self):
        self._auth(admin=False)
        response = self.client.get("/admin/quote-templates")
        self.assertEqual(response.status_code, 403)

    def test_admin_renders_page_without_db_query(self):
        self._auth(admin=True)
        response = self.client.get("/admin/quote-templates")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Mẫu báo giá", body)
        self.assertIn("admin_quote_templates.js", body)
        self.assertIn("qtUploadForm", body)
        self.assertIn("qtHistoryTable", body)


class AdminQuoteTemplatesStaticTests(unittest.TestCase):
    def test_nav_link_on_admin_pages_only(self):
        for path in ADMIN_TEMPLATES:
            html = path.read_text(encoding="utf-8")
            self.assertIn('/admin/quote-templates', html, f"Missing nav link: {path.name}")
            self.assertIn("Mẫu báo giá", html, f"Missing nav text: {path.name}")
        self.assertNotIn('/admin/quote-templates', INDEX_HTML.read_text(encoding="utf-8"))
        self.assertNotIn('/admin/quote-templates', QUICK_QUOTE_HTML.read_text(encoding="utf-8"))

    def test_html_has_required_sections(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        for text in [
            "Mẫu đang sử dụng",
            "Chưa có mẫu báo giá",
            "Upload phiên bản mới",
            "Kích hoạt ngay sau khi upload",
            "Mapping được kiểm tra",
            "Lịch sử phiên bản",
            "Đang sử dụng",
        ]:
            self.assertIn(text, html)
        for ident in [
            "qtActiveMeta",
            "qtUploadForm",
            "qtWorkbook",
            "qtActivate",
            "qtHistoryTable",
            "qtHistoryBody",
        ]:
            self.assertIn(ident, html)

    def test_mapping_read_only_and_fixed_bg_v1(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        for text in [
            "BG_V1",
            "<code>BG</code>",
            "<code>16</code>",
            "<code>17</code>",
            "<code>A</code></td><td>STT</td>",
            "<code>B</code></td><td>Name</td>",
            "<code>C</code></td><td>Code</td>",
            "<code>D</code></td><td>CAS</td>",
            "<code>E</code></td><td>Brand</td>",
            "<code>F</code></td><td>Size</td>",
            "<code>M</code></td><td>Note</td>",
            "<code>N</code></td><td>Compliance + Compliance Note</td>",
            "<code>P</code></td><td>Unit Price</td>",
            "<code>Tổng giá</code>",
            "chưa phải mapping tùy chỉnh",
        ]:
            self.assertIn(text, html)
        self.assertNotIn('name="mapping"', html)
        self.assertNotIn('contenteditable="true"', html)
        self.assertNotIn("<textarea", html)

    def test_js_uses_expected_admin_apis(self):
        js = ADMIN_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("const API_LIST = '/api/admin/quote-templates'", js)
        self.assertIn("const API_UPLOAD = '/api/admin/quote-templates'", js)
        self.assertIn("const API_ACTIVATE_PREFIX = '/api/admin/quote-templates/'", js)
        self.assertIn("const API_DOWNLOAD_PREFIX = '/api/admin/quote-templates/'", js)
        self.assertIn("fetch(API_LIST", js)
        self.assertIn("fetch(API_UPLOAD", js)
        self.assertIn("/activate", js)
        self.assertIn("/download", js)

    def test_upload_formdata_no_manual_content_type_and_client_validation(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        js = ADMIN_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn('accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"', html)
        self.assertIn("const MAX_XLSX_BYTES = 10 * 1024 * 1024", js)
        self.assertIn("new FormData()", js)
        self.assertIn("body.append('workbook', file)", js)
        self.assertIn("body.append('activate'", js)
        self.assertNotIn("Content-Type", js)
        self.assertIn("endsWith('.xlsx')", js)
        self.assertIn("10 MB", js)

    def test_double_submit_guard_activation_dialog_and_download_action(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        js = ADMIN_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("let uploadInProgress = false", js)
        self.assertIn("if (uploadInProgress) return", js)
        self.assertIn("qtActivateDialog", html)
        self.assertIn("<dialog", html)
        self.assertIn("showModal", js)
        self.assertNotIn("window.confirm", js)
        self.assertIn("buildDownloadLink", js)
        self.assertIn("fa-download", js)
        self.assertIn("title = `Tải lại", js)

    def test_textcontent_containment(self):
        js = ADMIN_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("textContent", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("document.createElement", js)

    def test_responsive_table_css_and_no_nested_cards(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn(".table-wrap", html)
        self.assertIn("overflow-x: auto", html)
        self.assertIn(".history-table { min-width: 880px; }", html)
        self.assertIn("@media (max-width: 760px)", html)
        self.assertNotIn('class="card', html)
        self.assertNotIn("gradient", html.lower())
        self.assertNotIn("orb", html.lower())
        self.assertNotIn("hero", html.lower())

    def test_no_sales_upload_or_product_list_wizard_in_this_phase(self):
        html = ADMIN_QUOTE_HTML.read_text(encoding="utf-8")
        js = ADMIN_QUOTE_JS.read_text(encoding="utf-8")
        forbidden = [
            "danh sách sản phẩm hằng ngày",
            "sales upload",
            "wizard nhập danh sách",
            "qqRequestGrid",
            "/api/quote-assistant/match",
        ]
        for text in forbidden:
            self.assertNotIn(text, html)
            self.assertNotIn(text, js)
        self.assertNotIn("fd.append('workbook'", QUICK_QUOTE_JS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
