"""LƯỢT 4 của VERIFIER độc lập cho thẻ NỘI BỘ có giá (specs/machine-api-internal-price/VERIFICATION.md, mục 'sửa lần 3').
Mục tiêu: kiểm bản sửa 'ORDER BY trong mỗi nhánh truy vấn lô tồn kho' (hàm _stock_lots):
  U1 - thẻ khách so với bản cũ 351cf1a (same_cas từng byte) với nhiều mã/nhiều hãng/lọc hãng của team, > 5000 lô cùng CAS, đổi thứ tự vật lý
       bằng CLUSTER (3 kiểu), biên cắt 5000 có chứa lô của mã đang hỏi;
  U2 - thẻ nội bộ: kết quả không đổi khi đổi thứ tự vật lý; same_cas nội bộ khớp oracle; vượt 50.000 lô: tập giữ lại tất định;
  U3 - hiệu năng truy vấn UNION ALL + ORDER BY trong nhánh với 300.000 lô (EXPLAIN ANALYZE + đo HTTP), so với bản cũ.

Chạy lại:
  export DATABASE_URL=postgresql://searchlocal:searchlocal@localhost:5432/products_local
  PYTHONPATH=.:tests:tests/independent .venv/bin/python -m unittest test_machine_api_internal_round4
"""

import json
import random
import shutil
import statistics
import time
import unittest

import machine_api
from test_machine_api_internal_independent import HAS_PG, V, Base
from test_machine_api_internal_round2 import active_snapshot, add_lots, oracle_quantity, strict_json
from test_machine_api_internal_round3 import OldRunner, obs

def same_cas_of(body):
    """same_cas / same_cas_more theo TỪNG sản phẩm (khóa = mã), vì thứ tự kết quả đã đổi có chủ ý (I7: hãng xen đều)."""
    def groups(node):
        return ([(x.get("code"), x.get("brand"), x.get("size"), x.get("status")) for x in node["same_cas"]], node.get("same_cas_more"))
    if isinstance(body, dict) and "results" in body:
        return {r.get("code"): groups(r["stock"]) for r in body["results"] if "same_cas" in r.get("stock", {})}
    return None   # /stock không có same_cas: so ở bộ T2/T2b của lượt 3


def recluster(env, name, cols):
    env.sql(f"CREATE INDEX IF NOT EXISTS {name} ON stock_items({cols})")
    old = env.conn.autocommit
    env.conn.autocommit = True
    with env.conn.cursor() as cur:
        cur.execute(f"CLUSTER stock_items USING {name}")
        cur.execute("ANALYZE stock_items")
    env.conn.autocommit = old


