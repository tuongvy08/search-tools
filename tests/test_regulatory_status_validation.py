"""Status input regressions using synthetic workbooks and mocked DB boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from flask import Flask
from openpyxl import Workbook

import admin_regulatory
from import_engine import ImportProblem
import regulatory_import_jobs as jobs
from regulatory import normalize_status_label


class StatusValidationTests(unittest.TestCase):
    def test_cas_labels_rejected_even_with_whitespace_or_bad_checksum(self):
        for label in ('1563-66-2', '944-22-9', ' 629-14-1 ', '50-00-1'):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'không phải mã CAS'):
                normalize_status_label(label)

    def test_custom_labels_remain_dynamic_and_trimmed(self):
        for label in ('Hoá chất kiểm soát đặc biệt', 'Theo dõi nội bộ 2026',
                      'Nhóm 2 (IVC)', 'Theo dõi CAS 629-14-1'):
            self.assertEqual(normalize_status_label(' ' + label + ' '), label)
        for label in (None, '', '   ', 'x' * 121):
            with self.subTest(label=label), self.assertRaises(ValueError):
                normalize_status_label(label)

    def parse_workbook(self, status):
        with tempfile.TemporaryDirectory(prefix='regulatory-status-test-') as directory:
            path = Path(directory) / 'rules.xlsx'
            workbook = Workbook()
            workbook.active.append(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'])
            workbook.active.append(['629-14-1', status, 'Dữ liệu thử'])
            workbook.save(path)
            workbook.close()
            return jobs._parse_workbook(path)

    def test_import_rejects_misplaced_cas_with_row_number(self):
        for status in ('1563-66-2', '944-22-9'):
            with self.subTest(status=status), self.assertRaisesRegex(
                    ImportProblem, 'Dòng 2:.*không phải mã CAS'):
                self.parse_workbook(status)

    def test_import_preserves_cas_in_correct_column_and_custom_status(self):
        rows = self.parse_workbook(' Theo dõi nội bộ ')
        self.assertEqual(rows[0]['match_value'], '629-14-1')
        self.assertEqual(rows[0]['status_label'], 'Theo dõi nội bộ')

    def test_plan_also_rejects_previously_parsed_invalid_status_before_db_access(self):
        cursor = mock.Mock()
        for mode in ('upsert', 'replace_scoped'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ImportProblem, 'Dòng 7:'):
                jobs.build_plan(cursor, [dict(row_number=7, status_label='1563-66-2',
                    match_field='cas', match_value='629-14-1', note='')], mode)
        cursor.execute.assert_not_called()

    def test_add_and_rename_routes_reject_cas_without_mutation(self):
        app = Flask(__name__)
        app.secret_key = 'synthetic-status-validation-only'
        app.testing = True
        admin_regulatory.register(app, lambda: None, lambda: 'test-actor')
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=1, auth_version=1)
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (1,)
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        with mock.patch.object(jobs, 'connection', return_value=connection), \
                mock.patch.object(admin_regulatory.manual, 'require_schema'), \
                mock.patch.object(admin_regulatory, '_revalidate_mutation_actor'), \
                mock.patch.object(admin_regulatory.session_security, 'verify_csrf_token', return_value=True):
            for action in ('add', 'rename'):
                cursor.reset_mock()
                response = client.post('/admin/regulatory/statuses', data={
                    'action': action, 'label': '944-22-9', 'status_id': '1', 'revision': 'test'})
                self.assertEqual(response.status_code, 302)
                error = parse_qs(urlparse(response.location).query)['err'][0]
                self.assertIn('không phải mã CAS', error)
                for call in cursor.execute.call_args_list:
                    self.assertTrue(call.args[0].lstrip().startswith('SELECT'), call.args[0])


if __name__ == '__main__':
    unittest.main()
