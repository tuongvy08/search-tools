"""VERIFIER độc lập lượt 3 cho POST /machine/v1/products/batch — hợp đồng v2 (specs/machine-api-batch/VERIFICATION.md).

Tập trung B4 v2: (1) mọi khối của một mục không phụ thuộc mục khác trong gói; (2) gợi ý cùng CAS đúng ĐỊNH NGHĨA
(5.000 lô đầu tiên có CAS X, mã khác C, hãng thẻ được thấy, thứ tự brand_norm, code_norm, size_norm, expiry NULLS LAST;
rồi nhóm/lọc còn hàng/cắt 5|10) — oracle viết lại bằng SQL độc lập; (3) y hệt /products?q=<mã>&limit=1 khi trang
/products chỉ có sản phẩm đó; (4) giá/pháp chế/tồn kho (trừ same_cas) y hệt /products trang mặc định.
Ca biên: lô của chính mã hỏi chen trước cửa sổ (3.000 lô), lô còn hàng duy nhất nằm đúng vị trí thứ 5.000.
Chạy: PYTHONPATH=.:tests:tests/independent DATABASE_URL=... .venv/bin/python -m unittest \
      tests.independent.test_machine_api_batch_round3
"""

import json
import sys
import unittest

import machine_api
from stock import expiry_state, normalized_text

from test_machine_api_batch_independent import V
from test_machine_api_batch_round2 import R2Base, STOCK_COLS

TOKENS = (("internal", 50), ("cust", 10), ("wide", 10), ("wideq", 10))
IDENT = ("name", "code", "brand", "size", "cas")


