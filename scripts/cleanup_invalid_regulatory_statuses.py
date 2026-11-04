"""Dry-run by default; explicit production approval is required outside this CLI."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from psycopg2.extras import RealDictCursor
from db import get_connection
from regulatory_status_cleanup import inspect_statuses, cleanup_statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='append', required=True, metavar='ID=CAS_LABEL')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--fingerprint')
    parser.add_argument('--actor')
    parser.add_argument('--reason')
    args = parser.parse_args()
    try:
        pairs = [item.split('=', 1) for item in args.status]
        targets = {int(key): label for key, label in pairs}
        if len(targets) != len(pairs):
            parser.error('Duplicate IDs are not allowed.')
    except ValueError:
        parser.error('Use --status ID=CAS_LABEL.')
    if args.apply and not all((args.fingerprint, args.actor, args.reason)):
        parser.error('--apply requires --fingerprint, --actor and --reason.')
    conn = get_connection()
    try:
        conn.set_session(isolation_level='READ COMMITTED' if args.apply else 'REPEATABLE READ',
                         readonly=not args.apply)
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = (cleanup_statuses(cur, targets, args.fingerprint, actor=args.actor, reason=args.reason)
                      if args.apply else inspect_statuses(cur, targets))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not echo connection errors/DSNs or database rows into logs.
        from regulatory_status_cleanup import CleanupProblem
        if isinstance(exc, CleanupProblem):
            print(str(exc), file=sys.stderr)
        else:
            print('Cleanup failed; transaction was not confirmed. Review the environment safely.', file=sys.stderr)
        sys.exit(1)
