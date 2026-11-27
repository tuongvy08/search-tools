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
from stock import expiry_state, normalized_text

from test_machine_api_batch_independent import VBase, V

STOCK_COLS = ("snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
              "brand_norm,code_norm,size_norm,cas_norm,stock_note")


class R2Base(VBase):
    def products_item(self, token, res, limit):
        single = self.get(V + "/products", token=token, q=res.get("code"), limit=str(limit)).get_json()
        same = [r for r in single["results"]
                if all(r.get(k) == res.get(k) for k in ("name", "code", "brand", "size", "cas"))]
        return same

    # Hợp đồng v2, B4: oracle độc lập (SQL) cho gợi ý cùng CAS tính RIÊNG cho từng sản phẩm (mã C, CAS X):
    # 5.000 lô đầu (brand_norm, code_norm, size_norm, expiry NULLS LAST) có CAS X, mã khác C, hãng thẻ được thấy;
    # rồi nhóm / lọc còn hàng / cắt 5 (khách) hoặc 10 (nội bộ) như /products.
    def v2_team_of(self, token):
        return {"cust": self.team_full, "wide": self.team_wide, "wideq": self.team_wide,
                "limited": self.team_limited}.get(token)

    def v2_expected_same_cas(self, token, code, cas):
        team = self.v2_team_of(token)
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
            entry = {"code": g["code"], "brand": g["brand"], "size": g["size"], "status": "IN_STOCK",
                     "expiry_warning": all(s == "near_expiry" for _q, s in g["lots"])}
            if token in ("wideq", "internal"):  # thẻ nội bộ luôn ở mức số lượng
                entry["quantity"] = sum(q for q, _s in g["lots"])
            out.append(entry)
        cap = 10 if token == "internal" else 5
        return out[:cap], len(out) > cap

    def v2_same_cas_diffs(self, token, res):
        """Hợp đồng v2, B4: same_cas/same_cas_more đối chiếu theo ĐỊNH NGHĨA (oracle SQL) và, khi trang
        /products?q=<mã>&limit=1 chỉ có đúng sản phẩm đó, phải y hệt trang đó. Không so với trang limit 10/50."""
        stock = res.get("stock")
        got = (stock.get("same_cas"), stock.get("same_cas_more"))
        out = []
        if "cas" not in res:
            if got != (None, None):
                out.append("same_cas-present-but-cas-hidden")
            return out
        if not normalized_text(res.get("cas")):
            if "same_cas" in stock or "same_cas_more" in stock:
                out.append(("same_cas-without-cas", got))
            return out
        exp = self.v2_expected_same_cas(token, res.get("code"), res.get("cas"))
        if got != exp:
            out.append(("same_cas-v2-definition", got, exp))
        machine_api.rate_limiter.reset()
        one = self.get(V + "/products", token=token, q=res.get("code"), limit="1").get_json()["results"]
        if one and all(one[0].get(k) == res.get(k) for k in ("name", "code", "brand", "size", "cas")):
            ost = one[0].get("stock") or {}
            if (ost.get("same_cas"), ost.get("same_cas_more")) != got:
                out.append(("same_cas-vs-/products-limit-1", got, (ost.get("same_cas"), ost.get("same_cas_more"))))
        return out

    def diffs(self, token, items, limit, include=("compliance", "stock"), same_cas_v2=False):
        # same_cas_v2=False (mặc định): hành vi cũ, so NGUYÊN phần tử với trang /products (limit = max_per_item).
        # same_cas_v2=True (hợp đồng v2, B4): mọi khối khác vẫn so với trang /products như cũ; riêng
        # stock.same_cas/same_cas_more so theo định nghĩa v2 (v2_same_cas_diffs).
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
                got = res
                if same_cas_v2 and isinstance(res.get("stock"), dict):
                    sc = self.v2_same_cas_diffs(token, res)
                    if sc:
                        out.append((res.get("code"), res.get("brand"), sc))
                    got, ref = json.loads(json.dumps(res)), json.loads(json.dumps(ref))
                    for blk in (got, ref):
                        if isinstance(blk.get("stock"), dict):
                            blk["stock"].pop("same_cas", None)
                            blk["stock"].pop("same_cas_more", None)
                if ref != got:
                    out.append((res.get("code"), res.get("brand"),
                                {k: (got.get(k), ref.get(k)) for k in set(got) | set(ref) if got.get(k) != ref.get(k)}))
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
            # Hợp đồng v2, B4: same_cas theo định nghĩa v2 (từng sản phẩm), các khối khác vẫn so với /products.
            checked, diffs = self.diffs(token, [{"code": "DUAL-1"}], limit, include=("stock",), same_cas_v2=True)
            self.assertEqual(checked, 2, token)
            if diffs:
                sys.stderr.write(f"\nOBSERVATION[R2-B4-dualcas-{token}]: {json.dumps(diffs, ensure_ascii=False)[:600]}\n")
            self.assertEqual(diffs, [], token)

    def test_contains_match_code_in_products_page(self):
        for token, limit in (("internal", 50), ("wide", 10)):
            # Hợp đồng v2, B4: same_cas theo định nghĩa v2 (từng sản phẩm), các khối khác vẫn so với /products.
            checked, diffs = self.diffs(token, [{"code": "CCC-ASK"}], limit, include=("stock",), same_cas_v2=True)
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
            # Hợp đồng v2, B4: same_cas theo định nghĩa v2 (từng sản phẩm), các khối khác vẫn so với /products.
            checked, diffs = self.diffs(token, items, limit, same_cas_v2=True)
            sys.stderr.write(f"\nOBSERVATION[R2-B4-mixed-{token}]: checked={checked} diffs={[(d[0], d[1]) for d in diffs]}\n")
            all_diffs[token] = diffs
        self.assertEqual({k: v for k, v in all_diffs.items() if v}, {})


if __name__ == "__main__":
    unittest.main()
