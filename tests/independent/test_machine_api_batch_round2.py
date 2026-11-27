"""VERIFIER độc lập lượt 2 cho POST /machine/v1/products/batch (specs/machine-api-batch/VERIFICATION.md).

Bổ sung cho tests/independent/test_machine_api_batch_independent.py (lượt 1, chạy lại nguyên trạng).
Mỗi lớp cố tìm một trường hợp làm claim sai. Dữ liệu riêng trong database thử tạm (pg_temp_db).
Chạy: PYTHONPATH=.:tests:tests/independent DATABASE_URL=... .venv/bin/python -m unittest \
      tests.independent.test_machine_api_batch_round2
"""

import json
import sys
import unittest

import machine_api

from test_machine_api_batch_independent import VBase, V

STOCK_COLS = ("snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
              "brand_norm,code_norm,size_norm,cas_norm,stock_note")


class R2Base(VBase):
    def products_item(self, token, res, limit):
        single = self.get(V + "/products", token=token, q=res.get("code"), limit=str(limit)).get_json()
        same = [r for r in single["results"]
                if all(r.get(k) == res.get(k) for k in ("name", "code", "brand", "size", "cas"))]
        return same

    def diffs(self, token, items, limit, include=("compliance", "stock")):
        body = self.ok(items, token=token, include=list(include), max_per_item=limit)
        out, checked = [], 0
        for item in body["items"]:
            for res in item["results"]:
                machine_api.rate_limiter.reset()
                same = self.products_item(token, res, limit)
                checked += 1
                if len(same) != 1:
                    out.append((res.get("code"), res.get("brand"), "not-unique-in-/products", len(same)))
                    continue
                ref = same[0]
                if include != ("compliance", "stock"):
                    ref = {k: v for k, v in ref.items()
                           if k not in ("compliance", "stock") or k in include}
                if ref != res:
                    out.append((res.get("code"), res.get("brand"),
                                {k: (res.get(k), ref.get(k)) for k in set(res) | set(ref) if res.get(k) != ref.get(k)}))
        return checked, out


# ---------------------------------------------------------------------------------------------------------------
# B2 — mục có ký tự lạ không làm hỏng cả lời gọi (lặp lại lỗi lượt 1 + biến thể)
# ---------------------------------------------------------------------------------------------------------------
class R2_B2_Unicode(R2Base):
    def test_surrogate_variants_only_invalidate_their_item(self):
        cases = [
            r'{"items": [{"code": "VX-100"}, {"code": "AB\ud800CD"}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code": "VX-100", "ref": "x\udc00"}, {"code": "MULTI-1"}]}',
            r'{"items": [{"cas": "\udfff"}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code": " \ud800 "}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code": "\ud800VX-100"}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code\ud800": "VX-100"}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code": ["\ud800"]}, {"code": "MULTI-1"}]}',
            r'{"items": [{"code": "VX-100", "ref": {"a": "\ud800"}}, {"code": "MULTI-1"}]}',
        ]
        for data in cases:
            r = self.raw(data)
            self.assertEqual(r.status_code, 200, (data, r.status_code, r.get_data(as_text=True)[:200]))
            items = r.get_json()["items"]
            bad = 1 if len(items) == 3 else 0
            self.assertIn(len(items), (2, 3), data)
            self.assertEqual(items[bad]["status"], "INVALID", data)
            self.assertTrue(items[bad]["message"])
            self.assertEqual(items[-1]["status"], "FOUND", data)
            if bad:
                self.assertEqual(items[0]["status"], "FOUND", data)
            self.assertNotIn("\\ud8", r.get_data(as_text=True).lower())

    def test_valid_astral_and_unicode_whitespace(self):
        data = '{"items": [{"code": "VX-100", "ref": "\\ud83d\\ude00 dong 1"}, {"code": "\\ud83d\\ude00"}, ' \
               '{"code": "\\u00a0VX-100\\u3000"}]}'
        r = self.raw(data)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        items = r.get_json()["items"]
        self.assertEqual(items[0]["ref"], "\U0001F600 dong 1")
        self.assertEqual(items[1]["status"], "NOT_FOUND")
        self.assertIn(items[2]["status"], ("FOUND", "NOT_FOUND"))
        sys.stderr.write(f"\nOBSERVATION[R2-B2-nbsp]: NBSP/ideographic-space bao quanh -> {items[2]['status']}\n")

    def test_surrogate_in_top_level_or_include_is_clean_400(self):
        for data in (r'{"items": [{"code": "VX-100"}], "x\ud800": 1}',
                     r'{"items": [{"code": "VX-100"}], "include": ["\ud800"]}'):
            r = self.raw(data)
            self.assertEqual(r.status_code, 400, data)
            self.assertNotIn("\\ud8", r.get_data(as_text=True).lower())

    def test_c1_and_trailing_controls(self):
        b = self.ok([{"code": "VX\u0085100"}, {"code": "VX\u009f100"}, {"code": "VX-100\u0085"},
                     {"code": "VX-100\x1f"}, {"code": "VX-100"}])
        st = [i["status"] for i in b["items"]]
        sys.stderr.write(f"\nOBSERVATION[R2-B2-controls]: {st}\n")
        self.assertEqual(st[0], "INVALID")
        self.assertEqual(st[1], "INVALID")
        self.assertEqual(st[4], "FOUND")

    def test_malformed_bodies_are_clean_4xx(self):
        for data in ("[" * 50000 + "]" * 50000, '{"items": ' + "[" * 50000 + "]" * 50000 + "}",
                     b'{"items": [{"code": "\xff\xfe"}]}', b'\xef\xbb\xbf{"items": [{"code": "VX-100"}]}',
                     '{"items": [{"code": "VX-100"}], "max_per_item": 1e400}',
                     '{"items": [{"code": NaN}], "max_per_item": 10}',
                     '{"items": [{"code": "VX-100"}], "max_per_item": ' + "9" * 5000 + "}"):
            r = self.raw(data)
            text = r.get_data(as_text=True)
            sys.stderr.write(f"\nOBSERVATION[R2-B2-malformed]: {str(data)[:30]!r}... -> {r.status_code}\n")
            self.assertLess(r.status_code, 500, str(data)[:60])
            self.assertNotIn("Traceback", text)


