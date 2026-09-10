"""Disposable Phase 6D1 browser QA server; loopback and temporary DB only."""

import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

import psycopg2
from openpyxl import Workbook
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from pg_temp_db import create_full_schema_temp_db, drop_temp_db  # noqa: E402


def main():
    dbname, dsn = create_full_schema_temp_db()
    with tempfile.TemporaryDirectory(prefix="phase6d1-browser-") as temp_dir:
        fixture_path = Path(temp_dir) / "phase6d1-rules.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "quy_tac_code"
        ws.append(["Code", "Tình trạng quản lý", "Ghi chú quản lý"])
        ws.append(["QA-REG-001", "Theo dõi đặc biệt", "Hồ sơ giả lập cho kiểm thử trình duyệt"])
        wb.save(fixture_path)
        wb.close()

        try:
            os.environ.update(
                DATABASE_URL=dsn,
                FLASK_SECRET_KEY="disposable-browser-phase6d1",
                DISABLE_IP_ALLOWLIST="1",
                ENABLE_LEGACY_PASSWORD_LOGIN="0",
                IMPORT_UPLOAD_DIR=temp_dir,
            )
            import regulatory_import_jobs
            import search

            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app_users(username,password_hash,is_admin,auth_provider,account_status)
                       VALUES ('qa-admin',%s,true,'LOCAL','ACTIVE')""",
                    (generate_password_hash("qa-only"),),
                )
                cur.execute(
                    """INSERT INTO teams(name,lifecycle_status,permission_keys)
                       VALUES ('QA staff no compliance','ACTIVE',%s) RETURNING id""",
                    (["SEARCH", "FIND_CODE", "QUICK_QUOTE", "VIEW_NAME", "VIEW_CODE",
                      "VIEW_CAS", "VIEW_PRICE", "VIEW_NOTE"],),
                )
                staff_team_id = cur.fetchone()[0]
                cur.execute("INSERT INTO team_brands(team_id,brand) VALUES (%s,'TRC')", (staff_team_id,))
                cur.execute(
                    """INSERT INTO app_users
                           (username,password_hash,team_id,is_admin,auth_provider,account_status)
                       VALUES ('qa-staff',%s,%s,false,'LOCAL','ACTIVE')""",
                    (generate_password_hash("qa-only"), staff_team_id),
                )
                cur.execute(
                    "SELECT id,label,priority FROM regulatory_statuses WHERE stable_key='PHU_LUC_II'"
                )
                status_id, label, priority = cur.fetchone()
                cur.execute(
                    """INSERT INTO regulatory_rules
                       (rule_type,rule_label,match_field,match_value,priority,is_active,note,status_id)
                       VALUES
                       ('PHU_LUC_II',%s,'cas','50-00-0',%s,true,%s,%s),
                       ('PHU_LUC_II',%s,'code','QA-MULTILINE',%s,true,%s,%s)""",
                    (label, priority, "Ghi chú CAS dòng 1\nGhi chú CAS dòng 2", status_id,
                     label, priority, "Ghi chú mã sản phẩm", status_id),
                )
                cur.execute(
                    """INSERT INTO products(name,code,cas,brand,size,ship,price,note)
                       VALUES ('Sản phẩm kiểm tra nhiều dòng','QA-MULTILINE','50-00-0',
                               'TRC','1 g','1','100','Ghi chú sản phẩm')"""
                )

            stop_worker = threading.Event()

            def worker():
                while not stop_worker.wait(0.25):
                    regulatory_import_jobs.run_once()

            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()

            def stop(*_args):
                stop_worker.set()
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            print(
                f"PHASE6D1_SMOKE http://127.0.0.1:5013 qa-admin/qa-staff qa-only {fixture_path}",
                flush=True,
            )
            search.app.run(host="127.0.0.1", port=5013, debug=False, use_reloader=False)
        finally:
            stop_worker.set()
            if "worker_thread" in locals():
                worker_thread.join(timeout=3)
            drop_temp_db(dbname)


if __name__ == "__main__":
    main()
