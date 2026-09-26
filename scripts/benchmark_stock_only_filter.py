#!/usr/bin/env python3
"""Disposable PostgreSQL benchmark for the Phase 6D8 Product Search filter.

The script creates and drops its own database via tests/pg_temp_db.py.  It
prints aggregate timings and selected EXPLAIN evidence only; it never prints a
DSN and must not be pointed at a production server.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pg_temp_db  # noqa: E402
import search  # noqa: E402
from regulatory import product_resolver_lateral  # noqa: E402
from stock import catalog_in_stock_filter_sql  # noqa: E402


def _plan_summary(plan):
    nodes = []

    def visit(node, depth=0):
        nodes.append(
            {
                "depth": depth,
                "node": node.get("Node Type"),
                "relation": node.get("Relation Name"),
                "index": node.get("Index Name"),
                "actual_rows": node.get("Actual Rows"),
                "loops": node.get("Actual Loops"),
                "filter": node.get("Filter"),
                "rows_removed": node.get("Rows Removed by Filter"),
            }
        )
        for child in node.get("Plans", []):
            visit(child, depth + 1)

    visit(plan["Plan"])
    return {
        "planning_ms": plan.get("Planning Time"),
        "execution_ms": plan.get("Execution Time"),
        "nodes": nodes,
    }


def _catalog_sql(*, filtered):
    where = "p.name ILIKE %s OR p.code ILIKE %s OR p.cas ILIKE %s"
    resolver = product_resolver_lateral("p", "bcs")
    if filtered:
        stock_cte, stock_predicate, stock_params = catalog_in_stock_filter_sql(
            "p", is_admin=True, team_id=None, include_same_cas=True
        )
        if stock_params:
            raise AssertionError("admin benchmark unexpectedly produced stock visibility parameters")
        return f"""
            WITH {stock_cte},
            filtered_products AS MATERIALIZED (
                SELECT p.* FROM products p
                WHERE ({where})
                  AND {stock_predicate}
            )
            SELECT p.id,rr.rule_label
            FROM filtered_products p
            LEFT JOIN brand_compliance_settings bcs
              ON bcs.brand_norm=UPPER(TRIM(COALESCE(p.brand,'')))
            {resolver}
        """
    return f"""
        SELECT p.id,rr.rule_label
        FROM products p
        LEFT JOIN brand_compliance_settings bcs
          ON bcs.brand_norm=UPPER(TRIM(COALESCE(p.brand,'')))
        {resolver}
        WHERE ({where})
    """


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", type=int, default=1_000_000)
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if args.products < 10_000 or args.products > 2_000_000:
        raise SystemExit("--products must be between 10000 and 2000000")
    if not pg_temp_db.probe_postgres_reachable():
        raise SystemExit("Disposable PostgreSQL is not reachable")

    db_name = None
    started = time.monotonic()
    try:
        db_name, dsn = pg_temp_db.create_full_schema_temp_db()
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                pg_temp_db.apply_brand_master_and_currency_migrations(cur)
                pg_temp_db.apply_dynamic_brand_currency_migration(cur)
                cur.execute("INSERT INTO brand_master(name,normalized_name,currency_code) VALUES ('Brand A','BRAND A','VND')")
                cur.execute(
                    """INSERT INTO products(name,code,cas,brand,size,ship,price,source_brand)
                       SELECT CASE WHEN n%%1000=0 THEN 'Needle product '||n
                                   WHEN n%%1000=1 THEN 'No CAS needle '||n
                                   WHEN n%%1000=2 THEN 'Unicode negative '||n
                                   ELSE 'Bulk product '||n END,
                              CASE WHEN n%%1000=2 THEN 'ITEM-ñ-'||n ELSE 'CODE-'||n END,
                              CASE WHEN n%%1000=1 THEN '' ELSE 'CAS-'||n END,
                              'Brand A','1g','1','100','Brand A'
                       FROM generate_series(1,%s) AS n""",
                    (args.products,),
                )
                active, historical = str(uuid.uuid4()), str(uuid.uuid4())
                cur.execute(
                    """INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256)
                       VALUES (%s,'IMPORT','benchmark',10000,''),(%s,'IMPORT','benchmark',10000,'')""",
                    (active, historical),
                )
                cur.execute(
                    "UPDATE stock_state SET active_snapshot_id=%s,revision=1,updated_at=now() WHERE singleton=TRUE",
                    (active,),
                )
                for snapshot in (active, historical):
                    cur.execute(
                        """INSERT INTO stock_items(
                               snapshot_id,name,code,cas,brand,size,quantity,stock_price_vnd,
                               stock_note,brand_norm,code_norm,cas_norm,size_norm)
                           SELECT %s,'Stock item '||n,
                                  CASE WHEN n<=100 THEN 'CODE-'||(n*1000) ELSE 'STOCK-'||n END,
                                  CASE WHEN n=101 THEN '' WHEN n<=100 THEN 'CAS-'||(n*1000) ELSE 'STOCK-CAS-'||n END,
                                  'Brand A','1g',CASE WHEN n<=100 AND n%%10=0 THEN 0 ELSE 5 END,
                                  1000,'','brand a',
                                  CASE WHEN n<=100 THEN 'code-'||(n*1000) ELSE 'stock-'||n END,
                                  CASE WHEN n=101 THEN '' WHEN n<=100 THEN 'cas-'||(n*1000) ELSE 'stock-cas-'||n END,
                                  '1g'
                           FROM generate_series(1,10000) AS n""",
                        (snapshot,),
                    )
                for migration in (
                    "migration_007_products_code_upper_trim_index.sql",
                    "migration_008_check_cas_perf_indexes.sql",
                    "migration_010_search_trgm_indexes.sql",
                ):
                    pg_temp_db.apply_sql_file_statement_by_statement(cur, ROOT / "sql" / migration)
                cur.execute("ANALYZE products")
                cur.execute("ANALYZE stock_items")

                pattern = "%Needle product%"
                plans = {}
                for label, filtered in (("off", False), ("on", True)):
                    cur.execute(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + _catalog_sql(filtered=filtered),
                        (pattern, pattern, pattern),
                    )
                    plans[label] = _plan_summary(cur.fetchone()[0][0])
                no_cas_pattern = "%No CAS needle%"
                cur.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + _catalog_sql(filtered=True),
                    (no_cas_pattern, no_cas_pattern, no_cas_pattern),
                )
                plans["on_no_cas_negative"] = _plan_summary(cur.fetchone()[0][0])
                unicode_pattern = "%Unicode negative%"
                cur.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + _catalog_sql(filtered=True),
                    (unicode_pattern, unicode_pattern, unicode_pattern),
                )
                plans["on_unicode_slow_negative"] = _plan_summary(cur.fetchone()[0][0])

                cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES ('benchmark_admin','x',TRUE) RETURNING id")
                admin_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO teams(name,permission_keys) VALUES ('Benchmark Team',%s) RETURNING id",
                    (['SEARCH','SEARCH_BY_CAS','VIEW_NAME','VIEW_CODE','VIEW_CAS','VIEW_BRAND','VIEW_SIZE','VIEW_PRICE','VIEW_NOTE','VIEW_COMPLIANCE','VIEW_COMPLIANCE_NOTE'],),
                )
                team_id = cur.fetchone()[0]
                cur.execute("INSERT INTO team_brands(team_id,brand) VALUES (%s,'Brand A')", (team_id,))
                cur.execute(
                    "INSERT INTO app_users(username,password_hash,is_admin,team_id) VALUES ('benchmark_team','x',FALSE,%s) RETURNING id",
                    (team_id,),
                )
                team_user_id = cur.fetchone()[0]
        finally:
            conn.close()

        old_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = dsn
        search.app.testing = True
        client = search.app.test_client()
        with client.session_transaction() as session:
            session.update(
                authenticated=True,
                user_id=admin_id,
                auth_version=1,
                is_admin=True,
                team_id=None,
                username="benchmark",
                auth_provider="LOCAL",
                csrf_token="benchmark",
            )
        team_client = search.app.test_client()
        with team_client.session_transaction() as session:
            session.update(
                authenticated=True,
                user_id=team_user_id,
                auth_version=1,
                is_admin=False,
                team_id=team_id,
                username="benchmark-team",
                auth_provider="LOCAL",
                csrf_token="benchmark-team",
            )
        service = {}
        try:
            for label, flag in (("off", "0"), ("on", "1")):
                durations, counts = [], []
                for _ in range(args.samples):
                    sample_started = time.perf_counter()
                    response = client.get(
                        "/search", query_string={"query": "Needle product", "in_stock_only": flag}
                    )
                    durations.append((time.perf_counter() - sample_started) * 1000)
                    if response.status_code != 200:
                        raise RuntimeError(f"service benchmark {label} returned {response.status_code}")
                    counts.append(len(response.get_json()["results"]))
                service[label] = {
                    "samples_ms": [round(value, 2) for value in durations],
                    "median_ms": round(statistics.median(durations), 2),
                    "result_counts": counts,
                }
            no_cas = client.get(
                "/search", query_string={"query": "No CAS needle", "in_stock_only": "1"}
            )
            if no_cas.status_code != 200:
                raise RuntimeError(f"no-CAS benchmark returned {no_cas.status_code}")
            service["on_no_cas_negative"] = {
                "result_count": len(no_cas.get_json()["results"]),
            }
            unicode_started = time.perf_counter()
            unicode_negative = client.get(
                "/search", query_string={"query": "Unicode negative", "in_stock_only": "1"}
            )
            if unicode_negative.status_code != 200:
                raise RuntimeError(f"Unicode slow-lane benchmark returned {unicode_negative.status_code}")
            service["on_unicode_slow_negative"] = {
                "elapsed_ms": round((time.perf_counter() - unicode_started) * 1000, 2),
                "candidate_count": args.products // 1000,
                "result_count": len(unicode_negative.get_json()["results"]),
            }
            team_durations, team_counts = [], []
            for _ in range(args.samples):
                sample_started = time.perf_counter()
                response = team_client.get(
                    "/search", query_string={"query": "Needle product", "in_stock_only": "1"}
                )
                team_durations.append((time.perf_counter() - sample_started) * 1000)
                if response.status_code != 200:
                    raise RuntimeError(f"non-admin team benchmark returned {response.status_code}")
                team_counts.append(len(response.get_json()["results"]))
            service["on_non_admin_team"] = {
                "samples_ms": [round(value, 2) for value in team_durations],
                "median_ms": round(statistics.median(team_durations), 2),
                "result_counts": team_counts,
            }
        finally:
            if old_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_database_url

        print(json.dumps({
            "synthetic_only_not_production_slo": True,
            "postgres_major": 14,
            "products": args.products,
            "active_stock": 10000,
            "historical_stock": 10000,
            "query_catalog_matches": args.products // 1000,
            "positive_catalog_matches": 90,
            "samples": args.samples,
            "setup_seconds": round(time.monotonic() - started, 2),
            "explain": plans,
            "service": service,
        }, ensure_ascii=False, indent=2))
    finally:
        if db_name:
            pg_temp_db.drop_temp_db(db_name)


if __name__ == "__main__":
    main()