LAYOUTS = [("code_desc", "code_norm DESC, brand_norm DESC"), ("qty_expiry", "quantity DESC, expiry_date DESC NULLS FIRST, code_norm"),
           ("brand_desc_size", "brand_norm DESC, size_norm DESC, code_norm DESC")]


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class U1_CustomerVsOld(OldRunner, Base):
    """Nhiều mã/nhiều hãng, hãng ngoài team xen lẫn, 7.200 lô cùng một CAS của mã khác (xáo trộn khi nhập) + CAS thứ hai 5.600 lô theo thứ tự
    ngược; các mã đang hỏi nằm đầu / giữa / cuối thứ tự sắp xếp."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        rnd = random.Random(11)
        snap = active_snapshot(env)
        cls.cas1, cls.cas2 = "97000-00-1", "97001-00-1"
        brands = ["ALPHA", "BETA", "CATO", "GAMMA"]   # GAMMA nằm ngoài team_full (CATO, ALPHA, BETA) và team_limited (CATO)
        rows = []   # (code, brand, size, qty, days, cas)
        for c in range(144):                           # 144 mã x 50 lô = 7200 lô
            code, brand = f"K1-{c:03d}", brands[c % 4]
            for g in range(1, 51):
                rows.append((code, brand, "1L", 1 + (c + g) % 4, 100 + g, cls.cas1))
        rnd.shuffle(rows)
        rows2 = []
        for c in range(112, 0, -1):                    # 112 mã x 50 lô = 5600 lô, mã giảm dần
            code, brand = f"K2-{c:03d}", brands[(c + 1) % 4]
            for g in range(1, 51):
                rows2.append((code, brand, "2L", 2, 100 + g, cls.cas2))
        with env.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "VALUES (%s,'x',%s,%s,%s,%s,5,%s,CURRENT_DATE+%s,%s,%s,%s,%s,'')",
                [(snap, r[0], r[5], r[1], r[2], r[3], r[4], r[1].lower(), r[0].lower(), r[2].lower(), r[5]) for r in rows + rows2])
        # mã đang hỏi: một đầu (A0-OWN), một giữa (K1-072X), một cuối (Z9-OWN); hãng đủ loại; có lô cùng CAS1
        for code, brand, cas in (("A0-OWN", "ALPHA", cls.cas1), ("K1-072X", "BETA", cls.cas1), ("Z9-OWN", "CATO", cls.cas1),
                                 ("A0-TWO", "ALPHA", cls.cas2), ("Z9-TWO", "CATO", cls.cas2), ("G0-HID", "GAMMA", cls.cas1)):
            env.raw_product("Own " + code, code, brand, "10", "1.3", cas=cas)
            add_lots(env, code, cas, brand, "1L", 6, qty_sql="2", expiry_sql="CURRENT_DATE + 200 + g")
        env.add_token("cqty", env.team_full, stock_detail="quantity")
        cls.base_entries = list(env.entries)      # setUp() ghi lại base_entries: phải có cả thẻ cqty (lượt 3 từng thiếu bước này)
        env.sql("ANALYZE stock_items")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def reqs(self):
        r = []
        for tok in ("cfull", "climited", "cqty"):
            t = self.env.tokens[tok]
            for code in ("A0-OWN", "K1-072X", "Z9-OWN", "A0-TWO", "Z9-TWO", "G0-HID"):
                r.append({"path": V + "/stock", "token": t, "query": {"code": code}})
                r.append({"path": V + "/products", "token": t, "query": {"q": code}})
            r.append({"path": V + "/products", "token": t, "query": {"q": "Own"}})            # nhiều sản phẩm một truy vấn
            r.append({"path": V + "/products", "token": t, "query": {"q": self.cas1}})
            r.append({"path": V + "/products", "token": t, "query": {"q": self.cas2}})
        return r

    def check_against_old(self, label):
        reqs = self.reqs()
        old, new = self.run_old(reqs), self.run_new(reqs)
        diffs = []
        for req, o, n in zip(reqs, old, new):
            self.assertEqual(o["status"], n["status"], req)
            self.assertEqual(n["status"], 200, req)
            if same_cas_of(o["body"]) != same_cas_of(n["body"]):
                diffs.append((req["path"], req["query"], same_cas_of(o["body"]), same_cas_of(n["body"])))
        obs("U1", label=label, calls=len(reqs), same_cas_diffs=len(diffs), first=diffs[:2])
        self.assertEqual(diffs, [], diffs[:2])
        return new

    def test_same_cas_matches_old_in_all_physical_layouts(self):
        base = self.check_against_old("initial")
        for name, cols in LAYOUTS:
            recluster(self.env, "u1_" + name, cols)
            again = self.check_against_old(name)
            # chính bản mới: toàn bộ phản hồi giống hệt trước khi CLUSTER (thẻ khách)
            self.assertEqual(again, base, f"phản hồi thẻ khách đổi sau CLUSTER {name}")

    def test_own_stock_exact_for_every_asked_code_new(self):
        """Mã đang hỏi luôn đúng trạng thái/số lượng (oracle SQL) dù có hơn 7.200 lô cùng CAS của mã khác."""
        t = self.env.tokens["cqty"]
        bad = []
        for code in ("A0-OWN", "K1-072X", "Z9-OWN", "A0-TWO", "Z9-TWO"):
            machine_api.rate_limiter.reset()
            body = self.client.get(V + "/products", headers={"Authorization": "Bearer " + t}, query_string={"q": code},
                                   environ_overrides={"REMOTE_ADDR": "198.51.100.7"}).get_json()
            self.assertIn("results", body, body)
            item = [r for r in body["results"] if r["code"] == code][0]
            want = int(oracle_quantity(self.env, code))
            if item["stock"]["status"] != "IN_STOCK" or item["stock"].get("quantity") != want:
                bad.append((code, item["stock"].get("status"), item["stock"].get("quantity"), want))
        self.assertEqual(bad, [])


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class U1b_BoundaryWindowIncludesOwn(OldRunner, Base):
    """Biên: bản cũ cắt 5000 dòng CHUNG (gồm cả lô của mã hỏi nằm đầu thứ tự); bản mới cắt 5000 dòng cùng CAS KHÔNG gồm lô mã hỏi. Khi mã hỏi
    sắp ĐẦU và có nhiều lô, cửa sổ same_cas của bản mới lớn hơn bản cũ đúng bằng số lô của mã hỏi. Kiểm: 5 nhóm đầu của thẻ khách vẫn khớp."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        cls.cas = "97100-00-1"
        snap = active_snapshot(env)
        env.raw_product("Early own", "A0-FIRST", "ALPHA", "1", "1", cas=cls.cas)
        add_lots(env, "A0-FIRST", cls.cas, "ALPHA", "1L", 300, qty_sql="1", expiry_sql="CURRENT_DATE + 100 + g")
        with env.conn.cursor() as cur:
            # nhóm B-xx: 49 lô mỗi nhóm; 5.300 lô khác (108 nhóm) => ranh giới 5000 cắt giữa nhóm
            for c in range(108):
                cur.execute(
                    "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s,'x',%s,%s,'ALPHA','1L',5,1,CURRENT_DATE+100+g,'alpha',lower(%s),'1l',%s,'' FROM generate_series(1,49) g",
                    (snap, f"B-{c:03d}", cls.cas, f"B-{c:03d}", cls.cas))
        env.sql("ANALYZE stock_items")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_first_five_groups_equal_old(self):
        reqs = [{"path": V + "/products", "token": self.env.tokens["cfull"], "query": {"q": "A0-FIRST"}},
                {"path": V + "/stock", "token": self.env.tokens["cfull"], "query": {"code": "A0-FIRST"}}]
        old, new = self.run_old(reqs), self.run_new(reqs)
        so, sn = [same_cas_of(x["body"]) for x in old], [same_cas_of(x["body"]) for x in new]
        obs("U1b", old=so[0], new=sn[0])
        self.assertEqual(so, sn)

    def test_internal_same_cas_ten_groups_vs_oracle(self):
        item = strict_json(self.products("A0-FIRST"))["results"][0]
        got = [x["code"] for x in item["stock"]["same_cas"]]
        want = [r[0] for r in self.env.sql("SELECT DISTINCT code FROM stock_items WHERE cas_norm=%s AND code<>'A0-FIRST' ORDER BY code LIMIT 10", (self.cas,))]
        self.assertEqual(got, want)
        self.assertTrue(item["stock"]["same_cas_more"])
        for x in item["stock"]["same_cas"]:       # số lượng của 10 nhóm đầu phải đủ (49), không bị cắt giữa chừng
            self.assertEqual(x["quantity"], 49, x)


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class U2_InternalDeterministic(Base):
    """Thẻ nội bộ: kết quả đúng và không phụ thuộc thứ tự vật lý, kể cả vượt 5.000 lô cùng CAS và vượt 50.000 lô của chính mã hỏi."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        snap = active_snapshot(env)
        cls.cas = "97200-00-1"
        brands = ["ALPHA", "BETA", "CATO", "GAMMA", "DELTA"]
        env.raw_product("Mid own", "M5-OWN", "BETA", "1", "1", cas=cls.cas)
        add_lots(env, "M5-OWN", cls.cas, "BETA", "1L", 10, qty_sql="3", expiry_sql="CURRENT_DATE + 100 + g")
        with env.conn.cursor() as cur:
            for c in range(80):                                   # 80 mã x 100 lô = 8000 lô cùng CAS, nhiều hãng
                code = f"N-{c:03d}"
                b = brands[c % 5]
                cur.execute(
                    "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s,'x',%s,%s,%s,'1L',5,1+(g%%4),CURRENT_DATE+100+g,%s,%s,'1l',%s,'' FROM generate_series(1,100) g",
                    (snap, code, cls.cas, b, b.lower(), code.lower(), cls.cas))
            # 50.050 lô của MỘT mã, 5 hãng x 10.010 hạn khác nhau
            env.raw_product("Huge own", "H-HUGE", "BETA", "1", "1", cas="97201-00-1")
            for i, b in enumerate(brands):
                cur.execute(
                    "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s,'x','H-HUGE','97201-00-1',%s,'1L',5,1+(g%%3),CURRENT_DATE+100+g,%s,'h-huge','1l','97201-00-1','' FROM generate_series(1,10010) g",
                    (snap, b, b.lower()))
        env.sql("ANALYZE stock_items")

    def snapshot(self):
        out = {}
        for q in ("M5-OWN", "H-HUGE"):
            out[q] = strict_json(self.products(q))
        out["stock_cas"] = strict_json(self.get(V + "/stock", cas=self.cas))
        out["stock_huge"] = strict_json(self.get(V + "/stock", code="H-HUGE"))
        return out

    def test_internal_identical_across_physical_layouts_and_matches_oracle(self):
        base = self.snapshot()
        item = base["M5-OWN"]["results"][0]
        got = [x["code"] for x in item["stock"]["same_cas"]]
        want = [r[0] for r in self.env.sql("SELECT code FROM (SELECT DISTINCT brand_norm, code FROM stock_items WHERE cas_norm=%s AND code<>'M5-OWN') s ORDER BY brand_norm, lower(code) LIMIT 10", (self.cas,))]
        self.assertEqual(got, want)
        self.assertEqual(item["stock"]["quantity"], int(oracle_quantity(self.env, "M5-OWN")))
        # H-HUGE: 50.050 lô > trần 50.000 => tập giữ lại phải là 50.000 lô ĐẦU theo (hãng, mã, quy cách, hạn)
        keep = self.env.sql(
            "SELECT COALESCE(SUM(quantity) FILTER (WHERE quantity>0 AND (expiry_date IS NULL OR expiry_date>=CURRENT_DATE)),0), COUNT(*) FROM ("
            "SELECT quantity, expiry_date FROM stock_items i JOIN stock_state s ON i.snapshot_id=s.active_snapshot_id WHERE i.code_norm='h-huge' "
            "ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST LIMIT 50000) z")[0]
        huge = base["H-HUGE"]["results"][0]["stock"]
        obs("U2", huge_keys=sorted(huge.keys()), quantity=huge.get("quantity"), lots_total=huge.get("lots_total"), oracle_first_50000=[int(keep[0]), int(keep[1])])
        self.assertEqual(huge["quantity"], int(keep[0]))
        self.assertEqual(huge["lots_total"], 50000)
        for name, cols in LAYOUTS:
            recluster(self.env, "u2_" + name, cols)
            again = self.snapshot()
            self.assertEqual(again, base, f"thẻ nội bộ đổi sau CLUSTER {name}")

    def test_lots_list_sorted_by_brand_code_size_expiry(self):
        item = strict_json(self.get(V + "/stock", code="M5-OWN"))
        lots = item["items"][0]["lots"]
        exp = [l["expiry_date"] for l in lots]
        self.assertEqual(exp, sorted(exp))


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class U3_Performance(OldRunner, Base):
    """300.000 lô (60.000 mã), plus CAS nóng 52.000 lô; đo EXPLAIN ANALYZE truy vấn mới (UNION ALL + ORDER BY trong nhánh) và HTTP, so bản cũ."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        snap = active_snapshot(env)
        with env.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s,'x','P'||c,'1'||(c/20)||'-00-1',(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%%6],'1L',100,1+g,CURRENT_DATE+g+50,"
                "lower((ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%%6]),lower('P'||c),'1l','1'||(c/20)||'-00-1','' "
                "FROM generate_series(1,60000) c, generate_series(1,5) g", (snap,))
            cur.execute(
                "INSERT INTO products(name,code,cas,brand,size,price,ship,note,source_brand) "
                "SELECT 'Perf product '||c,'P'||c,'1'||(c/20)||'-00-1',(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%6],'1L','10','1.3','',"
                "(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%6] FROM generate_series(1,60000) c")
            # CAS nóng: 520 mã x 100 lô = 52.000 lô cùng CAS
            cur.execute(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s,'x','HOT'||c,'98000-00-1','CATO','1L',100,1,CURRENT_DATE+g+50,'cato',lower('HOT'||c),'1l','98000-00-1','' "
                "FROM generate_series(1,520) c, generate_series(1,100) g", (snap,))
            cur.execute("INSERT INTO products(name,code,cas,brand,size,price,ship,note,source_brand) VALUES ('Hot product','HOTP','98000-00-1','CATO','1L','10','1.3','','CATO')")
            cur.execute(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s,'x','HOTP','98000-00-1','CATO','1L',100,1,CURRENT_DATE+g+50,'cato','hotp','1l','98000-00-1','' FROM generate_series(1,30) g", (snap,))
        env.sql("ANALYZE stock_items")
        env.sql("ANALYZE products")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def explain(self, codes, cas_values, order_in_branch):
        order = " ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST" if order_in_branch else ""
        cols = "i.code, i.code_norm, i.cas_norm, i.brand, i.size, i.quantity, i.expiry_date, i.brand_norm, i.size_norm, i.stock_price_vnd, i.stock_note"
        inner = f"SELECT {cols} FROM stock_state st JOIN stock_items i ON i.snapshot_id = st.active_snapshot_id WHERE st.singleton = TRUE AND "
        sql = ("EXPLAIN (ANALYZE, FORMAT TEXT) SELECT t.code, t.quantity FROM ((" + inner + "i.code_norm = ANY(%s)" + order + " LIMIT 50000) UNION ALL ("
               + inner + "i.cas_norm = ANY(%s) AND NOT (i.code_norm = ANY(%s))" + order + " LIMIT 5000)) t ORDER BY t.brand_norm, t.code_norm, t.size_norm, t.expiry_date NULLS LAST")
        plan = "\n".join(r[0] for r in self.env.sql(sql, (codes, cas_values, codes)))
        return float(plan.rsplit("Execution Time:", 1)[1].split("ms")[0]), plan

    def test_explain_with_and_without_branch_order(self):
        cases = {"one code, cas 20 codes": (["p777"], ["1038-00-1"]),
                 "50 codes": ([f"p{n}" for n in range(1000, 1050)], [f"1{n // 20}-00-1" for n in range(1000, 1050)]),
                 "hot cas 52k lots + own 30 lots": (["hotp"], ["98000-00-1"]),
                 "stock?cas= hot (50.000 cap, no codes)": ([], ["98000-00-1"])}
        res = {}
        for label, (codes, cas_values) in cases.items():
            without = statistics.median(self.explain(codes, cas_values, False)[0] for _ in range(3))
            with_o, plan = None, None
            times = []
            for _ in range(3):
                t, plan = self.explain(codes, cas_values, True)
                times.append(t)
            with_o = statistics.median(times)
            res[label] = {"no_branch_order_ms": round(without, 1), "branch_order_ms": round(with_o, 1), "seq_scans": plan.count("Seq Scan")}
            self.assertLess(with_o, 800, (label, plan[-600:]))
        obs("U3", explain_ms=res)

    def test_http_timings_internal_and_customer(self):
        rows = {}
        for label, call in [("products-hot-code", lambda tok: self.get(V + "/products", token=tok, q="HOTP")),
                            ("products-hot-cas", lambda tok: self.get(V + "/products", token=tok, q="98000-00-1")),
                            ("stock-hot-cas", lambda tok: self.get(V + "/stock", token=tok, cas="98000-00-1")),
                            ("stock-code", lambda tok: self.get(V + "/stock", token=tok, code="P777")),
                            ("products-name", lambda tok: self.get(V + "/products", token=tok, q="Perf product 123", limit="10"))]:
            per = {}
            for tok in ("cfull", "int"):
                ts = []
                for _ in range(5):
                    t0 = time.monotonic()
                    r = call(tok)
                    ts.append((time.monotonic() - t0) * 1000)
                    self.assertEqual(r.status_code, 200, (label, tok, r.get_data(as_text=True)[:200]))
                per[tok] = (round(statistics.median(ts)), round(max(ts)))
                self.assertLess(max(ts), 2500, (label, tok, ts))
            rows[label] = per
        t = self.env.tokens["cfull"]
        reqs = [{"path": V + "/products", "token": t, "query": {"q": "HOTP"}}] * 21
        t0 = time.monotonic(); self.run_old(reqs[:1]); b = time.monotonic() - t0
        t0 = time.monotonic(); self.run_old(reqs); o21 = time.monotonic() - t0
        t0 = time.monotonic(); self.run_new(reqs); n21 = time.monotonic() - t0
        obs("U3", http_median_max_ms=rows, old_customer_hotp_ms=round((o21 - b) / 20 * 1000), new_customer_hotp_ms=round(n21 / 21 * 1000))


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class U3b_ExtremeSingleCas(OldRunner, Base):
    """Tình huống cực đoan: MỘT CAS có 300.000 lô (3.000 mã x 100 lô) + mã hỏi 40 lô. Truy vấn ORDER BY trong nhánh phải sắp 300k dòng (top-N)
    cho nhánh cùng CAS và 300k dòng cho nhánh tra CAS (cap 50.000): đo thời gian, phải dưới statement_timeout (3s)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        snap = active_snapshot(env)
        cls.cas = "99000-00-1"
        env.raw_product("Extreme own", "X-OWN", "CATO", "1", "1", cas=cls.cas)
        add_lots(env, "X-OWN", cls.cas, "CATO", "1L", 40, qty_sql="1", expiry_sql="CURRENT_DATE + 100 + g")
        env.sql(
            "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
            "SELECT %s,'x','E'||c,'99000-00-1',(ARRAY['ALPHA','BETA','CATO','GAMMA'])[1+c%%4],'1L',5,1,CURRENT_DATE+100+g,"
            "lower((ARRAY['ALPHA','BETA','CATO','GAMMA'])[1+c%%4]),lower('E'||c),'1l','99000-00-1','' FROM generate_series(1,3000) c, generate_series(1,100) g", (snap,))
        env.sql("ANALYZE stock_items")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_http_times_and_customer_vs_old(self):
        res = {}
        for label, call in [("products-code", lambda tok: self.get(V + "/products", token=tok, q="X-OWN")),
                            ("products-cas", lambda tok: self.get(V + "/products", token=tok, q=self.cas)),
                            ("stock-cas", lambda tok: self.get(V + "/stock", token=tok, cas=self.cas))]:
            per = {}
            for tok in ("cfull", "int"):
                ts = []
                for _ in range(3):
                    t0 = time.monotonic()
                    r = call(tok)
                    ts.append((time.monotonic() - t0) * 1000)
                    self.assertEqual(r.status_code, 200, (label, tok, r.get_data(as_text=True)[:200]))
                per[tok] = (round(statistics.median(ts)), round(max(ts)))
                self.assertLess(max(ts), 2500, (label, tok, ts))
            res[label] = per
        t = self.env.tokens["cfull"]
        reqs = [{"path": V + "/products", "token": t, "query": {"q": "X-OWN"}}]
        old, new = self.run_old(reqs * 1), self.run_new(reqs * 1)
        t0 = time.monotonic(); self.run_old(reqs * 6); o6 = time.monotonic() - t0
        t0 = time.monotonic(); self.run_old(reqs); b = time.monotonic() - t0
        obs("U3b", http_median_max_ms=res, old_customer_ms=round((o6 - b) / 5 * 1000), same_cas_equal_old=(same_cas_of(old[0]["body"]) == same_cas_of(new[0]["body"])))
        self.assertEqual(same_cas_of(old[0]["body"]), same_cas_of(new[0]["body"]))
        item = strict_json(self.products("X-OWN"))["results"][0]
        self.assertEqual(item["stock"]["quantity"], int(oracle_quantity(self.env, "X-OWN")))
