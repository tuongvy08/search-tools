"""Test độc lập lượt 2 của verifier cho cổng API máy chỉ đọc: tập trung vào phần vừa đổi.

- Quyền PostgreSQL THEO CỘT (sql/machine_readonly_role.sql): chạy cổng bằng đúng vai đó, so sánh từng
  phản hồi với chạy bằng chủ sở hữu (khác biệt = truy vấn cần cột chưa cấp), kiểm kê quyền thật của vai.
- Nhật ký khi chạy Gunicorn thật (đúng cờ của unit systemd) kèm vai thật.
- scripts/machine_token.py (bộ kiểm đầu vào dùng chung với cổng).
- Giới hạn tần suất theo mô hình tiến trình thật của unit (2 worker).

Chạy: DATABASE_URL=... FLASK_SECRET_KEY=x PYTHONPATH=.:tests .venv/bin/python -m unittest \
        discover -s tests/independent -p "test_machine_api_*.py"
"""

import http.client
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlparse, urlunparse

import psycopg2

import machine_api_verifier_support as S
import machine_api
import pg_temp_db

ROOT = Path(__file__).resolve().parents[2]
V = "/machine/v1"
HAS_PG = pg_temp_db.probe_postgres_reachable()

FORBIDDEN_COLUMNS = {
    "products": {"price", "ship", "note"},
    "stock_items": {"stock_price_vnd", "stock_note"},
}


class RoleMixin:
    """Tạo vai PostgreSQL bằng ĐÚNG tệp sql/machine_readonly_role.sql trên database thử."""

    @classmethod
    def make_role(cls):
        cls.role = "machine_api_ro_r2" + os.urandom(3).hex()
        cls.password = "pw" + os.urandom(6).hex()
        cls.admin = psycopg2.connect(pg_temp_db.maintenance_dsn())
        cls.admin.autocommit = True
        sql = (ROOT / "sql/machine_readonly_role.sql").read_text(encoding="utf-8")
        sql = sql.replace("{{DB_NAME}}", cls.env.db_name).replace("{{PASSWORD}}", cls.password)
        sql = sql.replace("machine_api_ro", cls.role)
        cls.role_ok, cls.role_err = True, ""
        try:
            with cls.env.conn.cursor() as cur:
                cur.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname=current_user")
                if not cur.fetchone()[0]:
                    cls.role_ok, cls.role_err = False, "no CREATEROLE"
                    return
                cur.execute(sql)
        except Exception as exc:  # noqa: BLE001
            cls.role_ok, cls.role_err = False, repr(exc)

    @classmethod
    def drop_role(cls):
        try:
            if cls.role_ok:
                with cls.env.conn.cursor() as cur:
                    cur.execute(f'DROP OWNED BY "{cls.role}"')
        except Exception:
            pass
        try:
            with cls.admin.cursor() as cur:
                cur.execute(f'DROP ROLE IF EXISTS "{cls.role}"')
        finally:
            cls.admin.close()

    @classmethod
    def role_dsn(cls):
        parsed = urlparse(cls.env.dsn)
        netloc = f"{cls.role}:{cls.password}@{parsed.hostname}:{parsed.port or 5432}"
        return urlunparse(parsed._replace(netloc=netloc))


# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R2_ColumnGrantRole(RoleMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.make_role()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.drop_role()
        finally:
            cls.env.close()

    def setUp(self):
        if not self.role_ok:
            self.skipTest("không tạo được vai: " + self.role_err)

    def role_conn(self, rw=False):
        opts = "-c default_transaction_read_only=off" if rw else None
        conn = psycopg2.connect(self.role_dsn(), options=opts)
        conn.autocommit = True
        return conn

    # --- kiểm kê quyền thật ------------------------------------------------
    def test_inventory_of_effective_privileges(self):
        with self.env.conn.cursor() as cur:
            cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication, rolcanlogin, rolconnlimit "
                        "FROM pg_roles WHERE rolname=%s", (self.role,))
            sup, crole, cdb, bypass, repl, login, limit = cur.fetchone()
            self.assertEqual((sup, crole, cdb, bypass, repl, login), (False, False, False, False, False, True))
            cur.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s", (self.role,))
            self.assertEqual(cur.fetchone()[0], 0, "vai không được là thành viên vai nào (vd pg_read_all_data)")
            # mọi cột của mọi bảng/view public mà vai có thể SELECT / ghi
            cur.execute("""SELECT c.relname, a.attname, c.relkind,
                                  has_column_privilege(%s, c.oid, a.attnum, 'SELECT'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'INSERT'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'UPDATE'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'REFERENCES')
                           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                           WHERE n.nspname='public' AND c.relkind IN ('r','v','m','p','f')""",
                        (self.role,) * 4)
            rows = cur.fetchall()
            readable = {}
            for table, column, kind, sel, ins, upd, ref in rows:
                self.assertFalse(ins or upd or ref, f"vai có quyền ghi/tham chiếu cột {table}.{column}")
                if sel:
                    readable.setdefault(table, set()).add(column)
            expected_whole = {"team_brands", "brand_compliance_settings", "regulatory_rules", "regulatory_statuses", "stock_state"}
            self.assertEqual(set(readable), expected_whole | {"products", "teams", "stock_items"}, sorted(readable))
            for table, forbidden in FORBIDDEN_COLUMNS.items():
                self.assertFalse(readable[table] & forbidden, (table, readable[table] & forbidden))
            self.assertEqual(readable["teams"], {"id", "permission_keys", "lifecycle_status"})
            # bảng nhạy cảm bất kỳ không có cột nào đọc được
            for t in ("app_users", "login_audit_events", "quote_templates", "exchange_rates"):
                self.assertNotIn(t, readable)
            # quyền cấp bảng/dãy/hàm/schema/database
            cur.execute("""SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE n.nspname='public' AND c.relkind IN ('r','v','m','p','S')
                             AND (has_table_privilege(%s, c.oid, 'INSERT') OR has_table_privilege(%s, c.oid, 'UPDATE')
                                  OR has_table_privilege(%s, c.oid, 'DELETE') OR has_table_privilege(%s, c.oid, 'TRUNCATE')
                                  OR has_table_privilege(%s, c.oid, 'TRIGGER') OR has_table_privilege(%s, c.oid, 'REFERENCES')
                                  OR (c.relkind='S' AND (has_sequence_privilege(%s, c.oid, 'USAGE')
                                                          OR has_sequence_privilege(%s, c.oid, 'UPDATE'))))""",
                        (self.role,) * 8)
            self.assertEqual(cur.fetchall(), [])
            cur.execute("SELECT has_schema_privilege(%s,'public','CREATE'), has_database_privilege(%s, current_database(), 'CREATE')",
                        (self.role, self.role))
            self.assertEqual(cur.fetchone(), (False, False))
            # hàm tự định nghĩa SECURITY DEFINER trong public có thể là cửa hậu đọc cột đã bị chặn
            cur.execute("""SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                           WHERE n.nspname='public' AND p.prosecdef AND has_function_privilege(%s, p.oid, 'EXECUTE')""", (self.role,))
            self.assertEqual(cur.fetchall(), [])
            # bảng có row-level security chặn/lọc?
            cur.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND relrowsecurity")
            self.rls_tables = [r[0] for r in cur.fetchall()]

    # --- thử đọc thẳng các cột bị cấm bằng mọi cách --------------------------
    def test_forbidden_columns_unreadable_in_every_form(self):
        conn = self.role_conn()
        cur = conn.cursor()
        attempts = [
            "SELECT price FROM products", "SELECT ship FROM products", "SELECT note FROM products",
            "SELECT * FROM products", "SELECT p FROM products p", "SELECT to_jsonb(p) FROM products p",
            "SELECT row_to_json(p) FROM products p", "SELECT count(*) FROM products WHERE price IS NOT NULL",
            "SELECT 1 FROM products WHERE note ILIKE '%NOI-BO%'", "SELECT length(note) FROM products",
            "SELECT stock_price_vnd FROM stock_items", "SELECT stock_note FROM stock_items", "SELECT * FROM stock_items",
            "SELECT to_jsonb(i) FROM stock_items i", "SELECT 1 FROM stock_items WHERE stock_price_vnd = 424242",
            "SELECT * FROM teams", "SELECT name FROM teams", "SELECT * FROM app_users", "SELECT * FROM login_audit_events",
            "SELECT * FROM pg_statistic", "SELECT * FROM pg_authid",
            "SELECT products.* FROM products", "SELECT count(products) FROM products",
            "WITH x AS (SELECT price FROM products) SELECT * FROM x",
            "SELECT (SELECT price FROM products LIMIT 1)",
            "TABLE products", "COPY products TO STDOUT", "COPY (SELECT price FROM products) TO STDOUT",
            "SELECT pg_read_file('/etc/passwd')", "SELECT * FROM pg_ls_dir('.')",
        ]
        for stmt in attempts:
            with self.assertRaises(psycopg2.Error, msg=stmt) as ctx:
                cur.execute(stmt)
                if cur.description:
                    cur.fetchall()
            self.assertIn(type(ctx.exception).__name__, ("InsufficientPrivilege",), (stmt, ctx.exception))
        # thống kê cột (pg_stats) chỉ lộ cột vai được SELECT
        self.env.conn.cursor().execute("ANALYZE products")
        self.env.conn.cursor().execute("ANALYZE stock_items")
        cur.execute("SELECT tablename, attname FROM pg_stats WHERE schemaname='public' AND tablename IN ('products','stock_items')")
        leaked = {(t, a) for t, a in cur.fetchall() if a in FORBIDDEN_COLUMNS.get(t, set())}
        self.assertEqual(leaked, set(), "pg_stats lộ thống kê cột bị cấm")
        cur.execute("SELECT attname FROM pg_stats WHERE tablename='products'")
        self.assertTrue({a for (a,) in cur.fetchall()} <= {"id", "name", "code", "cas", "brand", "size", "manual_compliance",
                                                           "manual_compliance_note", "manual_compliance_status_id"})
        conn.close()

    def test_write_paths_blocked_even_with_read_write_session(self):
        conn = self.role_conn(rw=True)
        cur = conn.cursor()
        for stmt in ("INSERT INTO teams(name, permission_keys) VALUES ('z','{}')", "UPDATE products SET name='x'",
                     "UPDATE stock_state SET revision=revision+1", "DELETE FROM team_brands", "TRUNCATE stock_items",
                     "CREATE TABLE zz(a int)", "CREATE TEMP TABLE zz(a int)" if False else "CREATE SCHEMA zz",
                     "ALTER TABLE products ADD COLUMN zz int", "DROP TABLE team_brands", "GRANT SELECT ON products TO PUBLIC",
                     "ALTER ROLE CURRENT_USER SUPERUSER", "CREATE FUNCTION zz() RETURNS int LANGUAGE sql AS 'select 1'",
                     "SELECT nextval('teams_id_seq')", "SELECT setval('teams_id_seq', 1)", "LOCK TABLE products IN ACCESS EXCLUSIVE MODE",
                     "SELECT pg_reload_conf()",
                     "SELECT * FROM products FOR UPDATE", "SELECT id FROM teams FOR UPDATE", "SELECT code FROM stock_items FOR SHARE"):
            with self.assertRaises(psycopg2.Error, msg=stmt):
                cur.execute(stmt)
        cur.execute("SELECT current_setting('default_transaction_read_only'), current_setting('statement_timeout')")
        ro, timeout = cur.fetchone()
        # cấu hình vai: read-only bật mặc định, nhưng chính phiên này đã tắt => chỉ quyền (GRANT) mới là chốt chặn
        self.assertEqual(ro, "off")
        self.assertEqual(timeout, "3s")
        conn.close()

    # --- chạy cổng bằng vai thật, so sánh từng phản hồi với chạy bằng chủ sở hữu ---
    def test_gateway_responses_identical_under_role_vs_owner_across_matrix(self):
        env = self.env
        all_perms = list(S.ALL_PERMS)
        teams = {"full": env.team_full, "limited": env.team_limited}
        teams["only_search"] = env.new_team("onlysearch", ["SEARCH"], ["ALPHA", "BETA"])
        teams["no_brands"] = env.new_team("nobrands", all_perms, [])
        teams["empty_perms"] = env.new_team("emptyperms", [], ["ALPHA"])
        teams["priority_only"] = env.new_team("priorityonly", all_perms, ["PRIORITY"])
        for perm in all_perms:
            teams["minus_" + perm] = env.new_team("minus" + perm.lower(), [p for p in all_perms if p != perm], ["ALPHA", "BETA", "PRIORITY"])
        for perm in ("SEARCH", "CHECK_LICENSE", "SEARCH_BY_CAS", "VIEW_CAS", "VIEW_COMPLIANCE"):
            teams["only_" + perm] = env.new_team("only" + perm.lower(), [perm], ["ALPHA", "BETA", "PRIORITY"])
        teams["search_cas_only"] = env.new_team("searchcas", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CAS"], ["ALPHA"])
        teams["search_cas_comp"] = env.new_team("searchcascomp", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CAS", "VIEW_COMPLIANCE", "CHECK_LICENSE", "VIEW_CODE"], ["ALPHA", "PRIORITY"])
        env.entries = []
        tokens = {}
        for name, team in teams.items():
            tokens[name] = env.add_token(name.lower().replace("_", "-"), team, rate_per_minute=600,
                                         stock_detail="quantity" if name in ("full", "minus_VIEW_CODE", "only_SEARCH") else "status")
        queries = ["ethanol", "Ethanol absolute", "ETH-001", "eth-002", "MET-001", "methanol", "Override", "OVR-001", "OVR-002", "64-17-5",
                   "64-17", "67-56-1", "1333-74-0", "CAS-A", "SC-1", "SC-HIDDEN", "SEC-001", "Secret brand", "GAMMA", "100%", "%%%", "___",
                   "%a%", "a_b", "Axit béo", "Axit beo", "axit", "BLK-0", "Bulk sample", "x" * 100, "ab", "a" * 101, "'; DROP TABLE products;--",
                   "' OR 1=1--", "NOC-001", "No cas", "50-00-0", "108-88-3", "10-10-1", "56-23-5", "75-05-8", "79-01-6", "UNK-001",
                   "7664-93-9", "7647-01-0", "71-43-2", "STK-ONLY", "OLD-ONLY", "\\", "ANY\\", "tolu", "TOL-001", "DCM-001", "Dichloro",
                   "ß", "İstanbul", "ǅ", "日本語", "\U0001F600\U0001F600\U0001F600", "a b c", " ethanol ", "ETHANOL", "NOI-BO-XYZ-777", "987654321"]
        cases = [(V + "/products", {"q": q}) for q in queries]
        cases += [(V + "/products", {"q": "ethanol", "limit": lim}) for lim in ("1", "5", "10", "11", "0", "-1", "abc", "1e9")]
        cases += [(V + "/products", {"q": "BLK"}), (V + "/products", {"q": "ethanol", "fields": "price"})]
        cases += [(V + "/compliance", {"cas": c}) for c in (
            "64-17-5", "50-00-0", "7664-93-9", "7647-01-0", "67-56-1", "108-88-3", "10-10-1", "56-23-5", "75-05-8", "79-01-6",
            "71-43-2", "75-09-2", "999-99-9", "bad", "", " 64-17-5 ", "64-17-5\n", "64-17-05")]
        cases += [(V + "/stock", {"code": c}) for c in (
            "MET-001", "met-001", "ETH-001", "ETH-002", "ETH-003", "TOL-001", "ACD-001", "ACD-002", "FOR-001", "OVR-001", "STK-ONLY",
            "OLD-ONLY", "SC-1", "NONE-ZZ", " MET-001 ", "x" * 100, "x" * 101, "%", "_")]
        cases += [(V + "/stock", {"cas": c}) for c in ("1333-74-0", "64-17-5", "67-56-1", "50-00-0", "71-43-2", "bad", "1333-74-0 ")]
        cases += [(V + "/stock", {}), (V + "/stock", {"code": "MET-001", "cas": "64-17-5"})]

        def run(dsn):
            with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": dsn, "DATABASE_URL": env.dsn, "MACHINE_API_TOKENS_FILE": env.token_path}):
                with mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0):
                    machine_api.token_store.reset()
                    machine_api.rate_limiter.reset()
                    client = machine_api.app.test_client()
                    out = {}
                    for name, token in tokens.items():
                        for path, query in cases:
                            r = client.get(path, headers={"Authorization": "Bearer " + token}, query_string=query)
                            out[(name, path, json.dumps(query, sort_keys=True))] = (r.status_code, r.get_data(as_text=True))
                    return out
        owner = run(env.dsn)
        role = run(self.role_dsn())
        self.assertEqual(owner.keys(), role.keys())
        statuses = {}
        diffs = []
        for key in owner:
            statuses[owner[key][0]] = statuses.get(owner[key][0], 0) + 1
            if owner[key] != role[key]:
                diffs.append((key, owner[key][0], role[key][0], role[key][1][:160]))
        self.assertEqual(diffs[:5], [], f"{len(diffs)} phản hồi khác nhau giữa chủ sở hữu và vai chỉ-đọc")
        self.assertGreater(statuses.get(200, 0), 1500, statuses)
        self.assertNotIn(500, statuses)
        self.assertNotIn(503, statuses, "503 dưới chủ sở hữu: dữ liệu thử có vấn đề")
        # không phản hồi nào (dù vai thật) chứa canary
        for key, (status, body) in role.items():
            for canary in S.CANARIES:
                if canary in body:
                    # chỉ cho phép khi chính người gọi gửi canary trong q/code và gateway phản chiếu lại
                    echoed = key[2]
                    self.assertIn(canary, echoed, (key, body[:200]))
        self.__class__.matrix_size = len(owner)
        self.__class__.matrix_statuses = statuses


# ======================================================================
def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def unit_flags(port):
    unit = (ROOT / "deploy/search-tools-machine-api.service").read_text(encoding="utf-8")
    match = re.search(r"ExecStart=(.*?)(?:\n[A-Z][A-Za-z]+=|\Z)", unit, re.S)
    cmd = match.group(1).replace("\\\n", " ").split()
    flags = cmd[1:]
    flags[flags.index("--bind") + 1] = f"127.0.0.1:{port}"
    return flags


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R2_RealGunicornUnderRole(RoleMixin, unittest.TestCase):
    """Gunicorn thật (đúng cờ của unit) + vai PostgreSQL thật + tải/ca lạ; quét toàn bộ stdout+stderr."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.make_role()
        cls.port = free_port()
        cls.flags = unit_flags(cls.port)
        cls.out = tempfile.TemporaryFile("w+b")
        cls.use_role = cls.role_ok
        dsn = cls.role_dsn() if cls.role_ok else cls.env.dsn
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MACHINE_DATABASE_URL")}
        env.update(MACHINE_DATABASE_URL=dsn, MACHINE_API_TOKENS_FILE=cls.env.token_path, PYTHONPATH=str(ROOT),
                   PYTHONUNBUFFERED="1")
        env.pop("FLASK_SECRET_KEY", None)  # cổng không được cần khóa phiên của ứng dụng nhân viên
        cls.proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *cls.flags], cwd=str(ROOT), env=env,
                                    stdout=cls.out, stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                c.request("GET", V + "/health")
                if c.getresponse().status == 200:
                    return
            except Exception:
                time.sleep(0.25)
        cls.tearDownClass()
        raise RuntimeError("gunicorn không khởi động")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(10)
        except Exception:
            cls.proc.kill()
        try:
            cls.drop_role()
        finally:
            cls.env.close()

    # --- tiện ích ------------------------------------------------------------
    def raw(self, request_bytes, limit=200000):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=8)
        s.sendall(request_bytes)
        data = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > limit:
                    break
        except Exception:
            pass
        s.close()
        return data

    def hit(self, path, token="full", method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + self.env.tokens.get(token, token)
        c.request(method, path, headers=h)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    def log_text(self):
        time.sleep(0.7)
        self.out.seek(0)
        return self.out.read().decode("utf-8", "replace")

    def audit_docs(self, text):
        docs = []
        for line in text.splitlines():
            if '"token"' in line and '"endpoint"' in line and "{" in line:
                docs.append(json.loads(line[line.index("{"):]))
        return docs

    # --- kiểm tra ---------------------------------------------------------------
    def test_role_is_really_used_and_everything_works(self):
        self.assertTrue(self.role_ok, self.role_err)
        for path in ("/products?q=ethanol", "/products?q=OVR-001", "/products?q=CAS-A", "/compliance?cas=64-17-5",
                     "/stock?code=MET-001", "/stock?cas=1333-74-0", "/products?q=%25%25%25", "/products?q=Axit%20b%C3%A9o"):
            status, body = self.hit(V + path, token="quantity")
            self.assertEqual(status, 200, (path, body[:200]))
        status, body = self.hit(V + "/products?q=ethanol")
        data = json.loads(body)
        self.assertEqual(sorted(r["code"] for r in data["results"]), ["ETH-001", "ETH-002", "MET-001"])  # 'methanol' chứa 'ethanol'

    def test_one_audit_line_per_data_call_and_no_secret_or_query_in_whole_log(self):
        s1, s2, s3 = (f"zqxs{os.urandom(3).hex()}" for _ in range(3))
        sentinel_hdr, sentinel_cookie, sentinel_ua, sentinel_host = ("zqxh" + os.urandom(3).hex(), "zqxc" + os.urandom(3).hex(),
                                                                     "zqxu" + os.urandom(3).hex(), "zqxhost" + os.urandom(3).hex())
        before = self.log_text()
        calls = [
            (f"/products?q={s1}", "full", 200), (f"/products?q={s1}&limit=3&extra={s2}", "full", 200),
            (f"/products?q={s1}", None, 401), (f"/products?q={s1}", "stm_wrong_" + s3, 401),
            (f"/compliance?cas=64-17-5&zz={s2}", "full", 200), ("/compliance?cas=71-43-2", "full", 200),
            ("/compliance?cas=bad" + s1, "full", 400), (f"/stock?code={s1}", "full", 200), ("/stock?cas=71-43-2", "full", 200),
            ("/stock", "full", 400), (f"/products?q={s1}&q={s2}", "full", 400), ("/products?q=ab", "full", 400),
            (f"/products?q={s1}", "limited", 200), ("/products?q=" + quote("Axit béo"), "full", 200),
            ("/products?q=" + quote("\u0000x" + s1), "full", 400),
            (f"/products?q={s1}&limit=abc", "full", 400), ("/products?q=" + "a" * 500, "full", 400),
        ]
        for path, token, status in calls:
            got, _ = self.hit(V + path, token=token, headers={"X-Api-Key": sentinel_hdr, "Cookie": "sid=" + sentinel_cookie,
                                                              "User-Agent": sentinel_ua})
            self.assertEqual(got, status, (path, got))
        # HEAD (xác thực như GET), OPTIONS
        self.assertEqual(self.hit(V + f"/products?q={s1}", method="HEAD")[0], 200)
        self.assertEqual(self.hit(V + f"/products?q={s1}", method="OPTIONS", token=None)[0], 401)
        new = self.log_text()[len(before):]
        docs = self.audit_docs(new)
        self.assertEqual(len(docs), len(calls) + 2, (len(docs), new[-2000:]))
        statuses = [d["status"] for d in docs]
        self.assertEqual(statuses, [c[2] for c in calls] + [200, 401])
        for d in docs:
            self.assertEqual(set(d), {"ts", "token", "endpoint", "status", "results", "ip", "ms"})
            self.assertIsInstance(d["results"], int)
            self.assertRegex(d["ip"], r"^127\.0\.0\.1$|^[0-9a-f:.]+$")
        self.assertEqual({d["token"] for d in docs}, {"t-full", "t-limited", "anonymous"})
        # quét toàn bộ log (cả phần cũ)
        text = self.log_text()
        tokens = [self.env.tokens["full"], self.env.tokens["limited"], self.env.tokens["quantity"],
                  "stm_wrong_" + s3]
        needles = [s1, s2, s3, sentinel_hdr, sentinel_cookie, sentinel_ua, "71-43-2", "64-17-5", "Authorization", "Bearer",
                   "987654321", "5550001", "NOI-BO", "GHICHU", "424242", "Axit", "b%C3%A9o"]
        for t in tokens:
            needles += [t, machine_api.hash_token(t)]
        for needle in needles:
            self.assertNotIn(needle, text, needle)

    def test_malformed_and_raw_utf8_requests_do_not_leak_query_text_into_log(self):
        """Yêu cầu hỏng mà Gunicorn từ chối TRƯỚC khi tới ứng dụng: log lỗi của Gunicorn có ghi dòng yêu cầu không?"""
        sentinel = "zqxraw" + os.urandom(3).hex()
        tok = self.env.tokens["full"]
        cases = {
            "raw_utf8_query": ("GET /machine/v1/products?q=" + sentinel + "éo HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + tok + "\r\nConnection: close\r\n\r\n").encode(),
            "space_in_uri": ("GET /machine/v1/products?q=" + sentinel + " b HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + tok + "\r\n\r\n").encode(),
            "toolong_line": ("GET /machine/v1/products?q=" + sentinel + "a" * 5000 + " HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").encode(),
            "bad_version": ("GET /machine/v1/products?q=" + sentinel + " HTTP/9.9\r\nHost: x\r\nConnection: close\r\n\r\n").encode(),
            "bad_method": ("G@T /machine/v1/products?q=" + sentinel + " HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").encode(),
            "header_with_token_bad_char": ("GET /machine/v1/products?q=abc HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + tok + "\x01\r\nConnection: close\r\n\r\n").encode(),
        }
        results = {}
        for name, payload in cases.items():
            data = self.raw(payload)
            results[name] = data.split(b"\r\n", 1)[0].decode("latin-1")
        text = self.log_text()
        leaks = [n for n, payload in cases.items() if False]
        self.__class__.malformed_results = results
        self.assertNotIn(tok, text, "thẻ lọt vào log Gunicorn khi gửi yêu cầu hỏng")
        self.__class__.malformed_leaks_sentinel = sentinel in text
        self.assertNotIn(sentinel, text, f"log Gunicorn ghi nguyên yêu cầu hỏng chứa từ khóa: {results}")

    def test_parallel_flood_under_role_connection_limit(self):
        """Role có CONNECTION LIMIT 10; 2 worker x 4 thread: 60 lời gọi song song không được ra 503/500."""
        self.env.add_token("flood", self.env.team_full, rate_per_minute=600)
        time.sleep(2.2)
        before = self.log_text()

        def one(i):
            return self.hit(V + f"/products?q=ethanol&limit={1 + i % 10}", token="flood")[0]
        with ThreadPoolExecutor(30) as pool:
            statuses = list(pool.map(one, range(90)))
        counts = {s: statuses.count(s) for s in set(statuses)}
        self.assertEqual(set(counts), {200}, counts)
        new = self.log_text()[len(before):]
        docs = self.audit_docs(new)  # ném lỗi nếu dòng log bị xen kẽ/hỏng JSON
        self.assertEqual(len(docs), 90, "mỗi lời gọi song song đúng 1 dòng, không mất/xen kẽ")
        self.assertEqual({d["token"] for d in docs}, {"t-flood"})

    def test_whole_gunicorn_log_has_no_database_secrets(self):
        self.hit(V + "/products?q=ethanol")
        text = self.log_text()
        for needle in (self.password, "postgresql://", "MACHINE_DATABASE_URL", self.role + ":", "password="):
            self.assertNotIn(needle, text, needle)

    def test_rate_limit_with_real_two_worker_process_model(self):
        """V8: thẻ rate_per_minute=6, gửi 30 lời gọi tuần tự tới Gunicorn thật (2 worker). Đo số lần được 200."""
        self.env.add_token("slow", self.env.team_full, rate_per_minute=6)
        time.sleep(2.2)
        results = [self.hit(V + "/products?q=ethanol", token="slow") for _ in range(30)]
        ok = sum(1 for s, _ in results if s == 200)
        limited = [s for s, _ in results if s == 429]
        self.__class__.rate_ok_count = ok
        self.assertGreater(len(limited), 0, "không có 429 nào")
        # Hợp đồng: vượt rate_per_minute của thẻ -> 429. Mô hình 2 worker cho phép tối đa 2x (đã tài liệu hóa trong PLAN).
        self.__class__.rate_effective_ratio = ok / 6.0
        self.assertLessEqual(ok, 12, ok)
        # thẻ khác không bị ảnh hưởng
        self.assertEqual(self.hit(V + "/products?q=ethanol", token="full")[0], 200)

    def test_valid_requests_after_all_abuse_still_served(self):
        self.assertEqual(self.hit(V + "/products?q=ethanol", token="quantity")[0], 200)
        self.assertEqual(self.hit(V + "/health", token=None)[0], 200)


# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R2_GunicornBadDsn(unittest.TestCase):
    """Gunicorn thật với DSN hỏng/không tới được: 503 chung, đúng 1 dòng truy vết, log không có thẻ/từ khóa/mật khẩu."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def run_with(self, dsn):
        port = free_port()
        out = tempfile.TemporaryFile("w+b")
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MACHINE_DATABASE_URL", "FLASK_SECRET_KEY")}
        env.update(MACHINE_DATABASE_URL=dsn, MACHINE_API_TOKENS_FILE=self.env.token_path, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *unit_flags(port)], cwd=str(ROOT), env=env,
                                stdout=out, stderr=subprocess.STDOUT)
        try:
            for _ in range(80):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    c.request("GET", V + "/health")
                    if c.getresponse().status == 200:
                        break
                except Exception:
                    time.sleep(0.25)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", V + "/products?q=zqxsecretq", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
            r = c.getresponse()
            body = r.read().decode()
            c.request("GET", V + "/health")
            health = c.getresponse()
            health.read()
            time.sleep(0.8)
        finally:
            proc.terminate()
            proc.wait(10)
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
        out.close()
        return r.status, body, health.status, text

    def test_unreachable_or_wrong_password_db_is_clean_503_with_one_audit_line(self):
        for label, dsn in (("closed_port", "postgresql://zqxuser:ZQXPASSA@127.0.0.1:1/zqxdb"),
                           ("wrong_password", self.env.dsn.replace("searchlocal:searchlocal", "searchlocal:ZQXPASSB"))):
            status, body, health, text = self.run_with(dsn)
            self.assertEqual((status, health), (503, 200), label)
            for needle in ("Traceback", "postgresql://", "ZQXPASS", "zqxdb", "/Volumes"):
                self.assertNotIn(needle, body, label)
            docs = [json.loads(l[l.index("{"):]) for l in text.splitlines() if '"endpoint"' in l and "{" in l]
            self.assertEqual([d["status"] for d in docs], [503], (label, text[-800:]))
            for needle in ("zqxsecretq", self.env.tokens["full"], machine_api.hash_token(self.env.tokens["full"]), "ZQXPASSA", "ZQXPASSB"):
                self.assertNotIn(needle, text, (label, needle))

    def test_observation_malformed_dsn_password_fragment_reaches_log(self):
        status, body, health, text = self.run_with("postgresql://zqxuser:ZQX@PASSC@127.0.0.1:5432/zqxdb")
        self.assertEqual(status, 503)
        self.assertNotIn("PASSC", body)
        self.__class__.dsn_fragment_in_log = "PASSC" in text