class R3Base(R2Base):
    @classmethod
    def seed(cls):
        super().seed()

        def product(name, code, cas, brand, size, price="12.5", ship="1.3"):
            cls.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'n3',%s)", (name, code, cas, brand, size, price, ship, brand))
        snap = cls.sql("SELECT active_snapshot_id FROM stock_state WHERE singleton = TRUE")[0][0]

        def lots(code, cas, brand, n, qty_expr="3", start=0):
            cls.sql(f"INSERT INTO stock_items({STOCK_COLS}) SELECT %s, 'L3', %s, %s, %s, '1L', 1, {qty_expr}, "
                    "current_date + 30 + %s + g, lower(%s), lower(%s), '1l', %s, '' FROM generate_series(1, %s) g",
                    (snap, code, cas or None, brand, start, brand, code, cas or None, n))

        # CAS 551-00-1: mã hỏi AAB-OWN có 3.000 lô xếp TRƯỚC mọi mã khác; AAC-OTHER 5.000 lô, chỉ lô THỨ 5.000 còn hàng.
        product("R3 own heavy", "AAB-OWN", "551-00-1", "ALPHA", "1L")
        product("R3 plain beta", "PLAIN-551", "551-00-1", "BETA", "1L")
        product("R3 plain zeta", "PLAIN-551", "551-00-1", "ZETA", "2L")
        lots("AAB-OWN", "551-00-1", "ALPHA", 3000)
        lots("AAB-OWN", "999-55-5", "ALPHA", 3, start=9000)  # lô của chính mã nhưng CAS khác
        lots("AAC-OTHER", "551-00-1", "ALPHA", 5000, qty_expr="CASE WHEN g = 5000 THEN 2 ELSE 0 END")
        lots("BBB-551", "551-00-1", "BETA", 2)
        lots("ZZQ-LAST", "551-00-1", "ZETA", 1)
        # CAS 552-00-2: 12 mã khác nhau, mỗi mã 1 lô còn hàng -> cắt 10 (nội bộ) / 5 (khách)
        product("R3 ask 552", "ASK-552", "552-00-2", "ALPHA", "1L")
        for i in range(1, 13):
            lots(f"S552-{i:02d}", "552-00-2", "ALPHA" if i % 2 else "BETA", 1)
        # Một mã xuất hiện ở hai sản phẩm có hai CAS khác nhau, cả hai CAS đều có nhiều lô của mã khác
        product("R3 twin a", "TWIN-9", "553-00-3", "ALPHA", "1L")
        product("R3 twin b", "TWIN-9", "554-00-4", "BETA", "1L")
        lots("TWIN-9", "553-00-3", "ALPHA", 10)
        lots("AAA-553", "553-00-3", "ALPHA", 5001, qty_expr="CASE WHEN g > 5000 THEN 1 ELSE 0 END")
        lots("ABC-554", "554-00-4", "BETA", 4)
        # Lại ca FAIL lượt 2 (trần lô theo mã) và mã hai CAS > 5.000 lô
        product("Big lot code", "BIGLOT-A", "", "ALPHA", "1L")
        product("Small lot code", "ZLOT-B", "", "ZETA", "1L")
        lots("BIGLOT-A", "", "ALPHA", machine_api.EXACT_LOT_SCAN_CAP)
        lots("ZLOT-B", "", "ZETA", 3, qty_expr="5")
        product("Dual a", "DUAL-1", "881-11-1", "ALPHA", "1L")
        product("Dual z", "DUAL-1", "882-22-2", "ZETA", "1L")
        lots("AAA-881", "881-11-1", "ALPHA", machine_api.SAME_CAS_LOT_SCAN_CAP + 1)
        lots("ZZZ-882", "882-22-2", "ZETA", 1)

    MIXED = [{"code": "AAB-OWN"}, {"code": "PLAIN-551"}, {"cas": "551-00-1"}, {"code": "ASK-552"},
             {"cas": "552-00-2"}, {"code": "TWIN-9"}, {"cas": "553-00-3"}, {"code": "VX-100"}, {"code": "MULTI-1"},
             {"code": "CCC-ASK"}, {"cas": "777-77-7"}, {"code": "ZZZ-BIG"}, {"cas": "555-55-5"}, {"code": "ETH-AL-1L"},
             {"code": "LOT-205"}, {"code": " aab-own "}, {"code": "PLAIN-551"}, {"code": "BIGLOT-A"}, {"code": "ZLOT-B"},
             {"code": "DUAL-1"}, {"cas": "881-11-1"}, {"code": "NOPE-R3"}, {"code": 5}]

    def team_of(self, token):
        return {"cust": self.team_full, "wide": self.team_wide, "wideq": self.team_wide, "limited": self.team_limited}.get(token)

    def expected_same_cas(self, token, code, cas):
        """Oracle theo đúng định nghĩa B4 v2, viết lại độc lập bằng SQL."""
        team = self.team_of(token)
        vis = "" if team is None else " AND i.brand IN (SELECT brand FROM team_brands WHERE team_id = %s)"
        args = (normalized_text(cas), normalized_text(code)) + (() if team is None else (team,))
        rows = self.sql(
            "SELECT i.code, i.code_norm, i.brand, i.size, i.quantity, i.expiry_date FROM stock_items i "
            "JOIN stock_state st ON st.singleton AND st.active_snapshot_id = i.snapshot_id "
            "WHERE i.cas_norm = %s AND i.code_norm <> %s" + vis +
            " ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST LIMIT 5000", args)
        groups = {}
        for c, cn, brand, size, qty, exp in rows:
            state = expiry_state(exp)[0]
            if not (qty > 0 and state != "expired"):
                continue
            g = groups.setdefault((cn, normalized_text(brand), normalized_text(size)),
                                  {"code": c, "brand": brand, "size": size, "lots": []})
            g["lots"].append((qty, state))
        out = []
        for g in groups.values():
            item = {"code": g["code"], "brand": g["brand"], "size": g["size"], "status": "IN_STOCK",
                    "expiry_warning": all(s == "near_expiry" for _q, s in g["lots"])}
            if token in ("wideq", "internal"):  # thẻ nội bộ luôn ở mức số lượng (machine_api._validate_entry)
                item["quantity"] = sum(q for q, _s in g["lots"])
            out.append(item)
        limit = 10 if token == "internal" else 5
        return out[:limit], len(out) > limit

    def call(self, items, token, limit, include=("compliance", "stock")):
        machine_api.rate_limiter.reset()
        return self.ok(items, token=token, include=list(include), max_per_item=limit)


class R3_B4_Independence(R3Base):
    def test_each_item_equals_its_single_item_batch(self):
        diffs = []
        for token, limit in TOKENS:
            for include in (("compliance", "stock"), ("stock",)):
                mixed = self.call(self.MIXED, token, limit, include)["items"]
                for k, item in enumerate(self.MIXED):
                    alone = self.call([item], token, limit, include)["items"][0]
                    got = {key: v for key, v in mixed[k].items() if key != "position"}
                    exp = {key: v for key, v in alone.items() if key != "position"}
                    if got != exp:
                        diffs.append((token, include, item, [key for key in exp if got.get(key) != exp.get(key)]))
        sys.stderr.write(f"\nOBSERVATION[R3-B4-independence]: diffs={diffs[:6]}\n")
        self.assertEqual(diffs, [])

    def test_order_permutations_do_not_change_items(self):
        base = self.call(self.MIXED, "internal", 50)["items"]
        rev = self.call(list(reversed(self.MIXED)), "internal", 50)["items"]
        n = len(self.MIXED)
        bad = [k for k in range(n) if {x: v for x, v in base[k].items() if x != "position"}
               != {x: v for x, v in rev[n - 1 - k].items() if x != "position"}]
        self.assertEqual(bad, [])


