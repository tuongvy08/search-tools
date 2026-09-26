"""Small, permission-scoped candidates; never pricing/compliance or full search."""
import time
import unicodedata

import psycopg2

from stock import _visible_stock_sql, literal_like

MIN_QUERY = 3
MAX_QUERY = 500
MAX_RESULTS = 10
BUDGET_MS = 500
STATEMENT_MS = 150


def normalize_query(value):
    query = unicodedata.normalize('NFC', value.strip())
    if len(query) > MAX_QUERY or '\x00' in query:
        raise ValueError('Từ khóa không hợp lệ (tối đa 500 ký tự).')
    return query


def visible_fields(grants):
    return [field for field, grant in (('name', 'VIEW_NAME'), ('code', 'VIEW_CODE'), ('cas', 'VIEW_CAS'))
            if grant in grants and (field != 'cas' or 'SEARCH_BY_CAS' in grants)]


def candidate_query(query, fields, stage, *, is_admin, team_id):
    """At most 10 rows per source/field branch, visibility before each LIMIT.

    Exact code/CAS use existing 007/008 btrees; prefix/contains use 010 raw GIN.
    A GIN bitmap or selective team scan can still be expensive: timeout is the
    work bound, not LIMIT. No global sort/count over either relation.
    """
    branches, params = [], []
    for source, table in (('stock', 'stock_items'), ('catalog', 'products')):
        for field in fields:
            assert field in ('name', 'code', 'cas')
            # Exact name over a common trigram would build/recheck a huge bitmap
            # just to discover zero exact names. Prefix includes exact names;
            # rank those first within the bounded candidates instead.
            if stage == 'exact' and field == 'name':
                continue
            vis, vp = _visible_stock_sql('p', is_admin=is_admin, team_id=team_id)
            active = (' JOIN stock_state s ON s.active_snapshot_id=p.snapshot_id AND s.singleton=TRUE'
                      if source == 'stock' else '')
            if stage == 'exact':
                match = (f"p.{field} IS NOT NULL AND TRIM(p.{field})<>'' "
                         f"AND upper(TRIM(p.{field}))=upper(TRIM(%s))")
                pattern = query
            elif stage == 'prefix':
                match, pattern = f"p.{field} ILIKE %s ESCAPE '!'", literal_like(query) + '%'
            else:
                match, pattern = f"p.{field} ILIKE %s ESCAPE '!'", '%' + literal_like(query) + '%'
            branches.append(f"(SELECT p.{field} AS value,'{source}' AS source FROM {table} p{active} "
                            f"WHERE {match} {vis} LIMIT {MAX_RESULTS})")
            params.extend((pattern, *vp))
    return ' UNION ALL '.join(branches), tuple(params)


def suggest(conn, query, *, grants, is_admin, team_id):
    fields = visible_fields(grants)
    if len(query) < MIN_QUERY or not fields:
        return {'suggestions': [], 'degraded': False}
    deadline = time.monotonic() + BUDGET_MS / 1000
    found, seen = [], set()
    try:
        with conn, conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            for stage in ('exact', 'prefix', 'contains'):
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    return {'suggestions': found, 'degraded': True}
                cur.execute("SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                            (str(min(STATEMENT_MS, remaining)), str(min(100, remaining))))
                sql, params = candidate_query(query, fields, stage, is_admin=is_admin, team_id=team_id)
                if not sql:
                    continue
                cur.execute(sql, params)
                # Only bounded candidates, all from explicitly visible fields.
                # Stock first within each rank so common catalog names do not
                # invariably hide the much smaller active inventory.
                for value, source in sorted(cur.fetchall(), key=lambda row: (row[0].casefold() != query.casefold(), row[1] != 'stock', len(row[0]), row[0])):
                    key = value.casefold()
                    if not value.strip() or len(value) > MAX_QUERY or key in seen:
                        continue
                    seen.add(key)
                    found.append({'value': value, 'label': value, 'source': source})
                    if len(found) == MAX_RESULTS:
                        return {'suggestions': found, 'degraded': False}
        return {'suggestions': found, 'degraded': False}
    except psycopg2.Error:
        # The context manager rolled back. Do not leak SQL/hidden field data.
        return {'suggestions': found, 'degraded': True}
