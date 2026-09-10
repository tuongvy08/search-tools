"""Cache-busting contract for the Phase 6C0.1 permission UI assets."""
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, nodes

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = '20260908c2'
ASSET_VERSIONS = {
    'admin_quote_templates.js': '20260909c4', 'quick_quote.js': '20260909d1',
    'script.js': '20260910d1', 'styles.css': '20260910d1',
}
CAPABILITY_ASSETS = {
    'script.js', 'quick_quote.js', 'styles.css',
    'team_permissions.js', 'admin_team_permissions.js', 'admin_teams.css',
    'quote_export_context.js', 'admin_quote_templates.js',
}


class StaticAssetVersionTests(unittest.TestCase):
    def test_quote_export_frontend_sends_the_active_result_source(self):
        permissions_js = (ROOT / 'static' / 'team_permissions.js').read_text(encoding='utf-8')
        search_js = (ROOT / 'static' / 'script.js').read_text(encoding='utf-8')
        self.assertIn("body.append('source', String(source || ''))", permissions_js)
        self.assertIn('resultSource,', search_js)

    def test_every_template_load_site_uses_current_release(self):
        references = {asset: [] for asset in CAPABILITY_ASSETS}
        env = Environment()
        for path in sorted((ROOT / 'templates').rglob('*.html')):
            source = path.read_text(encoding='utf-8')
            for call in env.parse(source).find_all(nodes.Call):
                if not isinstance(call.node, nodes.Name) or call.node.name != 'url_for':
                    continue
                if not call.args or not isinstance(call.args[0], nodes.Const) or call.args[0].value != 'static':
                    continue
                kwargs = {kw.key: kw.value for kw in call.kwargs}
                filename = kwargs.get('filename')
                if not isinstance(filename, nodes.Const) or filename.value not in CAPABILITY_ASSETS:
                    continue
                asset = filename.value
                references[asset].append(path.name)
                version = kwargs.get('v')
                with self.subTest(template=path.name, asset=asset, line=call.lineno):
                    self.assertIsInstance(version, nodes.Const, 'Static asset needs an explicit release version')
                    self.assertEqual(version.value, ASSET_VERSIONS.get(asset, RELEASE_VERSION))

            # Also cover literal /static URLs, so switching away from url_for
            # cannot silently evade the release-version check.
            for match in re.finditer(r'''(?:src|href)\s*=\s*(["'])(/static/[^"']+)\1''', source):
                url = urlsplit(match.group(2))
                asset = url.path.removeprefix('/static/')
                if asset in CAPABILITY_ASSETS:
                    references[asset].append(path.name)
                    with self.subTest(template=path.name, asset=asset):
                        self.assertEqual(parse_qs(url.query).get('v'), [ASSET_VERSIONS.get(asset, RELEASE_VERSION)])

        for asset, sites in references.items():
            with self.subTest(asset=asset):
                self.assertTrue(sites, 'Expected asset must retain at least one template load site')
        self.assertIn('index.html', references['styles.css'])
        self.assertIn('quick_quote.html', references['styles.css'])


if __name__ == '__main__':
    unittest.main()
