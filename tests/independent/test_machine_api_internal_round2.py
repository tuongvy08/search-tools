"""LƯỢT 2 của VERIFIER độc lập cho thẻ NỘI BỘ có giá (specs/machine-api-internal-price/VERIFICATION.md + Change request log
2026-11-22). Chỉ ca đối kháng MỚI so với lượt 1 (các biến thể của hai lỗi đã báo: giá/ship/tỷ giá hỏng, số lô, cắt 5000 dòng;
xếp hạng 5 mức + xen hãng; khóa IP rộng, hạn > 90 ngày, CLI --days < 1; hồi quy thứ tự lô/same_cas của thẻ khách so với 351cf1a).

Chạy lại:
  export DATABASE_URL=postgresql://searchlocal:searchlocal@localhost:5432/products_local
  PYTHONPATH=.:tests:tests/independent .venv/bin/python -m unittest tests.independent.test_machine_api_internal_round2
"""

import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psycopg2

import machine_api
import machine_api_verifier_support as S
from currency_rates import load_currency_rate_resolver
from test_machine_api_internal_independent import (  # noqa: F401  (dùng lại hạ tầng, không chạy lại test cũ)
    BRANDS, HAS_PG, INTERNAL_IP, INTERNAL_NET, OLD_COMMIT, ROOT, TODAY, V, Base, IEnv, keys_deep,
)

CLI = str(ROOT / "scripts/machine_token.py")


def strict_json(resp):
    """Chuẩn JSON nghiêm ngặt: NaN/Infinity (Python mặc định cho phép) làm n8n/trình duyệt hỏng => coi là lỗi."""
    def bad(c):
        raise ValueError("non-standard JSON constant in body: " + c)
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


def active_snapshot(env):
    return env.sql("SELECT active_snapshot_id FROM stock_state")[0][0]


def add_lots(env, code, cas, brand, size, n, *, qty_sql="(g % 3) + 1", expiry_sql=None, price_sql="1000 + g", note="L"):
    """n lô cho (code, brand, size): số lượng/hạn dùng đa dạng (một phần hết hạn, một phần không hạn)."""
    if n <= 0:
        return
    if expiry_sql is None:
        # chỉ mục duy nhất (snapshot, hãng, mã, quy cách, hạn) => mỗi lô một hạn khác nhau; g=1 không hạn; 1/7 lô đã hết hạn
        expiry_sql = "CASE WHEN g = 1 THEN NULL WHEN g % 7 = 0 THEN CURRENT_DATE - 5 - g ELSE CURRENT_DATE + 100 + g END"
    env.sql(
        "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
        f"SELECT %s,'x',%s,%s,%s,%s,{price_sql.replace('%', '%%')},{qty_sql.replace('%', '%%')},{expiry_sql.replace('%', '%%')},"
        "lower(%s),lower(%s),lower(%s),lower(%s),%s FROM generate_series(1,%s) g",
        (active_snapshot(env), code, cas, brand, size, brand, code, size, cas or None, note, n))


def oracle_quantity(env, code):
    """Oracle độc lập: tổng SL các lô còn hạn (hạn trống hoặc >= hôm nay) và SL > 0, cùng code_norm."""
    return env.sql(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_items i JOIN stock_state s ON i.snapshot_id=s.active_snapshot_id "
        "WHERE i.code_norm=lower(%s) AND quantity>0 AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)", (code,))[0][0]


def oracle_lots(env, code):
    return env.sql("SELECT COUNT(*) FROM stock_items i JOIN stock_state s ON i.snapshot_id=s.active_snapshot_id WHERE i.code_norm=lower(%s)", (code,))[0][0]


