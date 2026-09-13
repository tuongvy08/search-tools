"""Disposable Phase 6D2 browser QA server; loopback and temporary DB only."""

from datetime import date, timedelta
import hashlib
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path
import uuid

from openpyxl import Workbook
import psycopg2
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from pg_temp_db import (  # noqa: E402
    apply_brand_master_and_currency_migrations,
    apply_dynamic_brand_currency_migration,
    create_full_schema_temp_db,
    drop_temp_db,
)


def main():
    dbname, dsn = create_full_schema_temp_db()
    stop_worker = threading.Event()
    with tempfile.TemporaryDirectory(prefix="phase6d2-browser-") as temp_dir:
        workbook_path = Path(temp_dir) / "phase6d2-full-snapshot.xlsx"
        wb = Workbook(); ws = wb.active; ws.title = "ton_kho"
        ws.append(["Name", "Code", "Cas", "Brand", "Size", "Giá tồn kho", "Số lượng tồn", "Hạn sử dụng"])
        ws.append(["Formaldehyde tồn 100 mL", "QA-STOCK-001", "50-00-0", "Brand A", "100 mL", 125000, 2, date.today()-timedelta(days=1)])
        ws.append(["Formaldehyde tồn 500 mL", "QA-STOCK-001", "50-00-0", "Brand A", "500 mL", 490000, 3, date.today()+timedelta(days=30)])
        ws.append(["Lựa chọn cùng CAS", "QA-ALT-001", "50-00-0", "Brand A", "1 L", None, 0, None])
        wb.save(workbook_path); wb.close()
        try:
            os.environ.update(
                DATABASE_URL=dsn, FLASK_SECRET_KEY="disposable-browser-phase6d2",
                DISABLE_IP_ALLOWLIST="1", ENABLE_LEGACY_PASSWORD_LOGIN="0",
                IMPORT_UPLOAD_DIR=temp_dir,
            )
            import search
            import stock_import_jobs

            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                apply_dynamic_brand_currency_migration(cur)
                cur.execute(
                    """INSERT INTO app_users(username,password_hash,is_admin,auth_provider,account_status)
                       VALUES ('qa-stock-admin',%s,true,'LOCAL','ACTIVE')""",
                    (generate_password_hash("qa-only"),),
                )
                cur.execute(
                    """INSERT INTO brand_master(name,normalized_name,currency_code)
                       VALUES ('Brand A','BRAND A','VND'),('Brand B','BRAND B','VND')"""
                )
                cur.execute(
                    """INSERT INTO products(name,code,cas,brand,source_brand,size,ship,price,note)
                       VALUES
                       ('Formaldehyde catalog','QA-STOCK-001','50-00-0','Brand A','Brand A','100 mL','1','1000','Giá catalog độc lập'),
                       ('Mặt hàng B cũ','QA-OLD-B','64-17-5','Brand B','Brand B','1 L','1','2000','Sẽ vắng ở snapshot mới')"""
                )
                cur.execute(
                    """INSERT INTO regulatory_rules(rule_type,rule_label,match_field,match_value,priority,is_active,note,status_id)
                       SELECT stable_key,label,'cas','50-00-0',priority,true,'Regulatory vẫn chặn dù có tồn',id
                       FROM regulatory_statuses WHERE stable_key='CAM_NHAP'"""
                )
                snapshot_id = str(uuid.uuid4())
                digest = hashlib.sha256(b"phase6d2-browser-initial").hexdigest()
                cur.execute(
                    """INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256)
                       VALUES (%s,'IMPORT','browser-seed',2,%s)""",
                    (snapshot_id, digest),
                )
                cur.execute(
                    """INSERT INTO stock_items
                       (snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,
                        brand_norm,code_norm,size_norm,cas_norm)
                       VALUES
                       (%s,'Tồn A hiện tại','QA-STOCK-001','50-00-0','Brand A','100 mL',120000,1,NULL,'brand a','qa-stock-001','100 ml','50-00-0'),
                       (%s,'Tồn B hiện tại','QA-OLD-B','64-17-5','Brand B','1 L',300000,4,NULL,'brand b','qa-old-b','1 l','64-17-5')""",
                    (snapshot_id, snapshot_id),
                )
                cur.execute(
                    "UPDATE stock_state SET active_snapshot_id=%s,revision=1,updated_at=now() WHERE singleton=TRUE",
                    (snapshot_id,),
                )

            def worker():
                while not stop_worker.wait(.2):
                    stock_import_jobs.run_once()

            worker_thread = threading.Thread(target=worker, daemon=True); worker_thread.start()

            def stop(*_args):
                stop_worker.set(); raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
            print(
                f"PHASE6D2_SMOKE http://127.0.0.1:5014 qa-stock-admin qa-only {workbook_path} DB={dbname}",
                flush=True,
            )
            search.app.run(host="127.0.0.1", port=5014, debug=False, use_reloader=False)
        finally:
            stop_worker.set()
            if "worker_thread" in locals():
                worker_thread.join(timeout=3)
            drop_temp_db(dbname)


if __name__ == "__main__":
    main()
