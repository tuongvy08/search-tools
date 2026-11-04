"""Explicit, fingerprinted cleanup of unreferenced CAS-shaped status labels.

Caller owns the transaction. Never loads dotenv or chooses a database.
"""
import hashlib
import json
import re

from psycopg2 import sql
from psycopg2.extras import Json

from regulatory import acquire_regulatory_lock, clean_text


class CleanupProblem(ValueError):
    pass


def inspect_statuses(cur, targets, *, lock=False):
    if not targets or len(targets) > 20:
        raise CleanupProblem('Choose 1-20 exact status IDs and labels.')
    for key, value in targets.items():
        if not isinstance(key, int) or key <= 0 or not re.fullmatch(r'[0-9]{2,7}-[0-9]{2}-[0-9]', value):
            raise CleanupProblem('Only exact CAS-shaped labels may be cleaned up.')
    cur.execute('SELECT current_database() AS database')
    database = cur.fetchone()['database']
    cur.execute('SELECT * FROM public.regulatory_statuses WHERE id=ANY(%s) ORDER BY id' +
                (' FOR UPDATE' if lock else ''), (list(targets),))
    statuses = [dict(row) for row in cur.fetchall()]
    if len(statuses) != len(targets) or any(row['label'] != targets[row['id']] for row in statuses):
        raise CleanupProblem('Status ID/label changed or missing. Inspect again.')
    cur.execute("""SELECT n.nspname AS schema,c.relname AS table,a.attname AS column,
                   cardinality(f.conkey) AS key_count
        FROM pg_constraint f
        JOIN pg_class c ON c.oid=f.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=f.conkey[1]
        WHERE f.contype='f' AND f.confrelid='public.regulatory_statuses'::regclass
        ORDER BY n.nspname,c.relname,a.attname""")
    foreign_keys = cur.fetchall()
    references = []
    for fk in foreign_keys:
        if fk['key_count'] != 1:
            raise CleanupProblem('Unsupported composite reference; manual review required.')
        cur.execute(sql.SQL('SELECT count(*) AS count FROM {}.{} WHERE {}=ANY(%s)').format(
            sql.Identifier(fk['schema']), sql.Identifier(fk['table']), sql.Identifier(fk['column'])),
            (list(targets),))
        references.append(dict(schema=fk['schema'], table=fk['table'], column=fk['column'],
                               count=cur.fetchone()['count']))
    snapshot = dict(database=database, statuses=statuses, references=references)
    # Match the JSON form written by the CLI and retained in the audit snapshot.
    snapshot = json.loads(json.dumps(snapshot, default=str, sort_keys=True))
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(snapshot, fingerprint=digest, can_delete=not any(r['count'] for r in references))


def cleanup_statuses(cur, targets, expected_fingerprint, *, actor, reason):
    actor = clean_text(actor, max_chars=200)
    reason = clean_text(reason, max_chars=1000)
    if not actor or not reason or not expected_fingerprint:
        raise CleanupProblem('Actor, reason and inspected fingerprint are required.')
    acquire_regulatory_lock(cur)
    plan = inspect_statuses(cur, targets, lock=True)
    if plan['fingerprint'] != expected_fingerprint:
        raise CleanupProblem('Data changed since inspection. Inspect again.')
    if not plan['can_delete']:
        raise CleanupProblem('Status has current or historical references; nothing deleted.')
    cur.execute('DELETE FROM public.regulatory_statuses WHERE id=ANY(%s) RETURNING id', (list(targets),))
    if {row['id'] for row in cur.fetchall()} != set(targets):
        raise CleanupProblem('Unexpected deletion scope; transaction must roll back.')
    cur.execute("""INSERT INTO public.regulatory_status_events
        (status_id,actor,event,before_json,after_json) VALUES (NULL,%s,'removed_invalid_labels',%s,%s)""",
        (actor, Json(plan), Json(dict(removed_ids=sorted(targets), reason=reason))))
    return dict(removed_ids=sorted(targets), fingerprint=expected_fingerprint)
