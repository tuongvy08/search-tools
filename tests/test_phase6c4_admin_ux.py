"""Regression contract for the Phase 6C4 admin-only UX layer."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
ADMIN_TEMPLATES = (
    "admin_teams.html", "admin_users.html", "admin_network.html",
    "admin_brand_compliance.html", "admin_import_center.html",
    "admin_quote_templates.html", "admin_exchange_rates.html",
    "admin_login_history.html", "admin_products.html", "admin_product_form.html",
    "admin_imports.html",
)


class AdminUxContractTests(unittest.TestCase):
    def test_admin_pages_opt_in_to_the_scoped_layer(self):
        for name in ADMIN_TEMPLATES:
            with self.subTest(name=name):
                source = (TEMPLATES / name).read_text(encoding="utf-8")
                self.assertIn('class="admin-page"', source)
                self.assertIn("filename='admin_ux.css'", source)

    def test_shared_css_cannot_restyle_search_or_quick_quote(self):
        source = (ROOT / "static" / "admin_ux.css").read_text(encoding="utf-8")
        self.assertIn("body.admin-page", source)
        self.assertNotIn("body {", source)
        self.assertIn("overflow:auto", source)
        self.assertIn(":focus-visible", source)
        self.assertIn("max-width:700px", source)

    def test_destructive_forms_keep_busy_guard_and_product_dialog_contract(self):
        script = (ROOT / "static" / "admin_ux.js").read_text(encoding="utf-8")
        products = (TEMPLATES / "admin_products.html").read_text(encoding="utf-8")
        self.assertIn("button.disabled=true", script)
        self.assertIn("aria-busy", script)
        self.assertIn('aria-labelledby="delete-title"', products)
        self.assertIn('aria-label="Đóng hộp xác nhận"', products)
        self.assertIn("dialogOpener.focus()", (ROOT / "static" / "admin_products.js").read_text(encoding="utf-8"))
        self.assertIn("dialogOpener.focus()", (ROOT / "static" / "admin_quote_templates.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
