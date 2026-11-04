"""Cleanup rehearsals use disposable local PostgreSQL databases only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

from regulatory_manual_support import RegulatoryCase
from regulatory_status_cleanup import CleanupProblem, cleanup_statuses, inspect_statuses


class CleanupTests(RegulatoryCase):
    def setUp(self):
        super().setUp()
        self.bad = self.rows("""INSERT INTO regulatory_statuses(stable_key,label,priority,export_policy)
            VALUES ('BAD_TEST','1563-66-2',99,'ALLOW') RETURNING id""")[0]['id']
        self.targets = {self.bad: '1563-66-2'}

    def inspect(self):
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return inspect_statuses(cur, self.targets)

    def apply(self, plan):
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return cleanup_statuses(cur, self.targets, plan['fingerprint'], actor='test', reason='invalid label')

    def test_cli_defaults_read_only_and_exact_deletion_is_audited(self):
        before = self.rows('SELECT * FROM regulatory_statuses ORDER BY id')
        proc = subprocess.run([sys.executable, '-B', 'scripts/cleanup_invalid_regulatory_statuses.py',
                               '--status', str(self.bad)+'=1563-66-2'],
                              cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertTrue(plan['can_delete'])
        self.assertEqual(self.rows('SELECT * FROM regulatory_statuses ORDER BY id'), before)
        self.assertEqual(self.rows('SELECT * FROM regulatory_status_events'), [])
        self.assertEqual(self.apply(plan)['removed_ids'], [self.bad])
        self.assertEqual(self.rows('SELECT * FROM regulatory_statuses ORDER BY id'),
                         [r for r in before if r['id'] != self.bad])
        audit = self.rows('SELECT * FROM regulatory_status_events')[0]
        self.assertIsNone(audit['status_id'])
        self.assertEqual(audit['before_json']['statuses'][0]['label'], '1563-66-2')

    def test_all_foreign_key_references_including_history_prevent_deletion(self):
        rule, _ = self.create()
        # Full production recovery table is optional in the common test schema.
        # Install its status FK so introspection must discover it too.
        self.rows('CREATE TABLE product_deleted_rows (manual_compliance_status_id bigint REFERENCES regulatory_statuses(id))')
        statements = [
            ("INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value) VALUES (%s,'','','cas','50-00-0')", (self.bad,)),
            ("INSERT INTO products(name,code,brand,manual_compliance) VALUES ('test','test','test','1563-66-2')", ()),
            ('INSERT INTO product_deleted_rows VALUES (%s)', (self.bad,)),
            ("INSERT INTO regulatory_rule_manual_keys(rule_id,status_id,match_field,match_value) VALUES (%s,%s,'cas','50-00-0')", (rule,self.bad)),
            ("INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%s,'test','created')", (self.bad,)),
        ]
        for statement, args in statements:
            with self.subTest(statement=statement):
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(statement, args)
                    plan = inspect_statuses(cur, self.targets)
                    self.assertFalse(plan['can_delete'])
                    with self.assertRaisesRegex(CleanupProblem, 'references'):
                        cleanup_statuses(cur, self.targets, plan['fingerprint'], actor='test', reason='test')
                self.conn.rollback()
                self.assertTrue(self.inspect()['can_delete'])

    def test_stale_fingerprint_label_and_new_reference_fail_closed(self):
        plan = self.inspect()
        self.rows('UPDATE regulatory_statuses SET priority=100 WHERE id=%s', (self.bad,))
        with self.assertRaisesRegex(CleanupProblem, 'changed'):
            self.apply(plan)
        plan = self.inspect()
        self.rows("INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%s,'other','created')", (self.bad,))
        with self.assertRaisesRegex(CleanupProblem, 'changed'):
            self.apply(plan)
        self.rows("UPDATE regulatory_statuses SET label='renamed' WHERE id=%s", (self.bad,))
        with self.assertRaisesRegex(CleanupProblem, 'changed or missing'):
            self.inspect()

    def test_concurrent_reference_times_out_instead_of_deleting(self):
        plan = self.inspect()
        other = psycopg2.connect(self.dsn)
        self.addCleanup(other.close)
        with other.cursor() as cur:
            cur.execute("INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%s,'other','created')", (self.bad,))
        with self.assertRaises(psycopg2.errors.LockNotAvailable):
            with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL lock_timeout='100ms'")
                cleanup_statuses(cur, self.targets, plan['fingerprint'], actor='test', reason='test')
        other.commit()
        self.assertFalse(self.inspect()['can_delete'])
        self.assertEqual(len(self.rows('SELECT id FROM regulatory_statuses WHERE id=%s', (self.bad,))), 1)

    def test_audit_failure_rolls_back_delete(self):
        self.rows("""CREATE FUNCTION reject_cleanup_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'audit failure'; END $$""")
        self.rows('CREATE TRIGGER reject_audit BEFORE INSERT ON regulatory_status_events FOR EACH ROW EXECUTE FUNCTION reject_cleanup_audit()')
        with self.assertRaises(psycopg2.Error):
            self.apply(self.inspect())
        self.assertTrue(self.inspect()['can_delete'])

    def test_bad_targets_and_missing_actor_are_rejected(self):
        for targets in ({}, {self.bad:'ordinary status'}, {self.bad:'944-22-9'}, {999999:'944-22-9'}):
            with self.subTest(targets=targets), self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                with self.assertRaises(CleanupProblem):
                    inspect_statuses(cur, targets)
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            with self.assertRaises(CleanupProblem):
                cleanup_statuses(cur, self.targets, 'hash', actor='', reason='test')
