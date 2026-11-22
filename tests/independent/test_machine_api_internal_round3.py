"""LƯỢT 3 của VERIFIER độc lập cho thẻ NỘI BỘ có giá (specs/machine-api-internal-price/VERIFICATION.md + Change request log).
Ca đối kháng MỚI so với lượt 1-2: các biến thể của lỗi tồn kho "tổng lô > 5000" (nhiều mã / nhiều hãng-quy cách-hạn / CAS nhiều mã /
lô sau khi lọc hãng / biên 50.000), thẻ khách so với bản cũ 351cf1a khi > 5000 lô (kể cả same_cas), hiệu năng truy vấn tồn kho UNION ALL,
dữ liệu giá/tỷ giá/giá tồn kho hỏng (kể cả 'Infinity' của numeric).

Chạy lại:
  export DATABASE_URL=postgresql://searchlocal:searchlocal@localhost:5432/products_local
  PYTHONPATH=.:tests:tests/independent .venv/bin/python -m unittest tests.independent.test_machine_api_internal_round3
"""

import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

import machine_api
import machine_api_verifier_support as S
from test_machine_api_internal_independent import (  # noqa: F401
    HAS_PG, INTERNAL_IP, OLD_COMMIT, ROOT, TODAY, V, Base, keys_deep,
)
from test_machine_api_internal_round2 import add_lots, active_snapshot, oracle_lots, oracle_quantity, strict_json


def obs(tag, **kw):
    sys.stderr.write(f"\nOBSERVATION[{tag}]: " + json.dumps(kw, ensure_ascii=False, default=str) + "\n")


def oracle_quantity_cas(env, cas):
    return int(env.sql(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_items i JOIN stock_state s ON i.snapshot_id=s.active_snapshot_id "
        "WHERE i.cas_norm=lower(%s) AND quantity>0 AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)", (cas,))[0][0])


def oracle_group(env, code, brand=None):
    q = ("SELECT COALESCE(SUM(quantity) FILTER (WHERE quantity>0 AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)),0), COUNT(*) "
         "FROM stock_items i JOIN stock_state s ON i.snapshot_id=s.active_snapshot_id WHERE i.code_norm=lower(%s)")
    args = [code]
    if brand:
        q += " AND i.brand_norm=lower(%s)"
        args.append(brand)
    row = env.sql(q, tuple(args))[0]
    return int(row[0]), int(row[1])


def ensure_plain_stock_snapshot(env):
    return active_snapshot(env)