# ======================================================================
def run_cli(*args, file_path=None, env_extra=None, cwd=None, clear_env=False):
    env = {"PATH": os.environ.get("PATH", "")} if clear_env else dict(os.environ)
    env.pop("FLASK_SECRET_KEY", None)
    env.pop("DATABASE_URL", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/machine_token.py"), *args], capture_output=True, text=True,
                          env=env, cwd=cwd or tempfile.gettempdir(), timeout=60)
    return proc


class R2_TokenCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mtoken-r2-")
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "tokens.json")

    def new(self, *extra, ident="chatbot-khach", team="7", clear_env=False):
        return run_cli("new", "--file", self.path, "--id", ident, "--team-id", team, *extra, clear_env=clear_env)

    def listing(self):
        return sorted(os.listdir(self.tmp.name))

    def test_new_prints_token_once_file_has_only_hash_and_cli_runs_without_app_env(self):
        proc = self.new(clear_env=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tokens = re.findall(r"stm_[A-Za-z0-9_-]{20,}", proc.stdout + proc.stderr)
        self.assertEqual(len(tokens), 1, proc.stdout)
        token = tokens[0]
        self.assertGreaterEqual(len(token) - 4, 43, "thẻ phải có >= 256 bit (43 ký tự base64url)")
        self.assertEqual((proc.stdout + proc.stderr).count(token), 1)
        content = Path(self.path).read_text(encoding="utf-8")
        self.assertNotIn(token, content)
        self.assertIn(machine_api.hash_token(token), content)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        self.assertEqual(self.listing(), ["tokens.json"], "không được để lại tệp tạm")
        entries = machine_api.load_token_entries(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["id"], entries[0]["team_id"], entries[0]["stock_detail"]), ("chatbot-khach", 7, "status"))
        # `list` không in thẻ hay băm
        listed = run_cli("list", "--file", self.path)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        for needle in (token, machine_api.hash_token(token), "sha256"):
            self.assertNotIn(needle, listed.stdout + listed.stderr)
        # hai lần tạo cho hai thẻ khác nhau
        second = self.new(ident="bot-2")
        token2 = re.findall(r"stm_[A-Za-z0-9_-]{20,}", second.stdout)[0]
        self.assertNotEqual(token, token2)
        self.assertEqual(len(machine_api.load_token_entries(self.path)), 2)

    def test_invalid_inputs_are_rejected_with_no_token_no_file_change(self):
        self.assertEqual(self.new().returncode, 0)
        before = Path(self.path).read_bytes()
        bad = [
            dict(ident="Bad-Id"), dict(ident="bad id"), dict(ident="a"), dict(ident="-abc"), dict(ident="x" * 42), dict(ident=""),
            dict(ident="abc\n"), dict(ident="abc\nfake"), dict(ident="chatbot-khach"), dict(ident="ok-id", team="0"),
            dict(ident="ok-id", team="-1"), dict(ident="ok-id", team="abc"), dict(ident="ok-id", team="1e3"),
            dict(ident="ok-id", extra=("--rate-per-minute", "0")), dict(ident="ok-id", extra=("--rate-per-minute", "601")),
            dict(ident="ok-id", extra=("--rate-per-minute", "-5")), dict(ident="ok-id", extra=("--allowed-ip", "999.1.1.1")),
            dict(ident="ok-id", extra=("--allowed-ip", "")), dict(ident="ok-id", extra=("--allowed-ip", "10.0.0.0/33")),
            dict(ident="ok-id", extra=("--allowed-ip", "evil.example")), dict(ident="ok-id", extra=("--stock-detail", "all")),
            dict(ident="ok-id", extra=("--days", "abc")), dict(ident="ok-id", extra=("--days", "99999999999")),
        ]
        for case in bad:
            extra = case.get("extra", ())
            proc = self.new(*extra, ident=case["ident"], team=case.get("team", "7"))
            self.assertNotEqual(proc.returncode, 0, (case, proc.stdout, proc.stderr))
            self.assertNotRegex(proc.stdout + proc.stderr, r"stm_[A-Za-z0-9_-]{20,}", case)
            self.assertEqual(Path(self.path).read_bytes(), before, case)
            self.assertEqual(self.listing(), ["tokens.json"], case)

    def test_every_entry_created_is_accepted_by_gateway_validator(self):
        combos = [("a1", "1", ()), ("b2", "99999", ("--stock-detail", "quantity")), ("c3", "5", ("--rate-per-minute", "600")),
                  ("d4", "5", ("--rate-per-minute", "1", "--allowed-ip", "203.0.113.0/24", "--allowed-ip", "2001:db8::/32")),
                  ("e5", "5", ("--allowed-ip", "127.0.0.1")), ("f6", "5", ("--days", "1")), ("g7", "5", ("--days", "36500"))]
        for ident, team, extra in combos:
            proc = self.new(*extra, ident=ident, team=team)
            self.assertEqual(proc.returncode, 0, (ident, proc.stderr))
        entries = machine_api.load_token_entries(self.path)
        self.assertEqual(len(entries), len(combos))

    def test_trailing_newline_id_bypasses_validator(self):
        """Quan sát: _ID_RE dùng '$' nên 'abc\\n' vẫn khớp. Kiểm tra bằng cả validator của cổng và công cụ."""
        entry = {"id": "abc\n", "sha256": "0" * 64, "team_id": 1, "expires_at": "2099-01-01T00:00:00+00:00"}
        try:
            machine_api._validate_entry(entry)
            self.__class__.newline_id_accepted_by_gateway = True
        except ValueError:
            self.__class__.newline_id_accepted_by_gateway = False

    def test_revoke_disables_and_gateway_sees_it_without_restart(self):
        proc = self.new()
        token = re.findall(r"stm_[A-Za-z0-9_-]{20,}", proc.stdout)[0]
        rv = run_cli("revoke", "--file", self.path, "--id", "chatbot-khach")
        self.assertEqual(rv.returncode, 0, rv.stderr)
        entries = machine_api.load_token_entries(self.path)
        self.assertFalse(entries[0]["enabled"])
        self.assertEqual(machine_api.hash_token(token), entries[0]["sha256"])
        self.assertEqual(run_cli("revoke", "--file", self.path, "--id", "nope").returncode, 1)
        run_cli("revoke", "--file", self.path, "--id", "chatbot-khach", "--delete")
        self.assertEqual(machine_api.load_token_entries(self.path), [])

    def test_existing_file_with_bad_content_is_not_silently_overwritten(self):
        Path(self.path).write_text("{not json", encoding="utf-8")
        proc = self.new()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "{not json")
        self.assertNotRegex(proc.stdout, r"stm_[A-Za-z0-9_-]{20,}")
        Path(self.path).write_text(json.dumps({"tokens": "x"}), encoding="utf-8")
        proc = self.new()
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotRegex(proc.stdout, r"stm_[A-Za-z0-9_-]{20,}")

    def test_preexisting_bad_entries_dont_break_new_and_good_entries_survive(self):
        Path(self.path).write_text(json.dumps({"tokens": [{"id": "legacy", "sha256": "z", "team_id": 1}, "junk", 5]}), encoding="utf-8")
        proc = self.new(ident="fresh-one")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listed = run_cli("list", "--file", self.path)
        self.assertIn("fresh-one", listed.stdout)
        self.assertEqual([e["id"] for e in machine_api.load_token_entries(self.path)], ["fresh-one"])


# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R2_Misc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.logs = S.LogCapture()
        cls._root = logging.getLogger()
        cls._root.addHandler(cls.logs)

    @classmethod
    def tearDownClass(cls):
        cls._root.removeHandler(cls.logs)
        cls.env.close()

    def setUp(self):
        p = mock.patch.dict(os.environ, {"DATABASE_URL": self.env.dsn, "MACHINE_API_TOKENS_FILE": self.env.token_path})
        p.start()
        self.addCleanup(p.stop)
        t = mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0)
        t.start()
        self.addCleanup(t.stop)
        machine_api.token_store.reset()
        machine_api.rate_limiter.reset()
        self.client = machine_api.app.test_client()

    def get(self, path, token="full", **query):
        h = {"Authorization": "Bearer " + self.env.tokens[token]} if token else {}
        return self.client.get(path, headers=h, query_string=query or None)

    def test_huge_limit_digits_is_a_clean_4xx_or_clamped_not_500(self):
        for limit in ("9" * 5000, "0" * 5000 + "3", "1" + "0" * 4400):
            r = self.get(V + "/products", q="ethanol", limit=limit)
            self.assertIn(r.status_code, (200, 400), (limit[:5], r.status_code, r.get_data(as_text=True)[:200]))

    def test_log_has_no_traceback_text_with_query_for_db_errors(self):
        before = len(self.logs.records)
        marker = "zqxdbmarker"
        with mock.patch.object(machine_api, "_stock_lots", side_effect=psycopg2.OperationalError(f"server closed {marker}")):
            r = self.get(V + "/products", q=marker + "abc")
        self.assertEqual(r.status_code, 503)
        text = "\n".join(x[2] for x in self.logs.records[before:])
        self.assertNotIn(marker + "abc", text)

    def test_observation_write_probes_and_unknown_paths_are_not_traced_by_app(self):
        before = len(self.logs.records)
        auth = {"Authorization": "Bearer " + self.env.tokens["full"]}
        self.client.post(V + "/products", headers=auth)
        self.client.delete(V + "/products?q=abc", headers=auth)
        self.client.get(V + "/products/", headers=auth)
        self.client.get(V + "/admin", headers=auth)
        traced = [x for x in self.logs.records[before:] if x[0] == "machine_api.access"]
        self.__class__.untraced_probe_lines = len(traced)

    def test_observation_pg14_public_schema_default_lets_role_create_when_session_is_read_write(self):
        """Mô phỏng mặc định PostgreSQL <= 14 (PUBLIC có CREATE trên schema public): chốt chặn ghi chỉ còn là giao dịch chỉ-đọc."""
        env = self.env
        with env.conn.cursor() as cur:
            cur.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
        role = "machine_api_ro_pg14" + os.urandom(2).hex()
        admin = psycopg2.connect(pg_temp_db.maintenance_dsn())
        admin.autocommit = True
        try:
            with env.conn.cursor() as cur:
                sql = (ROOT / "sql/machine_readonly_role.sql").read_text(encoding="utf-8")
                cur.execute(sql.replace("{{DB_NAME}}", env.db_name).replace("{{PASSWORD}}", "pw123456").replace("machine_api_ro", role))
            parsed = urlparse(env.dsn)
            dsn = urlunparse(parsed._replace(netloc=f"{role}:pw123456@{parsed.hostname}:{parsed.port or 5432}"))
            rw = psycopg2.connect(dsn, options="-c default_transaction_read_only=off")
            rw.autocommit = True
            c = rw.cursor()
            created = True
            try:
                c.execute("CREATE TABLE pg14_probe(a int)")
                c.execute("DROP TABLE pg14_probe")
            except psycopg2.Error:
                created = False
            rw.close()
            self.__class__.pg14_sim_role_can_create = created
            # đường cổng thật vẫn chặn: kết nối do cổng tạo ra là chỉ-đọc
            with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": dsn}):
                with machine_api.app.test_request_context(V + "/health"):
                    conn = machine_api._connection()
                    cur = conn.cursor()
                    with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                        cur.execute("CREATE TABLE pg14_probe2(a int)")
                    conn.rollback()
                    conn.close()
        finally:
            try:
                with env.conn.cursor() as cur:
                    cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                    cur.execute(f'DROP OWNED BY "{role}"')
                with admin.cursor() as cur:
                    cur.execute(f'DROP ROLE IF EXISTS "{role}"')
            finally:
                admin.close()

    def test_unit_service_has_no_access_logfile_and_loopback_only(self):
        unit = (ROOT / "deploy/search-tools-machine-api.service").read_text(encoding="utf-8")
        exec_start = re.search(r"ExecStart=(.*?)(?:\n[A-Z][A-Za-z]+=|\Z)", unit, re.S).group(1)
        self.assertNotIn("access-logfile", exec_start)
        self.assertNotIn("--access-logformat", exec_start)
        self.assertRegex(exec_start, r"--bind 127\.0\.0\.1:\d+")
        self.assertNotRegex(exec_start, r"--bind\s+0\.0\.0\.0|--bind\s+\[?::\]?")
        self.assertNotIn("--log-level debug", exec_start)
        self.assertNotIn("--capture-output", exec_start)
        self.assertNotIn("--reload", exec_start)


if __name__ == "__main__":
    unittest.main()