class R3_B4_SameCasDefinition(R3Base):
    def test_same_cas_matches_contract_definition(self):
        diffs, checked = [], 0
        for token, limit in TOKENS:
            body = self.call(self.MIXED, token, limit, ("stock",))
            for item in body["items"]:
                for res in item["results"]:
                    if not normalized_text(res.get("cas")):
                        self.assertNotIn("same_cas", res["stock"])
                        continue
                    checked += 1
                    exp, more = self.expected_same_cas(token, res["code"], res["cas"])
                    if (res["stock"].get("same_cas"), res["stock"].get("same_cas_more")) != (exp, more):
                        diffs.append((token, res["code"], res["brand"], res["cas"],
                                      res["stock"].get("same_cas"), res["stock"].get("same_cas_more"), exp, more))
        sys.stderr.write(f"\nOBSERVATION[R3-B4-definition]: checked={checked} diffs={diffs[:6]}\n")
        self.assertGreater(checked, 60)
        self.assertEqual(diffs, [])

    def test_boundary_5000th_lot_and_own_lots_before_window(self):
        body = self.call([{"code": "AAB-OWN"}, {"code": "PLAIN-551"}], "internal", 50, ("stock",))
        own = body["items"][0]["results"][0]["stock"]
        self.assertEqual([x["code"] for x in own["same_cas"]], ["AAC-OTHER"])  # chỉ lô thứ 5.000 còn hàng
        self.assertEqual(own["lots_total"], 3003)
        plain = {r["brand"]: r["stock"] for r in body["items"][1]["results"]}
        self.assertEqual([x["code"] for x in plain["BETA"]["same_cas"]], ["AAB-OWN"])

    def test_equals_products_limit_1(self):
        diffs, checked = [], 0
        for token, limit in TOKENS:
            body = self.call(self.MIXED, token, limit)
            for item in body["items"]:
                for res in item["results"]:
                    machine_api.rate_limiter.reset()
                    one = self.get(V + "/products", token=token, q=res["code"], limit="1").get_json()["results"]
                    if not one or any(one[0].get(k) != res.get(k) for k in IDENT):
                        continue
                    checked += 1
                    if one[0] != res:
                        diffs.append((token, res["code"], res["brand"],
                                      {k: (res.get(k), one[0].get(k)) for k in res if res.get(k) != one[0].get(k)}))
        sys.stderr.write(f"\nOBSERVATION[R3-B4-limit1]: checked={checked} diffs={json.dumps(diffs, ensure_ascii=False, default=str)[:800]}\n")
        self.assertGreater(checked, 30)
        self.assertEqual(diffs, [])


class R3_B4_ProductsDefaultPage(R3Base):
    """Giá, pháp chế, stock.status/quantity/pack_confirmed/lots/lots_total/lots_truncated y hệt /products?q=<mã>."""

    def test_non_same_cas_blocks_equal_products_page(self):
        diffs, checked = [], 0
        for token, limit in TOKENS:
            body = self.call(self.MIXED, token, limit)
            for item in body["items"]:
                for res in item["results"]:
                    machine_api.rate_limiter.reset()
                    page = self.get(V + "/products", token=token, q=res["code"], limit=str(limit)).get_json()["results"]
                    same = [r for r in page if all(r.get(k) == res.get(k) for k in IDENT)]
                    if len(same) != 1:
                        diffs.append((token, res["code"], res["brand"], "not-on-page", len(same)))
                        continue
                    checked += 1
                    a = json.loads(json.dumps(res))
                    b = json.loads(json.dumps(same[0]))
                    for blk in (a, b):
                        blk.get("stock", {}).pop("same_cas", None)
                        blk.get("stock", {}).pop("same_cas_more", None)
                    if a != b:
                        diffs.append((token, res["code"], res["brand"], [k for k in a if a.get(k) != b.get(k)]))
        sys.stderr.write(f"\nOBSERVATION[R3-B4-page]: checked={checked} diffs={diffs[:8]}\n")
        self.assertGreater(checked, 60)
        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main()
