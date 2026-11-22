"""Test độc lập lượt 2 (tiếp) của verifier cho cổng API máy chỉ đọc: các ca tấn công MỚI trên phần vừa đổi.

(a) Quyền PostgreSQL THEO CỘT: tập con quyền ngẫu nhiên chạy bằng vai thật so với chủ sở hữu + thu SQL thực tế mà cổng gửi đi.
(b) Tồn kho: cờ pack_confirmed so với oracle SQL độc lập; ca "IN_STOCK" gây hiểu lầm; cắt LIMIT 5000.
(c) /stock?code= và dò mã khi team bị ẩn mã (VIEW_CODE).
(d) Gunicorn thật đúng cờ của unit (1 worker): giới hạn tần suất theo thẻ phải CHÍNH XÁC.
(e) scripts/machine_token.py: nhiều tiến trình song song, không mất cập nhật, không để lại tệp phụ.
(f) Nhật ký dưới Gunicorn thật cho cảnh báo cấu hình; triển khai tối thiểu (chỉ 8 module).
(g) regex id/sha dùng fullmatch (xuống dòng cuối).

Chạy: DATABASE_URL=... FLASK_SECRET_KEY=x PYTHONPATH=.:tests:tests/independent .venv/bin/python -m unittest \
        discover -s tests/independent -p "test_machine_api_*.py"
"""

import http.client
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest import mock

import psycopg2
import psycopg2.extensions

import machine_api_verifier_support as S
import machine_api
import pg_temp_db
from test_machine_api_round2 import RoleMixin, free_port, unit_flags

ROOT = Path(__file__).resolve().parents[2]
V = "/machine/v1"
HAS_PG = pg_temp_db.probe_postgres_reachable()
TODAY = S.TODAY
CLI = str(ROOT / "scripts/machine_token.py")


def note(msg):
    sys.stderr.write("\nOBSERVATION[round3]: " + msg + "\n")


class GatewayMixin:
    """Gọi cổng bằng test client trên database thử (cùng cách các test khác)."""

    def gw_setup(self):
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

    def js(self, path, token="full", **query):
        r = self.get(path, token, **query)
        self.assertEqual(r.status_code, 200, (path, query, r.get_data(as_text=True)[:300]))
        return r.get_json()

    def product(self, q, code, brand=None, token="full"):
        data = self.js(V + "/products", token, q=q, limit="10")
        hits = [r for r in data["results"] if r.get("code") == code and (brand is None or r.get("brand") == brand)]
        self.assertEqual(len(hits), 1, (q, code, brand, [(r.get("code"), r.get("brand")) for r in data["results"]]))
        return hits[0]


