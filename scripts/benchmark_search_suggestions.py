"""1M synthetic products, disposable pgtest DB only. No production rows.

Run PYTHONPATH=.:tests python scripts/benchmark_search_suggestions.py.
Print JSON timings + EXPLAIN ANALYZE BUFFERS; redirect into LOCAL evidence.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from statistics import median
import time
from urllib.parse import urlparse
import uuid

import psycopg2
import pg_temp_db
import search_suggestions as suggest


def main():
    if urlparse(os.environ.get('DATABASE_URL','')).hostname not in ('localhost','127.0.0.1'):
        raise SystemExit('Explicit disposable localhost DATABASE_URL required')
    name, dsn = pg_temp_db.create_full_schema_temp_db()
    conn = psycopg2.connect(dsn); conn.autocommit = True
    root = Path(__file__).resolve().parents[1]
    try:
        start=time.monotonic()
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO products(name,code,cas,brand,size,ship,price)
                SELECT CASE WHEN n=999999 THEN 'Rare Ω needle reagent' ELSE 'Chemical standard '||n END,
                       'CAT-'||lpad(n::text,7,'0'),(n%1000)::text||'-00-0',
                       CASE WHEN n%1000=0 THEN 'Rare Brand' ELSE 'Common Brand' END,'1g','1','100'
                FROM generate_series(1,1000000) n""")
            cur.execute("INSERT INTO teams(name,permission_keys) VALUES ('Selective','{SEARCH,VIEW_NAME,VIEW_CODE,VIEW_CAS,SEARCH_BY_CAS}') RETURNING id")
            team=cur.fetchone()[0]
            cur.execute("INSERT INTO team_brands(team_id,brand) VALUES(%s,'Rare Brand')",(team,))
            for n in range(11):
                snapshot=str(uuid.uuid4())
                cur.execute("INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256) VALUES(%s,'IMPORT','benchmark',874,'')",(snapshot,))
                cur.execute("""INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,quantity,stock_note,brand_norm,code_norm,cas_norm,size_norm)
                    SELECT %s,'Chemical stock '||n,'STOCK-'||n,'50-00-0','Rare Brand','1g',7,'Kho A',
                    'rare brand','stock-'||n,'50-00-0','1g' FROM generate_series(1,874) n""",(snapshot,))
            cur.execute('UPDATE stock_state SET active_snapshot_id=%s,revision=1 WHERE singleton=true',(snapshot,))
            # Relevant production metadata supplied by user (no real rows).
            for filename in ('migration_007_products_code_upper_trim_index.sql','migration_008_check_cas_perf_indexes.sql','migration_010_search_trgm_indexes.sql'):
                pg_temp_db.apply_sql_file_statement_by_statement(cur,root/'sql'/filename)
            cur.execute('CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_products_admin_brand_id ON products(brand,id)')
            cur.execute('CREATE INDEX IF NOT EXISTS products_import_identity ON products(upper(trim(brand)),upper(trim(code)))')
            cur.execute('ANALYZE products; ANALYZE stock_items; ANALYZE team_brands; ANALYZE teams')
            cur.execute("SELECT pg_relation_size('products'),pg_indexes_size('products')")
            sizes=cur.fetchone()
        output={'setup_seconds':round(time.monotonic()-start,2),'product_rows':1000000,'active_stock':874,
                'historical_stock':8740,'heap_bytes':sizes[0],'index_bytes':sizes[1], 'cases':[]}
        fields=['name','code','cas']
        def run(query,is_admin):
            c=psycopg2.connect(dsn)
            try:
                start=time.perf_counter()
                data=suggest.suggest(c,query,grants={'VIEW_NAME','VIEW_CODE','VIEW_CAS','SEARCH_BY_CAS'},is_admin=is_admin,team_id=team)
                return {'ms':round((time.perf_counter()-start)*1000,2),'returned':len(data['suggestions']),'degraded':data['degraded']}
            finally: c.close()
        for query in ('CAT-1000000','CAT-0999','Chemical','needle','missingneedlexyz','50-00-0'):
            for is_admin in (True,False):
                samples=[run(query,is_admin) for _ in range(5)]
                case={'query':query,'scope':'admin' if is_admin else 'selective_0.1pct','samples':samples,
                      'median_ms':median(x['ms'] for x in samples),'plans':{}}
                # Explain each stage separately with a bounded diagnostic timeout.
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout='2s'")
                    for stage in ('exact','prefix','contains'):
                        sql,params=suggest.candidate_query(query,fields,stage,is_admin=is_admin,team_id=team)
                        try:
                            cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+sql,params)
                            case['plans'][stage]=cur.fetchone()[0]
                        except psycopg2.errors.QueryCanceled:
                            case['plans'][stage]={'timeout_ms':2000}
                    cur.execute('SET statement_timeout=0')
                output['cases'].append(case)
        with ThreadPoolExecutor(4) as pool:
            output['concurrent_4_readers']=list(pool.map(lambda n: run(('Chemical','needle','missingneedlexyz','CAT-1000000')[n%4],n%2==0),range(20)))
        print(json.dumps(output,ensure_ascii=False,indent=2))
    finally:
        conn.close(); pg_temp_db.drop_temp_db(name)


if __name__=='__main__': main()
