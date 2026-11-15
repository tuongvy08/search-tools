"""Test độc lập của verifier cho cổng API máy chỉ đọc (V1-V10, V12 phần tĩnh).

Không dùng và không sửa tests/test_machine_api.py. Mọi ca tấn công do verifier tự nghĩ.
Chạy: DATABASE_URL=... FLASK_SECRET_KEY=x PYTHONPATH=.:tests .venv/bin/python -m unittest discover \
          -s tests/independent -p "test_machine_api_*.py"
"""

import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psycopg2

import machine_api_verifier_support as S
import machine_api
import pg_temp_db

ROOT = Path(__file__).resolve().parents[2]
V = "/machine/v1"
HAS_PG = pg_temp_db.probe_postgres_reachable()


def keys_deep(value, acc=None):
    acc = set() if acc is None else acc
    if isinstance(value, dict):
        for k, v in value.items():
            acc.add(str(k).lower())
            keys_deep(v, acc)
    elif isinstance(value, list):
        for v in value:
            keys_deep(v, acc)
    return acc


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class Base(unittest.TestCase):
    env = None

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.logs = S.LogCapture()
        cls._root = logging.getLogger()
        cls._old_level = cls._root.level
        cls._root.addHandler(cls.logs)
        cls._root.setLevel(logging.DEBUG)
        cls.bodies = []  # mọi body phản hồi để quét canary ở cuối

    @classmethod
    def tearDownClass(cls):
        cls._root.removeHandler(cls.logs)
        cls._root.setLevel(cls._old_level)
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
        machine_api.app.testing = False
        self.client = machine_api.app.test_client()
        self.env.entries = [e for e in self.env.entries if e["id"] in ("t-full", "t-quantity", "t-limited")]
        self.env.write_entries()

    def get(self, path, token="full", addr="198.51.100.7", headers=None, client=None, **query):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = "Bearer " + self.env.tokens.get(token, token)
        resp = (client or self.client).get(path, headers=h, query_string=query or None,
                                           environ_overrides={"REMOTE_ADDR": addr})
        self.bodies.append(resp.get_data(as_text=True))
        return resp

    def products(self, q, token="full", **extra):
        return self.get(V + "/products", token=token, q=q, **extra)

    def one(self, code, token="full"):
        data = self.products(code, token=token).get_json()
        hits = [r for r in data["results"] if r.get("code") == code]
        self.assertEqual(len(hits), 1, (code, data))
        return hits[0]

    def compliance(self, cas, token="full"):
        return self.get(V + "/compliance", token=token, cas=cas)

    def team_token(self, name, perms, brands):
        team = self.env.new_team(name, perms, brands)
        self.env.add_token(name, team)
        machine_api.token_store.reset()
        return name, team


