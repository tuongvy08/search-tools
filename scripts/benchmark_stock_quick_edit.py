"""Local-only 10k measurement, using the disposable PostgreSQL test fixture.

Run with PYTHONPATH=.:tests and an explicitly selected localhost DATABASE_URL.
Never points application writes at the configured database: the fixture creates
and drops its own randomized pgtest database.
"""
import json
import os
import tracemalloc
from statistics import median
from time import perf_counter
from urllib.parse import urlparse

from tests.test_stock_quick_edit import StockQuickEditTests
import stock_import_jobs as jobs
import stock_manual as manual


def main():
    if urlparse(os.environ.get("DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Explicit localhost disposable PostgreSQL URL required")
    fixture = StockQuickEditTests()
    fixture.setUpClass()
    try:
        fixture.setUp()
        first = fixture.add()
        with fixture.conn.cursor() as cur:
            cur.execute("""INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,
                stock_price_vnd,quantity,expiry_date,stock_note,brand_norm,code_norm,size_norm,cas_norm)
                SELECT %s,'Hóa chất thử nghiệm '||n,'BENCH-'||lpad(n::text,5,'0'),NULL,
                'Brand A','1g',125000,7,NULL,'Kho A — dữ liệu đo giả lập',
                'brand a','bench-'||lpad(n::text,5,'0'),'1g',NULL
                FROM generate_series(2,10000) n""", (first['snapshot_id'],))
            cur.execute("UPDATE stock_snapshots SET row_count=10000,content_sha256=%s WHERE id=%s",
                        (jobs.content_digest(fixture.rows()), first['snapshot_id']))
            cur.execute("SELECT pg_total_relation_size('stock_items')")
            bytes_before = cur.fetchone()[0]
        results = {}
        for name, args in (("list25", {}), ("contains_brand25", {"q":"BENCH-09","brand":"Brand A"}),
                           ("last_page25", {"page":"400"})):
            samples = []
            for _ in range(7):
                start = perf_counter()
                result = manual.browse(manual.filters(args))
                samples.append(round((perf_counter()-start)*1000, 2))
                assert len(result['rows']) <= 25
            results[name] = {"milliseconds":samples,"median_ms":median(samples),"total":result['total']}
        samples = []
        for n in range(3):
            context = manual.form_context(first['item_id'])
            preview = fixture.preview(dict(context['values'], stock_note=f'Kho B — lần {n}'), first['item_id'])
            start = perf_counter()
            first = fixture.save(preview)
            samples.append(round((perf_counter()-start)*1000, 2))
            assert len(fixture.rows()) == 10000
        with fixture.conn.cursor() as cur:
            cur.execute("SELECT pg_total_relation_size('stock_items')")
            bytes_after = cur.fetchone()[0]
        results['save_clone_and_digest_10000'] = {"milliseconds":samples,"median_ms":median(samples)}
        context = manual.form_context(first['item_id'])
        preview = fixture.preview(dict(context['values'], stock_note='Đo peak memory'), first['item_id'])
        tracemalloc.start()
        fixture.save(preview)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results['separate_save_memory'] = {"python_tracemalloc_peak_bytes":peak,
            "scope":"Python allocations only; excludes libpq/PostgreSQL/RSS. Digest retains sorted repr strings and joined buffer O(n)."}
        results['storage'] = {"stock_items_bytes_before":bytes_before,"bytes_after_3_edits":bytes_after,
                              "bytes_growth_per_edit":(bytes_after-bytes_before)//3}
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        fixture.tearDownClass()


if __name__ == '__main__':
    main()