# ======================================================================
# T1 — I3/I8: biến thể mới của lỗi "tổng lô > 5000"
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class T1_LotsOver5000(Base):

    def make(self, code, n, brand="CATO", size="1L", cas="", price="10", **kw):
        self.env.raw_product("T1 " + code, code, brand, price, "1", size=size, cas=cas)
        add_lots(self.env, code, cas, brand, size, n, **kw)

    def check_item(self, item, code, problems, label, brand=None):
        want_q, want_n = oracle_group(self.env, code, brand)
        st = item["stock"]
        if st.get("quantity") != want_q or st.get("lots_total") != want_n:
            problems.append((label, code, "got", st.get("quantity"), st.get("lots_total"), "want", want_q, want_n))
        if st["status"] != ("IN_STOCK" if want_q > 0 else "OUT_OF_STOCK"):
            problems.append((label, code, "status", st["status"], want_q))

    def test_many_codes_one_query_total_far_over_5000(self):
        """50 mã × 300 lô = 15.000 lô của chính các mã hỏi (3 hãng, 2 quy cách, mã hoa/thường lẫn)."""
        for i in range(50):
            code = f"T1M-{i:02d}"
            brand = ("ALPHA", "BETA", "GAMMA")[i % 3]
            self.env.raw_product("T1 multi " + code, code, brand, "10", "1", size="1L")
            add_lots(self.env, code, "", brand, "1L", 150)
            add_lots(self.env, code, "", brand, "500mL", 150)
        r = self.products("T1 multi", limit="50")
        self.assertEqual(r.status_code, 200)
        body = strict_json(r)
        self.assertEqual(body["count"], 50)
        problems = []
        for it in body["results"]:
            self.check_item(it, it["code"], problems, "products")
        # cùng các mã qua /stock?code=
        for it in body["results"][:15]:
            b = strict_json(self.get(V + "/stock", code=it["code"]))
            want_q, want_n = oracle_group(self.env, it["code"])
            if b["quantity"] != want_q or sum(x["lots_total"] for x in b["items"]) != want_n:
                problems.append(("stock-code", it["code"], b["quantity"], want_q))
        self.assertEqual(problems, [], problems[:5])

    def test_many_codes_same_cas_one_query(self):
        """10 mã chung một CAS, mỗi mã 700 lô (tổng 7000 lô của chính các mã hỏi, đồng thời là lô cùng CAS của nhau)."""
        cas = "91000-00-1"
        for i in range(10):
            code = f"T1C-{i}"
            self.make(code, 700, brand=("ALPHA", "BETA")[i % 2], cas=cas)
        body = strict_json(self.products("T1 T1C-", limit="50"))
        # tìm theo tên "T1 T1C-" -> 10 sản phẩm
        self.assertEqual(body["count"], 10, body["count"])
        problems = []
        for it in body["results"]:
            self.check_item(it, it["code"], problems, "products-cas-group")
        # /stock?cas= : 7000 lô
        b = strict_json(self.get(V + "/stock", cas=cas))
        want = oracle_quantity_cas(self.env, cas)
        tot = sum(x["lots_total"] for x in b["items"])
        if b["quantity"] != want or tot != 7000 or len(b["items"]) != 10 and not b["more"]:
            problems.append(("stock-cas", b["quantity"], want, tot, len(b["items"]), b["more"]))
        self.assertEqual(problems, [], problems[:5])

    def test_cas_with_60_codes_100_lots_each_via_cas_stock_and_search_by_cas(self):
        cas = "92000-00-1"
        for i in range(60):
            code = f"T1K-{i:02d}"
            self.make(code, 100, brand=("ALPHA", "BETA", "CATO")[i % 3], cas=cas)
        b = strict_json(self.get(V + "/stock", cas=cas))
        want = oracle_quantity_cas(self.env, cas)
        tot = sum(x["lots_total"] for x in b["items"])
        self.assertEqual(b["quantity"], want, (b["quantity"], want))
        self.assertEqual(b["more"], True)          # 60 nhóm > 30 mục
        self.assertEqual(len(b["items"]), 30)
        # tìm theo CAS: tối đa 50 sản phẩm; nếu tổng lô của các mã hỏi (50x100=5000) -> vẫn phải đúng từng mã
        body = strict_json(self.products(cas, limit="50"))
        problems = []
        for it in body["results"]:
            self.check_item(it, it["code"], problems, "products-by-cas")
        self.assertEqual(problems, [], problems[:5])
        obs("T1", cas_items=len(b["items"]), cas_quantity=b["quantity"], oracle=want, sum_lots_total_items=tot)

    def test_same_cas_other_codes_over_5000_internal_group_quantities(self):
        """Mã P có ít lô; 6000 lô cùng CAS thuộc 30 mã KHÁC. Số lượng của P phải đúng; các nhóm same_cas phải đúng hoặc được báo cắt."""
        cas = "93000-00-1"
        self.make("T1P-OWN", 12, brand="CATO", cas=cas)
        for i in range(30):
            add_lots(self.env, f"T1P-OTH{i:02d}", cas, ("ALPHA", "BETA", "GAMMA")[i % 3], "1L", 200)
        item = strict_json(self.products("T1P-OWN"))["results"][0]
        problems = []
        self.check_item(item, "T1P-OWN", problems, "own")
        same = item["stock"]["same_cas"]
        wrong = []
        for g in same:
            # nhóm same_cas không có trường code ở thẻ nội bộ? (thẻ nội bộ có VIEW_CODE) -> đối chiếu từng nhóm
            q, n = oracle_group(self.env, g["code"], g["brand"])
            if g["quantity"] != q:
                wrong.append((g["code"], g["quantity"], q))
        obs("T1", same_cas_groups=len(same), same_cas_more=item["stock"]["same_cas_more"], wrong_group_quantity=wrong[:6], wrong_count=len(wrong))
        self.assertEqual(problems, [], problems)
        self.assertEqual(wrong, [], wrong[:6])

    def test_same_code_many_brands_sizes_expiries_around_cap_boundaries(self):
        for n in (4999, 5000, 5001, 7500):
            code = f"T1B-{n}"
            self.env.raw_product("T1 brand " + code, code, "ALPHA", "10", "1")
            per = n // 6
            rest = n - per * 5
            plan = [("ALPHA", "1L", per), ("ALPHA", "500mL", per), ("BETA", "1L", per), ("GAMMA", "1L", per), ("CATO", "2L", per),
                    ("EPS", "1L", rest)]
            for brand, size, cnt in plan:
                add_lots(self.env, code, "", brand, size, cnt)
            item = strict_json(self.products(code))["results"][0]
            problems = []
            self.check_item(item, code, problems, f"mix-{n}")
            self.assertEqual(problems, [], problems)
            self.assertTrue(item["stock"]["lots_truncated"])
            self.assertEqual(len(item["stock"]["lots"]), 200)
            b = strict_json(self.get(V + "/stock", code=code))
            self.assertEqual(b["quantity"], oracle_group(self.env, code)[0])
            self.assertEqual(sum(x["lots_total"] for x in b["items"]), n)

    def test_customer_brand_filter_does_not_let_hidden_brand_lots_consume_cap(self):
        """Lô của hãng KHÔNG thuộc team khách (6000 lô) không được chiếm trần; thẻ khách thấy số lượng đúng của các hãng được phép;
        thẻ nội bộ thấy cả hai."""
        code = "T1F-1"
        self.env.raw_product("T1 filter", code, "CATO", "10", "1")
        add_lots(self.env, code, "", "GAMMA", "1L", 6000, qty_sql="1")      # GAMMA không thuộc team_full (CATO, ALPHA, BETA)
        add_lots(self.env, code, "", "CATO", "1L", 7, qty_sql="5", expiry_sql="CURRENT_DATE + 100 + g")
        add_lots(self.env, code, "", "BETA", "1L", 4, qty_sql="3", expiry_sql="CURRENT_DATE + 100 + g")
        self.env.add_token("cqty", self.env.team_full, stock_detail="quantity")
        machine_api.token_store.reset()
        r = self.get(V + "/stock", token="cfull", code=code)
        self.assertEqual(r.status_code, 200)
        cb = r.get_json()
        self.assertEqual(cb["status"], "IN_STOCK")
        self.assertFalse(keys_deep(cb) & {"lots", "price", "stock_price_vnd", "unit_price_vnd", "quantity"}, keys_deep(cb))
        body = self.get(V + "/stock", token="cqty", code=code).get_json()
        self.assertEqual(body.get("quantity"), 7 * 5 + 4 * 3, body)
        ib = strict_json(self.get(V + "/stock", code=code))
        self.assertEqual(ib["quantity"], oracle_group(self.env, code)[0])
        self.assertEqual(sum(x["lots_total"] for x in ib["items"]), 6011)

    def test_limit_50_codes_each_exactly_1000_lots_is_50000_boundary(self):
        """Biên của trần 50.000 dòng (hợp đồng ghi trần này): đúng 50.000 lô của các mã hỏi phải chính xác."""
        for i in range(50):
            code = f"T1X-{i:02d}"
            self.env.raw_product("T1 edge", code, "ALPHA" if i % 2 else "CATO", "10", "1")
            add_lots(self.env, code, "", "ALPHA" if i % 2 else "CATO", "1L", 1000)
        t0 = time.monotonic()
        r = self.products("T1 edge", limit="50")
        dt = time.monotonic() - t0
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        problems = []
        for it in strict_json(r)["results"]:
            self.check_item(it, it["code"], problems, "edge50k")
        obs("T1", edge_50000_ms=round(dt * 1000), problems=len(problems))
        self.assertEqual(problems, [], problems[:4])

    def test_beyond_50000_cap_is_reported_not_silent_OBSERVATION(self):
        """> 50.000 lô của chính các mã hỏi: hợp đồng ghi trần 50.000 => chỉ GHI NHẬN hành vi (có báo cắt hay không)."""
        code = "T1Y-1"
        self.env.raw_product("T1 beyond", code, "CATO", "10", "1")
        add_lots(self.env, code, "", "CATO", "1L", 50050, qty_sql="1", expiry_sql="CURRENT_DATE + 100 + g")
        r = self.products(code)
        obs("T1", beyond_status=r.status_code)
        if r.status_code == 200:
            st = strict_json(r)["results"][0]["stock"]
            obs("T1", beyond_50000=dict(quantity=st["quantity"], oracle=oracle_group(self.env, code)[0], lots_total=st["lots_total"],
                                         truncated=st["lots_truncated"]))


