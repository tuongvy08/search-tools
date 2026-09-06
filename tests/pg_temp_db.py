"""Shared helper (Phase 6A -- Local Release Gate): create/drop a uniquely
named, throwaway Postgres database with a full real schema, for the
handful of pre-existing "local DB only" business-logic test files that
used to connect DIRECTLY to whatever `DATABASE_URL` pointed at (in
practice, the real local app database, `products_local`, per `.env`) and
write prefixed-but-real fixture rows there.

That pattern (write a `CURSOR_*`/`BATCH_*`-prefixed fixture, clean it up
in tearDown) predates Phase 6A and was "safe enough" back when the app had
no request-level middleware that could behave differently depending on
what's actually sitting in `products_local`/`teams`/`office_ip_allowlist`.
Phase 6A's real, always-on IP/team-policy middleware (`middleware_access.py`)
changes that: a test hitting a real Flask route with a real authenticated
session now triggers a REAL policy read against whatever `teams`/
`office_ip_allowlist` rows genuinely exist in the target database. Pointed
at `products_local`, that read depends on production-like configuration
this test suite has no business depending on (or perturbing) -- and more
simply, "never write to `products_local` from an automated test" is a hard
requirement on its own regardless of middleware.

This module does NOT invent a new schema-management system. It reuses,
verbatim, the exact migration-file set + minimal hand-written base schema
`tests/test_team_permissions_pg.py` already established and has been
running green against: `teams`/`app_users`/`team_brands` (minimal, pre-014
shape) + migrations 014/015/016/006 + `schema.sql` (products) + migrations
003/011/012 + a tiny `exchange_rates` stand-in (`search._exchange_rate_map`
needs the table to exist to avoid aborting the transaction, even though
none of these test files exercise exchange-rate conversion). Every table
any of the 6 affected business test files (`test_admin_brand_compliance`,
`test_batch_queries`, `test_search_result_display`,
`test_search_compliance_precedence`, `test_quote_assistant_api`,
`test_product_manual_compliance_import`) reference is covered by this set.

Usage (see any of the files above for the exact wiring):

    from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable

    @unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
    class SomeRealDbTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.db_name, cls.dsn = create_full_schema_temp_db()
            cls.conn = psycopg2.connect(cls.dsn)
            ...

        @classmethod
        def tearDownClass(cls):
            try:
                cls.conn.close()
            finally:
                drop_temp_db(cls.db_name)  # even if setUpClass partially failed above this line
"""
import os
import secrets
from urllib.parse import urlparse, urlunparse

import psycopg2

_SQL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql")

_FULL_SCHEMA_SQL_FILES = (
    "migration_014_google_oidc.sql",
    "migration_015_team_policy.sql",
    "migration_016_team_permission_previews.sql",
    "migration_006_office_ip_allowlist.sql",
    "schema.sql",
    "migration_003_regulatory_rules.sql",
    "migration_011_manual_compliance.sql",
    "migration_012_product_preparation_type.sql",
)

# Same minimal pre-014 base `test_admin_pg_integration.py` /
# `test_team_permissions_pg.py` use -- teams / app_users / team_brands only
# get their Google-OIDC / team-policy columns via the real migration_014 /
# migration_015 files applied right after, exactly like a real deploy would.
_MINIMAL_BASE_SCHEMA_SQL = """
CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE app_users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    team_id INTEGER NULL REFERENCES teams(id) ON DELETE SET NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    ip_bypass_allowlist BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE team_brands (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    PRIMARY KEY (team_id, brand)
);
"""

# search._exchange_rate_map() SELECTs this table and only gracefully falls
# back to JSON defaults if the query itself raises -- but a failed
# statement on a non-autocommit connection aborts the whole transaction for
# whatever runs next. None of the 6 files this helper serves test exchange
# rates; they just need the table to exist so an incidental call doesn't
# poison their transaction.
_EXCHANGE_RATES_SQL = "CREATE TABLE IF NOT EXISTS exchange_rates (brand TEXT PRIMARY KEY, rate NUMERIC NOT NULL)"

_TEST_DB_PREFIX = "p6a_release_gate_pgtest_"