# ---------------------------------------------------------------------------------------------------------------
# B4 — trùng khớp /products: các cửa sổ lô (trần lô theo mã, gợi ý cùng CAS) trong ca biên
# ---------------------------------------------------------------------------------------------------------------
class R2_B4_LotWindows(R2Base):
    @classmethod
    def seed(cls):
        super().seed()

        def product(name, code, cas, brand, size):
            cls.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                    "VALUES (%s,%s,%s,%s,%s,'12.5','1.3','',%s)", (name, code, cas, brand, size, brand))
        snap = cls.sql("SELECT active_snapshot_id FROM stock_state WHERE singleton = TRUE")[0][0]

        def lots(code, cas, brand, n, qty=3):
            cls.sql(f"INSERT INTO stock_items({STOCK_COLS}) SELECT %s, 'L', %s, %s, %s, '1L', 1, %s, "
                    "current_date + 30 + g, lower(%s), lower(%s), '1l', %s, '' FROM generate_series(1, %s) g",
                    (snap, code, cas, brand, qty, brand, code, cas, n))

        # (a) Trần lô THEO MÃ (EXACT_LOT_SCAN_CAP) dùng chung cho cả gói?
        product("Big lot code", "BIGLOT-A", "", "ALPHA", "1L")
        product("Small lot code", "ZLOT-B", "", "ZETA", "1L")
        lots("BIGLOT-A", "", "ALPHA", machine_api.EXACT_LOT_SCAN_CAP)
        lots("ZLOT-B", "", "ZETA", 3, qty=5)
        # (b) Một mã, hai CAS khác nhau (khác hãng) — /products dùng chung trần lô cùng CAS cho cả trang
        product("Dual a", "DUAL-1", "881-11-1", "ALPHA", "1L")
        product("Dual z", "DUAL-1", "882-22-2", "ZETA", "1L")
        lots("AAA-881", "881-11-1", "ALPHA", machine_api.SAME_CAS_LOT_SCAN_CAP + 1)
        lots("ZZZ-882", "882-22-2", "ZETA", 1)
        # (c) /products?q=<mã> trả thêm mã "chứa" cùng CAS; lô của mã đó xếp sau cửa sổ 5000
        product("Ask contains", "CCC-ASK-X", "777-77-7", "ALPHA", "1L")
        lots("CCC-ASK-X", "777-77-7", "ALPHA", 1)
        # (d) Gợi ý cùng CAS không được lộ lô của hãng ngoài team (thẻ khách cust: ALPHA, BETA)
        product("Vis probe", "VISX-1", "990-00-1", "ALPHA", "1L")
        lots("ALPHA-990", "990-00-1", "ALPHA", 1)
        lots("ZETA-990", "990-00-1", "ZETA", 1)

    def test_code_lot_cap_is_not_shared_across_batch(self):
        alone = self.ok([{"code": "ZLOT-B"}], include=["stock"])["items"][0]["results"][0]["stock"]
        paired = self.ok([{"code": "BIGLOT-A"}, {"code": "ZLOT-B"}], include=["stock"])["items"][1]["results"][0]["stock"]
        p = self.get(V + "/products", token="internal", q="ZLOT-B").get_json()["results"][0]["stock"]
        sys.stderr.write(f"\nOBSERVATION[R2-B4-codecap]: ZLOT-B status/lots_total: batch-alone="
                         f"{(alone['status'], alone.get('lots_total'))} batch-with-BIGLOT-A={(paired['status'], paired.get('lots_total'))}"
                         f" /products={(p['status'], p.get('lots_total'))}\n")
        checked, diffs = self.diffs("internal", [{"code": "BIGLOT-A"}, {"code": "ZLOT-B"}], 50, include=("stock",))
        self.assertEqual(checked, 2)
        self.assertEqual(alone, paired)
        self.assertEqual(diffs, [])

    def test_same_code_two_cas_values(self):
        for token, limit in (("internal", 50), ("wide", 10)):
            checked, diffs = self.diffs(token, [{"code": "DUAL-1"}], limit, include=("stock",))
            self.assertEqual(checked, 2, token)
            if diffs:
                sys.stderr.write(f"\nOBSERVATION[R2-B4-dualcas-{token}]: {json.dumps(diffs, ensure_ascii=False)[:600]}\n")
            self.assertEqual(diffs, [], token)

    def test_contains_match_code_in_products_page(self):
        for token, limit in (("internal", 50), ("wide", 10)):
            checked, diffs = self.diffs(token, [{"code": "CCC-ASK"}], limit, include=("stock",))
            self.assertEqual(checked, 1, token)
            if diffs:
                sys.stderr.write(f"\nOBSERVATION[R2-B4-contains-{token}]: {json.dumps(diffs, ensure_ascii=False)[:600]}\n")
            self.assertEqual(diffs, [], token)

    def test_products_itself_depends_on_page_context(self):
        # Không phải lỗi batch: ghi nhận /products cho CÙNG sản phẩm trả same_cas khác nhau tùy limit/trang kết quả.
        a = self.get(V + "/products", token="internal", q="CCC-ASK", limit="1").get_json()["results"][0]
        b = self.get(V + "/products", token="internal", q="CCC-ASK", limit="50").get_json()["results"][0]
        self.assertEqual((a["code"], b["code"]), ("CCC-ASK", "CCC-ASK"))
        sa = [x["code"] for x in a["stock"]["same_cas"]]
        sb = [x["code"] for x in b["stock"]["same_cas"]]
        sys.stderr.write(f"\nOBSERVATION[R2-B4-products-context]: limit=1 same_cas={sa} limit=50 same_cas={sb}\n")
        batch = self.ok([{"code": "CCC-ASK"}], include=["stock"])["items"][0]["results"][0]["stock"]["same_cas"]
        sys.stderr.write(f"OBSERVATION[R2-B4-products-context]: batch same_cas={[x['code'] for x in batch]}\n")

    def test_customer_same_cas_respects_team_brands(self):
        for token in ("cust", "limited"):
            r = self.batch([{"code": "VISX-1"}, {"cas": "990-00-1"}, {"code": "DUAL-1"}, {"cas": "882-22-2"}],
                           token=token, include=["stock", "compliance"])
            self.assertEqual(r.status_code, 200)
            text = r.get_data(as_text=True)
            self.assertNotIn("ZETA", text.upper(), token)
            self.assertNotIn("ZZZ-882", text, token)
            body = r.get_json()
            self.assertIn(body["items"][3]["status"], ("NOT_FOUND", "NOT_ALLOWED"), token)

    def test_cas_item_and_mixed_batch_parity(self):
        items = [{"cas": "777-77-7"}, {"cas": "881-11-1"}, {"code": "VX-100"}, {"code": "MULTI-1"},
                 {"code": "PAR-001"}, {"cas": "222-22-2"}]
        all_diffs = {}
        for token, limit in (("internal", 50), ("cust", 10), ("wide", 10)):
            checked, diffs = self.diffs(token, items, limit)
            sys.stderr.write(f"\nOBSERVATION[R2-B4-mixed-{token}]: checked={checked} diffs={[(d[0], d[1]) for d in diffs]}\n")
            all_diffs[token] = diffs
        self.assertEqual({k: v for k, v in all_diffs.items() if v}, {})


if __name__ == "__main__":
    unittest.main()
