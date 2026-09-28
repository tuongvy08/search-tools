import uuid
from psycopg2.extras import RealDictCursor
import regulatory_manual as manual
from regulatory_manual_support import RegulatoryCase


class ServiceTests(RegulatoryCase):
    def test_create_edit_noop_deactivate_restore_audit_and_replay(self):
        rule, create = self.create()
        self.assertTrue(self.mutate(create)['replayed'])
        edit = dict(action='edit', rule_id=rule, expected_revision=1, request_id=str(uuid.uuid4()),
                    match_field='code', match_value='DEF', status_id=self.statuses['DUOC_BAN'],
                    note='changed', reason='approved')
        self.mutate(edit)
        self.assertTrue(self.mutate(create)['replayed'])  # original create must not recreate old key
        noop = dict(edit, expected_revision=2, request_id=str(uuid.uuid4()))
        self.assertFalse(self.mutate(noop)['changed'])
        for action, revision in [('deactivate', 2), ('restore', 3)]:
            self.mutate(dict(action=action, rule_id=rule, expected_revision=revision,
                             request_id=str(uuid.uuid4()), reason='reason'))
        row = self.rows('SELECT * FROM regulatory_rules WHERE id=%s', (rule,))[0]
        self.assertEqual((row['revision'], row['is_active'], row['manual_protected']), (4, True, True))
        events = self.rows('SELECT * FROM regulatory_rule_manual_events ORDER BY id')
        self.assertEqual([e['event'] for e in events], ['created','edited','deactivated','restored'])
        self.assertEqual(events[1]['before_json']['match_value'], 'ABC')
        self.assertEqual(events[1]['after_json']['match_value'], 'DEF')
        self.assertEqual(events[0]['actor_id_snapshot'], self.actor)
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_keys')), 2)
        with self.assertRaises(manual.ManualProblem):
            self.mutate(dict(create, note='different'))

    def test_invalid_inputs_stale_immutable_field_reason_and_historical_keys(self):
        rule, original = self.create()
        base = dict(original, action='edit', rule_id=rule, expected_revision=1, request_id=str(uuid.uuid4()))
        for change in [dict(match_field='name'), dict(expected_revision=9), dict(status_id=99999),
                       dict(status_id=self.statuses['DUOC_BAN'], reason='  '), dict(is_active=False),
                       dict(note='x'*4001), dict(expected_revision='1.0')]:
            with self.subTest(change=list(change)):
                with self.assertRaises(manual.ManualProblem):
                    self.mutate(dict(base, **change))
        self.mutate(dict(base, match_value='DEF'))
        with self.assertRaises(manual.ManualProblem) as error:
            self.create('abc')
        self.assertEqual(error.exception.rule_id, rule)
        # Owner can return to own key, not another rule.
        self.mutate(dict(base, match_value='ABC', expected_revision=2, request_id=str(uuid.uuid4())))
        self.assertEqual(self.rows('SELECT match_value FROM regulatory_rules')[0]['match_value'], 'ABC')
        with self.assertRaises(manual.ManualProblem):
            self.create('123-45-6', 'cas')

    def test_fault_rolls_back_rule_keys_and_audit(self):
        from unittest import mock
        rule, _ = self.create()
        with mock.patch('regulatory_manual.preserve_key', side_effect=RuntimeError('fault')):
            with self.assertRaises(RuntimeError):
                self.mutate(dict(action='deactivate', rule_id=rule, expected_revision=1,
                                 request_id=str(uuid.uuid4()), reason='test'))
        self.assertTrue(self.rows('SELECT is_active FROM regulatory_rules')[0]['is_active'])
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_events')), 1)

    def test_audit_failure_after_update_and_key_insertion_is_atomic(self):
        import psycopg2
        rule, original = self.create()
        before = self.rows('SELECT * FROM regulatory_rules')
        self.rows("""CREATE FUNCTION fail_manual_event() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected audit failure'; END $$""")
        self.rows('CREATE TRIGGER fail_event BEFORE INSERT ON regulatory_rule_manual_events FOR EACH ROW EXECUTE FUNCTION fail_manual_event()')
        edit = dict(original,action='edit',rule_id=rule,expected_revision=1,request_id=str(uuid.uuid4()),match_value='NEW')
        with self.assertRaises(psycopg2.Error):
            self.mutate(edit)
        self.assertEqual(self.rows('SELECT * FROM regulatory_rules'),before)
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_keys')),1)
        self.rows('DROP TRIGGER fail_event ON regulatory_rule_manual_events')
        self.assertTrue(self.mutate(edit)['changed'])

    def test_list_filters_literal_wildcards_pagination(self):
        for i in range(103):
            self.create('CODE%_' + str(i))
        self.create('not a wildcard')
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            first = manual.list_rules(cur, dict(q='%_'))
            second = manual.list_rules(cur, dict(q='%_', page='2'))
            last = manual.list_rules(cur, dict(q='%_', page='3'))
            self.assertEqual((len(first['rules']), len(second['rules']), len(last['rules']), first['total']), (50,50,3,103))
            self.assertEqual(len({r['id'] for result in [first, second, last] for r in result['rules']}),103)
            for args in [dict(page='-1'), dict(state='bad'), dict(status_id='999999')]:
                with self.assertRaises(manual.ManualProblem):
                    manual.list_rules(cur, args)