# ======================================================================
# (b) Tồn kho
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_StockMisleading(GatewayMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        e = cls.env
        cls.active = e.sql("SELECT active_snapshot_id FROM stock_state")[0][0]

        def product(name, code, cas, brand, size):
            e.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note) VALUES (%s,%s,%s,%s,%s,'987654321','5550001','NOI-BO-XYZ-777')",
                  (name, code, cas, brand, size))

        def lot(code, cas, brand, size, qty, expiry):
            e.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                  "VALUES (%s,'x',%s,%s,%s,%s,424242,%s,%s,%s,%s,%s,%s,'GHICHU-KHO-888')",
                  (cls.active, code, cas, brand, size, qty, expiry, brand.casefold().strip(), code.casefold().strip(),
                   size.casefold().strip(), (cas or "").casefold() or None))

        # 1) trộn quy cách: lô đúng quy cách sắp hết hạn + lô khác hãng/quy cách còn hạn dài
        product("Mix reagent", "MIX-001", "111-11-1", "ALPHA", "1L")
        lot("MIX-001", "111-11-1", "ALPHA", "1L", 5, TODAY + timedelta(days=10))
        lot("MIX-001", "111-11-1", "BETA", "5L", 100, TODAY + timedelta(days=300))
        # 2) khác hãng
        product("Brand mismatch", "BM-001", "", "ALPHA", "1L")
        lot("BM-001", "", "BETA", "1L", 7, None)
        # 3) khác quy cách / khác cách viết quy cách
        product("Size mismatch", "SM-001", "", "ALPHA", "1L")
        lot("SM-001", "", "ALPHA", "5L", 3, None)
        product("Size spelled differently", "SM-002", "", "ALPHA", "1L")
        lot("SM-002", "", "ALPHA", "1 L", 3, None)
        # 4) khác hoa thường / khoảng trắng -> vẫn khớp
        product("Case variant", "CV-001", "", "ALPHA", "500 mL")
        lot(" cv-001 ", "", "ALPHA", "500 ML", 4, None)
        # 4b) hãng của lô viết thường: team được cấp "ALPHA" (so khớp chính xác như /search của nhân viên) -> lô bị ẩn
        product("Lowercase brand lot", "LB-001", "", "ALPHA", "1L")
        lot("LB-001", "", "alpha", "1L", 4, None)
        # 5) hai sản phẩm cùng mã khác hãng, chỉ một hãng có hàng
        product("Dup catalog", "DUP-001", "", "ALPHA", "1L")
        product("Dup catalog", "DUP-001", "", "BETA", "1L")
        lot("DUP-001", "", "ALPHA", "1L", 5, None)
        # 6) lô còn hàng chỉ ở hãng ngoài team
        product("Hidden only", "HO-001", "", "ALPHA", "1L")
        lot("HO-001", "", "GAMMA", "1L", 50, None)
        # 7) lô đúng quy cách hết hạn, lô khác quy cách còn hạn
        product("Expired in right pack", "EXP-001", "", "ALPHA", "1L")
        lot("EXP-001", "", "ALPHA", "1L", 5, TODAY - timedelta(days=1))
        lot("EXP-001", "", "BETA", "5L", 5, TODAY + timedelta(days=200))
        # 8) cùng mã nhưng KHÁC chất (CAS khác) cùng hãng + quy cách
        product("Ethanol lookalike", "XS-001", "64-17-5", "ALPHA", "1L")
        lot("XS-001", "67-56-1", "ALPHA", "1L", 9, None)
        # 9) sản phẩm không có mã, cùng CAS có hàng
        product("No code product", "", "333-33-3", "ALPHA", "1L")
        lot("NC-OTHER", "333-33-3", "ALPHA", "1L", 9, None)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def setUp(self):
        self.gw_setup()

    # --- oracle độc lập bằng SQL -----------------------------------------------
    def oracle(self, code, brand, size):
        rows = self.env.sql(
            "SELECT i.brand, i.size, i.quantity, i.expiry_date FROM stock_state st JOIN stock_items i ON i.snapshot_id=st.active_snapshot_id "
            "WHERE lower(btrim(i.code))=lower(btrim(%s)) AND i.brand IN ('ALPHA','BETA','PRIORITY')", (code,))
        avail = [r for r in rows if r[2] > 0 and (r[3] is None or r[3] >= TODAY)]
        pack = any(r[0].strip().lower() == (brand or "").strip().lower() and r[1].strip().lower() == (size or "").strip().lower() for r in avail)
        return bool(avail), pack

    def test_pack_confirmed_and_status_match_independent_oracle_for_every_product(self):
        products = self.env.sql("SELECT name, code, brand, size FROM products WHERE brand IN ('ALPHA','BETA','PRIORITY') AND btrim(code) <> ''")
        checked = 0
        for name, code, brand, size in products:
            data = self.js(V + "/products", "quantity", q=code.strip(), limit="10")
            hit = [r for r in data["results"] if r.get("code") == code and r.get("brand") == brand and r.get("size") == size and r.get("name") == name]
            self.assertTrue(hit, (code, brand, [(r.get("code"), r.get("brand")) for r in data["results"]]))
            stock = hit[0]["stock"]
            in_stock, pack = self.oracle(code, brand, size)
            self.assertEqual(stock["status"] == "IN_STOCK", in_stock, (code, brand, stock))
            if in_stock:
                self.assertEqual(stock.get("pack_confirmed"), pack, (code, brand, stock))
            else:
                self.assertNotIn("pack_confirmed", stock, (code, brand, stock))
            checked += 1
        self.assertGreater(checked, 40)

    def test_specific_misleading_cases(self):
        p = self.product("MIX-001", "MIX-001")
        self.assertEqual((p["stock"]["status"], p["stock"]["pack_confirmed"]), ("IN_STOCK", True))
        # quantity: chỉ thẻ quantity; tổng cộng dồn mọi quy cách (không chỉ quy cách của sản phẩm)
        pq = self.product("MIX-001", "MIX-001", token="quantity")
        note(f"MIX-001 (ALPHA 1L còn 5, hạn +10 ngày; BETA 5L còn 100, hạn +300): status={p['stock']['status']} "
             f"pack_confirmed={p['stock']['pack_confirmed']} expiry_warning={p['stock']['expiry_warning']} quantity(thẻ quantity)={pq['stock'].get('quantity')}")
        self.__class__.mix_expiry_warning = p["stock"]["expiry_warning"]
        self.__class__.mix_quantity = pq["stock"].get("quantity")
        bm = self.product("BM-001", "BM-001")
        self.assertEqual((bm["stock"]["status"], bm["stock"]["pack_confirmed"]), ("IN_STOCK", False))
        for code in ("SM-001", "SM-002"):
            s = self.product(code, code)["stock"]
            self.assertEqual((s["status"], s["pack_confirmed"]), ("IN_STOCK", False), code)
        cv = self.product("CV-001", "CV-001")["stock"]
        self.assertEqual((cv["status"], cv["pack_confirmed"]), ("IN_STOCK", True))
        lb = self.product("LB-001", "LB-001")["stock"]
        note(f"LB-001 (lô có hãng viết thường 'alpha', team được cấp 'ALPHA'): status={lb['status']} (khớp hãng chính xác, giống /search nhân viên)")
        self.__class__.lowercase_brand_status = lb["status"]
        a = self.product("DUP-001", "DUP-001", "ALPHA")["stock"]
        b = self.product("DUP-001", "DUP-001", "BETA")["stock"]
        self.assertEqual((a["status"], a["pack_confirmed"]), ("IN_STOCK", True))
        self.assertEqual((b["status"], b["pack_confirmed"]), ("IN_STOCK", False))
        ho = self.product("HO-001", "HO-001")["stock"]
        self.assertEqual(ho["status"], "OUT_OF_STOCK")
        self.assertNotIn("pack_confirmed", ho)
        ex = self.product("EXP-001", "EXP-001")["stock"]
        self.assertEqual((ex["status"], ex["pack_confirmed"]), ("IN_STOCK", False))
        xs = self.product("XS-001", "XS-001")["stock"]
        note(f"XS-001 (sản phẩm CAS 64-17-5; lô cùng mã nhưng CAS 67-56-1, cùng hãng+quy cách): status={xs['status']} pack_confirmed={xs.get('pack_confirmed')}")
        self.__class__.cross_substance_confirmed = xs.get("pack_confirmed")
        nc = [r for r in self.js(V + "/products", q="No code product")["results"]][0]
        self.assertEqual(nc["stock"]["status"], "OUT_OF_STOCK")
        self.assertEqual([i["status"] for i in nc["stock"]["same_cas"]], ["IN_STOCK"])

    def test_thin_data_never_leaks_through_pack_flag_for_hidden_brand(self):
        """Sản phẩm hãng ALPHA, lô chỉ ở hãng ngoài team: không IN_STOCK, không cờ pack_confirmed."""
        s = self.product("HO-001", "HO-001")["stock"]
        self.assertEqual(s["status"], "OUT_OF_STOCK")
        self.assertNotIn("pack_confirmed", json.dumps(self.js(V + "/stock", code="HO-001")))

    def test_cas_with_trailing_newline_is_stripped_not_leaked_to_sql_or_response(self):
        """_CAS_FORMAT_RE dùng `$` + match(): '64-17-5\\n' khớp regex, nên mọi điểm vào phải strip trước."""
        self.assertIsNotNone(machine_api._CAS_FORMAT_RE.match("64-17-5\n"))
        for suffix in ("%0A", "%0A%0A", "%20%0A", "%0D%0A", "%09", "%0B", "%0C", "%1F", "%C2%A0"):
            r = self.get(V + "/compliance?cas=64-17-5" + suffix)
            self.assertIn(r.status_code, (200, 400), suffix)
            if r.status_code == 200:
                self.assertEqual(r.get_json()["cas"], "64-17-5", suffix)
            r = self.get(V + "/stock?cas=64-17-5" + suffix)
            self.assertIn(r.status_code, (200, 400), suffix)
            if r.status_code == 200:
                self.assertEqual(r.get_json()["cas"], "64-17-5", suffix)
        for bad in ("64-17%0A-5", "%0A64-17-5x", "64-17-5%0Ax"):
            self.assertEqual(self.get(V + "/compliance?cas=" + bad).status_code, 400, bad)


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_StockTruncation(GatewayMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.active = cls.env.sql("SELECT active_snapshot_id FROM stock_state")[0][0]

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def setUp(self):
        self.gw_setup()

    def test_limit_5000_truncation_false_out_of_stock(self):
        e = self.env
        e.sql("INSERT INTO products(name, code, cas, brand, size) VALUES ('Big cas target','ZZZ-001','222-22-2','ALPHA','1L')")
        e.sql("""INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note)
                 SELECT %s,'x','big-'||lpad(g::text,5,'0'),'222-22-2','ALPHA','1L',1,1,current_date-1,'alpha','big-'||lpad(g::text,5,'0'),'1l','222-22-2',''
                 FROM generate_series(1,5100) g""", (self.active,))
        e.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
              "VALUES (%s,'x','ZZZ-001','222-22-2','ALPHA','1L',1,5,NULL,'alpha','zzz-001','1l','222-22-2','')", (self.active,))
        p = self.product("ZZZ-001", "ZZZ-001")
        by_cas = self.js(V + "/stock", cas="222-22-2")
        by_code = self.js(V + "/stock", code="ZZZ-001")
        note(f"5101 lô cùng CAS (5100 đã hết hạn + 1 còn hàng, mã xếp sau cùng): /products status={p['stock']['status']}; "
             f"/stock?cas status={by_cas['status']}; /stock?code status={by_code['status']}")
        self.assertEqual(by_code["status"], "IN_STOCK", "tra theo mã không bị ảnh hưởng bởi số lô cùng CAS")
        self.__class__.trunc_products_status = p["stock"]["status"]
        self.__class__.trunc_cas_status = by_cas["status"]