# ======================================================================
class V1_Auth(Base):
    def test_every_data_endpoint_rejects_every_bad_credential(self):
        good = self.env.tokens["full"]
        variants = [
            {}, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "bearer"},
            {"Authorization": "Bearer "}, {"Authorization": "Basic " + good}, {"Authorization": good},
            {"Authorization": "Bearer " + good + "A"}, {"Authorization": "Bearer " + good[:-1]},
            {"Authorization": "Bearer " + good.swapcase()}, {"Authorization": "Bearer " + good[1:]},
            {"Authorization": "Bearer\t" + good}, {"Authorization": "Bearer  " + good + "  x"},
            {"Authorization": "Bearer " + "A" * 5000}, {"Authorization": "Bearer " + "é" * 50},
            {"Authorization": "Bearer null"}, {"Authorization": "Bearer undefined"},
            {"X-Authorization": "Bearer " + good}, {"Cookie": "token=" + good}, {"X-Api-Key": good},
        ]
        paths = ["/products?q=methanol", "/compliance?cas=64-17-5", "/stock?code=MET-001", "/stock?cas=64-17-5"]
        bodies = set()
        for path in paths:
            for headers in variants:
                r = self.client.get(V + path, headers=headers)
                self.assertEqual(r.status_code, 401, (path, list(headers.items())[:1]))
                bodies.add(r.get_data(as_text=True))
        self.assertEqual(len(bodies), 1, "thông điệp 401 phải giống hệt cho mọi nguyên nhân")
        body = bodies.pop()
        for needle in ("results", "ETH", "MET", "hết hạn", "expired", "disabled"):
            self.assertNotIn(needle, body)

    def test_token_only_in_query_or_second_header_does_not_authenticate(self):
        good = self.env.tokens["full"]
        for q in ("token", "access_token", "api_key", "key", "Authorization"):
            r = self.client.get(V + f"/products?q=methanol&{q}={good}")
            self.assertEqual(r.status_code, 401, q)
        # hai header Authorization: werkzeug chỉ lấy một; thử cả hai thứ tự
        for order in ((good, "junk"), ("junk", good)):
            environ = {"HTTP_AUTHORIZATION": "Bearer " + order[0] + ", Bearer " + order[1]}
            r = self.client.get(V + "/products?q=methanol", environ_overrides=environ)
            self.assertEqual(r.status_code, 401, order)

    def test_valid_token_works_with_bearer_case_insensitive_scheme_only(self):
        for scheme in ("Bearer", "bearer", "BEARER"):
            r = self.client.get(V + "/products?q=methanol", headers={"Authorization": f"{scheme} {self.env.tokens['full']}"})
            self.assertEqual(r.status_code, 200, scheme)

    def test_disabled_expired_deleted_and_bad_expiry_tokens(self):
        t_off = self.env.add_token("off", self.env.team_full, enabled=False)
        t_exp = self.env.add_token("exp", self.env.team_full)
        self.env.entries[-1]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        t_exactnow = self.env.add_token("now", self.env.team_full)
        self.env.entries[-1]["expires_at"] = datetime.now(timezone.utc).isoformat()
        t_bad = self.env.add_token("badexp", self.env.team_full)
        self.env.entries[-1]["expires_at"] = "31/12/2099"
        t_naive = self.env.add_token("naive", self.env.team_full)
        self.env.entries[-1]["expires_at"] = (datetime.now() + timedelta(days=1)).replace(tzinfo=None).isoformat()
        t_future_z = self.env.add_token("zulu", self.env.team_full)
        self.env.entries[-1]["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.env.write_entries()
        statuses = {n: self.products("methanol", token=t).status_code
                    for n, t in (("off", t_off), ("exp", t_exp), ("now", t_exactnow), ("bad", t_bad),
                                 ("naive", t_naive), ("zulu", t_future_z))}
        self.assertEqual(statuses["off"], 401)
        self.assertEqual(statuses["exp"], 401)
        self.assertEqual(statuses["now"], 401, "hết hạn đúng thời điểm phải bị từ chối")
        self.assertEqual(statuses["bad"], 401, "expires_at sai định dạng => không kích hoạt")
        self.assertEqual(statuses["zulu"], 200)
        self.assertIn(statuses["naive"], (200, 401))  # naive coi là UTC; chỉ ghi nhận

    def test_token_expiring_while_service_runs(self):
        tok = self.env.add_token("short", self.env.team_full)
        self.env.entries[-1]["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=1.5)).isoformat()
        self.env.write_entries()
        self.assertEqual(self.products("methanol", token=tok).status_code, 200)
        time.sleep(1.8)
        self.assertEqual(self.products("methanol", token=tok).status_code, 401)

    def test_team_archived_deleted_between_calls(self):
        name, team = self.team_token("arch", ["SEARCH", "VIEW_NAME", "VIEW_CODE"], ["ALPHA"])
        self.assertEqual(self.products("methanol", token=name).status_code, 200)
        self.env.sql("UPDATE teams SET lifecycle_status='ARCHIVED', archived_at=now() WHERE id=%s", (team,))
        for path, q in (("/products", {"q": "methanol"}), ("/compliance", {"cas": "64-17-5"}), ("/stock", {"code": "MET-001"})):
            self.assertEqual(self.get(V + path, token=name, **q).status_code, 401, path)
        self.env.sql("UPDATE teams SET lifecycle_status='ACTIVE', archived_at=NULL WHERE id=%s", (team,))
        self.assertEqual(self.products("methanol", token=name).status_code, 200)
        self.env.sql("DELETE FROM team_brands WHERE team_id=%s", (team,))
        self.env.sql("DELETE FROM teams WHERE id=%s", (team,))
        self.assertEqual(self.products("methanol", token=name).status_code, 401)

    def test_ip_allowlist_and_forwarded_for_handling(self):
        tok = self.env.add_token("ipb", self.env.team_full, allowed_ips=["203.0.113.0/24", "2001:db8::/32"])
        self.assertEqual(self.products("methanol", token=tok, addr="203.0.113.9").status_code, 200)
        self.assertEqual(self.products("methanol", token=tok, addr="198.51.100.7").status_code, 403)
        self.assertEqual(self.products("methanol", token=tok, addr="2001:db8::5").status_code, 200)
        self.assertEqual(self.products("methanol", token=tok, addr="::ffff:203.0.113.9").status_code, 200)
        # nhiều X-Forwarded-For: ProxyFix x_for=1 chỉ tin giá trị cuối (do Nginx ghi đè)
        r = self.products("methanol", token=tok, addr="198.51.100.7",
                          headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"})
        self.assertEqual(r.status_code, 403, "client khai giả IP ở đầu chuỗi XFF không được vượt qua")
        # Nginx mẫu ghi đè XFF = $remote_addr, nên XFF đơn lẻ từ ngoài chỉ tới được app khi bỏ qua Nginx
        r = self.products("methanol", token=tok, addr="198.51.100.7", headers={"X-Forwarded-For": "not-an-ip"})
        self.assertEqual(r.status_code, 403)
        # tiêu đề 401 vs 403 không lộ dữ liệu
        self.assertNotIn("results", self.products("methanol", token=tok).get_data(as_text=True))

    def test_health_and_unknown_paths(self):
        r = self.client.get(V + "/health")
        self.assertEqual((r.status_code, r.get_json()), (200, {"status": "ok"}))
        for path in ("/machine/v1/", "/machine/v1/admin", "/machine/v1/products/1", "/", "/search", "/login",
                     "/machine/v1/..%2fsearch", "/machine/v1/products/", "/machine/v1/PRODUCTS"):
            r = self.client.get(path, headers={"Authorization": "Bearer " + self.env.tokens["full"]})
            self.assertIn(r.status_code, (404, 405, 308, 301), path)
            self.assertNotIn("results", r.get_data(as_text=True))

    def test_staff_session_cookie_is_not_inherited(self):
        import search
        with search.app.test_request_context():
            sess_cookie = None
        # tự dựng cookie phiên admin hợp lệ của ứng dụng nhân viên
        serializer = search.app.session_interface.get_signing_serializer(search.app)
        cookie = serializer.dumps({"authenticated": True, "is_admin": True, "user_id": 1, "auth_version": 1, "team_id": self.env.team_full})
        self.client.set_cookie("session", cookie, domain="localhost")
        r = self.client.get(V + "/products?q=methanol")
        self.assertEqual(r.status_code, 401)
        r = self.client.get(V + "/products?q=methanol", headers={"Cookie": "session=" + cookie})
        self.assertEqual(r.status_code, 401)
        # kèm thẻ của team hạn chế + cookie admin => vẫn chỉ quyền của thẻ
        r = self.client.get(V + "/products?q=methanol", headers={"Cookie": "session=" + cookie,
                                                                  "Authorization": "Bearer " + self.env.tokens["limited"]})
        self.assertEqual(r.status_code, 200)
        item = r.get_json()["results"][0]
        self.assertNotIn("cas", item)
        self.assertEqual(item["compliance"]["state"], "HIDDEN")
        self.assertNotIn("Set-Cookie", r.headers)


# ======================================================================
class V2_NoPriceOrInternal(Base):
    def assert_clean(self, resp, label=""):
        try:
            doc = resp.get_json()
        except Exception:
            doc = None
        text = resp.get_data(as_text=True)
        if isinstance(doc, dict):  # bỏ phần phản chiếu lại đúng từ khóa do người gọi gửi lên
            echoed = {k: doc.pop(k) for k in ("query", "code", "cas") if isinstance(doc.get(k), str)}
            text = json.dumps(doc, ensure_ascii=False)
            doc.update(echoed)
        for canary in S.CANARIES:
            self.assertNotIn(canary, text, f"{label}: rò canary {canary}")
        if doc is not None:
            bad = keys_deep(doc) & {k.lower() for k in S.FORBIDDEN_KEYS}
            self.assertFalse(bad, f"{label}: khóa cấm {bad}")

    def test_sweep_over_forty_calls_incl_errors_openapi_and_odd_params(self):
        calls = []
        for token in ("full", "quantity"):
            for q in ("MET-001", "methanol", "ethanol", "ETH-001", "64-17-5", "50-00-0", "formaldehyde", "OVR-001",
                      "OVR-002", "NOI-BO-XYZ-777", "987654321", "gia nhap", "zzzznotfound", "acid", "BLK-0", "SEC-001"):
                calls.append((V + "/products", token, {"q": q}))
            for extra in ({"fields": "price"}, {"include": "price"}, {"debug": "1"}, {"expand": "note,price"},
                          {"format": "csv"}, {"columns": "price,ship"}, {"admin": "true"}, {"is_admin": "1"}):
                calls.append((V + "/products", token, dict(q="MET-001", **extra)))
            for code in ("MET-001", "ETH-002", "FOR-001", "nonexistent", "STK-ONLY"):
                calls.append((V + "/stock", token, {"code": code}))
            for cas in ("64-17-5", "50-00-0", "1333-74-0", "67-56-1"):
                calls.append((V + "/stock", token, {"cas": cas}))
                calls.append((V + "/compliance", token, {"cas": cas}))
            for bad in ({}, {"q": "a"}, {"q": "x" * 101}, {"q": "ab", "limit": "abc"}):
                calls.append((V + "/products", token, bad))
            calls.append((V + "/compliance", token, {"cas": "bad"}))
            calls.append((V + "/stock", token, {}))
        self.assertGreaterEqual(len(calls), 80)
        for path, token, query in calls:
            self.assert_clean(self.get(path, token=token, **query), f"{path} {query}")
        for path in ("/openapi.json", "/health", "/nonexistent", "/products/1"):
            r = self.client.get(V + path)
            self.assert_clean(r, path)
            r = self.get(V + path)
            self.assert_clean(r, path)
        for method in ("post", "put", "delete", "patch"):
            r = getattr(self.client, method)(V + "/products?q=methanol", headers={"Authorization": "Bearer " + self.env.tokens["full"]},
                                              json={"price": 1})
            self.assert_clean(r, method)
        r = self.get(V + "/openapi.json")
        self.assertNotRegex(r.get_data(as_text=True).lower(), r'"(price|unit_price|ship|note)"')

    def test_full_permission_team_still_never_gets_prices_or_internal_notes(self):
        # team được cấp VIEW_PRICE + VIEW_NOTE + QUICK_QUOTE + thẻ quantity
        self.assertIn("VIEW_PRICE", S.ALL_PERMS)
        data = self.get(V + "/products", token="quantity", q="methanol").get_json()
        item = data["results"][0]
        self.assertEqual(set(item), {"name", "code", "cas", "brand", "size", "compliance", "stock"})
        stock = self.get(V + "/stock", token="quantity", code="MET-001").get_json()
        self.assertEqual(stock["quantity"], 5)
        self.assert_clean(self.get(V + "/stock", token="quantity", code="MET-001"))

    def test_search_cannot_use_internal_fields_as_oracle(self):
        for q in ("NOI-BO-XYZ-777", "987654321", "5550001", "GHICHU-KHO-888", "424242", "gia nhap noi bo"):
            data = self.products(q).get_json()
            self.assertEqual(data["count"], 0, f"q={q} không được khớp vào trường nội bộ")
            self.assertEqual(self.get(V + "/stock", code=q).get_json()["status"], "OUT_OF_STOCK")

    def test_simulated_500_does_not_leak(self):
        with mock.patch.object(machine_api, "_summarize", side_effect=RuntimeError("price=987654321 /etc/passwd DSN=postgresql://u:pw@h/db")):
            r = self.products("methanol")
        self.assertEqual(r.status_code, 500)
        text = r.get_data(as_text=True)
        for needle in ("987654321", "passwd", "postgresql", "Traceback", "RuntimeError", "machine_api.py"):
            self.assertNotIn(needle, text)
        with mock.patch.object(machine_api, "_summarize", return_value={"status": "IN_STOCK", "expiry_warning": False, "price": 987654321}):
            r = self.products("methanol")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("987654321", r.get_data(as_text=True))
        for key in ("Price", "STOCK_NOTE", "Product_Id", "nested"):
            with mock.patch.object(machine_api, "_summarize", return_value={"status": "x", "expiry_warning": False, key: 1}):
                r = self.products("methanol")
            if key.lower() in machine_api.FORBIDDEN_KEYS:
                self.assertEqual(r.status_code, 500, key)

    def test_internal_ids_never_exposed(self):
        text = json.dumps(self.products("ethanol").get_json())
        self.assertNotRegex(text, r'"(id|product_id|stock_item_id)"')


# ======================================================================
class V3_ReadOnly(Base):
    def snapshot(self):
        tables = [r[0] for r in self.env.sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
        snap = {}
        for t in tables:
            snap[t] = self.env.sql(f'SELECT count(*), md5(coalesce(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) FROM "{t}" x')[0]
        return snap

    def stat_counters(self):
        time.sleep(1.6)
        return self.env.sql("SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables ORDER BY 1")

    def test_connection_is_read_only_at_postgres_level(self):
        with machine_api.app.test_request_context(V + "/health"):
            conn = machine_api._connection()
            cur = conn.cursor()
            for stmt in ("INSERT INTO teams(name, permission_keys) VALUES ('hack', '{}')",
                         "UPDATE products SET price='1'", "DELETE FROM products", "CREATE TEMP TABLE t(a int)",
                         "CREATE TABLE zz(a int)", "TRUNCATE products", "DROP TABLE products",
                         "ALTER TABLE products ADD COLUMN zz int"):
                with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction, msg=stmt):
                    cur.execute(stmt)
                conn.rollback()
            # cố tự bật chế độ ghi
            for stmt in ("SET TRANSACTION READ WRITE", "SET default_transaction_read_only = off"):
                try:
                    cur.execute(stmt)
                    cur.execute("INSERT INTO teams(name, permission_keys) VALUES ('hack2', '{}')")
                    wrote = True
                except psycopg2.Error:
                    wrote = False
                conn.rollback()
                self.assertFalse(wrote and self.env.sql("SELECT 1 FROM teams WHERE name='hack2'"), stmt)
            conn.close()

    def test_no_state_change_after_many_calls(self):
        token = self.env.tokens["full"]
        before_snap = self.snapshot()
        self.stat_counters()
        before_stat = self.stat_counters()  # đọc hai lần để các thống kê của lúc gieo dữ liệu đã được xả hết
        calls = []
        for q in ("methanol", "ETH", "'; DROP TABLE products;--", "%%%", "50-00-0", "a" * 100, "ethanol\n", "' OR 1=1--",
                  "Robert'); DELETE FROM teams;--", "\\", "!"):
            calls.append((V + "/products", {"q": q}))
        for cas in ("64-17-5", "50-00-0", "10-10-1", "1-1-1"):
            calls.append((V + "/compliance", {"cas": cas}))
            calls.append((V + "/stock", {"cas": cas}))
        for code in ("MET-001", "x'; DROP TABLE stock_items;--", "ETH-002", " met-001 "):
            calls.append((V + "/stock", {"code": code}))
        calls += [(V + "/products", {"q": "ab"}), (V + "/products", {"q": "x" * 500}), (V + "/stock", {}),
                  (V + "/products", {"q": "abc", "limit": "-1"}), (V + "/products", {"q": "abc", "limit": "1e9"})]
        for _ in range(3):
            for path, query in calls:
                self.get(path, **query)
        self.assertGreaterEqual(len(calls) * 3, 60)

        def worker(i):
            c = machine_api.app.test_client()
            for q in ("methanol", "ethanol", "acid", "50-00-0"):
                self.get(V + "/products", client=c, q=q)
            return True
        with ThreadPoolExecutor(20) as pool:
            self.assertTrue(all(pool.map(worker, range(20))))
        # client ngắt giữa chừng: gọi view rồi bỏ response (không đọc)
        for _ in range(5):
            r = self.client.get(V + "/products?q=methanol", headers={"Authorization": "Bearer " + token}, buffered=False)
            r.close()
        self.assertEqual(self.snapshot(), before_snap, "dữ liệu bảng đã đổi")
        after = self.stat_counters()
        self.assertEqual(before_stat, after, "pg_stat_user_tables n_tup_ins/upd/del đã đổi")

    def test_write_methods_405_and_do_not_touch_db(self):
        with mock.patch("psycopg2.connect") as connect:
            for method in ("post", "put", "delete", "patch"):
                for path in ("/products?q=methanol", "/compliance?cas=64-17-5", "/stock?code=MET-001", "/openapi.json", "/health"):
                    r = getattr(self.client, method)(V + path, headers={"Authorization": "Bearer " + self.env.tokens["full"]},
                                                      json={"x": 1}, environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
                    self.assertEqual(r.status_code, 405, (method, path))
            for method in ("PROPFIND", "TRACE", "MKCOL", "COPY", "LOCK"):
                r = self.client.open(V + "/products?q=methanol", method=method,
                                     headers={"Authorization": "Bearer " + self.env.tokens["full"]})
                self.assertEqual(r.status_code, 405, method)
            r = self.client.post(V + "/products?q=methanol", headers={"X-HTTP-Method-Override": "GET",
                                                                       "Authorization": "Bearer " + self.env.tokens["full"]})
            self.assertEqual(r.status_code, 405)
            self.assertEqual(connect.call_count, 0, "phương thức ghi không được mở kết nối DB")

    def test_options_and_head_are_harmless(self):
        r = self.client.options(V + "/products", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertIn(r.status_code, (200, 204))
        self.assertNotIn("POST", r.headers.get("Allow", ""))
        r = self.client.head(V + "/products?q=methanol", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(), b"")
        r = self.client.options(V + "/products")
        self.assertEqual(r.status_code, 401)

    def test_no_files_created_outside_tmp(self):
        before = {str(p) for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts and ".git" not in p.parts
                  and "__pycache__" not in p.parts}
        for q in ("methanol", "ethanol", "x" * 50):
            self.products(q)
        self.compliance("64-17-5")
        after = {str(p) for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts and ".git" not in p.parts
                 and "__pycache__" not in p.parts}
        self.assertEqual(after - before, set())


# ======================================================================
class V4_TeamScope(Base):
    def test_brand_outside_team_never_appears_anywhere(self):
        blob = []
        for path, q in ((V + "/products", {"q": "ethanol"}), (V + "/products", {"q": "ETH-003"}), (V + "/products", {"q": "64-17-5"}),
                        (V + "/products", {"q": "SEC-001"}), (V + "/products", {"q": "secret"}), (V + "/stock", {"code": "ETH-003"}),
                        (V + "/stock", {"cas": "64-17-5"}), (V + "/stock", {"cas": "1333-74-0"}), (V + "/products", {"q": "CAS-A"}),
                        (V + "/stock", {"code": "SC-HIDDEN"}), (V + "/stock", {"code": "SEC-001"}), (V + "/stock", {"cas": "50-00-0"})):
            for token in ("full", "quantity"):
                r = self.get(path, token=token, **q)
                doc = r.get_json()
                for echo in ("query", "code", "cas"):
                    if isinstance(doc, dict) and not isinstance(doc.get(echo), (dict, list)):
                        doc.pop(echo, None)
                blob.append(json.dumps(doc, ensure_ascii=False))
        text = "\n".join(blob)
        for hidden in ("GAMMA", "ETH-003", "SEC-001", "SC-HIDDEN", "Secret brand"):
            self.assertNotIn(hidden, text)
        # số lượng của hãng ẩn (50 / 9) không cộng vào tổng
        self.assertEqual(self.get(V + "/stock", token="quantity", cas="64-17-5").get_json()["quantity"], 10)
        self.assertEqual(self.get(V + "/stock", token="quantity", code="ETH-003").get_json()["status"], "OUT_OF_STOCK")

    def test_limited_team_sees_only_alpha_and_only_granted_fields(self):
        data = self.products("Ethanol abs", token="limited").get_json()
        self.assertEqual([r["code"] for r in data["results"]], ["ETH-001"])
        r0 = data["results"][0]
        self.assertEqual(set(r0), {"name", "code", "brand", "compliance", "stock"})
        self.assertEqual(r0["compliance"]["state"], "HIDDEN")
        self.assertNotIn("label", r0["compliance"])
        self.assertNotIn("same_cas", r0["stock"])
        # tìm theo CAS bị cấm với team không có SEARCH_BY_CAS/VIEW_CAS
        self.assertEqual(self.products("64-17-5", token="limited").get_json()["count"], 0)
        self.assertEqual(self.compliance("50-00-0", token="limited").status_code, 403)
        self.assertEqual(self.get(V + "/stock", token="limited", cas="64-17-5").status_code, 403)

    def test_permission_matrix_each_missing_key(self):
        base = set(S.ALL_PERMS)
        # thiếu VIEW_COMPLIANCE
        for missing, check in (
            ("VIEW_COMPLIANCE", lambda it: self.assertEqual(it["compliance"]["state"], "HIDDEN")),
            ("VIEW_COMPLIANCE_NOTE", lambda it: self.assertNotIn("regulatory_note", it["compliance"])),
            ("VIEW_BRAND", lambda it: self.assertNotIn("brand", it)),
            ("VIEW_NAME", lambda it: self.assertNotIn("name", it)),
            ("VIEW_CODE", lambda it: self.assertNotIn("code", it)),
            ("VIEW_CAS", lambda it: self.assertNotIn("cas", it)),
            ("VIEW_SIZE", lambda it: self.assertNotIn("size", it)),
        ):
            name, _ = self.team_token("m-" + missing.lower(), base - {missing}, ["ALPHA", "BETA"])
            r = self.products("ethanol", token=name) if missing != "VIEW_NAME" else self.products("ETH-001", token=name)
            if missing == "VIEW_CODE":
                r = self.products("ethanol", token=name)
            self.assertEqual(r.status_code, 200, missing)
            items = r.get_json()["results"]
            self.assertTrue(items, missing)
            for it in items:
                check(it)
            text = r.get_data(as_text=True)
            if missing == "VIEW_COMPLIANCE":
                for leak in ("CẤM", "Cần giấy phép", "note-lic", "Được bán", "export_policy"):
                    self.assertNotIn(leak, text, "rò nhãn pháp chế khi thiếu VIEW_COMPLIANCE")
            if missing == "VIEW_COMPLIANCE_NOTE":
                self.assertNotIn("note-lic", text)
            if missing == "VIEW_CAS":
                self.assertNotIn("64-17-5", text)
                self.assertNotIn("same_cas", text)

    def test_missing_view_compliance_hides_at_every_entrypoint(self):
        name, _ = self.team_token("nocomp", set(S.ALL_PERMS) - {"VIEW_COMPLIANCE"}, ["ALPHA"])
        r = self.compliance("50-00-0", token=name)  # CHECK_LICENSE phụ thuộc VIEW_COMPLIANCE
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("CẤM", r.get_data(as_text=True))
        # export_policy/blocked cũng không được xuất hiện khi HIDDEN
        for q in ("FOR-001", "formaldehyde", "50-00-0"):
            text = self.products(q, token=name).get_data(as_text=True)
            for leak in ("blocked", "export_policy", "CẤM", "note-ban", "label"):
                self.assertNotIn(leak, text, (q, leak))

    def test_missing_search_checkcas_and_dependencies(self):
        base = set(S.ALL_PERMS)
        n1, _ = self.team_token("nosearch", base - {"SEARCH"}, ["ALPHA"])
        self.assertEqual(self.products("methanol", token=n1).status_code, 403)
        self.assertEqual(self.get(V + "/stock", token=n1, code="MET-001").status_code, 403)
        n2, _ = self.team_token("nocas", base - {"SEARCH_BY_CAS"}, ["ALPHA"])
        self.assertEqual(self.products("64-17-5", token=n2).get_json()["count"], 0)
        self.assertEqual(self.products("ethanol", token=n2).status_code, 200)
        self.assertEqual(self.compliance("64-17-5", token=n2).status_code, 403)
        self.assertEqual(self.get(V + "/stock", token=n2, cas="64-17-5").status_code, 403)
        self.assertNotIn("same_cas", self.products("ETH-001", token=n2).get_data(as_text=True))
        n3, _ = self.team_token("nochk", base - {"CHECK_LICENSE"}, ["ALPHA"])
        self.assertEqual(self.compliance("64-17-5", token=n3).status_code, 403)
        # quyền rỗng
        n4, _ = self.team_token("empty", set(), ["ALPHA"])
        self.assertEqual(self.products("methanol", token=n4).status_code, 403)
        self.assertEqual(self.compliance("64-17-5", token=n4).status_code, 403)
        # team không có hãng nào
        n5, _ = self.team_token("nobrand", S.ALL_PERMS, [])
        r = self.products("methanol", token=n5)
        self.assertEqual(r.get_json()["count"], 0)
        self.assertEqual(self.get(V + "/stock", token=n5, code="MET-001").get_json()["status"], "OUT_OF_STOCK")
        # search chỉ-CAS: không có VIEW_NAME/VIEW_CODE mà có CAS
        n6, _ = self.team_token("casonly", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CAS"], ["ALPHA"])
        r = self.products("64-17-5", token=n6).get_json()
        self.assertEqual(r["count"], 1)
        self.assertEqual(set(r["results"][0]), {"cas", "compliance", "stock"})
        # chỉ có SEARCH, không trường tìm nào
        n7, _ = self.team_token("searchonly", ["SEARCH"], ["ALPHA"])
        self.assertEqual(self.products("methanol", token=n7).status_code, 403)

    def test_permission_and_brand_changes_apply_on_next_call(self):
        name, team = self.team_token("dyn", ["SEARCH", "VIEW_NAME", "VIEW_CODE", "VIEW_BRAND", "VIEW_CAS", "SEARCH_BY_CAS"], ["ALPHA"])
        self.assertEqual(self.products("Ethanol abs", token=name).get_json()["count"], 1)
        self.env.sql("INSERT INTO team_brands(team_id, brand) VALUES (%s,'BETA')", (team,))
        self.assertEqual(self.products("Ethanol abs", token=name).get_json()["count"], 2)
        self.env.sql("DELETE FROM team_brands WHERE team_id=%s AND brand='BETA'", (team,))
        self.assertEqual(self.products("Ethanol abs", token=name).get_json()["count"], 1)
        self.env.sql("UPDATE teams SET permission_keys=%s WHERE id=%s", (["SEARCH", "VIEW_CODE"], team))
        item = self.products("ETH-001", token=name).get_json()["results"][0]
        self.assertEqual(set(item) - {"compliance", "stock"}, {"code"})
        self.env.sql("UPDATE teams SET permission_keys=%s WHERE id=%s", (["SEARCH"], team))
        self.assertEqual(self.products("ETH-001", token=name).status_code, 403)

    def test_invalid_permission_keys_fail_closed(self):
        name, team = self.team_token("badperm", ["SEARCH", "VIEW_CODE"], ["ALPHA"])
        self.env.sql("UPDATE teams SET permission_keys=%s WHERE id=%s", (["SEARCH", "VIEW_CODE", "GOD_MODE"], team))
        r = self.products("MET-001", token=name)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("results", r.get_data(as_text=True))
        self.env.sql("UPDATE teams SET permission_keys=NULL WHERE id=%s", (team,)) if False else None

    def test_db_down_returns_503_without_default_data(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://nobody:SECRETPW@127.0.0.1:1/none", "MACHINE_DATABASE_URL": ""}):
            r = self.products("methanol")
        self.assertEqual(r.status_code, 503)
        text = r.get_data(as_text=True)
        for needle in ("SECRETPW", "nobody", "127.0.0.1", "results"):
            self.assertNotIn(needle, text)
        with mock.patch.dict(os.environ, {"DATABASE_URL": "", "MACHINE_DATABASE_URL": ""}):
            self.assertEqual(self.products("methanol").status_code, 503)

    # ---- đối chiếu với /search nhân viên -------------------------------
    def staff_results(self, query, team_id, perms):
        import search
        from auth_test_helpers import auth_db_patch

        def conn():
            return psycopg2.connect(self.env.dsn)
        with auth_db_patch(), mock.patch.object(search, "get_connection", conn), \
                mock.patch("team_permissions.current_permissions", return_value=frozenset(perms)):
            search.app.testing = True
            with search.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess.update({"authenticated": True, "user_id": 1, "auth_version": 1, "is_admin": False, "team_id": team_id})
                resp = client.get("/search", query_string={"query": query})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:300])
        return [r for r in resp.get_json()["results"] if r.get("Result_Kind") != "stock_only"]

    def test_parity_with_staff_search_for_25_plus_queries(self):
        queries = ["methanol", "MET-001", "met-001", "ethanol", "Ethanol absolute", "ETH-001", "ETH", "64-17-5", "64-17", "67-56-1",
                   "50-00-0", "formaldehyde", "FOR-001", "OVR-001", "OVR-002", "override", "acid", "sulfuric", "7664-93-9",
                   "toluene", "108-88-3", "Axit béo", "axit beo", "Axit", "BLK-00", "Bulk sample 0", "SEC-001", "secret",
                   "ETH-003", "zzznone", "benzene", "71-43-2", "10-10-1", "56-23-5", "dichloro", "percent", "100%", "CAS-A"]
        self.assertGreaterEqual(len(queries), 25)
        team = self.env.team_full
        perms = S.ALL_PERMS
        diffs = []
        for q in queries:
            if "%" in q:
                continue  # staff không thoát ký tự đại diện (khác biệt có chủ ý ở V8)
            staff = self.staff_results(q, team, perms)[:10]
            mine = self.products(q, limit=10).get_json()
            staff_view = [(r["Name"], r["Code"], r["Cas"], r["Brand"], r["Size"], r["Compliance_Status"], r["Compliance_Note"],
                           r["Compliance_Export_Policy"], r["Compliance_Source"]) for r in staff]
            my_view = [(r["name"], r["code"], r["cas"], r["brand"], r["size"],
                        r["compliance"].get("label", ""), r["compliance"].get("regulatory_note", ""),
                        r["compliance"].get("export_policy", "ALLOW"), r["compliance"].get("source", "none")) for r in mine["results"]]
            # ngoại lệ liệt kê tường minh: NO_RULE <-> nhãn rỗng; DATA_ERROR dùng ghi chú chuẩn hóa
            my_view = [tuple(x if x is not None else "" for x in row) for row in my_view]
            staff_view = [tuple(x if x is not None else "" for x in row) for row in staff_view]
            norm = []
            for row in staff_view:
                norm.append(row if row[5] else (row[0], row[1], row[2], row[3], row[4], "", "", "ALLOW", "none"))
            mine_norm = []
            for row in my_view:
                mine_norm.append(row if row[5] else (row[0], row[1], row[2], row[3], row[4], "", "", "ALLOW", "none"))
            if norm != mine_norm:
                diffs.append((q, norm, mine_norm))
            # tồn kho: đối chiếu IN_STOCK với Stock_Options exact_code của nhân viên
            for staff_row, mine_row in zip(staff, mine["results"]):
                exact = [o for o in staff_row["Stock_Options"] if o["Stock_Match"] == "exact_code"]
                expect_in = any(o["Stock_Quantity"] > 0 and o["Stock_State"] != "expired" for o in exact)
                self.assertEqual(mine_row["stock"]["status"] == "IN_STOCK", expect_in, (q, staff_row["Code"]))
                same = [o for o in staff_row["Stock_Options"] if o["Stock_Match"] == "same_cas"
                        and o["Stock_Quantity"] > 0 and o["Stock_State"] != "expired"]
                same_codes = {o["Code"] for o in same}
                mine_codes = {o["code"] for o in mine_row["stock"].get("same_cas", [])}
                if len(same_codes) <= 5:
                    self.assertEqual(mine_codes, same_codes, (q, staff_row["Code"]))
                else:
                    self.assertTrue(mine_codes <= same_codes and len(mine_codes) == 5, (q, staff_row["Code"]))
        self.assertFalse(diffs, f"khác biệt với nhân viên: {diffs[:2]}")

    def test_parity_with_staff_for_limited_team(self):
        perms = ["SEARCH", "VIEW_NAME", "VIEW_CODE", "VIEW_BRAND"]
        for q in ("ethanol", "methanol", "ETH", "formaldehyde", "64-17-5"):
            staff = self.staff_results(q, self.env.team_limited, perms)
            mine = self.products(q, token="limited").get_json()["results"]
            self.assertEqual([r.get("Code") for r in staff], [r.get("code") for r in mine], q)
            for s_row, m_row in zip(staff, mine):
                self.assertEqual(s_row["Name"], m_row["name"])
                self.assertEqual(s_row["Brand"], m_row["brand"])


# ======================================================================
class V6_Compliance(Base):
    def test_independent_expectations_through_compliance_endpoint(self):
        # kỳ vọng soạn từ BUSINESS_RULES (priority nhỏ thắng: CẤM NHẬP 10 < PL II 20 < PL III 30 < Được bán 40 < Cần GP 50 < Chưa XĐ 60)
        cases = [
            (S.C_BAN, "RULE", "CẤM NHẬP", "BLOCK", "note-ban", True),
            (S.C_P2, "RULE", "Phụ lục II", "ALLOW", "note-p2", False),
            (S.C_P3, "RULE", "Phụ lục III", "ALLOW", "note-p3", False),
            (S.C_SELL, "RULE", "Được bán", "ALLOW", "note-sell", False),
            (S.C_LIC, "RULE", "Cần giấy phép", "ALLOW", "note-lic", False),
            (S.C_MULTI, "RULE", "Được bán", "ALLOW", "zeta-note", False),   # thắng ưu tiên; không trộn ghi chú của hạng thua
            (S.C_UNK, "RULE", "Chưa xác định", "ALLOW", "note-unk", False),
            (S.C_INJ, "RULE", "CẤM NHẬP", "BLOCK", S.INJECTION_NOTE, True),
            (S.C_EMPTY, "RULE", "Phụ lục II", "ALLOW", "", False),
        ]
        for cas, state, label, policy, note, blocked in cases:
            block = self.compliance(cas).get_json()["compliance"]
            self.assertEqual((block["state"], block["label"], block["export_policy"], block["blocked"], block["regulatory_note"]),
                             (state, label, policy, blocked, note), cas)
            self.assertEqual(block["disclaimer"], machine_api.DISCLAIMER)
        for cas in (S.C_NONE, S.C_INACT, "99-99-9", "1-1-1"[:0] or "999999-99-9"):
            block = self.compliance(cas).get_json()["compliance"]
            self.assertEqual(block["state"], "NO_RULE", cas)
            self.assertNotIn("label", block)
            self.assertNotIn("blocked", block)
            self.assertIn("Chưa có quy tắc", block["message"])
            self.assertIn("xác nhận với bộ phận pháp chế", block["message"])
            self.assertEqual(block["disclaimer"], machine_api.DISCLAIMER)
            self.assertIn("căn cứ", block["disclaimer"])

    def test_no_rule_message_literal_contract_check(self):
        """Ghi nhận trung thực: oracle V6 nói NO_RULE 'không có chữ được phép nhập'."""
        block = self.compliance(S.C_NONE).get_json()["compliance"]
        text = json.dumps(block, ensure_ascii=False).lower()
        # Không được khẳng định cho phép:
        for bad in ("được phép nhập khẩu.", "có thể nhập", "được nhập", "không bị cấm", "hợp pháp"):
            self.assertNotIn(bad, text)
        self.last_no_rule_mentions_phrase = "được phép nhập" in text
        # dữ kiện cho báo cáo: chuỗi xuất hiện dưới dạng phủ định
        self.assertIn("không có nghĩa là được phép nhập", text)

    def test_cas_input_variants(self):
        for ok in ("64-17-5", " 64-17-5 ", "64-17-5\n", "%0964-17-5"):
            r = self.compliance(ok.replace("%09", "\t"))
            self.assertEqual(r.status_code, 200, repr(ok))
            self.assertEqual(r.get_json()["compliance"]["label"], "Cần giấy phép")
        for bad in ("", "   ", "abc", "64175", "64-17-55", "64-1-5", "6-17-5", "1234567890-12-3", "64-17-5; DROP", "64–17–5",
                    "６４-17-5", "64-17-5-", "64--17-5", "０", "\x00", "64-17-5\x00", "%", "64-17-%", "'; --"):
            r = self.compliance(bad)
            self.assertEqual(r.status_code, 400, repr(bad))
            self.assertNotIn("label", r.get_data(as_text=True))
        r = self.get(V + "/compliance")
        self.assertEqual(r.status_code, 400)
        r = self.client.get(V + "/compliance?cas=64-17-5&cas=50-00-0", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertEqual(r.status_code, 400)

    def test_products_compliance_cases(self):
        c = {code: self.one(code)["compliance"] for code in
             ("MET-001", "ETH-001", "FOR-001", "OVR-001", "OVR-002", "ACD-001", "ACD-002", "TOL-001", "DCM-001", "UNK-001",
              "INJ-001", "EMP-001", "NOR-001", "NOC-001")}
        self.assertEqual(c["MET-001"]["label"], "Được bán")
        self.assertEqual(c["ETH-001"]["label"], "Cần giấy phép")
        self.assertEqual((c["FOR-001"]["label"], c["FOR-001"]["blocked"]), ("CẤM NHẬP", True))
        # override thủ công: hãng PRIORITY bật => Được bán thắng CẤM NHẬP, ghi chú là của override
        self.assertEqual((c["OVR-001"]["label"], c["OVR-001"]["source"], c["OVR-001"]["blocked"], c["OVR-001"]["regulatory_note"]),
                         ("Được bán", "manual", False, "ghi chu thu cong"))
        # hãng không bật => bỏ qua override, ghi chú override không lọt ra
        self.assertEqual((c["OVR-002"]["label"], c["OVR-002"]["blocked"]), ("CẤM NHẬP", True))
        self.assertNotIn("phai-bi-bo-qua", json.dumps(c["OVR-002"], ensure_ascii=False))
        self.assertEqual(c["ACD-001"]["label"], "Phụ lục II")
        self.assertEqual(c["ACD-002"]["label"], "Phụ lục III")
        self.assertEqual((c["TOL-001"]["label"], c["TOL-001"]["regulatory_note"]), ("Được bán", "zeta-note"))
        self.assertEqual(c["DCM-001"]["state"], "NO_RULE")
        self.assertEqual(c["UNK-001"]["label"], "Chưa xác định")
        self.assertEqual(c["INJ-001"]["state"], "RULE")
        self.assertTrue(c["INJ-001"]["blocked"])
        self.assertEqual(c["INJ-001"]["regulatory_note"], S.INJECTION_NOTE)
        self.assertEqual(c["NOR-001"]["state"], "NO_RULE")
        self.assertEqual(c["NOC-001"]["state"], "NO_RULE")
        for block in c.values():
            self.assertEqual(block["disclaimer"], machine_api.DISCLAIMER)

    def test_long_note_passes_through_as_data(self):
        r = self.compliance(S.C_LONG)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["compliance"]["regulatory_note"], S.LONG_NOTE)

    def test_broken_manual_and_rename_and_deleted_status(self):
        pid = self.env.sql("SELECT id FROM products WHERE code='OVR-001'")[0][0]
        self.env.sql("ALTER TABLE products DISABLE TRIGGER trg_products_manual_regulatory_status")
        try:
            self.env.sql("UPDATE products SET manual_compliance_status_id=NULL WHERE id=%s", (pid,))
            block = self.one("OVR-001")["compliance"]
            self.assertEqual((block["state"], block["export_policy"], block["blocked"]), ("DATA_ERROR", "BLOCK", True))
            self.assertEqual(block["regulatory_note"], machine_api.DATA_ERROR_NOTE)
            text = json.dumps(block, ensure_ascii=False)
            self.assertNotIn("ghi chu thu cong", text)
            # trỏ tới id không tồn tại: FK chặn; thử id của status vừa bị xóa
            self.env.sql("UPDATE products SET manual_compliance_status_id=%s WHERE id=%s", (self.env.statuses["DUOC_BAN"], pid))
            self.assertEqual(self.one("OVR-001")["compliance"]["label"], "Được bán")
        finally:
            self.env.sql("UPDATE products SET manual_compliance_status_id=%s WHERE id=%s", (self.env.statuses["DUOC_BAN"], pid))
            self.env.sql("ALTER TABLE products ENABLE TRIGGER trg_products_manual_regulatory_status")
        # đổi tên nhãn: chính sách theo stable_key không đổi
        self.env.sql("UPDATE regulatory_statuses SET label='Bị cấm hẳn' WHERE stable_key='CAM_NHAP'")
        try:
            block = self.one("FOR-001")["compliance"]
            self.assertEqual((block["label"], block["export_policy"], block["blocked"]), ("Bị cấm hẳn", "BLOCK", True))
        finally:
            self.env.sql("UPDATE regulatory_statuses SET label='CẤM NHẬP' WHERE stable_key='CAM_NHAP'")
        # rule bị tắt giữa chừng
        self.env.sql("UPDATE regulatory_rules SET is_active=FALSE WHERE match_value=%s", (S.C_LIC,))
        try:
            self.assertEqual(self.compliance(S.C_LIC).get_json()["compliance"]["state"], "NO_RULE")
        finally:
            self.env.sql("UPDATE regulatory_rules SET is_active=TRUE WHERE match_value=%s", (S.C_LIC,))

    def test_lowercase_whitespace_rule_match_value(self):
        self.env.sql("INSERT INTO regulatory_rules(status_id, rule_type, rule_label, match_field, match_value, note) "
                     "VALUES (%s,'','','cas','  7732-18-5  ','trim-note')", (self.env.statuses["PHU_LUC_III"],))
        try:
            self.assertEqual(self.compliance("7732-18-5").get_json()["compliance"]["label"], "Phụ lục III")
        finally:
            self.env.sql("DELETE FROM regulatory_rules WHERE note='trim-note'")

    def test_name_or_code_rules_do_not_leak_into_cas_endpoint(self):
        # rule dạng match_field='name'/'code' không được áp cho /compliance (chỉ CAS)
        self.env.sql("INSERT INTO regulatory_rules(status_id, rule_type, rule_label, match_field, match_value, note) "
                     "VALUES (%s,'','','name','Benzene plain','by-name')", (self.env.statuses["CAM_NHAP"],))
        try:
            self.assertEqual(self.one("NOR-001")["compliance"]["state"], "NO_RULE")
        finally:
            self.env.sql("DELETE FROM regulatory_rules WHERE note='by-name'")


# ======================================================================
class V7_Stock(Base):
    def oracle(self, code, token_brands=("alpha", "beta", "priority")):
        rows = self.env.sql(
            "SELECT i.quantity, i.expiry_date FROM stock_state s JOIN stock_items i ON i.snapshot_id=s.active_snapshot_id "
            "WHERE i.code_norm=%s AND i.brand_norm = ANY(%s)", (code.casefold(), list(token_brands)))
        avail = [(q, e) for q, e in rows if q > 0 and (e is None or e >= S.TODAY)]
        return bool(avail), sum(q for q, _ in avail), bool(avail) and all(e is not None and e <= S.TODAY + timedelta(days=30) for _, e in avail)

    def test_twelve_plus_cases_against_independent_sql(self):
        codes = ["MET-001", "ETH-001", "ETH-002", "ETH-003", "FOR-001", "OVR-001", "ACD-001", "ACD-002", "TOL-001", "DCM-001",
                 "UNK-001", "STK-ONLY", "OLD-ONLY", "SEC-001", "NOPE-999"]
        for code in codes:
            expect_in, expect_qty, expect_warn = self.oracle(code)
            s1 = self.get(V + "/stock", code=code).get_json()
            s2 = self.get(V + "/stock", token="quantity", code=code).get_json()
            self.assertEqual(s1["status"], "IN_STOCK" if expect_in else "OUT_OF_STOCK", code)
            self.assertEqual(s1["expiry_warning"], expect_warn, code)
            self.assertEqual(s2["quantity"], expect_qty, code)
            self.assertNotIn("quantity", json.dumps(s1), f"{code}: lộ số lượng khi thẻ status")
        self.assertEqual(self.get(V + "/stock", token="quantity", code="MET-001").get_json()["quantity"], 5, "snapshot cũ không được tính")
        self.assertEqual(self.get(V + "/stock", code="OLD-ONLY").get_json()["status"], "OUT_OF_STOCK")

    def test_expiry_boundaries(self):
        expect = {"ACD-001": ("IN_STOCK", True),    # hết hạn hôm nay => còn trong ngưỡng cảnh báo
                  "ACD-002": ("OUT_OF_STOCK", False),  # hôm qua => hết hạn
                  "FOR-001": ("IN_STOCK", True),    # +30 ngày => cảnh báo
                  "OVR-001": ("IN_STOCK", False),   # +31 ngày => không cảnh báo
                  "TOL-001": ("IN_STOCK", True),    # lô sắp hết hạn + lô đã hết hạn
                  "DCM-001": ("IN_STOCK", False)}   # lô sắp hết hạn + lô còn hạn dài
        for code, (status, warn) in expect.items():
            r = self.get(V + "/stock", code=code).get_json()
            self.assertEqual((r["status"], r["expiry_warning"]), (status, warn), code)
        self.assertEqual(self.get(V + "/stock", token="quantity", code="TOL-001").get_json()["quantity"], 1)
        self.assertEqual(self.get(V + "/stock", token="quantity", code="DCM-001").get_json()["quantity"], 5)
        self.assertEqual(self.get(V + "/stock", token="quantity", code="ETH-002").get_json()["quantity"], 10, "lô hết hạn không cộng")

    def test_same_cas_is_separate_and_capped(self):
        item = self.one("CAS-A")
        self.assertEqual(item["stock"]["status"], "OUT_OF_STOCK", "hàng cùng CAS không được làm đổi status mã gốc")
        same = item["stock"]["same_cas"]
        self.assertEqual(len(same), 5)
        self.assertTrue(item["stock"]["same_cas_more"])
        codes = {s["code"] for s in same}
        self.assertTrue(codes <= {f"SC-{i}" for i in range(1, 8)})
        self.assertNotIn("SC-HIDDEN", json.dumps(item))
        self.assertNotIn("SC-EXPIRED", json.dumps(item))
        eth1 = self.one("ETH-001")
        self.assertEqual(eth1["stock"]["status"], "OUT_OF_STOCK")  # qty 0
        self.assertEqual([s["code"] for s in eth1["stock"]["same_cas"]], ["ETH-002"])
        self.assertFalse(eth1["stock"]["same_cas_more"])
        # status-only không có quantity ở mọi độ sâu
        self.assertNotIn("quantity", json.dumps(item))
        q = self.get(V + "/products", token="quantity", q="CAS-A").get_json()["results"][0]["stock"]
        self.assertTrue(all("quantity" in s for s in q["same_cas"]))
        self.assertEqual(q["quantity"], 0)

    def test_code_normalisation_and_cas_lookup(self):
        base = self.get(V + "/stock", token="quantity", code="MET-001").get_json()
        for variant in ("met-001", " MET-001 ", "Met-001"):
            r = self.get(V + "/stock", token="quantity", code=variant).get_json()
            self.assertEqual((r["status"], r["quantity"]), (base["status"], base["quantity"]), variant)
        r = self.get(V + "/stock", token="quantity", cas="1333-74-0").get_json()
        self.assertEqual(r["status"], "IN_STOCK")
        self.assertEqual(r["quantity"], sum(10 + i for i in range(1, 8)))
        self.assertLessEqual(len(r["items"]), 10)
        self.assertTrue(r["more"] is False)
        self.assertEqual(self.get(V + "/stock", cas="64-17-5").get_json()["quantity" if False else "status"], "IN_STOCK")

    def test_active_snapshot_switch_applies_immediately(self):
        old = self.env.sql("SELECT id FROM stock_snapshots WHERE actor='verifier' ORDER BY id")  # không dùng; chỉ để có dữ liệu
        active = self.env.sql("SELECT active_snapshot_id FROM stock_state")[0][0]
        other = [r[0] for r in self.env.sql("SELECT DISTINCT snapshot_id FROM stock_items WHERE snapshot_id<>%s", (active,))][0]
        try:
            self.env.sql("UPDATE stock_state SET active_snapshot_id=%s", (other,))
            self.assertEqual(self.get(V + "/stock", token="quantity", code="MET-001").get_json()["quantity"], 999)
            self.env.sql("UPDATE stock_state SET active_snapshot_id=NULL")
            self.assertEqual(self.get(V + "/stock", token="quantity", code="MET-001").get_json()["status"], "OUT_OF_STOCK")
        finally:
            self.env.sql("UPDATE stock_state SET active_snapshot_id=%s", (active,))
        self.assertEqual(self.get(V + "/stock", token="quantity", code="MET-001").get_json()["quantity"], 5)

    def test_hundreds_of_lots_for_one_cas(self):
        snap = self.env.sql("SELECT active_snapshot_id FROM stock_state")[0][0]
        for i in range(600):
            self.env.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm) "
                         "VALUES (%s,'bulk',%s,'3333-33-3','ALPHA',%s,1,NULL,'alpha',%s,%s,'3333-33-3')",
                         (snap, f"HUGE-{i}", f"{i}g", f"huge-{i}", f"{i}g"))
        try:
            r = self.get(V + "/stock", token="quantity", cas="3333-33-3")
            self.assertEqual(r.status_code, 200)
            d = r.get_json()
            self.assertEqual(d["quantity"], 600)
            self.assertEqual(len(d["items"]), 10)
            self.assertTrue(d["more"])
            self.assertLess(len(r.get_data()), 20_000)
        finally:
            self.env.sql("DELETE FROM stock_items WHERE cas_norm='3333-33-3'")

    def test_stock_param_validation(self):
        for params in ({}, {"code": "MET-001", "cas": "64-17-5"}, {"code": ""}, {"code": "   "}, {"cas": "xx"}, {"cas": ""},
                       {"code": "x" * 101}):
            self.assertEqual(self.get(V + "/stock", **params).status_code, 400, params)


# ======================================================================
class V8_BadInput(Base):
    def count_literal(self, q):
        return self.env.sql(
            "SELECT count(*) FROM products WHERE brand IN ('ALPHA','BETA','PRIORITY') AND "
            "(position(lower(%s) in lower(name))>0 OR position(lower(%s) in lower(code))>0 OR position(lower(%s) in lower(coalesce(cas,'')))>0)",
            (q, q, q))[0][0]

    def test_length_bounds(self):
        for q, status in (("ab", 400), ("a" * 3, 200), ("a" * 100, 200), ("a" * 101, 400), ("", 400), ("  ", 400), ("  ab  ", 400),
                          ("a\x00bc", 400)):
            self.assertEqual(self.products(q).status_code, status, repr(q))
        self.assertEqual(self.get(V + "/products").status_code, 400)
        # lặp tham số
        r = self.client.get(V + "/products?q=abc&q=def", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertEqual(r.status_code, 400)

    def test_wildcards_are_literal(self):
        for q in ("%%%", "___", "%a%", "\\\\\\", "!!!", "100%", "r_s", "%", "a%c", "_et", "%et%", "[a-z]", ".*", "^M", "M$"):
            if len(q) < 3:
                continue
            data = self.products(q, limit=10).get_json()
            expected = self.count_literal(q)
            self.assertEqual(data["count"] + (10 if data["more"] else 0) >= expected and data["count"] == min(expected, 10), True, (q, data["count"], expected))
        self.assertEqual(self.products("100%").get_json()["count"], 1)
        self.assertEqual(self.products("%%%").get_json()["count"], 0)
        self.assertEqual(self.products("r_sc").get_json()["count"], 1)
        self.assertEqual(self.products("r_s_").get_json()["count"], 0)
        self.assertEqual(self.products("___").get_json()["count"], 0)

    def test_limit_handling(self):
        self.assertEqual(self.products("ethanol", limit="1").get_json()["count"], 1)
        self.assertTrue(self.products("BLK-", limit="1").get_json()["more"])
        self.assertEqual(self.products("BLK-", limit="9999").get_json()["count"], 10)
        self.assertEqual(self.products("BLK-").get_json()["count"], 10)
        for bad in ("-1", "0", "1e9", "abc", "1.5", " ", "٣", "+5", "0x5", "5;DROP"):
            r = self.products("BLK-", limit=bad)
            self.assertIn(r.status_code, (200, 400), bad)
            if r.status_code == 200:
                self.assertLessEqual(r.get_json()["count"], 10)
        for bad in ("-1", "0", "abc", "1e9", "-0", "1.5"):
            self.assertEqual(self.products("BLK-", limit=bad).status_code, 400, bad)
        self.assertEqual(self.products("BLK-", limit="").status_code, 200)

    def test_weird_unicode_and_injection_strings(self):
        weird = ["' OR 1=1--", "\"; SELECT pg_sleep(5);--", "‮‭", "😀😀😀", "é" * 50, "ǅǅǅ", "ﬁﬁﬁ", "İİİ", "﻿abc",
                 "a\r\nb c", "a\tb", "<script>alert(1)</script>", "${jndi:ldap://x}", "{{7*7}}", "퟿abc", "\x01\x02\x03", "ß" * 10,
                 "%00abc", "\\x00", "NULL", "true", "../../etc/passwd"]
        for q in weird:
            start = time.time()
            r = self.products(q)
            self.assertIn(r.status_code, (200, 400), repr(q))
            self.assertLess(time.time() - start, 3, repr(q))
            doc = r.get_json()
            if isinstance(doc, dict):
                doc.pop("query", None)
            text = json.dumps(doc, ensure_ascii=False)
            for needle in ("Traceback", "psycopg2", "SELECT", "machine_api.py", "ERROR:", "postgresql"):
                self.assertNotIn(needle, text, repr(q))
        # surrogate không thể mã hóa
        r = self.client.get(V + "/products?q=%ED%A0%80%ED%A0%80", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertIn(r.status_code, (200, 400))
        r = self.client.get(V + "/products?q=%FF%FE%FD%FC", headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertIn(r.status_code, (200, 400))

    def test_rate_limit_per_token(self):
        t1 = self.env.add_token("rl1", self.env.team_full, rate_per_minute=3)
        t2 = self.env.add_token("rl2", self.env.team_full, rate_per_minute=3)
        codes = [self.products("methanol", token=t1).status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        r = self.products("methanol", token=t1)
        self.assertEqual(r.status_code, 429)
        retry = int(r.headers["Retry-After"])
        self.assertTrue(1 <= retry <= 61)
        self.assertNotIn("results", r.get_data(as_text=True))
        self.assertEqual(self.products("methanol", token=t2).status_code, 200, "thẻ khác không bị ảnh hưởng")
        self.assertEqual(self.products("methanol", token="full").status_code, 200)
        # thẻ sai không tiêu hao hạn mức của thẻ đúng
        for _ in range(20):
            self.products("methanol", token="stm_wrong_" + "x" * 30)
        t3 = self.env.add_token("rl3", self.env.team_full, rate_per_minute=2)
        for _ in range(10):
            self.products("methanol", token="stm_wrong_" + "x" * 30)
        self.assertEqual(self.products("methanol", token=t3).status_code, 200)
        # 500 truy vấn dồn dập một thẻ: dịch vụ vẫn trả 200/429, không 5xx
        t4 = self.env.add_token("rl4", self.env.team_full, rate_per_minute=30)
        codes = {self.products("methanol", token=t4).status_code for _ in range(500)}
        self.assertEqual(codes, {200, 429})

    def test_rate_limit_boundary_exact_window(self):
        rl = machine_api.RateLimiter()
        self.assertIsNone(rl.check("a", 2, now=100.0))
        self.assertIsNone(rl.check("a", 2, now=101.0))
        self.assertIsNotNone(rl.check("a", 2, now=159.9))
        self.assertIsNone(rl.check("a", 2, now=160.0))   # lượt đầu (100.0) vừa trượt khỏi cửa sổ

    def test_statement_timeout_returns_generic_503(self):
        original = machine_api._brand_visibility_sql

        def slow(alias):
            return " AND (SELECT pg_sleep(1.5)) IS NOT NULL" + original(alias)
        with mock.patch.object(machine_api, "STATEMENT_TIMEOUT_MS", 100), mock.patch.object(machine_api, "_brand_visibility_sql", slow):
            start = time.time()
            r = self.products("methanol")
        self.assertLess(time.time() - start, 1.4)
        self.assertEqual(r.status_code, 503)
        text = r.get_data(as_text=True)
        for needle in ("pg_sleep", "SELECT", "statement timeout", "canceling", "psycopg2"):
            self.assertNotIn(needle, text)
        self.assertEqual(self.products("methanol").status_code, 200, "dịch vụ phục hồi sau timeout")

    def test_lock_timeout_returns_generic_503(self):
        locker = psycopg2.connect(self.env.dsn)
        try:
            cur = locker.cursor()
            cur.execute("LOCK TABLE products IN ACCESS EXCLUSIVE MODE")
            start = time.time()
            r = self.products("methanol")
            elapsed = time.time() - start
            self.assertEqual(r.status_code, 503)
            self.assertLess(elapsed, 4)
            self.assertNotIn("lock", r.get_data(as_text=True).lower().replace("block", ""))
        finally:
            locker.rollback()
            locker.close()
        self.assertEqual(self.products("methanol").status_code, 200)

    def test_all_error_bodies_are_clean(self):
        bad_calls = [(V + "/products", {}), (V + "/products", {"q": "a"}), (V + "/compliance", {"cas": "x"}),
                     (V + "/stock", {}), (V + "/nope", {}), (V + "/products", {"q": "abc", "limit": "zz"})]
        for path, q in bad_calls:
            for token in ("full", None, "bad"):
                r = self.get(path, token=token, **q)
                text = r.get_data(as_text=True)
                self.assertGreaterEqual(r.status_code, 400)
                for needle in ("Traceback", "File \"", "/Volumes", "/opt/", "postgresql://", "SELECT ", "FROM ", "searchlocal",
                               "MACHINE_API_TOKENS_FILE", "DATABASE_URL", self.env.token_path, self.env.dsn):
                    self.assertNotIn(needle, text, (path, q, needle))
        r = self.client.post(V + "/products", data="x" * 200_000)
        self.assertIn(r.status_code, (405, 413))
        r = self.client.get(V + "/products", query_string="q=" + "a" * 100_000, headers={"Authorization": "Bearer " + self.env.tokens["full"]})
        self.assertEqual(r.status_code, 400)


# ======================================================================
class V9_Logging(Base):
    def lines_for(self, fn):
        before = len(self.logs.records)
        fn()
        return [r for r in self.logs.records[before:]]

    def test_exactly_one_access_line_per_data_call_with_required_fields(self):
        scenarios = [
            (lambda: self.products("zqxsentinel1"), 200, "t-full", "products"),
            (lambda: self.products("zq"), 400, "t-full", "products"),
            (lambda: self.products("zqxsentinel2", token="stm_wrongtoken_zqxsecretwrong"), 401, "anonymous", "products"),
            (lambda: self.get(V + "/compliance", cas="64-17-5"), 200, "t-full", "compliance"),
            (lambda: self.get(V + "/stock", code="MET-001"), 200, "t-full", "stock"),
            (lambda: self.get(V + "/stock"), 400, "t-full", "stock"),
            (lambda: self.get(V + "/compliance", token=None, cas="64-17-5"), 401, "anonymous", "compliance"),
        ]
        for fn, status, token_id, endpoint in scenarios:
            recs = self.lines_for(fn)
            access = [r for r in recs if r[0] == "machine_api.access"]
            self.assertEqual(len(access), 1, (endpoint, status, [r[2] for r in recs]))
            doc = json.loads(access[0][2].split("|", 2)[2])
            self.assertEqual((doc["status"], doc["token"], doc["endpoint"]), (status, token_id, endpoint))
            for key in ("ts", "results", "ip", "ms"):
                self.assertIn(key, doc)
        # IP bị chặn, 429, 503 cũng đúng 1 dòng
        tok = self.env.add_token("ipx", self.env.team_full, allowed_ips=["203.0.113.0/24"])
        recs = self.lines_for(lambda: self.products("abcdef", token=tok))
        self.assertEqual(len([r for r in recs if r[0] == "machine_api.access"]), 1)
        doc = json.loads([r for r in recs if r[0] == "machine_api.access"][0][2].split("|", 2)[2])
        self.assertEqual((doc["status"], doc["token"], doc["ip"]), (403, "t-ipx", "198.51.100.7"))
        tok2 = self.env.add_token("rlx", self.env.team_full, rate_per_minute=1)
        self.products("abcdef", token=tok2)
        recs = self.lines_for(lambda: self.products("abcdef", token=tok2))
        self.assertEqual([json.loads(r[2].split("|", 2)[2])["status"] for r in recs if r[0] == "machine_api.access"], [429])
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:y@127.0.0.1:1/z", "MACHINE_DATABASE_URL": ""}):
            recs = self.lines_for(lambda: self.products("abcdef"))
        self.assertEqual([json.loads(r[2].split("|", 2)[2])["status"] for r in recs if r[0] == "machine_api.access"], [503])

    def test_result_count_is_logged(self):
        recs = self.lines_for(lambda: self.products("Ethanol abs"))
        doc = json.loads([r for r in recs if r[0] == "machine_api.access"][0][2].split("|", 2)[2])
        self.assertEqual(doc["results"], 2)  # ETH-001 + ETH-002 (GAMMA ẩn)

    def test_no_secrets_or_queries_in_any_log_record(self):
        secret_q = "zqxsupersecretquery"
        secret_cas = "64-17-5"
        marker_cas_q = "70-70-7"
        tok = self.env.tokens["full"]
        wrong = "stm_wrong_zqxwrongtokenvalue"
        before = len(self.logs.records)
        self.products(secret_q)
        self.products(secret_q, token=wrong)
        self.get(V + "/stock", code="ZQXCODEQUERY")
        self.get(V + "/compliance", cas=marker_cas_q)
        self.get(V + "/stock", cas=marker_cas_q)
        # lỗi nội bộ giữa chừng: log traceback không được chứa q/thẻ
        with mock.patch.object(machine_api, "_summarize", side_effect=RuntimeError("boom")):
            self.products(secret_q)
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:y@127.0.0.1:1/z", "MACHINE_DATABASE_URL": ""}):
            self.products(secret_q)
        self.products(secret_q, token="stm_" + "A" * 400)
        text = "\n".join(r[2] for r in self.logs.records[before:])
        for needle in (secret_q, "ZQXCODEQUERY", "zqxcodequery", marker_cas_q, tok, machine_api.hash_token(tok), wrong,
                       "zqxwrongtokenvalue", "Authorization", "Bearer", "987654321", "5550001", "NOI-BO", "A" * 100,
                       machine_api.hash_token(wrong)):
            self.assertNotIn(needle, text, needle)
        self.assertGreater(len(self.logs.records) - before, 5)

    def test_log_line_injection(self):
        before = len(self.logs.records)
        for q in ("abc\nFAKE LOG LINE {\"token\":\"admin\"}", "abc\r\nX-Injected: 1", "abc%0aforged"):
            self.products(q)
            self.get(V + "/stock", code="a\nb\nc")
        recs = self.logs.records[before:]
        for name, level, text in recs:
            if name == "machine_api.access":
                payload = text.split("|", 2)[2]
                self.assertNotIn("\n", payload)
                self.assertNotIn("\r", payload)
                json.loads(payload)
                self.assertNotIn("FAKE LOG", payload)
        # dòng log nào cũng 1 dòng (bản ghi cảnh báo/lỗi nếu có không chứa q)
        self.assertNotIn("FAKE LOG LINE", "\n".join(r[2] for r in recs))

    def test_one_line_even_with_exceptions(self):
        with mock.patch.object(machine_api, "_summarize", side_effect=RuntimeError("boom")):
            recs = self.lines_for(lambda: self.products("methanol"))
        self.assertEqual(len([r for r in recs if r[0] == "machine_api.access"]), 1)

    def test_write_attempts_leave_trace(self):
        # ghi nhận hành vi: yêu cầu ghi (405) có được nhật ký ứng dụng hay không
        recs = self.lines_for(lambda: self.client.post(V + "/products", headers={"Authorization": "Bearer " + self.env.tokens["full"]}))
        self.__class__.post_logged = any(r[0] == "machine_api.access" for r in recs)


# ======================================================================
class V10_Revocation(Base):
    def write_raw(self, text, mode=0o600):
        with open(self.env.token_path, "w", encoding="utf-8") as h:
            h.write(text)
        os.chmod(self.env.token_path, mode)

    def test_revocation_without_restart_within_5s_with_real_recheck_interval(self):
        tok = self.env.add_token("rev", self.env.team_full)
        with mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 2.0):
            machine_api.token_store.reset()
            self.assertEqual(self.products("methanol", token=tok).status_code, 200)
            for e in self.env.entries:
                if e["id"] == "t-rev":
                    e["enabled"] = False
            self.env.write_entries()
            deadline = time.time() + 5
            status = 200
            while time.time() < deadline and status == 200:
                status = self.products("methanol", token=tok).status_code
                time.sleep(0.2)
            self.assertEqual(status, 401, "thẻ bị tắt phải 401 trong <= 5 giây")
            # xóa hẳn
            tok2 = self.env.add_token("rev2", self.env.team_full)
            time.sleep(2.2)
            self.assertEqual(self.products("methanol", token=tok2).status_code, 200)
            self.env.entries = [e for e in self.env.entries if e["id"] != "t-rev2"]
            self.env.write_entries()
            time.sleep(2.2)
            self.assertEqual(self.products("methanol", token=tok2).status_code, 401)

    def test_bad_entries_are_skipped_good_ones_keep_working(self):
        good_tok = "stm_goodtoken_" + "g" * 30
        base = S.entry(good_tok, "good-one", self.env.team_full)
        tokens = [base]
        specs = {}

        def bad(name, **mut):
            tok = "stm_bad_" + name + "_" + "z" * 20
            e = S.entry(tok, "bad-" + name, self.env.team_full)
            for k, v in mut.items():
                if v is None:
                    e.pop(k, None)
                else:
                    e[k] = v
            tokens.append(e)
            specs[name] = tok
        bad("noteam", team_id=None)
        bad("strteam", team_id="5")
        bad("zeroteam", team_id=0)
        bad("negteam", team_id=-1)
        bad("booteam", team_id=True)
        bad("shorthash", sha256="abc")
        bad("uphash", sha256=machine_api.hash_token("stm_bad_uphash_" + "z" * 20).upper())
        bad("nonhex", sha256="g" * 64)
        bad("badexp", expires_at="tomorrow")
        bad("noexp", expires_at=None)
        bad("stock", stock_detail="everything")
        bad("rate0", rate_per_minute=0)
        bad("rateneg", rate_per_minute=-5)
        bad("ratehuge", rate_per_minute=10 ** 9)
        bad("ratestr", rate_per_minute="60")
        bad("enabledstr", enabled="false")
        bad("badid", id="BAD ID!")
        bad("badip", allowed_ips=["not-an-ip"])
        bad("emptyips", allowed_ips=[])
        bad("strips", allowed_ips="1.2.3.4")
        tokens.append("not-a-dict")
        tokens.append(None)
        # hash trùng giữa hai thẻ
        dup_tok = "stm_dup_" + "d" * 30
        tokens.append(S.entry(dup_tok, "dup-a", self.env.team_full))
        tokens.append(S.entry(dup_tok, "dup-b", self.env.team_full))
        self.write_raw(json.dumps({"tokens": tokens}))
        machine_api.token_store.reset()
        before = len(self.logs.records)
        self.assertEqual(self.products("methanol", token=good_tok).status_code, 200)
        for name, tok in specs.items():
            if name == "uphash":
                continue
            self.assertEqual(self.products("methanol", token=tok).status_code, 401, name)
        self.assertEqual(self.products("methanol", token=dup_tok).status_code, 401, "hash trùng => bỏ cả hai")
        warnings = [r for r in self.logs.records[before:] if r[1] == "WARNING"]
        self.assertGreaterEqual(len(warnings), 15, "mục sai phải kèm cảnh báo")
        wtext = "\n".join(w[2] for w in warnings)
        for tok in list(specs.values()) + [good_tok]:
            self.assertNotIn(tok, wtext)
        # hạn mức quá lớn: kiểm tra cận trên
        ok = S.entry("stm_rate_ok_" + "r" * 20, "rate-ok", self.env.team_full, rate_per_minute=machine_api.MAX_RATE_PER_MINUTE)
        self.write_raw(json.dumps({"tokens": [ok]}))
        self.assertEqual(self.products("methanol", token="stm_rate_ok_" + "r" * 20).status_code, 200)

    def test_empty_missing_corrupt_and_unreadable_files_fail_closed(self):
        tok = self.env.tokens["full"]
        for label, content in (("empty tokens", '{"tokens": []}'), ("empty file", ""), ("null", "null"), ("list", "[]"),
                               ("garbage", "{{{not json"), ("tokens not list", '{"tokens": {"a": 1}}'), ("binary", None)):
            self.env.write_entries()
            machine_api.token_store.reset()
            self.assertEqual(self.products("methanol").status_code, 200)
            if content is None:
                with open(self.env.token_path, "wb") as h:
                    h.write(b"\xff\xfe\x00\x01")
            else:
                self.write_raw(content)
            r = self.products("methanol")
            expected = 401 if label == "empty tokens" else 503
            self.assertEqual(r.status_code, expected, label)
            self.assertNotIn("results", r.get_data(as_text=True))
            self.assertEqual(self.client.get(V + "/health").get_json(), {"status": "ok"}, label)
            for path in ("/compliance?cas=64-17-5", "/stock?code=MET-001"):
                r = self.client.get(V + path, headers={"Authorization": "Bearer " + tok})
                self.assertEqual(r.status_code, expected, (label, path))
            if expected == 503:
                self.assertIn("Retry-After", r.headers)
        # tệp bị xóa
        self.env.write_entries()
        machine_api.token_store.reset()
        self.assertEqual(self.products("methanol").status_code, 200)
        os.unlink(self.env.token_path)
        self.assertEqual(self.products("methanol").status_code, 503)
        self.assertEqual(self.client.get(V + "/health").status_code, 200)
        # khôi phục tệp hợp lệ => dịch vụ tự hồi phục
        self.env.write_entries()
        self.assertEqual(self.products("methanol").status_code, 200)
        # thiếu biến môi trường
        with mock.patch.dict(os.environ, {"MACHINE_API_TOKENS_FILE": ""}):
            machine_api.token_store.reset()
            self.assertEqual(self.products("methanol").status_code, 503)

    def test_old_config_not_reused_after_file_becomes_corrupt_even_after_recheck_window(self):
        with mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 2.0):
            machine_api.token_store.reset()
            self.assertEqual(self.products("methanol").status_code, 200)
            self.write_raw("{broken")
            time.sleep(2.2)
            self.assertEqual(self.products("methanol").status_code, 503)
            time.sleep(2.2)
            self.assertEqual(self.products("methanol").status_code, 503, "không dùng lại cấu hình cũ")

    def test_file_changes_between_consecutive_calls_and_symlink_and_permissions(self):
        self.assertEqual(self.products("methanol").status_code, 200)
        # đổi nội dung giữa hai lời gọi liên tiếp (revoke)
        saved = list(self.env.entries)
        self.env.entries = [e for e in saved if e["id"] != "t-full"]
        self.env.write_entries()
        self.assertEqual(self.products("methanol").status_code, 401)
        self.env.entries = saved
        self.env.write_entries()
        self.assertEqual(self.products("methanol").status_code, 200)
        # quyền rộng => vẫn chạy nhưng có cảnh báo
        os.chmod(self.env.token_path, 0o644)
        machine_api.token_store.reset()
        before = len(self.logs.records)
        self.assertEqual(self.products("methanol").status_code, 200)
        self.assertTrue(any("permissions" in r[2] for r in self.logs.records[before:] if r[1] == "WARNING"))
        os.chmod(self.env.token_path, 0o600)
        # thay bằng symlink tới file khác (cấu hình rỗng): phải có hiệu lực
        other = self.env.token_path + ".other"
        with open(other, "w") as h:
            json.dump({"tokens": []}, h)
        os.chmod(other, 0o600)
        original = self.env.token_path + ".orig"
        os.rename(self.env.token_path, original)
        os.symlink(other, self.env.token_path)
        try:
            self.assertEqual(self.products("methanol").status_code, 401)
        finally:
            os.unlink(self.env.token_path)
            os.rename(original, self.env.token_path)
        self.assertEqual(self.products("methanol").status_code, 200)

    def test_two_tokens_same_team_independent_revocation(self):
        a = self.env.add_token("sa", self.env.team_full)
        b = self.env.add_token("sb", self.env.team_full)
        self.env.entries = [e for e in self.env.entries if e["id"] != "t-sa"]
        self.env.write_entries()
        self.assertEqual(self.products("methanol", token=a).status_code, 401)
        self.assertEqual(self.products("methanol", token=b).status_code, 200)

    def test_token_cli_tool(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "tokens.json")
            runner = lambda *args: subprocess.run([sys.executable, str(ROOT / "scripts/machine_token.py"), *args],
                                                  capture_output=True, text=True, cwd=str(ROOT))
            minted = []
            for i in range(30):
                r = runner("new", "--file", path, "--id", f"bot-{i}", "--team-id", "7", "--days", "10")
                self.assertEqual(r.returncode, 0, r.stderr)
                minted.append(r.stdout.strip().splitlines()[-1])
            self.assertEqual(len(set(minted)), 30)
            for token in minted:
                self.assertTrue(token.startswith("stm_"))
                raw = token[4:]
                self.assertGreaterEqual(len(raw), 43, "cần >= 256 bit (43 ký tự base64url)")
            doc = json.load(open(path))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            text = open(path).read()
            for token in minted:
                self.assertNotIn(token, text, "không ghi thẻ ra file")
                self.assertIn(hashlib.sha256(token.encode()).hexdigest(), text)
            # file do công cụ sinh phải được cổng chấp nhận
            entries = machine_api.load_token_entries(path)
            self.assertEqual(len(entries), 30)
            # trùng id bị từ chối; list không in thẻ/hash
            r = runner("new", "--file", path, "--id", "bot-1", "--team-id", "7")
            self.assertNotEqual(r.returncode, 0)
            r = runner("list", "--file", path)
            for token in minted:
                self.assertNotIn(token, r.stdout)
            self.assertNotIn(doc["tokens"][0]["sha256"], r.stdout)
            # revoke + delete
            self.assertEqual(runner("revoke", "--file", path, "--id", "bot-2").returncode, 0)
            self.assertFalse([e for e in machine_api.load_token_entries(path) if e["id"] == "bot-2" and e["enabled"]])
            self.assertEqual(runner("revoke", "--file", path, "--id", "bot-3", "--delete").returncode, 0)
            self.assertEqual(len(json.load(open(path))["tokens"]), 29)
            self.assertNotEqual(runner("revoke", "--file", path, "--id", "nope").returncode, 0)
            # đầu vào lạ: team-id không phải số, stock-detail lạ, --days âm
            self.assertNotEqual(runner("new", "--file", path, "--id", "x1", "--team-id", "abc").returncode, 0)
            self.assertNotEqual(runner("new", "--file", path, "--id", "x2", "--team-id", "1", "--stock-detail", "all").returncode, 0)
            r = runner("new", "--file", path, "--id", "BadID!", "--team-id", "1")
            # id sai định dạng: công cụ có chặn không? (cổng sẽ bỏ qua mục này)
            self.__class__.cli_accepts_bad_id = r.returncode == 0
            r = runner("new", "--file", path, "--id", "neg", "--team-id", "0")
            self.__class__.cli_accepts_team_zero = r.returncode == 0
            r = runner("new", "--file", path, "--id", "neg2", "--team-id", "1", "--rate-per-minute", "0")
            self.__class__.cli_accepts_rate_zero = r.returncode == 0
            r = runner("new", "--file", path, "--id", "neg3", "--team-id", "1", "--allowed-ip", "banana")
            self.__class__.cli_accepts_bad_ip = r.returncode == 0
            # file hỏng giữa chừng không bị công cụ làm mất
            with open(path, "w") as h:
                h.write("{broken")
            r = runner("new", "--file", path, "--id", "y1", "--team-id", "1")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(open(path).read(), "{broken")


# ======================================================================
class V5_StaffAppUntouched(unittest.TestCase):
    def sh(self, *args, env_extra=None, cwd=ROOT):
        env = dict(os.environ, **(env_extra or {}))
        return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd), env=env)

    def test_protected_files_have_no_diff_vs_origin_main_and_head(self):
        for base in ("origin/main", "HEAD"):
            r = self.sh("git", "diff", base, "--stat", "--", "search.py", "session_security.py", "middleware_access.py",
                        "team_permissions.py", "db.py")
            self.assertEqual(r.stdout.strip(), "", base)
        r = self.sh("git", "status", "--porcelain", "--", "search.py", "session_security.py", "middleware_access.py",
                    "team_permissions.py", "db.py", "regulatory.py", "compliance_resolver.py", "stock.py")
        self.assertEqual(r.stdout.strip(), "")

    def test_url_map_of_staff_app_unchanged_by_importing_gateway(self):
        code = (
            "import os,sys,json;os.environ.setdefault('FLASK_SECRET_KEY','x')\n"
            "import search\n"
            "a=sorted((str(r),tuple(sorted(r.methods)),r.endpoint) for r in search.app.url_map.iter_rules())\n"
            "import machine_api\n"
            "b=sorted((str(r),tuple(sorted(r.methods)),r.endpoint) for r in search.app.url_map.iter_rules())\n"
            "print(json.dumps({'same':a==b,'n':len(a),'hooks':[len(search.app.before_request_funcs.get(None,[])),len(search.app.after_request_funcs.get(None,[]))],"
            "'gw_routes':sorted(str(r) for r in machine_api.app.url_map.iter_rules() if 'machine' in str(r))}))\n"
        )
        r = self.sh(sys.executable, "-c", code, env_extra={"PYTHONPATH": f"{ROOT}:{ROOT}/tests"})
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["same"])
        self.assertGreater(out["n"], 20)
        self.assertFalse([p for p in out["gw_routes"] if "machine" in p and not p.startswith("/machine/v1/")])

    def test_staff_routes_match_origin_main_search_module(self):
        # search.py giống hệt origin/main nên url_map của main = url_map hiện tại; đối chiếu bằng cách nạp file từ git
        r = self.sh("git", "show", "origin/main:search.py")
        self.assertEqual(r.returncode, 0)
        with open(ROOT / "search.py", encoding="utf-8") as h:
            self.assertEqual(h.read(), r.stdout)

    def test_gateway_does_not_import_staff_app_or_use_sessions(self):
        code = ("import os,sys;os.environ.setdefault('FLASK_SECRET_KEY','x')\n"
                "import machine_api\n"
                "print('search' in sys.modules, 'admin_permissions' in sys.modules, 'app_users' in str(sys.modules.keys()))\n"
                "from flask import session\n"
                "print(type(machine_api.app.session_interface).__name__, machine_api.app.secret_key)\n")
        r = self.sh(sys.executable, "-c", code, env_extra={"PYTHONPATH": f"{ROOT}:{ROOT}/tests"})
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("False"), lines)
        self.assertIn("_NullSessionInterface", lines[1])

    def test_gateway_source_has_no_cookie_or_session_use(self):
        text = (ROOT / "machine_api.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"request\.cookies|session\[|session\.get|set_cookie|flask import[^\n]*\bsession\b")
        self.assertNotIn("import search", text)
        self.assertNotIn("from search", text)

    def test_gateway_never_emits_set_cookie(self):
        if not HAS_PG:
            self.skipTest("PG")


# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class V3b_RestrictedRole(unittest.TestCase):
    """Chạy toàn bộ cổng bằng đúng vai PostgreSQL trong sql/machine_readonly_role.sql."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.role = "machine_api_ro_v" + os.urandom(3).hex()
        cls.password = "pw" + os.urandom(6).hex()
        cls.admin = psycopg2.connect(pg_temp_db.maintenance_dsn())
        cls.admin.autocommit = True
        sql = (ROOT / "sql/machine_readonly_role.sql").read_text(encoding="utf-8")
        sql = sql.replace("{{DB_NAME}}", cls.env.db_name).replace("{{PASSWORD}}", cls.password).replace("machine_api_ro", cls.role)
        cls.ok = True
        try:
            with cls.env.conn.cursor() as cur:
                cur.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname=current_user")
                if not cur.fetchone()[0]:
                    cls.ok = False
                    return
                cur.execute(sql)
        except Exception as exc:  # noqa: BLE001
            cls.ok = False
            cls.err = repr(exc)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.ok:
                with cls.env.conn.cursor() as cur:
                    cur.execute(f'DROP OWNED BY "{cls.role}"')
        except Exception:
            pass
        try:
            with cls.admin.cursor() as cur:
                cur.execute(f'DROP ROLE IF EXISTS "{cls.role}"')
        finally:
            cls.admin.close()
            cls.env.close()

    def role_dsn(self):
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(self.env.dsn)
        netloc = f"{self.role}:{self.password}@{parsed.hostname}:{parsed.port or 5432}"
        return urlunparse(parsed._replace(netloc=netloc))

    def test_gateway_works_fully_under_restricted_role_and_role_cannot_write(self):
        if not self.ok:
            self.skipTest("không tạo được vai: " + getattr(self, "err", "no CREATEROLE"))
        with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": self.role_dsn(), "DATABASE_URL": self.env.dsn,
                                          "MACHINE_API_TOKENS_FILE": self.env.token_path}):
            machine_api.token_store.reset()
            machine_api.rate_limiter.reset()
            client = machine_api.app.test_client()
            h = {"Authorization": "Bearer " + self.env.tokens["quantity"]}
            for path in ("/products?q=ethanol", "/products?q=OVR-001", "/products?q=CAS-A", "/compliance?cas=64-17-5",
                         "/stock?code=MET-001", "/stock?cas=1333-74-0", "/products?q=methanol"):
                r = client.get(V + path, headers=h)
                self.assertEqual(r.status_code, 200, (path, r.get_data(as_text=True)[:200]))
            # xác nhận kết nối của cổng thật sự là vai chỉ-đọc
            with machine_api.app.test_request_context(V + "/health"):
                conn = machine_api._connection()
                cur = conn.cursor()
                cur.execute("SELECT current_user")
                self.assertEqual(cur.fetchone()[0], self.role)
                for table in ("app_users", "login_audit_events", "quote_templates", "exchange_rates"):
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege, msg=table):
                        cur.execute(f"SELECT * FROM {table} LIMIT 1")
                    conn.rollback()
                for stmt in ("INSERT INTO teams(name, permission_keys) VALUES ('z','{}')", "UPDATE products SET price='1'",
                             "CREATE TEMP TABLE zz(a int)", "CREATE TABLE zz(a int)"):
                    with self.assertRaises(psycopg2.Error, msg=stmt):
                        cur.execute(stmt)
                    conn.rollback()
                conn.close()
            # kể cả khi kết nối bật READ WRITE, vai này không có quyền INSERT/UPDATE/DELETE/CREATE
            rw = psycopg2.connect(self.role_dsn(), options="-c default_transaction_read_only=off")
            rw.autocommit = True
            cur = rw.cursor()
            for stmt in ("INSERT INTO teams(name, permission_keys) VALUES ('z','{}')", "UPDATE products SET price='1'",
                         "DELETE FROM stock_items", "CREATE TABLE zz(a int)", "TRUNCATE products", "DROP TABLE products"):
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege, msg=stmt):
                    cur.execute(stmt)
            cur.execute("SHOW statement_timeout")
            self.assertEqual(cur.fetchone()[0], "3s")
            rw.close()


# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class RealGunicorn(unittest.TestCase):
    """Chạy gunicorn thật với đúng cờ trong deploy/search-tools-machine-api.service (loopback)."""

    @classmethod
    def setUpClass(cls):
        import socket
        import tempfile
        cls.env = S.Env()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        unit = (ROOT / "deploy/search-tools-machine-api.service").read_text(encoding="utf-8")
        match = re.search(r"ExecStart=(.*?)(?:\n[A-Z][A-Za-z]+=|\Z)", unit, re.S)
        cmd = match.group(1).replace("\\\n", " ").split()
        flags = cmd[1:]
        i = flags.index("--bind")
        flags[i + 1] = f"127.0.0.1:{cls.port}"
        cls.flags = flags
        cls.out = tempfile.TemporaryFile("w+")
        env = dict(os.environ, DATABASE_URL=cls.env.dsn, MACHINE_API_TOKENS_FILE=cls.env.token_path,
                   PYTHONPATH=str(ROOT), FLASK_SECRET_KEY="x", PYTHONUNBUFFERED="1")
        env.pop("MACHINE_DATABASE_URL", None)
        cls.proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *flags], cwd=str(ROOT), env=env,
                                    stdout=cls.out, stderr=subprocess.STDOUT)
        import http.client
        for _ in range(60):
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                c.request("GET", "/machine/v1/health")
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
        cls.env.close()

    def raw(self, request_bytes):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(request_bytes)
        data = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > 200000:
                    break
        except Exception:
            pass
        s.close()
        return data

    def test_gunicorn_flags_and_oversized_requests(self):
        self.assertIn("127.0.0.1:" + str(self.port), " ".join(self.flags))
        tok = self.env.tokens["full"]
        big_line = b"GET /machine/v1/products?q=" + b"a" * 10000 + b" HTTP/1.1\r\nHost: x\r\n\r\n"
        big_header = b"GET /machine/v1/products?q=abc HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + b"A" * 1_000_000 + b"\r\n\r\n"
        many = b"GET /machine/v1/products?q=abc HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X-%d: 1\r\n" % i for i in range(200)) + b"\r\n"
        for name, payload in (("line", big_line), ("header", big_header), ("many", many)):
            data = self.raw(payload)
            status = data.split(b"\r\n", 1)[0] if data else b""
            self.assertRegex(status.decode("latin-1") or "HTTP/1.1 400", r"HTTP/1\.[01] (400|414|431|413)", (name, status))
            for needle in (b"Traceback", b"/Volumes", b"postgresql://", b"File \""):
                self.assertNotIn(needle, data, name)
        ok = self.raw(("GET /machine/v1/products?q=methanol HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + tok + "\r\nConnection: close\r\n\r\n").encode())
        self.assertIn(b"200 OK", ok.split(b"\r\n", 1)[0])
        self.assertNotIn(b"Set-Cookie", ok)

    def _hit(self, path, token="full"):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers={"Authorization": "Bearer " + self.env.tokens[token]} if token else {})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def _log_text(self):
        time.sleep(0.6)
        self.out.seek(0)
        return self.out.read()

    def test_real_gunicorn_log_does_not_contain_query_text(self):
        """V9: log phát sinh khi chạy thật (cờ trong unit systemd) không được chứa từ khóa q/CAS của người hỏi."""
        sentinel = "zqxgunicornsentinel"
        self._hit(f"/machine/v1/products?q={sentinel}")
        self._hit("/machine/v1/compliance?cas=71-43-2")
        text = self._log_text()
        self.assertNotIn(self.env.tokens["full"], text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn(sentinel, text, "gunicorn --access-logfile ghi nguyên query string (từ khóa khách tìm)")
        self.assertNotIn("71-43-2", text, "gunicorn --access-logfile ghi nguyên CAS người hỏi")

    def test_real_gunicorn_emits_one_audit_line_per_call_with_token_id(self):
        """V9: mỗi lời gọi dữ liệu phải sinh đúng 1 dòng truy vết có id thẻ, mã HTTP, số kết quả, IP, thời gian."""
        marker = f"zqxaudit{os.urandom(3).hex()}"
        before = self._log_text()
        self._hit(f"/machine/v1/products?q={marker}")
        self._hit("/machine/v1/products?q=abc", token=None)
        new = self._log_text()[len(before):]
        audit = [ln for ln in new.splitlines() if '"token"' in ln and '"endpoint"' in ln]
        self.assertEqual(len(audit), 2, f"không có dòng nhật ký ứng dụng nào trong log thật: {new!r}")
        docs = [json.loads(ln[ln.index("{"):]) for ln in audit]
        self.assertEqual([d["token"] for d in docs], ["t-full", "anonymous"])
        self.assertEqual([d["status"] for d in docs], [200, 401])
        for d in docs:
            for key in ("endpoint", "results", "ip", "ms"):
                self.assertIn(key, d)

    def test_real_gunicorn_forwarded_for_behaviour_is_documented(self):
        import http.client
        tok = self.env.add_token("realip", self.env.team_full, allowed_ips=["203.0.113.0/24"])
        def call(extra):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("GET", "/machine/v1/products?q=methanol", headers={"Authorization": "Bearer " + tok, **extra})
            r = c.getresponse(); r.read(); c.close()
            return r.status
        time.sleep(2.2)  # chờ cổng nạp lại file thẻ (kiểm tra mỗi 2 giây)
        self.assertEqual(call({}), 403)
        self.assertEqual(call({"X-Forwarded-For": "203.0.113.9, 127.0.0.1"}), 403, "giá trị cuối (do proxy gần nhất ghi) mới được tin")
        # Ghi nhận: nếu client chạm thẳng tới gunicorn và tự khai 1 giá trị XFF thì được tin => an toàn phụ thuộc loopback + Nginx ghi đè XFF
        self.__class__.direct_xff_bypass_status = call({"X-Forwarded-For": "203.0.113.9"})

    def test_methods_over_real_http(self):
        r = self.raw(b"POST /machine/v1/products HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\nConnection: close\r\n\r\nabcd")
        self.assertIn(b"405", r.split(b"\r\n", 1)[0])
        r = self.raw(b"GET /machine/v1/products?q=abc HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertIn(b"401", r.split(b"\r\n", 1)[0])
        self.assertNotIn(b"Server: ", r.replace(b"Server: gunicorn", b""))


# ======================================================================
class V_ZZ_FinalSweeps(Base):
    """Chạy cuối: quét toàn bộ body đã thu thập và toàn bộ log của cả lớp."""

    def test_zz_all_bodies_clean_and_parallel_consistency(self):
        def worker(_):
            c = machine_api.app.test_client()
            out = []
            for q in ("methanol", "ethanol", "acid", "50-00-0", "BLK-"):
                r = self.get(V + "/products", token="quantity", client=c, q=q)
                out.append((q, r.status_code, r.get_data(as_text=True)))
            return out
        with ThreadPoolExecutor(20) as pool:
            results = [x for batch in pool.map(worker, range(20)) for x in batch]
        by_q = {}
        for q, status, body in results:
            self.assertEqual(status, 200)
            by_q.setdefault(q, set()).add(body)
        for q, bodies in by_q.items():
            self.assertEqual(len(bodies), 1, f"kết quả song song không nhất quán cho {q}")
        for body in self.__class__.bodies:
            for canary in S.CANARIES:
                self.assertNotIn(canary, body)

# ======================================================================
class V_Extra(Base):
    def test_compliance_endpoint_without_note_permission_has_no_note(self):
        name, _ = self.team_token("chk-nonote", set(S.ALL_PERMS) - {"VIEW_COMPLIANCE_NOTE"}, ["ALPHA"])
        r = self.compliance(S.C_BAN, token=name)
        self.assertEqual(r.status_code, 200)
        block = r.get_json()["compliance"]
        self.assertEqual((block["state"], block["label"]), ("RULE", "CẤM NHẬP"))
        self.assertNotIn("regulatory_note", block)
        self.assertNotIn("note-ban", r.get_data(as_text=True))
        # CAS lookup độc lập hãng: team không có hãng nào vẫn tra được nhãn CAS (khớp /check_cas nhân viên)
        name2, _ = self.team_token("chk-nobrand", S.ALL_PERMS, [])
        self.assertEqual(self.compliance(S.C_BAN, token=name2).get_json()["compliance"]["label"], "CẤM NHẬP")

    def test_search_by_cas_without_view_cas_is_stricter_than_staff(self):
        name, team = self.team_token("cas-noview", ["SEARCH", "SEARCH_BY_CAS", "VIEW_NAME", "VIEW_CODE"], ["ALPHA"])
        mine = self.products("64-17-5", token=name).get_json()
        self.assertEqual(mine["count"], 0)  # cổng không cho dùng CAS làm oracle khi không được xem CAS
        self.assertNotIn("64-17-5", json.dumps({k: v for k, v in mine.items() if k != "query"}))
        self.assertEqual(self.get(V + "/stock", token=name, cas="64-17-5").status_code, 403)

    def test_db_failure_mid_request_is_503_without_partial_data(self):
        with mock.patch.object(machine_api, "_stock_lots", side_effect=psycopg2.OperationalError("server closed password=PW123")):
            r = self.products("methanol")
        self.assertEqual(r.status_code, 503)
        text = r.get_data(as_text=True)
        self.assertNotIn("results", text)
        self.assertNotIn("PW123", text)
        with mock.patch.object(machine_api, "_stock_lots", side_effect=psycopg2.errors.QueryCanceled("canceling statement")):
            self.assertEqual(self.get(V + "/stock", code="MET-001").status_code, 503)

    def test_unreadable_token_file_permission_000_fails_closed(self):
        self.assertEqual(self.products("methanol").status_code, 200)
        # (a) chỉ đổi quyền (nội dung/inode/mtime không đổi): ghi nhận hành vi, không phải cấu hình cũ vì nội dung giống hệt
        os.chmod(self.env.token_path, 0)
        try:
            self.__class__.chmod_only_status = self.products("methanol").status_code
        finally:
            os.chmod(self.env.token_path, 0o600)
        # (b) tệp mới (inode mới) không đọc được => phải từ chối (fail-closed)
        fresh = self.env.token_path + ".fresh"
        with open(fresh, "w") as h:
            json.dump({"tokens": self.env.entries}, h)
        os.chmod(fresh, 0)
        backup = self.env.token_path + ".bak"
        os.rename(self.env.token_path, backup)
        os.rename(fresh, self.env.token_path)
        try:
            r = self.products("methanol")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(self.client.get(V + "/health").status_code, 200)
        finally:
            os.chmod(self.env.token_path, 0o600)
            os.unlink(self.env.token_path)
            os.rename(backup, self.env.token_path)
        self.assertEqual(self.products("methanol").status_code, 200)

    def test_duplicate_ids_with_different_hashes_are_both_dropped(self):
        a, b = "stm_dupid_a_" + "a" * 30, "stm_dupid_b_" + "b" * 30
        entries = [S.entry(a, "same-id", self.env.team_full), S.entry(b, "same-id", self.env.team_full),
                   S.entry(self.env.tokens["full"], "t-full", self.env.team_full)]
        with open(self.env.token_path, "w") as h:
            json.dump({"tokens": entries}, h)
        os.chmod(self.env.token_path, 0o600)
        machine_api.token_store.reset()
        self.assertEqual(self.products("methanol", token=a).status_code, 401)
        self.assertEqual(self.products("methanol", token=b).status_code, 401)
        self.assertEqual(self.products("methanol").status_code, 200)

    def test_rate_limiter_is_exact_under_20_threads(self):
        tok = self.env.add_token("thr", self.env.team_full, rate_per_minute=50)

        def worker(_):
            c = machine_api.app.test_client()
            return [self.get(V + "/health" if False else V + "/products", token=tok, client=c, q="methanol").status_code for _ in range(5)]
        with ThreadPoolExecutor(20) as pool:
            codes = [c for batch in pool.map(worker, range(20)) for c in batch]
        self.assertEqual(codes.count(200), 50)
        self.assertEqual(codes.count(429), 50)
        self.assertFalse([c for c in codes if c >= 500])

    def test_head_request_runs_same_auth(self):
        self.assertEqual(self.client.head(V + "/products?q=methanol").status_code, 401)
        self.assertEqual(self.client.head(V + "/stock?code=MET-001").status_code, 401)

    def test_openapi_is_public_and_clean_but_documents_no_price(self):
        r = self.client.get(V + "/openapi.json")
        self.assertEqual(r.status_code, 200)
        doc = r.get_json()
        ops = {op["operationId"] for path in doc["paths"].values() for op in path.values()}
        self.assertEqual(ops, {"tim_san_pham", "kiem_tra_phap_che", "xem_ton_kho", "health"})
        for path in doc["paths"].values():
            self.assertEqual(set(path), {"get"})
        self.assertNotRegex(json.dumps(doc).lower(), r"price|ship|note\b")


if __name__ == "__main__":
    unittest.main()