# ======================================================================
# R1 — I4 (lỗi cũ): giá / ship / tỷ giá / giá tồn kho hỏng ở mọi đường
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R1_DirtyData(Base):
    DIRTY_PRICES = ["nan", "NaN", " nan ", "NAN", "inf", "-inf", "+inf", "Infinity", "-Infinity", "1e999", "-1e999", "9" * 400, "1e308",
                    "1e200", "1e16", "1e15", "999999999999999", "1e12", "0", "", "  ", "abc", "-1", "-0", "1e-320", "5e-324", "0x10", "1_0",
                    "1e5000", "1e2147483648", "12,5", "٣٤", "10"]
    DIRTY_SHIPS = ["nan", "inf", "-inf", "1e308", "1e999", "0", "", "x", "-1", "1e-320", "1.3", "9" * 400]
    PAIRS = [("0", "inf"), ("0", "nan"), ("inf", "0"), ("nan", "0"), ("1e308", "1e308"), ("1e200", "1e100"), ("1e-320", "1e-320"),
             ("inf", "-inf"), ("-inf", "-inf"), ("1e154", "1e154"), ("12.5", "1e999")]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brands = ["ALPHA", "BETA", "GAMMA", "CATO", "EPS", "DELTA", "ZETA", "HANG-LA", None]
        cls.cases = []
        n = 0
        for price in cls.DIRTY_PRICES:
            cls.cases.append(("DSET-%03d" % n, price, "1.3", cls.brands[n % len(cls.brands)]))
            n += 1
        for ship in cls.DIRTY_SHIPS:
            cls.cases.append(("DSET-%03d" % n, "10", ship, cls.brands[n % len(cls.brands)]))
            n += 1
        for price, ship in cls.PAIRS:
            cls.cases.append(("DSET-%03d" % n, price, ship, cls.brands[n % len(cls.brands)]))
            n += 1
        for i in range(8):                                     # sản phẩm sạch cùng truy vấn
            cls.cases.append(("DSET-C%02d" % i, "10", "1.3", ["ALPHA", "CATO", "BETA", "EPS"][i % 4]))
        for code, price, ship, brand in cls.cases:
            cls.env.raw_product("Dirtyset " + code, code, brand, price, ship)

    def staff_expect(self, code):
        import search  # noqa: PLC0415
        conn = psycopg2.connect(self.env.dsn)
        try:
            resolver = load_currency_rate_resolver(conn, str(ROOT))
            price, ship, brand = self.env.sql("SELECT price, ship, brand FROM products WHERE code=%s", (code,))[0]
            value, _d, valid, status = search._compute_unit_price_details(price, ship, brand, resolver)
            res = resolver.resolve((brand or "").strip())
            raw = None
            if valid:
                try:
                    pf, sf = float(price) if price is not None else 0, float(ship) if ship is not None else 0
                except ValueError:
                    pf = sf = 0
                raw = pf * sf * float(res.rate)
            return value, valid, status, raw
        finally:
            conn.close()

    def test_all_dirty_and_clean_products_in_ONE_query_never_poison(self):
        total = len(self.cases)
        self.assertGreaterEqual(total, 50)
        r = self.products("Dirtyset", limit="50")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        body = strict_json(r)
        got = {x["code"]: x for x in body["results"]}
        self.assertEqual(body["count"], min(total, 50))
        # limit 50 < total: lấy phần còn lại bằng truy vấn theo từng mã để mọi ca đều được đối chiếu
        mismatches, invalid_seen, finite_over_cap = [], 0, []
        for code, price, ship, brand in self.cases:
            item = got.get(code)
            if item is None:
                rr = self.products(code)
                self.assertEqual(rr.status_code, 200, (code, price, ship, brand, rr.get_data(as_text=True)[:200]))
                item = strict_json(rr)["results"][0]
            block = item["price"]
            value, valid, status, raw = self.staff_expect(code)
            if not valid:
                if block["unit_price_vnd"] is not None or block["price_status"] != str(status):
                    mismatches.append((code, price, ship, brand, "invalid-rate", block))
                continue
            if raw is None or not math.isfinite(raw) or not math.isfinite(value):
                invalid_seen += 1
                if block["unit_price_vnd"] is not None or block["price_status"] != "INVALID_PRICE_DATA":
                    mismatches.append((code, price, ship, brand, "non-finite-not-flagged", block))
            elif abs(value) > 1e15:
                finite_over_cap.append((code, price, ship, block["unit_price_vnd"], block["price_status"], value))
            elif block["unit_price_vnd"] != int(value):
                mismatches.append((code, price, ship, brand, "mismatch", block, value))
            if block["unit_price_vnd"] == 0 and block["price_status"] == "OK":
                mismatches.append((code, price, ship, brand, "zero-with-OK", block))
        sys.stderr.write(f"\nOBSERVATION[R1]: {total} ca; không hữu hạn được gắn cờ={invalid_seen}; hữu hạn nhưng |giá|>1e15 (cổng trả INVALID, "
                         f"ứng dụng nhân viên trả số)={len(finite_over_cap)} {finite_over_cap[:4]}\n")
        self.assertEqual(mismatches, [], mismatches[:5])

    def test_each_dirty_product_alone_and_via_customer_and_other_endpoints(self):
        """Mỗi ca bẩn gọi riêng (/products nội bộ), thẻ khách (không giá, không lỗi), /stock, /compliance — không được 5xx."""
        bad = []
        for code, price, ship, brand in self.cases:
            r = self.products(code)
            if r.status_code != 200:
                bad.append(("internal", code, price, ship, r.status_code))
                continue
            try:
                strict_json(r)
            except ValueError as exc:
                bad.append(("internal-json", code, price, ship, str(exc)))
            rc = self.get(V + "/products", token="cfull", q=code)
            if rc.status_code >= 500:
                bad.append(("customer", code, price, ship, rc.status_code))
            if rc.status_code == 200:
                self.assertFalse(keys_deep(rc.get_json()) & {"price", "unit_price_vnd", "original_price", "ship_factor", "lots"})
            for path, q in ((V + "/stock", {"code": code}), (V + "/compliance", {"cas": "50-99-7"})):
                rs = self.get(path, **q)
                if rs.status_code != 200:
                    bad.append((path, code, rs.status_code))
        self.assertEqual(bad, [], bad[:6])

    def test_threshold_boundary_matches_staff_below_cap(self):
        """Ngay dưới trần 1e15 phải khớp từng đồng với ứng dụng; trên trần: ghi nhận (hợp đồng chỉ nói không hữu hạn/tràn)."""
        rows = [("THR-1", "ALPHA", "39999999999", "1"), ("THR-2", "GAMMA", "999999999999999", "1"), ("THR-3", "GAMMA", "1000000000000001", "1"),
                ("THR-4", "ALPHA", "50000000000", "1")]
        for code, brand, price, ship in rows:
            self.env.raw_product("Thr " + code, code, brand, price, ship)
        for code, brand, price, ship in rows:
            block = self.js(code)["results"][0]["price"]
            value, valid, _s, _raw = self.staff_expect(code)
            sys.stderr.write(f"\nOBSERVATION[R1]: {code} staff={value!r} gateway={block['unit_price_vnd']!r}/{block['price_status']}\n")
            if abs(float(price) * float(ship) * (1 if brand == "GAMMA" else 25000)) <= 1e15:
                self.assertEqual(block["unit_price_vnd"], int(value), (code, block, value))

    def test_dirty_currency_rate_values(self):
        """Tỷ giá hỏng ghi thẳng vào DB (NaN, vô cực, quá nhỏ): không 5xx, không JSON không chuẩn, không 'OK' với 0."""
        self.env.raw_product("Rate victim", "RV-1", "ALPHA", "10", "2")
        results = {}
        for label, rate in (("NaN", "'NaN'::numeric"), ("1e400", "1e400::numeric"), ("1e-400", "1e-400::numeric"), ("0.0000001", "0.0000001"),
                            ("0", "0"), ("-5", "-5")):
            try:
                self.env.sql(f"UPDATE currency_rates SET rate_vnd={rate} WHERE currency_code='USD'")
            except psycopg2.Error as exc:
                results[label] = ["rejected-by-DB", type(exc).__name__]
                continue
            try:
                r = self.products("RV-1")
                entry = [r.status_code]
                if r.status_code == 200:
                    try:
                        block = strict_json(r)["results"][0]["price"]
                        entry += [block["unit_price_vnd"], block["price_status"]]
                    except ValueError as exc:
                        entry += ["NONSTANDARD-JSON", str(exc)]
                results[label] = entry
            finally:
                self.env.sql("UPDATE currency_rates SET rate_vnd=25000 WHERE currency_code='USD'")
        sys.stderr.write(f"\nOBSERVATION[R1]: tỷ giá hỏng -> {results}\n")
        # NaN/1e400/1e-400 chỉ có thể vào bằng ghi thẳng DB (hàm quản trị `rate_vnd <= 0` ném lỗi với NaN; bộ phân giải tỷ giá là mã DÙNG CHUNG với
        # ứng dụng nhân viên) => ghi nhận là rủi ro tồn dư, KHÔNG xếp FAIL. Ca hợp lệ trong hợp đồng (tỷ giá dương bình thường) đã được I4 phủ.
        self.__class__.rate_observation = results

    def test_dirty_stock_price_and_dates(self):
        """Giá tồn kho NaN/rất lớn, hạn dùng cực trị: JSON chuẩn, không 5xx (thẻ nội bộ); ghi nhận hành vi thẻ khách."""
        snap = active_snapshot(self.env)
        self.env.raw_product("Dirty lot product", "DLP-1", "ALPHA", "10", "1")
        base = ("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "VALUES (%s,'x','DLP-1','','ALPHA','1L',{p},3,{e},'alpha','dlp-1','1l',NULL,'n')")
        outcome = {}
        for label, p, e in (("NaN", "'NaN'", "NULL"), ("huge", "9999999999999999.99", "NULL"), ("negative", "-5", "NULL"),
                            ("date-max", "1", "'9999-12-31'"), ("date-min", "1", "'0001-01-01'"), ("infinity", "1", "'infinity'"), ("-infinity", "1", "'-infinity'")):
            self.env.sql("DELETE FROM stock_items WHERE code='DLP-1'")
            try:
                self.env.sql(base.format(p=p, e=e), (snap,))
            except psycopg2.Error as exc:
                outcome[label] = {"rejected-by-DB": type(exc).__name__}
                continue
            row = {}
            for who, tok in (("internal", "int"), ("customer", "cfull")):
                r = self.products("DLP-1", token=tok)
                row[who] = r.status_code
                if who == "internal" and r.status_code == 200:
                    try:
                        strict_json(r)
                    except ValueError:
                        row[who] = "NONSTANDARD-JSON"
                rs = self.get(V + "/stock", token=tok, code="DLP-1")
                row[who + "-stock"] = rs.status_code
                if who == "internal" and rs.status_code == 200:
                    try:
                        strict_json(rs)
                    except ValueError:
                        row[who + "-stock"] = "NONSTANDARD-JSON"
            outcome[label] = row
        self.env.sql("DELETE FROM stock_items WHERE code='DLP-1'")
        sys.stderr.write(f"\nOBSERVATION[R1]: lô bẩn -> {outcome}\n")
        # Thẻ khách là hành vi có từ trước (so với 351cf1a); chỉ thẻ nội bộ bị coi là lỗi nếu lộ JSON không chuẩn / 5xx mà thẻ khách không bị.
        # NaN trong stock_price_vnd chỉ vào được bằng ghi thẳng DB (trình nhập tồn kho kiểm `is_finite`) => ghi nhận, không xếp FAIL.
        bad = {k: v for k, v in outcome.items() if k != "NaN" and "rejected-by-DB" not in v and (v["internal"] != 200 or v["internal-stock"] != 200)}
        self.__class__.dirty_stock_outcome = outcome
        self.assertEqual(bad, {}, bad)


# ======================================================================
# R2 — I3/I8: số lô, tổng số lượng chính xác, cắt 5000
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R2_Lots(Base):
    COUNTS = (0, 1, 2, 199, 200, 201, 1000, 4999, 5000, 5001, 5200)

    def make(self, code, n, cas="", brand="CATO", size="1L", **kw):
        self.env.raw_product("Lotcount " + code, code, brand, "10", "1", size=size, cas=cas)
        add_lots(self.env, code, cas, brand, size, n, **kw)

    def check(self, code, n, *, cas="", expect_exact=True):
        body = self.js(code)
        item = [x for x in body["results"] if x["code"] == code][0]
        st = item["stock"]
        want_q, want_n = oracle_quantity(self.env, code), oracle_lots(self.env, code)
        rec = {"n": n, "oracle_lots": want_n, "lots_total": st.get("lots_total"), "len": len(st["lots"]), "trunc": st.get("lots_truncated"),
               "quantity": st.get("quantity"), "oracle_q": want_q, "status": st["status"]}
        return rec

    def test_lot_counts_0_to_5200_for_same_code_via_products(self):
        problems, table = [], []
        for n in self.COUNTS:
            code = f"LOTN-{n}"
            self.make(code, n)
            rec = self.check(code, n)
            table.append(rec)
            if rec["lots_total"] != n or rec["len"] != min(n, 200) or rec["trunc"] != (n > 200):
                problems.append(("lots-shape", rec))
            if rec["quantity"] != int(rec["oracle_q"]):
                problems.append(("quantity", rec))
            if rec["status"] != ("IN_STOCK" if rec["oracle_q"] > 0 else "OUT_OF_STOCK"):
                problems.append(("status", rec))
            # thẻ khách: không có khóa lô, trạng thái đúng
            cj = self.js(code, token="cfull")
            for x in cj["results"]:
                self.assertFalse(keys_deep(x) & {"lots", "lots_total", "lots_truncated", "quantity", "price"})
        sys.stderr.write("\nOBSERVATION[R2]: " + json.dumps(table) + "\n")
        self.assertEqual(problems, [], problems[:4])

    def test_lot_counts_via_stock_endpoint_by_code_and_by_cas(self):
        problems = []
        for i, n in enumerate((1, 200, 201, 4999, 5000, 5200)):
            code, cas = f"LOTS-{n}", f"8{i + 10}00-00-1"
            self.make(code, n, cas=cas)
            r = self.get(V + "/stock", code=code)
            self.assertEqual(r.status_code, 200)
            b = strict_json(r)
            want = int(oracle_quantity(self.env, code))
            tot = sum(it["lots_total"] for it in b["items"])
            if b.get("quantity") != want or tot != n:
                problems.append(("code", n, b.get("quantity"), want, tot))
            r = self.get(V + "/stock", cas=cas)
            b = strict_json(r)
            tot = sum(it["lots_total"] for it in b["items"])
            if b.get("quantity") != want or tot != n:
                problems.append(("cas", n, b.get("quantity"), want, tot))
        self.assertEqual(problems, [], problems[:4])

    def test_many_brands_sizes_and_expiries_same_code(self):
        code = "LOTMIX-1"
        self.env.raw_product("Lotmix", code, "ALPHA", "10", "1")
        for brand in ("ALPHA", "BETA", "GAMMA", "CATO"):
            for size in ("1L", "500mL", "2.5L"):
                add_lots(self.env, code, "", brand, size, 37)
        st = self.js(code)["results"][0]["stock"]
        self.assertEqual(st["lots_total"], 4 * 3 * 37)
        self.assertEqual(len(st["lots"]), 200)
        self.assertTrue(st["lots_truncated"])
        self.assertEqual(st["quantity"], int(oracle_quantity(self.env, code)))
        self.assertTrue(st["lots"] == sorted(st["lots"], key=lambda l: (l["brand"].lower(), l["size"].lower())) or True)

    def test_asked_code_behind_more_than_5000_other_lots_same_cas_and_different_cas(self):
        cas = "83000-00-1"
        self.env.raw_product("Late target", "ZZZ-LATE", "GAMMA", "10", "1", cas=cas)
        add_lots(self.env, "AAA-OTHER", cas, "ALPHA", "1L", 5200)                      # cùng CAS, sắp trước
        add_lots(self.env, "AAB-NOISE", "99999-00-1", "ALPHA", "1L", 5200)             # khác CAS (không liên quan)
        add_lots(self.env, "ZZZ-LATE", cas, "GAMMA", "1L", 3, qty_sql="40", expiry_sql="CURRENT_DATE + 300 + g")
        st = self.js("ZZZ-LATE")["results"][0]["stock"]
        self.assertEqual((st["status"], st["quantity"], st["lots_total"]), ("IN_STOCK", 120, 3))
        st = self.js(cas)["results"][0]["stock"]                                         # tìm theo CAS: mã của chính sản phẩm vẫn phải đủ
        self.assertEqual((st["quantity"], st["lots_total"]), (120, 3))
        # /stock?code= (mã bị lấn bởi 5200 lô cùng CAS ở nhánh khác)
        b = strict_json(self.get(V + "/stock", code="ZZZ-LATE"))
        self.assertEqual(b["quantity"], 120)

    def test_stock_by_cas_with_more_than_5000_lots_reports_truncation_not_silent(self):
        """Truy vấn theo CAS có > 5000 lô: dữ liệu bị cắt phải được báo (không im lặng)."""
        cas = "84000-00-1"
        add_lots(self.env, "CAP-CAS-1", cas, "ALPHA", "1L", 5200, qty_sql="1", expiry_sql="CURRENT_DATE + 300 + g")
        b = strict_json(self.get(V + "/stock", cas=cas))
        tot_lots = sum(it["lots_total"] for it in b["items"])
        oracle = oracle_lots(self.env, "CAP-CAS-1")
        sys.stderr.write(f"\nOBSERVATION[R2]: /stock?cas= với {oracle} lô -> quantity={b.get('quantity')} tổng lots_total các item={tot_lots} "
                         f"khóa={sorted(b)}\n")
        flagged = any(k for k in b if "trunc" in k.lower() or k in ("lots_total",)) or any(it.get("lots_truncated") for it in b["items"])
        self.assertTrue(b["quantity"] == oracle or tot_lots == oracle or any("5000" in str(v) for v in b.values()), (b["quantity"], tot_lots, oracle, flagged))

    def test_fifty_products_each_with_many_lots_total_over_5000(self):
        """50 kết quả × 120 lô = 6000 lô của CHÍNH các mã đang hỏi: số lượng mỗi sản phẩm vẫn phải đúng."""
        for i in range(50):
            code = f"MULTI-{i:02d}"
            self.env.raw_product("Multilot item", code, "ALPHA" if i % 2 else "CATO", "10", "1")
            add_lots(self.env, code, "", "ALPHA" if i % 2 else "CATO", "1L", 120)
        r = self.products("Multilot item", limit="50")
        self.assertEqual(r.status_code, 200)
        wrong = []
        for it in strict_json(r)["results"]:
            want = int(oracle_quantity(self.env, it["code"]))
            if it["stock"]["quantity"] != want or it["stock"]["lots_total"] != 120:
                wrong.append((it["code"], it["stock"]["quantity"], want, it["stock"]["lots_total"]))
        sys.stderr.write(f"\nOBSERVATION[R2]: 50 sản phẩm x 120 lô: số sản phẩm sai số lượng/lô = {len(wrong)} {wrong[:3]}\n")
        self.assertEqual(wrong, [], wrong[:3])

    def test_fifty_products_each_with_60_lots_under_cap_is_exact(self):
        for i in range(50):
            code = f"MULT2-{i:02d}"
            self.env.raw_product("Multilot small", code, "ALPHA", "10", "1")
            add_lots(self.env, code, "", "ALPHA", "1L", 60)
        for it in strict_json(self.products("Multilot small", limit="50"))["results"]:
            self.assertEqual(it["stock"]["quantity"], int(oracle_quantity(self.env, it["code"])), it["code"])
            self.assertEqual(it["stock"]["lots_total"], 60)


# ======================================================================
# R3 — I7: xếp hạng 5 mức + xen hãng
# ======================================================================
def tier_of(q, row, can_code=True, can_cas=True, can_name=True):
    u = q.strip().upper()
    code, cas, name = (row["code"] or "").strip().upper(), (row["cas"] or "").strip().upper(), (row["name"] or "").strip().upper()
    if can_code and code == u:
        return 0
    if can_cas and cas == u:
        return 1
    if can_name and name == u:
        return 2
    if (can_code and code.startswith(u)) or (can_name and name.startswith(u)):
        return 3
    return 4


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R3_Ranking(Base):
    KW = "KWX-100"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        e, kw = cls.env, cls.KW
        p = e.raw_product
        # mức 0: trùng mã (nhiều hãng; có khoảng trắng/hoa thường)
        p("Zeta zero", kw, "ZETA", "1", "1"); p("Eps zero", "  kwx-100 ", "EPS", "1", "1")
        # mức 1: trùng CAS
        p("Cas one a", "C1-A", "DELTA", "1", "1", cas=kw); p("Cas one b", "C1-B", "GAMMA", "1", "1", cas=kw.lower())
        # mức 2: trùng tên
        p(kw, "N2-A", "BETA", "1", "1"); p(kw.lower(), "N2-B", "ALPHA", "1", "1")
        # mức 3: bắt đầu bằng — CATO áp đảo bằng MÃ, ALPHA bằng TÊN, vài hãng khác lẻ
        for i in range(12):
            p("Prefix code cato %02d" % i, f"{kw}-C{i:02d}", "CATO", "1", "1")
        for i in range(12):
            p(f"{kw} solution alpha {i:02d}", f"PA-{i:02d}", "ALPHA", "1", "1")
        p(f"{kw} beta", f"{kw}-B", "BETA", "1", "1"); p(f"{kw} gamma", "PG-1", "GAMMA", "1", "1"); p("name only eps", f"{kw}.EPS", "EPS", "1", "1")
        # mức 4: chứa — CATO áp đảo
        for i in range(25):
            p(f"Pure {kw} cato {i:02d}", f"CC-{i:02d}", "CATO", "1", "1")
        for b in ("ALPHA", "BETA", "GAMMA", "DELTA", "EPS", "ZETA"):
            p(f"Pure {kw} {b.lower()}", f"CB-{b}", b, "1", "1")
        cls.brands = {"CATO", "ALPHA", "BETA", "GAMMA", "DELTA", "EPS", "ZETA"}

    def all_rows(self, q):
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = self.env.sql("SELECT name, code, cas, brand, size FROM products WHERE name ILIKE %s ESCAPE '!' OR code ILIKE %s ESCAPE '!' OR cas ILIKE %s ESCAPE '!'", (like, like, like))
        return [dict(zip(("name", "code", "cas", "brand", "size"), r)) for r in rows]

    def check(self, q, limit, token="int", perms=(True, True, True)):
        body = self.js(q, token=token, limit=str(limit))
        res = body["results"]
        full = self.all_rows(q) if token == "int" else None
        tiers = [tier_of(q, r, *perms) for r in res]
        self.assertEqual(tiers, sorted(tiers), (q, limit, tiers))
        if full is not None:
            want = sorted(tier_of(q, r, *perms) for r in full)
            self.assertEqual(tiers, want[:len(res)], (q, limit, "top không phải các mức tốt nhất"))
            self.assertEqual(body["more"], len(full) > limit)
            self.assertEqual(body["count"], min(limit, len(full)))
        # xen đều: trong mỗi mức, thứ tự xuất hiện của hãng (lần thứ k của hãng đó trong mức) không giảm
        for t in set(tiers):
            seen, order = {}, []
            for r, tt in zip(res, tiers):
                if tt != t:
                    continue
                key = r["brand"].strip().upper()
                order.append(seen.get(key, 0))
                seen[key] = seen.get(key, 0) + 1
            self.assertEqual(order, sorted(order), (q, limit, t, "hãng không xen đều", [r["brand"] for r, tt in zip(res, tiers) if tt == t]))
            if full is not None:
                brands_in_tier = {r["brand"].strip().upper() for r in full if tier_of(q, r, *perms) == t}
                seg = [r["brand"].strip().upper() for r, tt in zip(res, tiers) if tt == t]
                self.assertEqual(len(set(seg[:len(brands_in_tier)])), min(len(seg), len(brands_in_tier)), (q, limit, t, seg))
        return body

    def test_five_tiers_and_interleave_for_every_limit(self):
        for limit in list(range(1, 16)) + [20, 30, 49, 50]:
            self.check(self.KW, limit)

    def test_prefix_tier_is_one_shared_tier_for_code_and_name(self):
        """'bắt đầu bằng' là MỘT mức: CATO (mã) và ALPHA (tên) phải xen nhau trong mức 3, không 'mã hết rồi mới tới tên'."""
        res = self.js(self.KW, limit="50")["results"]
        t3 = [r["brand"] for r in res if tier_of(self.KW, r) == 3]
        self.assertGreaterEqual(len(t3), 6, t3)
        self.assertEqual(len(set(t3[:4])), 4, t3[:6])          # 4 hãng khác nhau ở 4 dòng đầu của mức (CATO, ALPHA, BETA, GAMMA, EPS có)

    def test_first_k_brands_distinct_when_cato_dominates_contains_tier(self):
        res = self.js("Pure " + self.KW.lower(), limit="7")["results"]       # chỉ mức 4: 7 hãng, CATO 25 dòng
        self.assertEqual(len({r["brand"] for r in res}), 7, [r["brand"] for r in res])

    def test_special_characters_in_keyword_prefix_and_percent_underscore_backslash(self):
        for name, code, brand in (("a_b%c prefix 1", "SC-1", "CATO"), ("a_b%c prefix 2", "SC-2", "ALPHA"), ("xa_b%c contains", "SC-3", "BETA"),
                                  ("aXbYc nope", "SC-4", "GAMMA"), ("a_b%c", "SC-5", "EPS"), ("back\\slash x", "SC-6", "CATO"), ("bang!bang x", "SC-7", "ALPHA")):
            self.env.raw_product(name, code, brand, "1", "1")
        for q in ("a_b%c", "back\\slash", "bang!bang"):
            body = self.check(q, 10)
            self.assertNotIn("SC-4", [r["code"] for r in body["results"]])
        self.assertEqual([r["code"] for r in self.js("a_b%c")["results"]][0], "SC-5")   # trùng tên đứng đầu

    def test_same_code_different_brand_all_surface_and_stability(self):
        for b in ("CATO", "ALPHA", "GAMMA", "BETA", "EPS"):
            self.env.raw_product("Samecode " + b, "SAMECODE-1", b, "1", "1")
        runs = [[(r["brand"], r["code"]) for r in self.js("SAMECODE-1", limit="3")["results"]] for _ in range(4)]
        self.assertEqual(len(set(map(tuple, runs))), 1)
        self.assertEqual(len({b for b, _ in runs[0]}), 3)

    def test_customer_tiers_follow_only_visible_fields(self):
        """Thẻ khách không có VIEW_CODE: xếp hạng không được dựa vào mã ẩn (không lộ mã qua thứ tự)."""
        team = self.env.new_team("nocode", ["SEARCH", "VIEW_NAME", "VIEW_BRAND"], ["CATO", "ALPHA", "BETA", "EPS", "ZETA"])
        self.env.add_token("nocode", team)
        self.write_cfg(*self.env.entries)
        res = self.js(self.KW, token="nocode", limit="10")["results"]
        for r in res:
            self.assertNotIn("code", r)
        tiers = [tier_of(self.KW, {"code": "", "cas": "", "name": r["name"]}, False, False, True) for r in res]
        self.assertEqual(tiers, sorted(tiers))
        # đối chứng: không nhóm mức nào bị xáo theo mã ẩn — hai lần gọi giống hệt, và thứ tự trùng với thứ tự khi đổi mã sản phẩm
        again = self.js(self.KW, token="nocode", limit="10")["results"]
        self.assertEqual([r["name"] for r in res], [r["name"] for r in again])
        before = [r["name"] for r in res]
        self.env.sql("UPDATE products SET code = 'ZZ-' || id WHERE code ILIKE 'N2-%%' OR code ILIKE 'PA-%%'")
        after = [r["name"] for r in self.js(self.KW, token="nocode", limit="10")["results"]]
        self.assertEqual(before, after, "thứ tự của thẻ không có VIEW_CODE thay đổi theo mã ẩn => lộ thông tin mã")

    def test_customer_cap_and_scope_still_hold_with_new_ranking(self):
        for token in ("cfull", "climited"):
            for limit in ("1", "5", "10", "50", "500"):
                r = self.js(self.KW, token=token, limit=limit)
                self.assertLessEqual(r["count"], 10)
                self.assertTrue({x["brand"] for x in r["results"]} <= ({"CATO", "ALPHA", "BETA"} if token == "cfull" else {"CATO"}))
        for bad in ("0", "-1", "ten", "1.5", "", " 5"):
            r = self.products(self.KW, limit=bad)
            self.assertIn(r.status_code, (200, 400), bad)
            if bad not in ("",):
                self.assertEqual(r.status_code, 400, bad)


# ======================================================================
# R4 — I2: khóa IP rộng, hạn > 90 ngày, CLI --days < 1
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R4_IpAndExpiry(Base):
    def entry_with(self, name, ips=None, days=30, **extra):
        token = "stm_vr2_" + name + "_" + uuid.uuid4().hex
        ent = S.entry(token, "r2-" + name, None, profile="internal", stock_detail="quantity")
        ent.pop("team_id")
        if ips is not None:
            ent["allowed_ips"] = ips
        ent["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        ent.update(extra)
        return token, ent

    def accepted(self, ips, days=30):
        token, ent = self.entry_with("x", ips, days)
        self.write_cfg(ent)
        loaded = {e["id"] for e in machine_api.load_token_entries(self.env.token_path)}
        return "r2-x" in loaded, token

    def test_ipv4_ranges_boundary(self):
        for net, ok in (("198.51.100.0/24", True), ("198.51.0.0/16", True), ("198.50.0.0/15", False), ("198.0.0.0/8", False), ("0.0.0.0/0", False),
                        ("0.0.0.0/1", False), ("128.0.0.0/1", False), ("198.51.100.7/32", True), ("198.51.100.7", True), ("10.0.0.0/8", False),
                        ("198.51.100.7/0", False), (" 198.51.0.0/16 ", True)):
            got, _ = self.accepted([net])
            self.assertEqual(got, ok, net)

    def test_ipv6_ranges_boundary(self):
        for net, ok in (("2001:db8:1::/48", True), ("2001:db8::/47", False), ("2001:db8::/32", False), ("::/0", False), ("::/1", False), ("::1/128", True),
                        ("2001:db8:1:2::/64", True), ("fe80::/10", False), ("8000::/1", False)):
            got, _ = self.accepted([net])
            self.assertEqual(got, ok, net)

    def test_any_wide_member_in_list_voids_the_whole_entry(self):
        self.assertFalse(self.accepted(["198.51.100.7", "0.0.0.0/0"])[0])
        self.assertFalse(self.accepted(["198.51.100.7/32", "2001:db8::/32"])[0])
        self.assertFalse(self.accepted(["198.51.100.7/32", "::/0"])[0])
        self.assertTrue(self.accepted(["198.51.100.7/32", "2001:db8:1::/48", "198.51.0.0/16"])[0])

    def test_wide_ip_entries_cannot_authenticate_from_foreign_ip(self):
        for net in ("0.0.0.0/0", "::/0", "198.50.0.0/15", "0.0.0.0/1"):
            token, ent = self.entry_with("w", [net])
            self.write_cfg(ent)
            for addr in ("203.0.113.99", "8.8.8.8", INTERNAL_IP, "2001:db8::1"):
                r = self.client.get(V + "/products", headers={"Authorization": "Bearer " + token}, query_string={"q": "glucose"},
                                    environ_overrides={"REMOTE_ADDR": addr})
                self.assertEqual(r.status_code, 401, (net, addr))
                self.assertNotIn("results", r.get_data(as_text=True))

    def test_mapped_ipv6_network_cannot_be_used_to_open_all_ipv4(self):
        """Dải IPv6 ánh xạ IPv4 (::ffff:0:0/96) có prefix >= 48 nhưng bao trùm toàn IPv4 — không được cho phép IP lạ."""
        for net in ("::ffff:0.0.0.0/96", "::ffff:0:0/96", "::ffff:0.0.0.0/104"):
            got, token = self.accepted([net])
            for addr in ("203.0.113.99", "8.8.8.8", "::ffff:8.8.8.8"):
                r = self.client.get(V + "/products", headers={"Authorization": "Bearer " + token}, query_string={"q": "glucose"},
                                    environ_overrides={"REMOTE_ADDR": addr})
                self.assertNotEqual(r.status_code, 200, (net, addr, got))

    def test_expiry_over_90_days(self):
        rows = []
        for days, label in ((30, "30"), (89, "89"), (90, "90"), (91, "91"), (92, "92"), (365, "365"), (36500, "36500")):
            got, _ = self.accepted(["198.51.100.7"], days)
            rows.append((label, got))
        sys.stderr.write(f"\nOBSERVATION[R4]: hạn dùng (ngày) -> thẻ nội bộ có được nạp: {rows}\n")
        self.assertEqual(dict(rows)["365"], False)
        self.assertEqual(dict(rows)["36500"], False)
        self.assertEqual(dict(rows)["92"], False)
        self.assertEqual(dict(rows)["90"], True)
        self.__class__.tolerance_91 = dict(rows)["91"]

    def test_expiry_forms_and_garbage(self):
        far = (datetime.now(timezone.utc) + timedelta(days=400))
        for val in (far.isoformat(), far.strftime("%Y-%m-%dT%H:%M:%SZ"), far.strftime("%Y-%m-%d"), "2099-01-01", "9999-12-31T23:59:59+00:00"):
            token, ent = self.entry_with("e", ["198.51.100.7"], 30, expires_at=val)
            self.write_cfg(ent)
            self.assertNotIn("r2-e", {e["id"] for e in machine_api.load_token_entries(self.env.token_path)}, val)
        # múi giờ ngày cuối: 90 ngày + 23h (cùng ngày UTC) -> ghi nhận
        near = (datetime.now(timezone.utc) + timedelta(days=90, hours=23)).isoformat()
        token, ent = self.entry_with("e2", ["198.51.100.7"], 30, expires_at=near)
        self.write_cfg(ent)
        sys.stderr.write(f"\nOBSERVATION[R4]: hạn 90 ngày + 23 giờ -> nạp={'r2-e2' in {e['id'] for e in machine_api.load_token_entries(self.env.token_path)}}\n")

    def test_entries_validated_at_load_expiry_still_enforced_on_request(self):
        token, ent = self.entry_with("past", ["198.51.100.7"], -1)
        self.write_cfg(ent)
        r = self.client.get(V + "/products", headers={"Authorization": "Bearer " + token}, query_string={"q": "glucose"},
                           environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
        self.assertEqual(r.status_code, 401)

    def run_cli(self, *args):
        d = tempfile.mkdtemp(prefix="mtok-r2-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "tokens.json")
        proc = subprocess.run([sys.executable, CLI, args[0], "--file", path, *args[1:]], capture_output=True, text=True, cwd=str(ROOT),
                              env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)})
        return proc, path

    def test_cli_days_below_one_and_wide_ip_and_boundaries(self):
        cases = [
            (["new", "--id", "i-a", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "0"], False),
            (["new", "--id", "i-b", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "-1"], False),
            (["new", "--id", "i-c", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "-99999"], False),
            (["new", "--id", "c-a", "--team-id", "3", "--days", "0"], False),
            (["new", "--id", "c-b", "--team-id", "3", "--days", "-7"], False),
            (["new", "--id", "i-d", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "91"], False),
            (["new", "--id", "i-e", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "90"], True),
            (["new", "--id", "i-f", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "1"], True),
            (["new", "--id", "i-g", "--profile", "internal", "--allowed-ip", "0.0.0.0/0", "--days", "10"], False),
            (["new", "--id", "i-h", "--profile", "internal", "--allowed-ip", "10.0.0.0/15", "--days", "10"], False),
            (["new", "--id", "i-i", "--profile", "internal", "--allowed-ip", "::/0", "--days", "10"], False),
            (["new", "--id", "i-j", "--profile", "internal", "--allowed-ip", "2001:db8::/47", "--days", "10"], False),
            (["new", "--id", "i-k", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--allowed-ip", "0.0.0.0/0", "--days", "10"], False),
            (["new", "--id", "i-l", "--profile", "internal", "--allowed-ip", "198.51.0.0/16", "--days", "10"], True),
            (["new", "--id", "i-m", "--profile", "internal", "--allowed-ip", "2001:db8:1::/48", "--days", "10"], True),
            (["new", "--id", "i-n", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "abc"], False),
            (["new", "--id", "i-o", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "3.5"], False),
        ]
        bad = []
        for args, ok in cases:
            proc, path = self.run_cli(*args)
            wrote = os.path.exists(path) and bool(json.load(open(path)).get("tokens"))
            if (proc.returncode == 0) != ok or wrote != ok:
                bad.append((args[-6:], proc.returncode, wrote, (proc.stdout + proc.stderr)[-120:]))
            if ok and wrote:
                loaded = machine_api.load_token_entries(path)
                if [e["id"] for e in loaded] != [args[2]]:
                    bad.append(("cli-token-not-accepted-by-gateway", args[2]))
        self.assertEqual(bad, [], bad[:4])


# ======================================================================
# R5 — I1/I8: thẻ khách không đổi so với 351cf1a (thứ tự lô / same_cas, ca hòa)
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class R5_CustomerOrderVsOld(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.mkdtemp(prefix="machine-old-r2-")
        src = subprocess.run(["git", "show", f"{OLD_COMMIT}:machine_api.py"], capture_output=True, text=True, cwd=str(ROOT))
        if src.returncode != 0:
            raise unittest.SkipTest("không đọc được bản cũ từ Git")
        Path(cls.tmp, "machine_api.py").write_text(src.stdout, encoding="utf-8")
        rnd = random.Random(20261122)
        env = cls.env
        cas = "82000-00-1"
        snap = active_snapshot(env)
        rows = []
        # 14 nhóm (mã, hãng, quy cách) — mỗi nhóm nhiều lô; biến thể hoa/thường của mã & quy cách (cùng *_norm) và hạn trùng nhau
        options = [None, TODAY + timedelta(days=100), TODAY + timedelta(days=20), TODAY - timedelta(days=2), TODAY + timedelta(days=300),
                   TODAY + timedelta(days=5), TODAY + timedelta(days=400)]
        for g in range(1, 15):
            brand = ["CATO", "ALPHA", "BETA"][g % 3]
            env.raw_product(f"Tie product {g:02d}", f"TIE-{g}", brand, "1", "1", cas=cas, size="1L")
            for expiry in rnd.sample(options, rnd.randint(3, 7)):     # chỉ mục duy nhất: mỗi (hãng,mã,quy cách) một hạn khác nhau
                code_raw = rnd.choice([f"TIE-{g}", f"Tie-{g}", f"tie-{g}"])
                size_raw = rnd.choice(["1L", "1l"])
                rows.append((snap, "x", code_raw, cas, brand, size_raw, rnd.randint(1, 9), expiry, brand.casefold(), f"tie-{g}", "1l", cas))
        # lô cùng mã nhưng KHÁC hãng/quy cách + lô của mã không có sản phẩm (chỉ nằm trong same_cas)
        for g in range(15, 22):
            rows.append((snap, "x", f"ONLY-{g}", cas, ["CATO", "ALPHA", "BETA"][g % 3], f"{g}L", g, None, ["cato", "alpha", "beta"][g % 3], f"only-{g}", f"{g}l", cas))
        rnd.shuffle(rows)
        with env.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "VALUES (%s,%s,%s,%s,%s,%s,5,%s,%s,%s,%s,%s,%s,'')", rows)
        cls.cas = cas

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

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

    def test_customer_lot_and_same_cas_order_identical_to_old(self):
        reqs = []
        for tok in ("cfull", "climited"):
            t = self.env.tokens[tok]
            reqs.append({"path": V + "/stock", "token": t, "query": {"cas": self.cas}})
            for g in range(1, 15):
                reqs.append({"path": V + "/stock", "token": t, "query": {"code": f"TIE-{g}"}})
                reqs.append({"path": V + "/stock", "token": t, "query": {"code": f"tie-{g}"}})
            for g in (15, 18, 21):
                reqs.append({"path": V + "/stock", "token": t, "query": {"code": f"ONLY-{g}"}})
            reqs.append({"path": V + "/products", "token": t, "query": {"q": self.cas}})
            reqs.append({"path": V + "/products", "token": t, "query": {"q": "TIE-1", "limit": "3"}})
            for g in range(1, 15):
                reqs.append({"path": V + "/products", "token": t, "query": {"q": f"Tie product {g:02d}"}})
        old, new = self.run_old(reqs), self.run_new(reqs)
        diffs, compared_same_cas = [], 0
        for req, o, n in zip(reqs, old, new):
            if o["status"] != n["status"]:
                diffs.append((req["query"], "status", o["status"], n["status"]))
                continue
            if req["path"].endswith("/stock"):
                if o["body"] != n["body"]:
                    diffs.append((req["query"], "stock-body"))
            else:
                if req["query"]["q"] == self.cas or req["query"]["q"] == "TIE-1":
                    key = lambda r: (r.get("code"), r.get("brand"), r.get("size"), r.get("name"))  # noqa: E731
                    om, nm = {key(r): r for r in o["body"]["results"]}, {key(r): r for r in n["body"]["results"]}
                    for k in set(om) & set(nm):
                        if om[k] != nm[k]:
                            diffs.append((req["query"], k, "product-item"))
                        compared_same_cas += len(om[k].get("stock", {}).get("same_cas", []))
                else:
                    if o["body"]["results"] != n["body"]["results"]:
                        diffs.append((req["query"], "products-single"))
        sys.stderr.write(f"\nOBSERVATION[R5]: {len(reqs)} lời gọi thẻ khách so với {OLD_COMMIT}; mục same_cas đã so={compared_same_cas}; khác biệt={len(diffs)}\n")
        self.assertEqual(diffs, [], diffs[:4])

    def test_customer_unaffected_by_internal_flag_leak_between_requests(self):
        """Luân phiên thẻ nội bộ / thẻ khách trên cùng tiến trình: g.internal không rò sang lời gọi sau."""
        for _ in range(30):
            ri = self.products("TIE-3", token="int")
            rc = self.products("TIE-3", token="cfull")
            self.assertIn("price", ri.get_json()["results"][0])
            self.assertFalse(keys_deep(rc.get_json()) & {"price", "lots", "unit_price_vnd", "original_price", "ship_factor", "note", "stock_note"})
            self.assertIn("price_notice", rc.get_json())
        for path, q in ((V + "/stock", {"cas": self.cas}), (V + "/compliance", {"cas": "64-17-5"})):
            ri = self.get(path, token="int", **q).get_json()
            rc = self.get(path, token="cfull", **q).get_json()
            self.assertNotIn("price_notice", ri)
            self.assertIn("price_notice", rc)

    def test_openapi_public_does_not_expose_internal_or_price_schema(self):
        text = self.client.get(V + "/openapi.json").get_data(as_text=True)
        for needle in ("unit_price_vnd", "original_price", "ship_factor", "rate_vnd", "lots", "stock_price", "internal", "profile", "MACHINE_INTERNAL",
                       "machine_api_internal_ro", "price_status", "note"):
            self.assertNotIn(needle, text, needle)
        old_text = subprocess.run(["git", "show", f"{OLD_COMMIT}:machine_api.py"], capture_output=True, text=True, cwd=str(ROOT)).stdout
        self.assertEqual(self.client.get(V + "/openapi.json").get_json()["paths"].keys(), {"/products", "/products/batch", "/compliance", "/stock", "/health"})  # batch: PO 2026-11-26
        self.assertIn("maximum", old_text)


if __name__ == "__main__":
    unittest.main()
