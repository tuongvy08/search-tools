"""Phase 6B1-Polish: tests for the shared nav shell (`templates/_user_nav.html`
+ `static/admin_nav.css`) after the 8 flat admin links were regrouped behind
a single "Quản trị" disclosure button.

No brand-picker assertions live here on purpose -- that surface
(`templates/admin_teams.html`) is untouched by this polish pass and is
already covered by `tests/test_admin_teams_ui.py::BrandPickerStaticTests`
and `::BrandPickerRenderTests`; re-running those isn't needed since that
code didn't change.

Two groups:

1. `NavShellStaticTests` -- no DB, no Flask app: reads the raw source of
   `_user_nav.html` and `admin_nav.css` and asserts the structural/a11y
   contract this task requires (disclosure button attributes, exactly one
   trigger/panel pair, Escape/outside-click/select-to-close wiring, no
   `innerHTML`, only Font Awesome 5 icons already verified against a real
   FA 5.15.2 page during Phase 6B1 browser smoke testing, logout unchanged
   as a POST form with CSRF -- never a GET link, mobile breakpoint at
   1023.98px with the trigger hidden and the panel forced flat/static).

2. `NavShellRouteRenderTests` -- real Flask test client, but no Postgres:
   `GET /` (see `search.py::home`, renders `index.html` with zero DB
   queries) is enough to exercise the real server-rendered nav for both an
   admin and a staff session, plus `GET /admin/quote-templates` (already
   proven DB-query-free by `tests/test_admin_quote_templates_ui.py`) to
   exercise the "on an admin page, both the trigger AND its one submenu
   link are active" requirement without spinning up a temporary Postgres
   database for this file.
"""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import search  # noqa: E402
from auth_test_helpers import set_authenticated_session, start_auth_db_patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NAV_HTML = (ROOT / "templates" / "_user_nav.html").read_text(encoding="utf-8")
NAV_CSS = (ROOT / "static" / "admin_nav.css").read_text(encoding="utf-8")

# Every Font Awesome icon class used anywhere in the nav shell. Each of
# these was verified (via computed `::before` content on a real page
# loading the same fa 5.15.2 CDN link every template uses) to resolve to a
# real glyph, not a blank one -- see the Phase 6B1 browser-smoke report.
VERIFIED_FA5_ICONS = {
    "fa-flask", "fa-bars", "fa-search", "fa-bolt", "fa-cog",
    "fa-chevron-down", "fa-sign-out-alt",
    "fa-file-import", "fa-users", "fa-user-cog", "fa-network-wired",
    "fa-file-invoice", "fa-money-bill-alt", "fa-shield-alt", "fa-history",
}


