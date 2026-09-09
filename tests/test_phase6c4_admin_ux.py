"""Regression contract for the Phase 6C4 admin-only UX layer."""
import unittest
import subprocess
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
        self.assertNotIn("max-width:1200px", source)
        self.assertNotIn("clamp(", source)
        self.assertIn(":focus-visible", source)
        self.assertIn("@media (max-width: 700px)", source)

    def test_changed_script_assets_have_phase6c4_cache_versions(self):
        products = (TEMPLATES / "admin_products.html").read_text(encoding="utf-8")
        form = (TEMPLATES / "admin_product_form.html").read_text(encoding="utf-8")
        quotes = (TEMPLATES / "admin_quote_templates.html").read_text(encoding="utf-8")
        self.assertIn("admin_products.js', v='20260909c4'", products)
        self.assertIn("admin_products.js', v='20260909c4'", form)
        self.assertIn("admin_quote_templates.js', v='20260909c4'", quotes)

    def test_native_submit_guard_blocks_repeat_and_resets_after_bfcache(self):
        subprocess.run(["node", "tests/admin_ux_dom_test.js"], cwd=ROOT, check=True)

    def test_dialog_contracts_keep_accessible_labels_and_focus_return(self):
        products = (TEMPLATES / "admin_products.html").read_text(encoding="utf-8")
        self.assertIn('aria-labelledby="delete-title"', products)
        self.assertIn('aria-label="Đóng hộp xác nhận"', products)
        self.assertIn("dialogOpener.focus()", (ROOT / "static" / "admin_products.js").read_text(encoding="utf-8"))
        self.assertIn("dialogOpener.focus()", (ROOT / "static" / "admin_quote_templates.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