# ======================================================================
# T2 — Thẻ khách so với bản cũ 351cf1a khi có > 5000 lô
# ======================================================================
class OldRunner:
    @classmethod
    def prep_old(cls):
        cls.tmp = tempfile.mkdtemp(prefix="machine-old-r3-")
        src = subprocess.run(["git", "show", f"{OLD_COMMIT}:machine_api.py"], capture_output=True, text=True, cwd=str(ROOT))
        if src.returncode != 0:
            raise unittest.SkipTest("không đọc được bản cũ từ Git")
        Path(cls.tmp, "machine_api.py").write_text(src.stdout, encoding="utf-8")

    def run_old(self, requests):
        path = os.path.join(self.tmp, "req.json")
        Path(path).write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "MACHINE_INTERNAL_DATABASE_URL"}
        env.update(DATABASE_URL=self.env.dsn, MACHINE_API_TOKENS_FILE=self.env.token_path, PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("machine_api_internal_old_runner.py")), self.tmp, path],
                              capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def run_new(self, requests):
        out = []
        for req in requests:
            machine_api.rate_limiter.reset()
            resp = self.client.get(req["path"], headers={"Authorization": "Bearer " + req["token"]}, query_string=req.get("query") or None,
                                   environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
            out.append({"status": resp.status_code, "body": resp.get_json()})
        return out


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class T2_CustomerVsOldBigLots(OldRunner, Base):
    """Thẻ khách + dữ liệu > 5000 lô: so từng lời gọi với bản cũ. Hai kịch bản: (A) lô cùng CAS khác mã (gợi ý same_cas) vượt 5000, thứ tự chèn
    ngược/xáo; (B) lô của chính mã hỏi ít, same_cas ít (dưới trần) => phải y hệt bản cũ."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        rnd = random.Random(7)
        snap = active_snapshot(env)
        cls.cas_big = "94000-00-1"
        # 120 mã khác, mỗi mã 50 lô, cùng CAS (6000 lô same_cas); tên mã sắp theo chữ cái với hãng đan xen
        rows = []
        brands = ["ALPHA", "BETA", "CATO"]
        for c in range(120):
            code = f"BIG-{c:03d}"
            brand = brands[c % 3]
            for g in range(50):
                rows.append((snap, "x", code, cls.cas_big, brand, "1L", 1 + (c + g) % 5, TODAY + timedelta(days=100 + g),
                             brand.lower(), code.lower(), "1l", cls.cas_big))
        rnd.shuffle(rows)
        with env.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "VALUES (%s,%s,%s,%s,%s,%s,5,%s,%s,%s,%s,%s,%s,'')", rows)
        # sản phẩm hỏi: mã ZZ-OWN (sắp sau mọi BIG-) và AA-OWN (sắp trước)
        for code in ("ZZ-OWN", "AA-OWN"):
            env.raw_product("Own " + code, code, "CATO", "1", "1", cas=cls.cas_big)
            add_lots(env, code, cls.cas_big, "CATO", "1L", 5, qty_sql="2", expiry_sql="CURRENT_DATE + 100 + g")
        env.add_token("cqty", env.team_full, stock_detail="quantity")
        cls.base_entries = list(env.entries)      # lượt 4: setUp() ghi lại base_entries, thiếu dòng này thẻ cqty bị 401 ở cả hai bên (so sánh rỗng)
        env.sql("ANALYZE stock_items")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def reqs(self):
        r = []
        for tok in ("cfull", "climited", "cqty"):
            t = self.env.tokens[tok]
            for code in ("ZZ-OWN", "AA-OWN"):
                r.append({"path": V + "/stock", "token": t, "query": {"code": code}})
                r.append({"path": V + "/products", "token": t, "query": {"q": code}})
            r.append({"path": V + "/stock", "token": t, "query": {"cas": self.cas_big}})
            r.append({"path": V + "/products", "token": t, "query": {"q": self.cas_big}})
            r.append({"path": V + "/products", "token": t, "query": {"q": "Own"}})
        return r

    def test_customer_same_cas_and_stock_vs_old_commit_with_over_5000_cas_lots(self):
        """Mọi lời gọi thẻ khách phải y hệt bản cũ, NGOẠI TRỪ khối stock của ZZ-OWN: bản cũ cắt 5000 dòng theo thứ tự hãng/mã nên mất HẾT lô của
        ZZ-OWN và báo OUT_OF_STOCK dù có 5 lô còn hàng (sai); bản mới đọc đủ lô của mã hỏi nên IN_STOCK (đúng theo oracle SQL). Ngoại lệ này
        được kiểm bằng oracle, không bỏ qua mù."""
        reqs = self.reqs()
        old = self.run_old(reqs)
        new = self.run_new(reqs)
        diffs, fixed = [], 0
        for req, o, n in zip(reqs, old, new):
            self.assertNotEqual((o["status"], n["status"]), (401, 401), req)    # lượt 4: không để thẻ bị từ chối làm so sánh rỗng
            if o["status"] != n["status"]:
                diffs.append((req["path"], req["query"], "status", o["status"], n["status"]))
                continue
            if o["body"] == n["body"]:
                continue
            ob, nb = json.loads(json.dumps(o["body"])), json.loads(json.dumps(n["body"]))
            if req["path"].endswith("/products"):
                for body in (ob, nb):
                    for r in body["results"]:
                        if r.get("code") == "ZZ-OWN":
                            r["stock"] = {k: v for k, v in r["stock"].items() if k in ("same_cas", "same_cas_more")}
                for r in n["body"]["results"]:
                    if r.get("code") == "ZZ-OWN":
                        want = oracle_quantity(self.env, "ZZ-OWN")
                        self.assertGreater(want, 0)
                        self.assertEqual(r["stock"]["status"], "IN_STOCK")
                        fixed += 1
            if req["path"].endswith("/stock") and "cas" in req["query"]:
                # lượt 4: tra theo CAS giờ đọc tới 50.000 lô (bản cũ 5.000) nên tổng `quantity` (chỉ thẻ stock_detail=quantity) khác CÓ CHỦ Ý và
                # phải bằng oracle SQL; mọi khóa còn lại (status, items, more...) vẫn phải giống bản cũ từng byte.
                if "quantity" in nb:
                    self.assertEqual(nb["quantity"], oracle_quantity_cas(self.env, self.cas_big), req)
                    self.assertLessEqual(ob.get("quantity", 0), nb["quantity"])
                    fixed += 1
                ob.pop("quantity", None)
                nb.pop("quantity", None)
            if ob != nb:
                diffs.append((req["path"], req["query"], "body"))
        obs("T2", calls=len(reqs), diffs=len(diffs), old_truncation_bug_fixed_in_new=fixed, first=diffs[:6])
        self.assertEqual(diffs, [], diffs[:6])

    def test_customer_same_cas_deterministic_and_independent_of_physical_order(self):
        """Gọi lặp + sau VACUUM FULL/CLUSTER-lại thứ tự vật lý: thẻ khách phải nhận đúng cùng nội dung (bản cũ có ORDER BY trước LIMIT)."""
        t = self.env.tokens["cfull"]
        reqs = [{"path": V + "/products", "token": t, "query": {"q": "ZZ-OWN"}}, {"path": V + "/products", "token": t, "query": {"q": "AA-OWN"}}]
        base = self.run_new(reqs)
        for _ in range(3):
            self.assertEqual(self.run_new(reqs), base)
        self.env.sql("CREATE INDEX IF NOT EXISTS t2_tmp_idx ON stock_items(quantity DESC, code_norm DESC)")
        self.env.conn.autocommit = True
        with self.env.conn.cursor() as cur:
            cur.execute("CLUSTER stock_items USING t2_tmp_idx")
            cur.execute("ANALYZE stock_items")
        after = self.run_new(reqs)
        old = self.run_old(reqs)
        obs("T2", new_changed_after_recluster=(after != base), new_equals_old_after=(after == old), old_stable=(old == self.run_old(reqs)))
        self.assertEqual(after, base, "nội dung thẻ khách đổi khi thứ tự vật lý đổi")


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class T2b_CustomerSameCasLateRows(OldRunner, Base):
    """Nhóm same_cas đứng ĐẦU theo thứ tự chữ cái nhưng nằm CUỐI vật lý, sau > 5000 lô cùng CAS của các mã khác: bản cũ (ORDER BY trước LIMIT)
    luôn đưa nhóm đó lên; bản mới phải cho cùng kết quả thẻ khách."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        cls.cas = "96000-00-1"
        snap = active_snapshot(env)
        env.raw_product("Late own", "MID-OWN", "CATO", "1", "1", cas=cls.cas)
        add_lots(env, "MID-OWN", cls.cas, "CATO", "1L", 3, qty_sql="2", expiry_sql="CURRENT_DATE + 100 + g")
        with env.conn.cursor() as cur:
            for c in range(60):    # 6000 lô, mã ZZ (sắp SAU), nhập trước
                cur.execute(
                    "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s,'x',%s,%s,'ALPHA','1L',5,1,CURRENT_DATE+100+g,'alpha',lower(%s),'1l',%s,'' FROM generate_series(1,100) g",
                    (snap, f"ZZ-{c:02d}", cls.cas, f"ZZ-{c:02d}", cls.cas))
            for c in range(3):     # mã AA (sắp TRƯỚC), nhập SAU
                cur.execute(
                    "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s,'x',%s,%s,'ALPHA','1L',5,4,CURRENT_DATE+100+g,'alpha',lower(%s),'1l',%s,'' FROM generate_series(1,5) g",
                    (snap, f"AA-{c}", cls.cas, f"AA-{c}", cls.cas))
        env.sql("ANALYZE stock_items")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_customer_same_cas_groups_match_old_when_late_rows(self):
        reqs = []
        for tok in ("cfull",):
            t = self.env.tokens[tok]
            reqs += [{"path": V + "/products", "token": t, "query": {"q": "MID-OWN"}},
                     {"path": V + "/stock", "token": t, "query": {"code": "MID-OWN"}},
                     {"path": V + "/stock", "token": t, "query": {"cas": self.cas}}]
        old, new = self.run_old(reqs), self.run_new(reqs)
        diffs = []
        # Lượt 4: so sánh same_cas từng byte với bản cũ. Khác biệt CÓ CHỦ Ý duy nhất được bỏ qua: khối stock của MID-OWN (status/pack_confirmed/
        # quantity): bản cũ sắp theo hãng rồi cắt 5000 nên làm rơi lô của mã đang hỏi (hãng CATO sau ALPHA) và báo OUT_OF_STOCK dù có 3 lô còn
        # hàng; bản mới đọc đủ lô của mã hỏi nên IN_STOCK. Điều này được kiểm bằng oracle SQL bên dưới, không bỏ qua mù.
        want = oracle_quantity(self.env, "MID-OWN")
        self.assertEqual(want, 6)
        fixed = 0
        for req, o, n in zip(reqs, old, new):
            self.assertEqual(o["status"], n["status"])
            ob, nb = json.loads(json.dumps(o["body"])), json.loads(json.dumps(n["body"]))
            if req["path"].endswith("/products"):
                for body in (ob, nb):
                    for r in body["results"]:
                        if r.get("code") == "MID-OWN":
                            r["stock"] = {k: v for k, v in r["stock"].items() if k in ("same_cas", "same_cas_more")}
                for r in n["body"]["results"]:
                    if r.get("code") == "MID-OWN":
                        self.assertEqual(r["stock"]["status"], "IN_STOCK")
                        fixed += 1
            if ob != nb:
                diffs.append((req["path"], req["query"], "body"))
        obs("T2b", diffs=diffs, old_truncation_bug_fixed_in_new=fixed)
        self.assertEqual(diffs, [], diffs)
        self.assertEqual(fixed, 1)

    def test_customer_same_cas_independent_of_physical_row_order(self):
        """Cùng dữ liệu logic, đổi thứ tự vật lý (CLUSTER theo mã giảm dần): bản cũ cho cùng nội dung; bản mới không được phụ thuộc thứ tự vật lý."""
        t = self.env.tokens["cfull"]
        reqs = [{"path": V + "/products", "token": t, "query": {"q": "MID-OWN"}}]
        before_new, before_old = self.run_new(reqs), self.run_old(reqs)
        self.env.sql("CREATE INDEX IF NOT EXISTS t2b_tmp_idx ON stock_items(code_norm DESC)")
        self.env.conn.autocommit = True
        with self.env.conn.cursor() as cur:
            cur.execute("CLUSTER stock_items USING t2b_tmp_idx")
            cur.execute("ANALYZE stock_items")
        after_new, after_old = self.run_new(reqs), self.run_old(reqs)
        codes = lambda r: [x["code"] for x in r[0]["body"]["results"][0]["stock"]["same_cas"]]  # noqa: E731
        obs("T2b", old_before=codes(before_old), old_after=codes(after_old), new_before=codes(before_new), new_after=codes(after_new))
        self.assertEqual(before_old, after_old)
        self.assertEqual(after_new, before_new, "same_cas của thẻ khách đổi theo thứ tự vật lý của bảng")

    def test_internal_same_cas_groups_for_late_rows(self):
        """Thẻ nội bộ: nhóm same_cas phải gồm AA-* (đầu bảng chữ cái, 3 nhóm) hoặc được báo; số lượng nhóm đúng."""
        item = strict_json(self.products("MID-OWN"))["results"][0]
        codes = [x["code"] for x in item["stock"]["same_cas"]]
        want = [r[0] for r in self.env.sql("SELECT DISTINCT code FROM stock_items WHERE cas_norm=%s AND code<>'MID-OWN' ORDER BY code LIMIT 10", (self.cas,))]
        obs("T2b", internal_same_cas=codes, expected_first_by_alpha=want)
        self.assertEqual(codes, want, "same_cas nội bộ bỏ sót nhóm đầu bảng chữ cái khi > 5000 lô cùng CAS")


# ======================================================================
# T3 — hiệu năng truy vấn tồn kho mới so với cũ
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class T3_Performance(OldRunner, Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prep_old()
        env = cls.env
        snap = active_snapshot(env)
        with env.conn.cursor() as cur:
            # 300.000 lô: 60.000 mã × 5 lô; CAS chia nhóm 20 mã/CAS; 6 hãng
            cur.execute(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s,'x','P'||c,'1'||(c/20)||'-00-1',(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%%6],'1L',100,1+g,CURRENT_DATE+g+50,"
                "lower((ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%%6]),lower('P'||c),'1l','1'||(c/20)||'-00-1','' "
                "FROM generate_series(1,60000) c, generate_series(1,5) g", (snap,))
            cur.execute(
                "INSERT INTO products(name,code,cas,brand,size,price,ship,note,source_brand) "
                "SELECT 'Perf product '||c,'P'||c,'1'||(c/20)||'-00-1',(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%6],'1L','10','1.3','',"
                "(ARRAY['ALPHA','BETA','CATO','GAMMA','EPS','DELTA'])[1+c%6] FROM generate_series(1,60000) c")
        env.sql("ANALYZE stock_items")
        env.sql("ANALYZE products")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def timed(self, fn, n=5):
        out = []
        for _ in range(n):
            t0 = time.monotonic()
            fn()
            out.append((time.monotonic() - t0) * 1000)
        return statistics.median(out), max(out)

    def test_query_time_new_vs_old_for_customer_and_internal(self):
        t = self.env.tokens["cfull"]
        cases = [("products-name", lambda tok: self.get(V + "/products", token=tok, q="Perf product 123", limit="10")),
                 ("products-code", lambda tok: self.get(V + "/products", token=tok, q="P12345")),
                 ("products-cas", lambda tok: self.get(V + "/products", token=tok, q="1617-00-1")),
                 ("stock-code", lambda tok: self.get(V + "/stock", token=tok, code="P777")),
                 ("stock-cas", lambda tok: self.get(V + "/stock", token=tok, cas="1500-00-1"))]
        rows = []
        for label, call in cases:
            new_c = self.timed(lambda: call("cfull"))
            new_i = self.timed(lambda: call("int"))
            # bản cũ: chạy cùng lời gọi trong tiến trình riêng, đo tổng thời gian 5 lần rồi chia (đã trừ chi phí khởi động bằng gọi 1 lần)
            req = {"cas": "1617-00-1"} if "cas" in label else {}
            rows.append((label, new_c, new_i))
        # đo bản cũ riêng cho thẻ khách bằng nhiều lời gọi trong một tiến trình
        reqs_one = [{"path": V + "/products", "token": t, "query": {"q": "P12345"}}]
        t0 = time.monotonic(); self.run_old(reqs_one * 1); base_old = time.monotonic() - t0
        t0 = time.monotonic(); self.run_old(reqs_one * 21); old_21 = time.monotonic() - t0
        old_each = (old_21 - base_old) / 20 * 1000
        t0 = time.monotonic(); self.run_new(reqs_one * 21); new_21 = time.monotonic() - t0
        new_each = new_21 / 21 * 1000
        obs("T3", rows=[(a, [round(x) for x in b], [round(x) for x in c]) for a, b, c in rows], old_customer_products_code_ms=round(old_each),
            new_customer_products_code_ms=round(new_each))
        for label, new_c, new_i in rows:
            self.assertLess(new_i[1], 2500, (label, new_i))    # statement_timeout là 3000ms
            self.assertLess(new_c[1], 2500, (label, new_c))
        self.assertLess(new_each, max(old_each * 3, 150), (old_each, new_each))

    def test_explain_new_stock_query_uses_indexes(self):
        """EXPLAIN truy vấn UNION ALL của _stock_lots: không được quét tuần tự 300k dòng cho truy vấn một mã."""
        sql = ("EXPLAIN (ANALYZE, FORMAT TEXT) SELECT t.code FROM ((SELECT i.code, i.code_norm, i.brand_norm, i.size_norm, i.expiry_date FROM stock_state st "
               "JOIN stock_items i ON i.snapshot_id = st.active_snapshot_id WHERE st.singleton = TRUE AND i.code_norm = ANY(%s) LIMIT 50000) "
               "UNION ALL (SELECT i.code, i.code_norm, i.brand_norm, i.size_norm, i.expiry_date FROM stock_state st JOIN stock_items i "
               "ON i.snapshot_id = st.active_snapshot_id WHERE st.singleton = TRUE AND i.cas_norm = ANY(%s) AND NOT (i.code_norm = ANY(%s)) LIMIT 5000)) t "
               "ORDER BY t.brand_norm, t.code_norm, t.size_norm, t.expiry_date NULLS LAST")
        plan = "\n".join(r[0] for r in self.env.sql(sql, (["p777"], ["1038-00-1"], ["p777"])))
        obs("T3", plan_tail=plan.splitlines()[-3:], seq_scans=plan.count("Seq Scan"))
        t = float(plan.rsplit("Execution Time:", 1)[1].split("ms")[0])
        self.assertLess(t, 500, plan[-400:])


# ======================================================================
# T4 — giá / tỷ giá / giá tồn kho hỏng, lần cuối
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class T4_DirtyNumbers(Base):

    def run_rate(self, label, rate_sql, expect_ok=True):
        self.env.raw_product("T4 rate " + label, "T4R-" + label, "ALPHA", "10", "2")
        try:
            self.env.sql(f"UPDATE currency_rates SET rate_vnd={rate_sql} WHERE currency_code='USD'")
        except Exception as exc:  # noqa: BLE001
            self.env.conn.rollback() if not self.env.conn.autocommit else None
            return ("rejected-by-DB", type(exc).__name__)
        try:
            outs = {}
            for path, q in ((V + "/products", {"q": "T4R-" + label}), (V + "/stock", {"code": "T4R-" + label})):
                r = self.get(path, **q)
                if r.status_code != 200:
                    outs[path] = ("HTTP", r.status_code, r.get_data(as_text=True)[:120])
                    continue
                try:
                    body = strict_json(r)
                except ValueError as exc:
                    outs[path] = ("NONSTANDARD-JSON", str(exc))
                    continue
                if path.endswith("products"):
                    outs[path] = (body["results"][0]["price"])
                else:
                    outs[path] = "ok"
            # trộn với sản phẩm sạch thuộc hãng khác tiền tệ: không được mất
            clean = self.get(V + "/products", q="Glucose", limit="50")
            outs["clean-still-200"] = clean.status_code
            return outs
        finally:
            self.env.sql("UPDATE currency_rates SET rate_vnd=25000 WHERE currency_code='USD'")

    def test_rate_values_nan_infinity_tiny_never_500_never_nonstandard_json(self):
        results = {}
        for label, rate_sql in (("nan", "'NaN'::numeric"), ("pinf", "'Infinity'::numeric"), ("ninf", "'-Infinity'::numeric"),
                                ("e400", "1e400::numeric"), ("tiny", "1e-400::numeric"), ("zero", "0"), ("neg", "-5")):
            results[label] = self.run_rate(label, rate_sql)
        obs("T4", rate_results=results)
        bad = {}
        for label, res in results.items():
            if isinstance(res, tuple):
                continue
            for k, v in res.items():
                if (isinstance(v, tuple) and v[0] in ("HTTP", "NONSTANDARD-JSON")) or (k == "clean-still-200" and v != 200):
                    bad[(label, k)] = v
        self.assertEqual(bad, {}, bad)

    def test_stock_price_infinity_nan_and_dates_via_products_stock_cas(self):
        snap = active_snapshot(self.env)
        self.env.raw_product("T4 stockprice", "T4S-1", "ALPHA", "10", "1", cas="95000-00-1")
        base = ("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "VALUES (%s,'x','T4S-1','95000-00-1','ALPHA','1L',{p},3,{e},'alpha','t4s-1','1l','95000-00-1','n')")
        bad = []
        outcome = {}
        for label, p, e in (("nan", "'NaN'", "NULL"), ("inf", "'Infinity'", "NULL"), ("huge", "1e300", "NULL"), ("max", "99999999999.99", "NULL"),
                            ("date-inf", "1", "'infinity'"), ("date-ninf", "1", "'-infinity'"), ("date-max", "1", "'9999-12-31'")):
            self.env.sql("DELETE FROM stock_items WHERE code='T4S-1'")
            try:
                self.env.sql(base.format(p=p, e=e), (snap,))
            except Exception as exc:  # noqa: BLE001
                outcome[label] = "rejected-by-DB:" + type(exc).__name__
                continue
            row = {}
            for path, q in ((V + "/products", {"q": "T4S-1"}), (V + "/stock", {"code": "T4S-1"}), (V + "/stock", {"cas": "95000-00-1"}),
                            (V + "/products", {"q": "95000-00-1"})):
                r = self.get(path, **q)
                key = path[-8:] + str(sorted(q))
                if r.status_code != 200:
                    row[key] = r.status_code
                    bad.append((label, path, q, r.status_code))
                    continue
                try:
                    strict_json(r)
                    row[key] = 200
                except ValueError as exc:
                    row[key] = "NONSTANDARD-JSON"
                    bad.append((label, path, q, str(exc)))
            outcome[label] = row
        self.env.sql("DELETE FROM stock_items WHERE code='T4S-1'")
        obs("T4", stock_dirty=outcome)
        self.assertEqual(bad, [], bad[:6])

    def test_dirty_price_mix_in_one_query_all_clean_survive_and_json_strict(self):
        dirty = ["nan", "inf", "-inf", "1e999", "9" * 400, "1e308", "Infinity", "-0"]
        for i, p in enumerate(dirty):
            self.env.raw_product("T4 mix", f"T4X-D{i}", "ALPHA", p, "1.3")
            self.env.raw_product("T4 mix", f"T4X-S{i}", "CATO", "10", "inf" if i % 2 else "nan")
        for i in range(10):
            self.env.raw_product("T4 mix", f"T4X-C{i}", ("ALPHA", "BETA", "CATO", "EPS")[i % 4], "10", "1.3")
        body = strict_json(self.products("T4 mix", limit="50"))
        got = {x["code"]: x for x in body["results"]}
        self.assertEqual(body["count"], 26)
        for i in range(10):
            blk = got[f"T4X-C{i}"]["price"]
            self.assertEqual(blk["price_status"], "OK", blk)
            self.assertIsInstance(blk["unit_price_vnd"], int)
        for i in range(8):
            for code in (f"T4X-D{i}", f"T4X-S{i}"):
                blk = got[code]["price"]
                self.assertIn(blk["price_status"], ("INVALID_PRICE_DATA", "NO_PRICE_DATA"), (code, blk))
                if blk["price_status"] == "INVALID_PRICE_DATA":
                    self.assertIsNone(blk["unit_price_vnd"])


if __name__ == "__main__":
    unittest.main()