def _read_sql(name):
    with open(os.path.join(_SQL_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def real_database_url():
    return os.environ.get("DATABASE_URL", "")


def dsn_for(dbname):
    """Build a DSN identical to the real DATABASE_URL except for the
    database name -- i.e. same host/port/user/password, different `dbname`.
    """
    parsed = urlparse(real_database_url())
    return urlunparse(parsed._replace(path="/" + dbname))


def maintenance_dsn():
    # `postgres` is Postgres's own always-present maintenance database --
    # deliberately NOT `products_local`, so CREATE/DROP DATABASE never even
    # opens a connection to the real application database.
    return dsn_for("postgres")


def probe_postgres_reachable():
    if not real_database_url():
        return False
    try:
        conn = psycopg2.connect(maintenance_dsn(), connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def create_full_schema_temp_db():
    """Create a brand-new, uniquely-named database (never `products_local`)
    with the full real schema described in this module's docstring.
    Returns `(db_name, dsn)`. Caller MUST call `drop_temp_db(db_name)` in
    `tearDown`/`tearDownClass` for the success path, even if a later
    (caller-side) setup step fails after this function returns.

    If schema application itself fails partway through (this function's
    own responsibility, not the caller's), the just-created database is
    dropped before the exception propagates -- CREATE DATABASE succeeding
    but a later migration statement failing must not leak a stray
    `p6a_release_gate_pgtest_*` database that nothing will ever clean up
    (unittest does NOT call `tearDownClass` when `setUpClass` raises, so a
    caller relying solely on its own tearDownClass to clean up would leak
    exactly this way otherwise).
    """
    db_name = _TEST_DB_PREFIX + secrets.token_hex(4)
    dsn = dsn_for(db_name)

    maint = psycopg2.connect(maintenance_dsn())
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        maint.close()

    try:
        conn = psycopg2.connect(dsn)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_MINIMAL_BASE_SCHEMA_SQL)
                    for fname in _FULL_SCHEMA_SQL_FILES:
                        cur.execute(_read_sql(fname))
                    cur.execute(_EXCHANGE_RATES_SQL)
        finally:
            conn.close()
    except Exception:
        drop_temp_db(db_name)
        raise

    return db_name, dsn


def apply_brand_master_and_currency_migrations(cur):
    """Applies migration_017 (canonical Brand Master) + migration_018
    (central currency_rates) on top of an already-created
    `create_full_schema_temp_db()` database.

    Deliberately NOT folded into `_FULL_SCHEMA_SQL_FILES`/
    `create_full_schema_temp_db()` itself: dozens of pre-existing test files
    build their fixtures against the pre-017 shape (arbitrary ad-hoc brand
    strings with no `brand_master`/`source_brand` at all) and must keep
    working unmodified. Only tests that specifically exercise the Phase
    6B2B1/6B2B2 canonical-brand/currency-rate behavior should call this
    helper, right after `create_full_schema_temp_db()`, before inserting
    any fixture rows.

    Both migration files are additive/idempotent and contain no
    `CREATE INDEX CONCURRENTLY` statements, so they can run as a single
    `cur.execute(full_text)` inside the caller's existing transaction --
    migration_017's preflight checks are satisfied trivially on a
    freshly-created, empty `products` table (0 unmapped brands).
    """
    for fname in ("migration_017_brand_master.sql", "migration_018_currency_rates.sql"):
        cur.execute(_read_sql(fname))


def apply_sql_file_statement_by_statement(cur, sql_path):
    """Execute a `.sql` file one statement per `cur.execute()` call, instead
    of the whole file text in one call.

    Needed specifically for files containing `CREATE INDEX CONCURRENTLY`
    (migrations 007/008/010): that statement cannot run inside a
    transaction block, and even with the connection in autocommit mode,
    psycopg2/libpq's "simple query protocol" implicitly wraps a
    MULTI-statement string passed to one `execute()` call in a transaction
    regardless of the session's autocommit setting -- so a file with more
    than one statement must still be split and executed one at a time.

    Splitting naively on `;` is NOT enough: comment lines can contain
    literal semicolons (e.g. migration_010's "Local/dev first; run on
    production..."), which would otherwise be misread as a statement
    boundary and produce an empty/truncated fragment. This strips whole
    `--`-comment LINES first (not inline trailing comments -- none of the
    migration files this is used for have any), then splits what's left on
    `;`, so comment semicolons never affect statement boundaries.

    Caller's connection must be in autocommit mode already.
    """
    with open(sql_path, "r", encoding="utf-8") as f:
        raw = f.read()
    code_only = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("--")
    )
    for statement in code_only.split(";"):
        statement = statement.strip()
        if statement:
            cur.execute(statement)


def drop_temp_db(db_name):
    """Drop a database created by `create_full_schema_temp_db`. Refuses to
    drop anything not created by this helper (wrong name prefix) or not on
    localhost, as a last-resort safety net against ever touching a real
    database by a typo/bug elsewhere.
    """
    if not db_name or not db_name.startswith(_TEST_DB_PREFIX):
        raise ValueError(f"Refusing to drop DB with unexpected name: {db_name!r}")
    parsed = urlparse(dsn_for(db_name))
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"Refusing to drop DB on unexpected host: {parsed.hostname!r}")

    maint = psycopg2.connect(maintenance_dsn())
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        maint.close()