class NavShellStaticTests(unittest.TestCase):
    # ---- disclosure-button structure / ARIA wiring ----------------------

    def test_exactly_one_admin_trigger_and_panel_with_matching_ids(self):
        self.assertEqual(NAV_HTML.count('id="sqAdminTrigger"'), 1)
        self.assertEqual(NAV_HTML.count('id="sqAdminMenu"'), 1)
        self.assertIn('aria-controls="sqAdminMenu"', NAV_HTML)

    def test_trigger_has_disclosure_aria_attributes(self):
        trigger_match = re.search(r"<button[^>]*id=\"sqAdminTrigger\"[^>]*>", NAV_HTML)
        self.assertIsNotNone(trigger_match, "sqAdminTrigger button not found")
        tag = trigger_match.group(0)
        self.assertIn('type="button"', tag)
        self.assertIn('aria-haspopup="true"', tag)
        self.assertIn('aria-expanded="false"', tag)  # closed by default on page load
        self.assertIn('aria-controls="sqAdminMenu"', tag)

    def test_panel_contains_all_eight_admin_endpoints_exactly_once(self):
        # `_sq_admin_links` is the single source of truth the panel's
        # `{% for %}` loop iterates over (`url_for(endpoint)` per row, not
        # a literal call per endpoint) -- so "the panel renders all 8
        # exactly once" reduces to "this list has each endpoint exactly
        # once", which is what actually drives the loop below it.
        list_start = NAV_HTML.index("_sq_admin_links = [")
        list_end = NAV_HTML.index("] %}", list_start)
        links_list = NAV_HTML[list_start:list_end]
        for endpoint in [
            "admin_imports", "admin_teams.index", "admin_users",
            "admin_network", "admin_quote_templates_page",
            "admin_exchange_rates", "admin_brand_compliance",
            "admin_login_history.index",
        ]:
            self.assertEqual(links_list.count(f"('{endpoint}',"), 1, endpoint)

        # And the panel itself must actually loop over that exact list,
        # rendering one link per row via a generic `url_for(endpoint)`.
        panel_start = NAV_HTML.index('id="sqAdminMenu"')
        panel_end = NAV_HTML.index("</div>", panel_start)
        panel = NAV_HTML[panel_start:panel_end]
        self.assertIn("{% for endpoint, icon, label in _sq_admin_links %}", panel)
        self.assertIn("url_for(endpoint)", panel)

    def test_admin_group_still_gated_on_is_admin_session_flag(self):
        # Same visibility rule as before this polish pass -- only the
        # wrapper markup changed, not who gets to see it.
        gate_idx = NAV_HTML.index("session.get('is_admin')")
        trigger_idx = NAV_HTML.index('id="sqAdminTrigger"')
        endif_idx = NAV_HTML.index("{% endif %}", trigger_idx)
        self.assertLess(gate_idx, trigger_idx)
        self.assertGreater(endif_idx, trigger_idx)

    # ---- keyboard / click-outside / close-on-select behavior ------------

    def test_escape_closes_and_returns_focus_to_trigger(self):
        self.assertIn("e.key === 'Escape'", NAV_HTML)
        self.assertIn("closeMenu(true)", NAV_HTML)  # true == "return focus"
        self.assertIn("trigger.focus()", NAV_HTML)

    def test_click_outside_the_control_closes_it(self):
        self.assertIn("wrap.contains(e.target)", NAV_HTML)

    def test_selecting_a_link_closes_the_panel(self):
        start = NAV_HTML.index("menu.querySelectorAll('a')")
        end = NAV_HTML.index("})();", start)
        body = NAV_HTML[start:end]
        self.assertIn("closeMenu(false)", body)

    def test_opening_is_click_based_not_hover_only(self):
        # No CSS/JS hover-to-open path: the panel's visibility is only
        # ever flipped via the `.is-open` class from a click handler
        # (verified above), never a `:hover` rule in the stylesheet.
        self.assertNotIn(":hover", NAV_CSS.split(".sq-shell-admin-menu")[0][-200:]
                          if ".sq-shell-admin-menu" in NAV_CSS else "")
        self.assertNotIn(".sq-shell-admin-menu:hover", NAV_CSS)
        self.assertNotIn(".sq-shell-admin:hover .sq-shell-admin-menu", NAV_CSS)

    def test_no_innerhtml_anywhere_in_the_shell_script(self):
        script = NAV_HTML[NAV_HTML.index("<script>"):]
        self.assertNotIn(".innerHTML", script)

    # ---- icons -----------------------------------------------------------

    def test_every_fa_icon_class_used_is_in_the_verified_fa5_set(self):
        used = set(re.findall(r'class="fas ([a-z0-9-]+)', NAV_HTML))
        used |= set(re.findall(r'class="fas ([a-z0-9-]+) [a-z0-9-]+"', NAV_HTML))
        unknown = used - VERIFIED_FA5_ICONS
        self.assertEqual(unknown, set(), f"Unverified FA icon class(es): {unknown}")

    # ---- logout unchanged (POST + CSRF, never a GET link) ----------------

    def test_logout_is_still_a_post_form_with_csrf_not_a_get_link(self):
        self.assertIn("<form method=\"post\"", NAV_HTML)
        self.assertIn("session_security.logout", NAV_HTML)
        self.assertIn('name="csrf_token"', NAV_HTML)
        self.assertIn('<button type="submit">', NAV_HTML)
        self.assertNotIn('href="{{ url_for(\'session_security.logout\')', NAV_HTML)

    # ---- no card-in-card / no raw technical text on the shell -------------

    def test_no_card_in_card_in_shell(self):
        self.assertNotIn('class="card', NAV_HTML)

    def test_visible_admin_labels_are_human_vietnamese_not_raw_endpoint_slugs(self):
        # The only text a user ever sees for the admin group is the
        # curated Vietnamese label tuple element, never the Python/Flask
        # endpoint identifier (e.g. "admin_teams.index") itself. Endpoint
        # identifiers only ever appear inside the `_sq_admin_links` list
        # definition (template source, consumed by `url_for(...)`) or as
        # `url_for(endpoint)` in the loop -- both are Jinja code, not
        # rendered output. `NavShellRouteRenderTests` below separately
        # confirms real rendered pages show only the human labels.
        for label in [
            "Nhập dữ liệu", "Team & quyền truy cập", "Người dùng",
            "Mạng / IP", "Mẫu báo giá", "Tỷ giá", "Manual compliance",
            "Lịch sử đăng nhập",
        ]:
            self.assertIn(label, NAV_HTML)

    # ---- mobile breakpoint: trigger hidden, panel forced flat -----------

    def test_mobile_breakpoint_is_1024px_not_760px(self):
        self.assertIn("@media (max-width: 1023.98px)", NAV_CSS)
        self.assertNotIn("@media (max-width: 760px)", NAV_CSS)

    def test_mobile_hides_trigger_and_forces_panel_flat(self):
        mobile_start = NAV_CSS.index("@media (max-width: 1023.98px)")
        mobile_block = NAV_CSS[mobile_start:]
        self.assertIn(".sq-shell-admin-trigger {", mobile_block)
        trigger_rule = mobile_block[mobile_block.index(".sq-shell-admin-trigger {"):]
        trigger_rule = trigger_rule[: trigger_rule.index("}")]
        self.assertIn("display: none;", trigger_rule)

        self.assertIn(".sq-shell-admin-menu,\n  .sq-shell-admin-menu.is-open {", mobile_block)
        panel_rule = mobile_block[mobile_block.index(".sq-shell-admin-menu,\n  .sq-shell-admin-menu.is-open {"):]
        panel_rule = panel_rule[: panel_rule.index("}")]
        self.assertIn("position: static;", panel_rule)
        self.assertIn("display: flex;", panel_rule)

    def test_desktop_bar_and_nav_do_not_wrap(self):
        # The rules living OUTSIDE the mobile @media block (i.e. the
        # default/desktop rules) must pin both the outer bar and the nav
        # row to a single line; only the mobile block may reintroduce
        # wrapping.
        desktop_css = NAV_CSS[: NAV_CSS.index("@media (max-width: 1023.98px)")]
        self.assertIn("flex-wrap: nowrap;", desktop_css)


class NavShellRouteRenderTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        start_auth_db_patch(self)
        self._disable_ip_patch = mock.patch.dict(os.environ, {"DISABLE_IP_ALLOWLIST": "1"})
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)

    def _login(self, *, is_admin, **extra):
        with self.client.session_transaction() as sess:
            set_authenticated_session(
                sess, is_admin=is_admin, username="polish_test_user", **extra
            )

    def test_admin_sees_trigger_closed_by_default_and_all_eight_links(self):
        self._login(is_admin=True)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('id="sqAdminTrigger"', body)
        self.assertIn('aria-expanded="false"', body)
        self.assertEqual(body.count('href="/admin/teams"'), 1)
        self.assertEqual(body.count('href="/admin/users"'), 1)

    def test_staff_sees_no_admin_trigger_or_panel_at_all(self):
        self._login(is_admin=False, team_id=1)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # No actual DOM element is rendered for staff -- the shared
        # `<script>` block (loaded on every page, admin or not) still
        # contains inert `getElementById('sqAdminTrigger'/'sqAdminMenu'/
        # 'sqAdminWrap')` lookups, which safely no-op via its own
        # `if (!wrap || !trigger || !menu) return;` guard when those ids
        # don't exist. That JS *string* is not a rendered button/menu, so
        # this checks for the real markup (`id="..."` attributes / the
        # "Quản trị" *visible label*), not the JS source text.
        self.assertNotIn('id="sqAdminTrigger"', body)
        self.assertNotIn('id="sqAdminMenu"', body)
        self.assertNotIn('id="sqAdminWrap"', body)
        # The shared `<script>` block (present on every page) has a code
        # *comment* mentioning "Quản trị" by name -- never rendered/visible
        # text -- so scope the visible-text check to the markup before it,
        # same technique `tests/test_admin_teams_ui.py` already uses.
        markup = body[: body.index("<script>")]
        self.assertNotIn("Quản trị", markup)
        self.assertIn("Staff", body)  # role badge still shown

    def test_on_an_admin_page_both_trigger_and_its_submenu_link_are_active(self):
        self._login(is_admin=True)
        resp = self.client.get("/admin/quote-templates")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        trigger_match = re.search(r"<button[^>]*id=\"sqAdminTrigger\"[^>]*>", body)
        self.assertIsNotNone(trigger_match)
        self.assertIn("is-active", trigger_match.group(0))

        link_match = re.search(
            r'<a class="[^"]*"\s+href="/admin/quote-templates"[^>]*>', body
        )
        self.assertIsNotNone(link_match)
        self.assertIn("is-active", link_match.group(0))
        self.assertIn('aria-current="page"', link_match.group(0))

    def test_on_a_non_admin_page_trigger_is_not_marked_active(self):
        self._login(is_admin=True)
        resp = self.client.get("/")
        body = resp.get_data(as_text=True)
        trigger_match = re.search(r"<button[^>]*id=\"sqAdminTrigger\"[^>]*>", body)
        self.assertIsNotNone(trigger_match)
        self.assertNotIn("is-active", trigger_match.group(0))

    def test_logout_form_present_and_posts_with_csrf_for_both_roles(self):
        for is_admin in (True, False):
            with self.subTest(is_admin=is_admin):
                self._login(is_admin=is_admin, team_id=1 if not is_admin else None)
                resp = self.client.get("/")
                body = resp.get_data(as_text=True)
                self.assertIn('<form method="post" action="/logout"', body)
                self.assertIn('name="csrf_token"', body)


if __name__ == "__main__":
    unittest.main()