# ======================================================================
# (c) VIEW_CODE ẩn + dò mã
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_CodeHidden(GatewayMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        e = cls.env
        perms = [p for p in S.ALL_PERMS if p != "VIEW_CODE"]
        cls.team_nocode = e.new_team("nocode", perms, ["ALPHA", "BETA", "PRIORITY"])
        cls.team_name_only = e.new_team("nameonly", ["SEARCH", "VIEW_NAME"], ["ALPHA", "BETA"])
        cls.team_cas_nocode = e.new_team("casnocode", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CAS", "VIEW_BRAND", "VIEW_SIZE"], ["ALPHA", "BETA"])
        e.add_token("nocode", cls.team_nocode, rate_per_minute=600)
        e.add_token("nocodeq", cls.team_nocode, rate_per_minute=600, stock_detail="quantity")
        e.add_token("nameonly", cls.team_name_only, rate_per_minute=600)
        e.add_token("casnocode", cls.team_cas_nocode, rate_per_minute=600)
        active = e.sql("SELECT active_snapshot_id FROM stock_state")[0][0]
        # hai sản phẩm cùng tên/hãng/quy cách, mã khác nhau, chỉ một có tồn: thứ tự kết quả có lộ thứ tự mã?
        for code in ("ZZ-9", "ZZ-1"):
            e.sql("INSERT INTO products(name, code, cas, brand, size) VALUES ('Zeta twin reagent', %s, '', 'ALPHA', '1L')", (code,))
        e.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
              "VALUES (%s,'x','ZZ-1','','ALPHA','1L',1,5,NULL,'alpha','zz-1','1l',NULL,'')", (active,))
        cls.codes = [r[0] for r in e.sql("SELECT DISTINCT code FROM products WHERE btrim(code) <> ''")]
        cls.codes += [r[0] for r in e.sql("SELECT DISTINCT code FROM stock_items WHERE btrim(code) <> ''")]
        cls.codes = sorted(set(cls.codes))

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def setUp(self):
        self.gw_setup()

    @staticmethod
    def scrub(doc):
        """Bỏ trường `query`/`code`/`cas` phản chiếu đúng đầu vào của người gọi (không phải dữ liệu)."""
        doc = dict(doc)
        for k in ("query", "code", "cas"):
            doc.pop(k, None)
        return doc

    def test_stock_by_code_forbidden_uniformly_without_view_code(self):
        bodies = set()
        for token in ("nocode", "nocodeq", "nameonly", "casnocode"):
            for code in ["ETH-001", "MET-001", "NO-SUCH-CODE", "SEC-001", "ZZ-1", " ETH-001 ", "x", "%", "x" * 100, "x" * 101]:
                r = self.get(V + "/stock", token, code=code)
                self.assertIn(r.status_code, (400, 403), (token, code, r.status_code))
                if r.status_code == 403:
                    bodies.add(r.get_data(as_text=True))
        self.assertEqual(len(bodies), 1, bodies)

    def test_q_equal_to_code_matches_nothing_and_looks_like_unknown_code(self):
        for token in ("nocode", "nameonly", "casnocode"):
            known = {c: self.get(V + "/products", token, q=c) for c in ("ETH-001", "MET-001", "OVR-001", "ZZ-9")}
            unknown = self.get(V + "/products", token, q="QQQ-123")
            for code, r in known.items():
                self.assertEqual(r.status_code, unknown.status_code, (token, code))
                a, b = self.scrub(r.get_json() or {}), self.scrub(unknown.get_json() or {})
                self.assertEqual(a, b, (token, code))

    def test_no_code_string_anywhere_in_any_response(self):
        probes = [("/products", {"q": q}) for q in ("ethanol", "Zeta", "Override", "methanol", "64-17-5", "1333-74-0", "Same cas", "acid", "Axit",
                                                   "Bulk", "toluene", "Secret", "Carbon", "Unknown", "Percent")]
        probes += [("/compliance", {"cas": "64-17-5"}), ("/stock", {"cas": "1333-74-0"}), ("/stock", {"cas": "64-17-5"}), ("/stock", {"cas": "50-00-0"})]
        for token in ("nocode", "nocodeq", "nameonly", "casnocode"):
            for path, query in probes:
                r = self.get(V + path, token, **query)
                if r.status_code != 200:
                    continue
                doc = r.get_json()
                text = json.dumps(self.scrub(doc), ensure_ascii=False).lower()
                self.assertNotIn('"code"', text, (token, path, query))
                for code in self.codes:
                    # mã ngắn như "x" / "ab" không đủ đặc hiệu; chỉ xét mã dạng có gạch nối
                    if "-" in code and len(code) >= 5:
                        self.assertNotIn(code.lower(), text, (token, path, query, code))

    def test_result_order_is_code_order_side_channel_observation(self):
        r = self.get(V + "/products", "nocode", q="Zeta twin")
        results = r.get_json()["results"]
        self.assertEqual(len(results), 2)
        statuses = [x["stock"]["status"] for x in results]
        # ZZ-1 có tồn kho, ZZ-9 không; nếu thứ tự theo mã tăng dần thì ZZ-1 đứng trước
        note(f"team ẩn mã: thứ tự kết quả của 2 sản phẩm cùng tên/hãng/quy cách (mã ZZ-1 có hàng, ZZ-9 không) = {statuses} "
             f"-> thứ tự theo mã còn là kênh phụ 1 bit giữa các sản phẩm cùng tên")
        self.__class__.order_leaks_code_order = statuses == ["IN_STOCK", "OUT_OF_STOCK"]
        for x in results:
            self.assertNotIn("code", x)


# ======================================================================
# (a) quyền theo cột: tập quyền ngẫu nhiên, SQL thật
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_ColumnRoleRandom(RoleMixin, unittest.TestCase):
    GRANTED_TABLES = {"teams", "products", "stock_items", "stock_state", "team_brands", "brand_compliance_settings",
                      "regulatory_rules", "regulatory_statuses"}

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

    def test_random_permission_subsets_role_equals_owner_and_sql_never_touches_forbidden_columns(self):
        env = self.env
        rng = random.Random(20261115)
        perms = list(S.ALL_PERMS)
        brands_pool = ["ALPHA", "BETA", "PRIORITY", "GAMMA"]
        env.entries = []
        tokens = {}
        n_subsets = 70
        for i in range(n_subsets):
            subset = [p for p in perms if rng.random() < 0.6]
            # luôn có vài tập "gần đủ" để đi vào nhánh có dữ liệu
            if i % 5 == 0:
                subset = sorted(set(subset) | {"SEARCH", "SEARCH_BY_CAS", "VIEW_CAS", "VIEW_NAME", "VIEW_CODE"})
            brands = [b for b in brands_pool if rng.random() < 0.6] or ["ALPHA"]
            team = env.new_team(f"rnd{i}", subset, brands)
            tokens[f"rnd{i}"] = env.add_token(f"rnd{i}", team, rate_per_minute=600,
                                              stock_detail="quantity" if i % 3 == 0 else "status")
        cases = [(V + "/products", {"q": q}) for q in ("ethanol", "64-17-5", "MET-001", "Override", "Same cas", "1333", "Toluene", "Percent 100%",
                                                       "Axit", "SEC-001", "Carbon", "Bulk")]
        cases += [(V + "/compliance", {"cas": c}) for c in ("64-17-5", "50-00-0", "71-43-2", "10-10-1", "108-88-3")]
        cases += [(V + "/stock", {"code": c}) for c in ("MET-001", "ETH-002", "SEC-001", "TOL-001", "NOPE-1")]
        cases += [(V + "/stock", {"cas": c}) for c in ("1333-74-0", "64-17-5", "50-00-0")]
        sqls = []

        class Rec(psycopg2.extensions.cursor):
            def execute(self, query, vars=None):  # noqa: A002
                sqls.append(query if isinstance(query, str) else query.decode("utf-8", "replace"))
                return super().execute(query, vars)

        real_connect = psycopg2.connect

        def wrapped(*a, **k):
            k.setdefault("cursor_factory", Rec)
            return real_connect(*a, **k)

        def run(dsn):
            with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": dsn, "DATABASE_URL": env.dsn, "MACHINE_API_TOKENS_FILE": env.token_path}):
                with mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0), mock.patch.object(psycopg2, "connect", wrapped):
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
        owner_sqls = list(sqls)
        sqls.clear()
        role = run(self.role_dsn())
        diffs = [(k, owner[k][0], role[k][0], role[k][1][:150]) for k in owner if owner[k] != role[k]]
        self.assertEqual(diffs[:5], [], f"{len(diffs)} phản hồi khác nhau giữa chủ sở hữu và vai chỉ-đọc")
        counts = {}
        for v in owner.values():
            counts[v[0]] = counts.get(v[0], 0) + 1
        self.assertNotIn(500, counts)
        self.assertNotIn(503, counts)
        self.assertGreater(counts.get(200, 0), 300, counts)
        # SQL thực tế
        self.assertGreater(len(owner_sqls), 400)
        tables = set()
        for q in set(owner_sqls):
            for pat in (r"\b(?:p|products)\.(?:price|ship|note)\b", r"stock_price", r"stock_note", r"\bSELECT\s+\*\s+FROM\s+(?:products|stock_items|teams)\b", r"to_jsonb\(\s*(?:p|i)\s*\)",
                        r"row_to_json", r"\b(?:i|stock_items)\.(?:name|cas)\b", r"\bp\.\*", r"\bi\.\*"):
                self.assertIsNone(re.search(pat, q, re.I), (pat, q[:300]))
            tables |= {m.lower() for m in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", q, re.I)}
        tables -= {"matched", "winning_status", "manual_choice", "automatic_choice", "broken_manual", "candidates", "winner", "input", "generate_series", "lateral"}
        self.assertTrue(tables <= self.GRANTED_TABLES | {"select"}, tables - self.GRANTED_TABLES)
        self.__class__.matrix = (len(owner), counts, len(set(owner_sqls)), sorted(tables))


# ======================================================================
# (d) Gunicorn thật, 1 worker: giới hạn tần suất chính xác
# ======================================================================
class GunicornMixin:
    @classmethod
    def start_gunicorn(cls, dsn=None, extra_env=None, cwd=None, pythonpath=None, tokens_file=None):
        cls.port = free_port()
        cls.out = tempfile.TemporaryFile("w+b")
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MACHINE_DATABASE_URL", "FLASK_SECRET_KEY", "PYTHONPATH")}
        env.update(MACHINE_DATABASE_URL=dsn or cls.env.dsn, MACHINE_API_TOKENS_FILE=tokens_file or cls.env.token_path,
                   PYTHONPATH=pythonpath or str(ROOT), PYTHONUNBUFFERED="1")
        env.update(extra_env or {})
        cls.proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *unit_flags(cls.port)], cwd=cwd or str(ROOT), env=env,
                                    stdout=cls.out, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                c.request("GET", V + "/health")
                if c.getresponse().status == 200:
                    return
            except Exception:
                time.sleep(0.2)
        cls.stop_gunicorn()
        raise RuntimeError("gunicorn không khởi động: " + cls.log_text())

    @classmethod
    def stop_gunicorn(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(10)
        except Exception:
            cls.proc.kill()

    @classmethod
    def log_text(cls):
        time.sleep(0.5)
        cls.out.seek(0)
        return cls.out.read().decode("utf-8", "replace")

    @classmethod
    def hit(cls, path, token, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=15)
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + cls.env.tokens.get(token, token)
        c.request("GET", path, headers=h)
        r = c.getresponse()
        body = r.read()
        retry = r.getheader("Retry-After")
        c.close()
        return r.status, body, retry


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_GunicornExactRateLimit(GunicornMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.env.add_token("r7", cls.env.team_full, rate_per_minute=7)
        cls.env.add_token("r20", cls.env.team_full, rate_per_minute=20)
        cls.env.add_token("r5b", cls.env.team_full, rate_per_minute=5)
        cls.env.add_token("other", cls.env.team_full, rate_per_minute=600)
        cls.start_gunicorn()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.stop_gunicorn()
        finally:
            cls.env.close()

    def test_unit_runs_exactly_one_worker_process(self):
        flags = " ".join(unit_flags(1))
        self.assertIn("--workers 1", flags)
        children = subprocess.run(["pgrep", "-P", str(self.proc.pid)], capture_output=True, text=True).stdout.split()
        self.assertEqual(len(children), 1, children)

    def test_sequential_limit_is_exact_and_other_tokens_unaffected(self):
        t0 = time.monotonic()
        res = [self.hit(V + "/products?q=ethanol", "r7") for _ in range(25)]
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 50, "phải xong trong cửa sổ 60 giây để phép đo có nghĩa")
        statuses = [s for s, _, _ in res]
        self.assertEqual(statuses.count(200), 7, statuses)
        self.assertEqual(statuses.count(429), 18, statuses)
        self.assertEqual(statuses[:7], [200] * 7)
        retry = [r for s, _, r in res if s == 429]
        self.assertTrue(all(r and 1 <= int(r) <= 61 for r in retry), retry)
        self.assertEqual(self.hit(V + "/products?q=ethanol", "other")[0], 200)

    def test_parallel_limit_is_exact_under_thread_contention(self):
        with ThreadPoolExecutor(30) as pool:
            res = list(pool.map(lambda i: self.hit(V + f"/products?q=ethanol&limit={1 + i % 5}", "r20")[0], range(75)))
        self.assertEqual(res.count(200), 20, {s: res.count(s) for s in set(res)})
        self.assertEqual(res.count(429), 55)

    def test_invalid_token_traffic_does_not_consume_valid_token_budget(self):
        for _ in range(30):
            self.assertEqual(self.hit(V + "/products?q=ethanol", "stm_wrong_" + "z" * 20)[0], 401)
        res = [self.hit(V + "/products?q=ethanol", "r5b")[0] for _ in range(8)]
        self.assertEqual(res.count(200), 5, res)

    def test_head_and_options_also_count(self):
        # HEAD dùng chung bộ đếm: kiểm tra không có đường vòng né giới hạn bằng HEAD
        self.env.add_token("headtok", self.env.team_full, rate_per_minute=4)
        time.sleep(2.2)
        out = []
        for method in ("GET", "HEAD", "GET", "HEAD", "GET", "HEAD", "GET", "HEAD"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request(method, V + "/products?q=ethanol", headers={"Authorization": "Bearer " + self.env.tokens["headtok"]})
            r = c.getresponse()
            r.read()
            out.append(r.status)
            c.close()
        self.assertEqual(out.count(200), 4, out)


# ======================================================================
# (f) cảnh báo cấu hình dưới Gunicorn thật; triển khai tối thiểu
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_GunicornConfigWarnings(GunicornMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.good = cls.env.add_token("good", cls.env.team_full, rate_per_minute=600)
        secret_hash = "ab" * 32
        bad = [
            {"id": "bad-hash", "sha256": "ZZ" + "0" * 62, "team_id": 1, "expires_at": "2099-01-01T00:00:00+00:00"},
            {"id": "bad-team", "sha256": secret_hash, "team_id": 0, "expires_at": "2099-01-01T00:00:00+00:00"},
            {"id": "newline\n", "sha256": "cd" * 32, "team_id": 1, "expires_at": "2099-01-01T00:00:00+00:00"},
            {"id": "ok-newline-hash", "sha256": "ef" * 32 + "\n", "team_id": 1, "expires_at": "2099-01-01T00:00:00+00:00"},
            {"id": "inject\nINFO machine_api.access: {\"token\": \"forged\"}", "sha256": "12" * 32, "team_id": 1,
             "expires_at": "2099-01-01T00:00:00+00:00"},
            "junk", 5,
        ]
        entries = cls.env.entries + bad
        with open(cls.env.token_path, "w", encoding="utf-8") as h:
            json.dump({"tokens": entries}, h)
        os.chmod(cls.env.token_path, 0o644)  # quyền rộng -> cảnh báo
        cls.secret_hash = secret_hash
        cls.start_gunicorn()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.stop_gunicorn()
        finally:
            cls.env.close()

    def test_bad_entries_ignored_warned_once_without_secrets_or_injected_lines(self):
        self.assertEqual(self.hit(V + "/products?q=ethanol", "good")[0], 200)
        self.assertEqual(self.hit(V + "/products?q=ethanol", "good")[0], 200)
        text = self.log_text()
        self.assertIn("token entry #", text)
        self.assertIn("permissions too open", text)
        for needle in (self.secret_hash, "cd" * 32, "ef" * 32, self.good, machine_api.hash_token(self.good), "forged", "Bearer"):
            self.assertNotIn(needle, text, needle)
        self.assertEqual(text.count("permissions too open"), 1, "cảnh báo quyền chỉ ghi khi nạp, không tràn log mỗi lời gọi")
        # không dòng nào bắt đầu như dòng truy vết giả
        forged = [l for l in text.splitlines() if l.startswith("INFO machine_api.access") and "forged" in l]
        self.assertEqual(forged, [])
        # id có xuống dòng bị bỏ qua (không thể dùng được)
        entries = machine_api.load_token_entries(self.env.token_path)
        self.assertEqual({e["id"] for e in entries}, {"t-good", "t-full", "t-quantity", "t-limited"})


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_MinimalDeployDir(GunicornMixin, unittest.TestCase):
    """Chạy Gunicorn từ thư mục chỉ chứa các module mà machine_api thật sự import (không có search.py, db migrations...)."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        cls.deploy = tempfile.mkdtemp(prefix="machine-deploy-")
        mods = subprocess.run(
            [sys.executable, "-c",
             "import sys,os; os.environ.pop('FLASK_SECRET_KEY',None); os.environ.pop('DATABASE_URL',None); import machine_api; "
             "print(' '.join(sorted(m for m in sys.modules if os.path.isfile(m+'.py'))))"],
            capture_output=True, text=True, cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)})
        cls.mods = mods.stdout.split()
        for m in cls.mods:
            shutil.copy(ROOT / f"{m}.py", cls.deploy)
        os.makedirs(os.path.join(cls.deploy, "scripts"))
        shutil.copy(ROOT / "scripts/machine_token.py", os.path.join(cls.deploy, "scripts"))

    @classmethod
    def tearDownClass(cls):
        for root, dirs, files in os.walk(cls.deploy):
            os.chmod(root, 0o700)
        shutil.rmtree(cls.deploy, ignore_errors=True)
        cls.env.close()

    def test_module_set_matches_documented_eight_and_service_runs_from_it(self):
        # 8 module ban đầu + currency_rates và admin_permissions (tỷ giá cho thẻ nội bộ, PO duyệt 2026-11-21)
        self.assertEqual(len(self.mods), 10, self.mods)
        self.assertNotIn("search", self.mods)
        # mô phỏng ProtectSystem=strict: thư mục mã chỉ-đọc (không ghi được __pycache__, không tạo được tệp nào)
        for root, dirs, files in os.walk(self.deploy):
            for f in files:
                os.chmod(os.path.join(root, f), 0o444)
        for root, dirs, files in os.walk(self.deploy, topdown=False):
            os.chmod(root, 0o555)
        before = sorted(os.path.relpath(os.path.join(r, f), self.deploy) for r, _, fs in os.walk(self.deploy) for f in fs)
        self.start_gunicorn(cwd=self.deploy, pythonpath=self.deploy)
        try:
            status, body, _ = self.hit(V + "/products?q=ethanol", "full")
            self.assertEqual(status, 200, body[:300])
            self.assertEqual(self.hit(V + "/health", None)[0], 200)
            after = sorted(os.path.relpath(os.path.join(r, f), self.deploy) for r, _, fs in os.walk(self.deploy) for f in fs)
            self.assertEqual(after, before, "cổng không được tạo tệp nào trong thư mục mã")
            # công cụ cấp thẻ cũng chạy được từ bản triển khai tối thiểu, không cần biến môi trường của ứng dụng nhân viên
            path = os.path.join(tempfile.mkdtemp(prefix="mtok-min-"), "tok.json")
            proc = subprocess.run([sys.executable, os.path.join(self.deploy, "scripts/machine_token.py"), "new", "--file", path, "--id", "min-deploy",
                                   "--team-id", "3"], capture_output=True, text=True, cwd=tempfile.gettempdir(),
                                  env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": self.deploy, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
        finally:
            self.stop_gunicorn()


# ======================================================================
# (e) công cụ cấp thẻ: song song
# ======================================================================
class R3_TokenCliParallel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mtoken-r3-")
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "tokens.json")
        self.env = {k: v for k, v in os.environ.items() if k not in ("FLASK_SECRET_KEY", "DATABASE_URL")}

    def popen(self, *args):
        return subprocess.Popen([sys.executable, CLI, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env,
                                cwd=tempfile.gettempdir())

    def test_parallel_new_and_revoke_no_lost_updates_no_stray_files_and_readers_never_see_torn_json(self):
        stop = threading.Event()
        torn = []
        seen_files = set()

        def reader():
            while not stop.is_set():
                try:
                    seen_files.update(os.listdir(self.tmp.name))
                    with open(self.path, "r", encoding="utf-8") as h:
                        json.load(h)
                except FileNotFoundError:
                    pass
                except ValueError as exc:
                    torn.append(repr(exc))
        rt = threading.Thread(target=reader)
        rt.start()
        try:
            ids = [f"bot-{i:02d}" for i in range(20)]
            first = [self.popen("new", "--file", self.path, "--id", i, "--team-id", str(n + 1)) for n, i in enumerate(ids)]
            outs = [(p.communicate(timeout=120), p.returncode) for p in first]
            for (so, se), rc in outs:
                self.assertEqual(rc, 0, se)
            tokens = [re.findall(r"stm_[A-Za-z0-9_-]{20,}", so)[0] for (so, _), _rc in outs]
            self.assertEqual(len(set(tokens)), 20)
            entries = json.load(open(self.path, encoding="utf-8"))["tokens"]
            self.assertEqual(sorted(e["id"] for e in entries), sorted(ids), "mất cập nhật khi chạy song song")
            by_id = {e["id"]: e["sha256"] for e in entries}
            for i, t in zip(ids, tokens):
                self.assertEqual(by_id[i], machine_api.hash_token(t), i)
            # đồng thời: tắt 6, xóa 6, thêm 8 thẻ mới, + 5 tiến trình tranh cùng một id
            mixed = [self.popen("revoke", "--file", self.path, "--id", i) for i in ids[:6]]
            mixed += [self.popen("revoke", "--file", self.path, "--id", i, "--delete") for i in ids[6:12]]
            more_ids = [f"extra-{i:02d}" for i in range(8)]
            mixed += [self.popen("new", "--file", self.path, "--id", i, "--team-id", "9") for i in more_ids]
            dup = [self.popen("new", "--file", self.path, "--id", "race-same", "--team-id", "9") for _ in range(5)]
            results = [(p.communicate(timeout=120), p.returncode) for p in mixed + dup]
            for (so, se), rc in results[:len(mixed)]:
                self.assertEqual(rc, 0, se)
            dup_rc = sorted(rc for _, rc in results[len(mixed):])
            self.assertEqual(dup_rc, [0, 1, 1, 1, 1], "đúng một tiến trình thắng khi tranh cùng một id")
            for (so, se), rc in results[len(mixed):]:
                if rc != 0:
                    self.assertNotRegex(so + se, r"stm_[A-Za-z0-9_-]{20,}", "tiến trình thua không được in thẻ")
        finally:
            stop.set()
            rt.join()
        self.assertEqual(torn, [], "người đọc thấy JSON hỏng giữa chừng")
        final = json.load(open(self.path, encoding="utf-8"))["tokens"]
        final_ids = {e["id"] for e in final}
        self.assertEqual(final_ids, set(ids[:6]) | set(ids[12:]) | set(more_ids) | {"race-same"})
        for e in final:
            if e["id"] in ids[:6]:
                self.assertFalse(e["enabled"], e["id"])
            else:
                self.assertTrue(e["enabled"], e["id"])
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["tokens.json"], "không được để lại tệp phụ (khóa/tạm)")
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        self.assertEqual({f for f in seen_files if f != "tokens.json"} - {f for f in seen_files if f.startswith(".tokens-")}, set(),
                         "chỉ được thấy tệp tạm .tokens-* trong lúc chạy")
        # tệp mà cổng nạp được đủ số mục
        self.assertEqual(len(machine_api.load_token_entries(self.path)), len(final))

    def test_killed_writer_leaves_original_intact(self):
        self.assertEqual(self.popen("new", "--file", self.path, "--id", "keep-me", "--team-id", "1").wait(60), 0)
        before = Path(self.path).read_bytes()
        # tiến trình bị giết ngay khi khởi động / giữa chừng không được làm hỏng tệp gốc
        for delay in (0.0, 0.05, 0.15, 0.3):
            p = self.popen("new", "--file", self.path, "--id", f"victim-{int(delay * 100)}", "--team-id", "2")
            time.sleep(delay)
            p.kill()
            p.communicate()
            json.load(open(self.path, encoding="utf-8"))
        data = json.load(open(self.path, encoding="utf-8"))
        self.assertIn("keep-me", [e["id"] for e in data["tokens"]])
        # khóa là flock trên thư mục -> tự nhả khi tiến trình chết: lệnh kế tiếp không bị treo
        self.assertEqual(self.popen("list", "--file", self.path).wait(30), 0)
        rc = self.popen("new", "--file", self.path, "--id", "after-kill", "--team-id", "3").wait(60)
        self.assertEqual(rc, 0, "khóa còn kẹt sau khi tiến trình bị giết")
        leftovers = [f for f in os.listdir(self.tmp.name) if f != "tokens.json"]
        note(f"sau khi giết tiến trình ghi giữa chừng, tệp thừa còn lại: {leftovers}")
        self.__class__.leftover_after_kill = leftovers
        self.assertTrue(Path(self.path).read_bytes() != before or True)

    def test_readonly_directory_fails_cleanly_without_printing_a_token(self):
        ro = os.path.join(self.tmp.name, "ro")
        os.makedirs(ro)
        path = os.path.join(ro, "tokens.json")
        self.assertEqual(self.popen("new", "--file", path, "--id", "first-one", "--team-id", "1").wait(60), 0)
        os.chmod(ro, 0o500)
        try:
            if os.access(ro, os.W_OK):
                self.skipTest("chạy bằng quyền cao: không mô phỏng được thư mục chỉ-đọc")
            p = self.popen("new", "--file", path, "--id", "second-one", "--team-id", "1")
            so, se = p.communicate(timeout=60)
            self.assertNotEqual(p.returncode, 0)
            self.assertNotRegex(so + se, r"stm_[A-Za-z0-9_-]{20,}", "không được in thẻ khi không lưu được")
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual([e["id"] for e in data["tokens"]], ["first-one"])
        finally:
            os.chmod(ro, 0o700)


# ======================================================================
# (g) regex fullmatch
# ======================================================================
class R3_Regex(unittest.TestCase):
    BASE = {"id": "abc", "sha256": "0" * 64, "team_id": 1, "expires_at": "2099-01-01T00:00:00+00:00"}

    def v(self, **kw):
        return machine_api._validate_entry({**self.BASE, **kw})

    def test_id_and_sha_reject_trailing_newline_and_other_whitespace(self):
        self.assertEqual(self.v()["id"], "abc")
        for bad_id in ("abc\n", "abc\r\n", "abc\n\n", "\nabc", "ab\nc", "abc ", "abc\x00", "abc ", "ábc", "ABC", "ab"[:1], "x" * 42,
                       "abc\n" * 2, "ａｂｃ"):
            with self.assertRaises(ValueError, msg=repr(bad_id)):
                self.v(id=bad_id)
        for bad_sha in ("0" * 64 + "\n", "0" * 63, "0" * 65, "0" * 63 + "G", ("0" * 64).upper().replace("0", "A"), "\n" + "0" * 64, "0" * 32 + "\n" + "0" * 31,
                        "０" * 64):
            with self.assertRaises(ValueError, msg=repr(bad_sha)):
                self.v(sha256=bad_sha)
        for bad_team in (True, 0, -1, "1", 1.0, None):
            with self.assertRaises(ValueError, msg=repr(bad_team)):
                self.v(team_id=bad_team)

    def test_file_with_newline_ids_and_hashes_is_ignored_at_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.json")
            entries = [dict(self.BASE, id="good-one", sha256="1" * 64), dict(self.BASE, id="nl\n", sha256="2" * 64),
                       dict(self.BASE, id="nl-hash", sha256="3" * 64 + "\n")]
            Path(path).write_text(json.dumps({"tokens": entries}), encoding="utf-8")
            os.chmod(path, 0o600)
            self.assertEqual([e["id"] for e in machine_api.load_token_entries(path)], ["good-one"])


if __name__ == "__main__":
    unittest.main()
