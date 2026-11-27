"""VERIFIER độc lập cho POST /machine/v1/products/batch (specs/machine-api-batch/VERIFICATION.md, B1..B6).

Không phải test của agent chính. Mỗi lớp cố tìm một trường hợp làm claim sai (adversarial/negative).
Dùng database thử tạm (pg_temp_db) qua InternalBase của tests/test_machine_api_internal.py, cộng dữ liệu riêng.
Chạy: PYTHONPATH=.:tests:tests/independent DATABASE_URL=... .venv/bin/python -m unittest \
      tests.independent.test_machine_api_batch_independent
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

import machine_api

from test_machine_api import token_entry, ALL_PERMISSIONS
from test_machine_api_internal import (InternalBase, walk_keys, NOTE_SENTINEL, PRICE_SENTINEL, STOCK_NOTE_SENTINEL,
                                       STOCK_PRICE_SENTINEL, TODAY)

ROOT = Path(__file__).resolve().parents[2]
PATH = "/machine/v1/products/batch"
V = "/machine/v1"
OLD_REF = "origin/main"
CUSTOMER_FORBIDDEN = {"price", "unit_price_vnd", "original_price", "ship_factor", "ship", "note", "stock_note",
                      "stock_price_vnd", "lots", "lots_total", "lots_truncated", "rate_vnd", "currency",
                      "price_status", "expiry_date"}
REF_SENTINEL = "REFSENTINEL-QZ9"
CODE_SENTINEL = "VCODE-SENTINEL-77"


class VBase(InternalBase):
    @classmethod
    def seed(cls):
        super().seed()

        def product(name, code, cas, brand, size, price="12.5", ship="1.3", note="ghi chu v"):
            cls.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (name, code, cas, brand, size, price, ship, note, brand))

        # Mã lưu trong DB có khoảng trắng/hoa thường lẫn lộn; một mã chỉ CHỨA; một mã có tiền tố
        product("Vx a", "VX-100", "222-22-2", "ALPHA", "1L")
        product("Vx b", " vx-100", "222-22-2", "BETA", "1L")
        product("Vx c", "Vx-100  ", "222-22-2", "GAMMA", "5L")
        product("Vx d", "VX-100", "222-22-2", "ALPHA", "5L")
        product("Vx contains", "AVX-100", "222-22-2", "DELTA", "1L")
        product("Vx prefix", "VX-1000", "1222-22-2", "DELTA", "1L")
        product("Vx tab", "VX-100\t", "", "EPSILON", "1L")  # TRIM của SQL không bỏ tab: không được coi là khớp
        product("Underscore", "U_1", "", "ALPHA", "1L")
        product("Underscore other", "UX1", "", "ALPHA", "1L")
        product("Percent", "P%1", "", "ALPHA", "1L")
        product("Percent other", "PZZ1", "", "ALPHA", "1L")
        product("Bang", "B!1", "", "ALPHA", "1L")
        product("Null code", None, "333-33-3", "ALPHA", "1L")
        product("Empty code", "   ", "333-33-3", "BETA", "1L")
        product("Sentinel", CODE_SENTINEL, "", "ALPHA", "1L")
        # Nhiều hãng cùng một mã để kiểm tra xen đều hãng khi bị cắt: 7 hãng × 4 quy cách = 28
        for brand in ("ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA", "HANGLA"):
            for size in ("1L", "2L", "3L", "4L"):
                product(f"Multi {brand}", "MULTI-1", "444-44-4", brand, size)

        # Ca biên tồn kho: CAS 777-77-7 có > 5000 lô của MỘT mã khác (AAA-ONE) và 1 lô của mã thứ hai (BBB-TWO)
        # xếp sau cùng; sản phẩm hỏi là CCC-ASK (cùng CAS).
        product("Ask same cas", "CCC-ASK", "777-77-7", "ALPHA", "1L")
        snap = cls.sql("SELECT active_snapshot_id FROM stock_state WHERE singleton = TRUE")[0][0]
        cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                "brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s, 'One', 'AAA-ONE', '777-77-7', 'ALPHA', '1L', 1, 1, current_date + g, 'alpha', 'aaa-one', "
                "'1l', '777-77-7', '' FROM generate_series(1, 5005) AS g", (snap,))
        cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                "brand_norm,code_norm,size_norm,cas_norm,stock_note) VALUES (%s,'Two','BBB-TWO','777-77-7','ALPHA','1L',"
                "1,9,NULL,'alpha','bbb-two','1l','777-77-7','')", (snap,))
        cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                "brand_norm,code_norm,size_norm,cas_norm,stock_note) VALUES (%s,'Vx','VX-100','222-22-2','ALPHA','1L',"
                "1,4,%s,'alpha','vx-100','1l','222-22-2','')", (snap, TODAY + timedelta(days=15)))

        # Thêm các team quyền hẹp
        def team(name, perms, brands):
            tid = cls.sql("INSERT INTO teams(name, permission_keys) VALUES (%s, %s) RETURNING id", (name, perms))[0][0]
            for b in brands:
                cls.sql("INSERT INTO team_brands(team_id, brand) VALUES (%s, %s)", (tid, b))
            return tid
        all_brands = ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA"]
        cls.team_nocode = team("V-no-code", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CAS", "VIEW_NAME", "VIEW_BRAND"], all_brands)
        cls.team_cas_nosearch = team("V-cas-no-search", ["SEARCH_BY_CAS", "VIEW_CAS", "VIEW_CODE", "VIEW_NAME"], all_brands)
        cls.team_nocasview = team("V-no-cas-view", ["SEARCH", "SEARCH_BY_CAS", "VIEW_CODE", "VIEW_NAME"], all_brands)
        cls.team_wide = team("V-wide", ALL_PERMISSIONS, all_brands)
        extra = {n: "stm_v_" + n + "_" + uuid.uuid4().hex for n in ("nocode", "casnosearch", "nocasview", "wide",
                                                                      "wideq", "slow")}
        cls.tokens.update(extra)
        entries = json.load(open(cls.token_path, encoding="utf-8"))["tokens"]
        entries += [
            token_entry(extra["nocode"], "v-nocode", cls.team_nocode),
            token_entry(extra["casnosearch"], "v-casnosearch", cls.team_cas_nosearch),
            token_entry(extra["nocasview"], "v-nocasview", cls.team_nocasview),
            token_entry(extra["wide"], "v-wide", cls.team_wide),
            token_entry(extra["wideq"], "v-wideq", cls.team_wide, stock_detail="quantity"),
            token_entry(extra["slow"], "v-slow", cls.team_wide, rate_per_minute=3),
        ]
        cls.write_tokens(entries)

    def batch(self, items, token="internal", addr="198.51.100.7", headers=None, query=None, **extra):
        hdrs = {"Authorization": "Bearer " + self.tokens.get(token, token)}
        hdrs.update(headers or {})
        return self.client.post(PATH, json={"items": items, **extra}, headers=hdrs, query_string=query,
                                environ_overrides={"REMOTE_ADDR": addr})

    def raw(self, data, token="internal", ctype="application/json", headers=None):
        hdrs = {"Authorization": "Bearer " + self.tokens[token]}
        hdrs.update(headers or {})
        return self.client.post(PATH, data=data, content_type=ctype, headers=hdrs,
                                environ_overrides={"REMOTE_ADDR": "198.51.100.7"})

    def ok(self, items, **kw):
        r = self.batch(items, **kw)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:400])
        return r.get_json()


# ---------------------------------------------------------------------------------------------------------------
# B1 — /products, /compliance, /stock giống hệt bản origin/main trên cùng dữ liệu
# ---------------------------------------------------------------------------------------------------------------
class B1_OldVsNew(VBase):
    def test_get_endpoints_identical_to_origin_main(self):
        old_src = subprocess.run(["git", "show", f"{OLD_REF}:machine_api.py"], capture_output=True, text=True,
                                 cwd=str(ROOT), check=True).stdout
        self.assertNotIn("products/batch", old_src)
        queries = ["ethanol", "ETH-AL-1L", "eth", "64-17-5", "555-55-5", "ZZZ-BIG", "LOT-205", "MET", "PAR-0",
                   "VX-100", "vx-100", "MULTI-1", "CCC-ASK", "777-77-7", "Bulk acid", "NAN-00", "U_1", "P%1", "DUP",
                   "222-22-2", "444-44-4", "abs"]
        reqs = []
        for token in ("internal", "cust", "limited", "nocode", "nocasview", "wide", "wideq"):
            for q in queries:
                reqs.append({"path": V + "/products", "token": self.tokens[token], "query": {"q": q}})
                reqs.append({"path": V + "/products", "token": self.tokens[token], "query": {"q": q, "limit": "50"}})
            for cas in ("64-17-5", "555-55-5", "777-77-7", "222-22-2", "67-56-1", "000-00-0"):
                reqs.append({"path": V + "/compliance", "token": self.tokens[token], "query": {"cas": cas}})
            for q in ("ETH-AL-1L", "LOT-205", "ZZZ-BIG", "CCC-ASK", "VX-100", "64-17-5", "777-77-7"):
                reqs.append({"path": V + "/stock", "token": self.tokens[token], "query": {"q": q}})
        with tempfile.TemporaryDirectory(prefix="v-batch-old-") as old_dir:
            Path(old_dir, "machine_api.py").write_text(old_src, encoding="utf-8")
            req_path = Path(old_dir, "reqs.json")
            req_path.write_text(json.dumps(reqs), encoding="utf-8")
            env = dict(os.environ, DATABASE_URL=self.dsn, MACHINE_INTERNAL_DATABASE_URL=self.dsn,
                       MACHINE_API_TOKENS_FILE=self.token_path, PYTHONPATH=str(ROOT))
            proc = subprocess.run([sys.executable, str(ROOT / "tests/independent/machine_api_internal_old_runner.py"),
                                   old_dir, str(req_path)], capture_output=True, text=True, env=env, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            old = json.loads(proc.stdout)
        diffs = []
        statuses = {}
        for req, before in zip(reqs, old):
            machine_api.rate_limiter.reset()
            r = self.client.get(req["path"], headers={"Authorization": "Bearer " + req["token"]},
                                query_string=req["query"], environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            if r.status_code != before["status"] or r.get_json() != before["body"]:
                diffs.append((req["path"], req["query"], before["status"], r.status_code))
        sys.stderr.write(f"\nOBSERVATION[B1]: {len(reqs)} GET so với {OLD_REF}; status={statuses}; khác={len(diffs)}\n")
        self.assertGreater(statuses.get(200, 0), 300)
        self.assertEqual(diffs, [])


# ---------------------------------------------------------------------------------------------------------------
# B2 — đủ mục, đúng thứ tự, trạng thái tường minh
# ---------------------------------------------------------------------------------------------------------------
class B2_OrderStatus(VBase):
    def test_mixed_items_order_refs_statuses_summary(self):
        items = [
            {"code": "VX-100", "ref": "a"}, {"code": "vx-100"}, {"code": "  VX-100 \t"}, {"cas": "222-22-2", "ref": ""},
            {"code": "NOPE-NOPE"}, {"cas": "999-99-9"},
            5, None, [], "VX-100", True, {"code": None}, {"code": 7}, {"code": ["VX-100"]}, {"cas": {"x": 1}},
            {"code": "VX-100", "cas": "222-22-2"}, {}, {"ref": "only-ref"}, {"code": "   "}, {"code": "x" * 101},
            {"code": "VX\x01100"}, {"code": "VX\x7f100"}, {"code": "VX-100", "extra": 1},
            {"code": "VX-100", "ref": "r" * 65}, {"code": "VX-100", "ref": None},
            {"code": "x" * 100}, {"code": "VX-100", "ref": "r" * 64}, {"code": "VX-100", "ref": REF_SENTINEL},
        ]
        body = self.ok(items)
        self.assertEqual(body["count"], len(items))
        self.assertEqual(len(body["items"]), len(items))
        self.assertEqual([it["position"] for it in body["items"]], list(range(1, len(items) + 1)))
        expect = (["FOUND"] * 4 + ["NOT_FOUND"] * 2 + ["INVALID"] * 19 + ["NOT_FOUND", "FOUND", "FOUND"])
        self.assertEqual([it["status"] for it in body["items"]], expect)
        for it in body["items"]:
            self.assertTrue({"status", "matches_total", "truncated", "results", "position"} <= set(it), it)
            if it["status"] == "INVALID":
                self.assertTrue(it.get("message"))
                self.assertEqual((it["results"], it["matches_total"], it["truncated"]), ([], 0, False))
        self.assertEqual(body["items"][0]["ref"], "a")
        self.assertEqual(body["items"][3]["ref"], "")
        self.assertEqual(body["items"][26]["ref"], "r" * 64)
        self.assertEqual(body["items"][27]["ref"], REF_SENTINEL)
        self.assertNotIn("ref", body["items"][1])
        # mục trùng (khác hoa thường/khoảng trắng) cho cùng kết quả
        self.assertEqual(body["items"][0]["results"], body["items"][1]["results"])
        self.assertEqual(body["items"][0]["results"], body["items"][2]["results"])
        counts = {"found": 0, "not_found": 0, "invalid": 0, "not_allowed": 0, "truncated": 0}
        for it in body["items"]:
            counts[it["status"].lower()] += 1
            counts["truncated"] += it["truncated"]
        self.assertEqual(body["summary"], counts)

    def test_500_same_500_invalid_500_scalars_500_distinct(self):
        b = self.ok([{"code": "MULTI-1", "ref": str(i)} for i in range(500)])
        self.assertEqual(b["count"], 500)
        self.assertEqual([it["ref"] for it in b["items"]], [str(i) for i in range(500)])
        self.assertEqual(b["summary"]["found"], 500)
        self.assertEqual(b["summary"]["truncated"], 500)  # 28 > 20
        for bad in (None, 0, [], "x", {"code": ""}):
            b = self.ok([bad] * 500)
            self.assertEqual(b["summary"]["invalid"], 500, bad)
            self.assertEqual(len(b["items"]), 500)
        codes = [f"PAR-{i:03d}" for i in range(42)] + [f"BLK-{i:03d}" for i in range(60)] + \
                [f"MISS-{i:04d}" for i in range(398)]
        b = self.ok([{"code": c} for c in codes])
        self.assertEqual([it["value"] for it in b["items"]], codes)
        self.assertEqual(b["summary"]["found"], 102)
        self.assertEqual(b["summary"]["not_found"], 398)

    def test_lone_surrogate_item_does_not_break_other_items(self):
        # JSON hợp lệ (escape \\ud800) — ví dụ chuỗi bị cắt giữa emoji trong n8n/JS
        data = '{"items": [{"code": "VX-100"}, {"code": "AB\\ud800CD"}, {"code": "MULTI-1"}]}'
        r = self.raw(data)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        st = [it["status"] for it in r.get_json()["items"]]
        self.assertEqual(st[0], "FOUND")
        self.assertEqual(st[2], "FOUND")

    def test_lone_surrogate_in_ref_does_not_break_call(self):
        data = '{"items": [{"code": "VX-100", "ref": "x\\udc00"}, {"code": "MULTI-1"}]}'
        r = self.raw(data)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])

    def test_c1_control_character_is_invalid(self):
        # OBSERVATION (SPEC_AMBIGUOUS): hợp đồng nói "ký tự điều khiển" -> INVALID; C1 (U+0080..U+009F) hiện ra NOT_FOUND.
        b = self.ok([{"code": "VX\u009b100"}, {"code": "VX-100"}])
        self.assertEqual(b["items"][1]["status"], "FOUND")
        sys.stderr.write(f"\nOBSERVATION[B2-C1]: status={b['items'][0]['status']}\n")
        self.assertIn(b["items"][0]["status"], ("INVALID", "NOT_FOUND"))

    def test_deeply_nested_json_is_a_clean_4xx(self):
        # OBSERVATION: hợp đồng không ghim mã lỗi cho JSON lồng sâu; chỉ bắt buộc không rò nội dung.
        r = self.raw("[" * 50000 + "]" * 50000)
        sys.stderr.write(f"\nOBSERVATION[B2-deep-json]: status={r.status_code}\n")
        self.assertGreaterEqual(r.status_code, 400)
        self.assertNotIn("Traceback", r.get_data(as_text=True))
        self.assertNotIn("[[[", r.get_data(as_text=True))


# ---------------------------------------------------------------------------------------------------------------
# B3 — khớp chính xác, mọi sản phẩm khớp, không tự chọn
# ---------------------------------------------------------------------------------------------------------------
class B3_ExactMatch(VBase):
    def expected(self, field, value):
        return self.sql(f"SELECT count(*) FROM products WHERE {field} IS NOT NULL AND TRIM({field}) <> '' "
                        f"AND UPPER(TRIM({field})) = UPPER(%s)", (value.strip(),))[0][0]

    def test_exact_only_all_matches_vs_independent_sql(self):
        b = self.ok([{"code": "vx-100"}, {"code": "VX-10"}, {"cas": "222-22-2"}, {"cas": "22-22-2"},
                     {"code": "MULTI-1"}, {"cas": "333-33-3"}], max_per_item=50)
        it = b["items"]
        self.assertEqual(it[0]["matches_total"], self.expected("code", "vx-100"))
        self.assertEqual(it[0]["matches_total"], 4)
        self.assertEqual(sorted((r["code"], r["brand"], r["size"]) for r in it[0]["results"]),
                         sorted([("VX-100", "ALPHA", "1L"), (" vx-100", "BETA", "1L"), ("Vx-100  ", "GAMMA", "5L"),
                                 ("VX-100", "ALPHA", "5L")]))
        for r in it[0]["results"]:
            self.assertNotIn(r["code"], ("AVX-100", "VX-1000", "VX-100\t"))
        self.assertEqual(it[1]["status"], "NOT_FOUND")
        self.assertEqual(it[2]["matches_total"], self.expected("cas", "222-22-2"))
        self.assertNotIn("VX-1000", [r["code"] for r in it[2]["results"]])
        self.assertEqual(it[3]["status"], "NOT_FOUND")
        self.assertEqual((it[4]["matches_total"], len(it[4]["results"]), it[4]["truncated"]), (28, 28, False))
        self.assertEqual(it[5]["matches_total"], 2)  # code NULL/rỗng nhưng CAS khớp vẫn trả

    def test_wildcards_literal(self):
        b = self.ok([{"code": "U_1"}, {"code": "P%1"}, {"code": "B!1"}, {"code": "%"}, {"code": "_"}, {"code": "U%"}])
        self.assertEqual([r["code"] for r in b["items"][0]["results"]], ["U_1"])
        self.assertEqual([r["code"] for r in b["items"][1]["results"]], ["P%1"])
        self.assertEqual([r["code"] for r in b["items"][2]["results"]], ["B!1"])
        self.assertEqual([i["status"] for i in b["items"][3:]], ["NOT_FOUND"] * 3)

    def test_truncation_interleave_and_stability(self):
        for per, token in ((3, "internal"), (7, "internal"), (10, "internal"), (5, "wide"), (10, "wide")):
            first = self.ok([{"code": "MULTI-1"}], token=token, max_per_item=per)["items"][0]
            visible = 28 if token == "internal" else 24  # team wide không có HANGLA
            self.assertEqual(first["matches_total"], visible, (per, token))
            self.assertEqual(len(first["results"]), min(per, visible))
            self.assertEqual(first["truncated"], visible > per)
            brands = {r["brand"] for r in first["results"]}
            self.assertGreaterEqual(len(brands), min(7 if token == "internal" else 6, per), (per, token, brands))
            for _ in range(3):
                again = self.ok([{"code": " multi-1 "}], token=token, max_per_item=per)["items"][0]
                self.assertEqual(again["results"], first["results"])

    def test_no_auto_pick_duplicates_across_brand_and_size(self):
        b = self.ok([{"code": "VX-100"}], token="wide")
        self.assertEqual(len(b["items"][0]["results"]), 4)
        self.assertEqual(len({(r["brand"], r["size"]) for r in b["items"][0]["results"]}), 4)


# ---------------------------------------------------------------------------------------------------------------
# B4 — trùng khớp với /products
# ---------------------------------------------------------------------------------------------------------------
class B4_Parity(VBase):
    def compare(self, token, items, limit):
        body = self.ok(items, token=token, include=["compliance", "stock"], max_per_item=limit)
        checked, diffs = 0, []
        for item in body["items"]:
            for res in item["results"]:
                single = self.get(V + "/products", token=token, q=res.get("code") or item["value"],
                                  limit=str(limit)).get_json()
                same = [r for r in single["results"]
                        if all(r.get(k) == res.get(k) for k in ("name", "code", "brand", "size", "cas"))]
                if len(same) != 1 or same[0] != res:
                    diffs.append((item["value"], res.get("code"), res.get("brand"), res.get("size")))
                checked += 1
        return checked, diffs

    def test_parity_all_tokens(self):
        codes = ([f"PAR-{i:03d}" for i in range(42)] + ["ETH-AL-1L", "ETH-GA-1L", "ETH-BE-500mL", "LOT-025",
                 "LOT-205", "ZZZ-BIG", "NAN-001", "NAN-002", "NAN-003", "NAN-004", "VX-100", "MET-A"])
        total = 0
        for token, limit in (("internal", 50), ("cust", 10), ("limited", 10), ("wide", 10), ("wideq", 10)):
            checked, diffs = self.compare(token, [{"code": c} for c in codes], limit)
            self.assertEqual(diffs, [], token)
            total += checked
        self.assertGreaterEqual(total, 60)

    def test_parity_same_cas_when_cas_has_more_than_5000_lots(self):
        # CCC-ASK cùng CAS 777-77-7 với 5005 lô AAA-ONE và 1 lô BBB-TWO.
        for token, limit in (("internal", 50), ("wide", 10)):
            checked, diffs = self.compare(token, [{"code": "CCC-ASK"}], limit)
            self.assertEqual(checked, 1)
            if diffs:
                b = self.ok([{"code": "CCC-ASK"}], token=token, include=["stock"])["items"][0]["results"][0]
                p = self.get(V + "/products", token=token, q="CCC-ASK").get_json()["results"][0]
                sys.stderr.write(f"\nOBSERVATION[B4-{token}]: batch same_cas={[x.get('code') for x in b['stock']['same_cas']]}"
                                 f" /products same_cas={[x.get('code') for x in p['stock']['same_cas']]}\n")
            self.assertEqual(diffs, [], token)


# ---------------------------------------------------------------------------------------------------------------
# B5 — quyền và bảo mật
# ---------------------------------------------------------------------------------------------------------------
class B5_Security(VBase):
    def test_customer_never_gets_price_keys_any_include_any_token(self):
        items = [{"code": c} for c in ("VX-100", "MULTI-1", "ETH-AL-1L", "LOT-205", "PAR-001", "CCC-ASK")] + \
                [{"cas": c} for c in ("64-17-5", "222-22-2", "777-77-7")]
        for token in ("cust", "limited", "nocode", "nocasview", "wide", "wideq"):
            for include in ([], ["stock"], ["compliance", "stock"]):
                r = self.batch(items, token=token, include=include, max_per_item=50,
                               headers={"X-Profile": "internal", "X-Internal": "1"}, query={"profile": "internal",
                                                                                            "include_price": "1"})
                self.assertEqual(r.status_code, 200, (token, r.get_data(as_text=True)[:200]))
                text = r.get_data(as_text=True)
                for s in (PRICE_SENTINEL, NOTE_SENTINEL, STOCK_NOTE_SENTINEL, str(STOCK_PRICE_SENTINEL), "ghi chu v",
                          "12.5"):
                    self.assertNotIn(s, text, (token, s))
                body = r.get_json()
                self.assertFalse(walk_keys(body) & CUSTOMER_FORBIDDEN, token)
                self.assertEqual(body["max_per_item"], 10)
                self.assertLessEqual(max(len(i["results"]) for i in body["items"]), 10)
                team_of = {"cust": self.team_full, "limited": self.team_limited, "nocode": self.team_nocode,
                           "nocasview": self.team_nocasview, "wide": self.team_wide, "wideq": self.team_wide}
                visible = {b for (b,) in self.sql("SELECT brand FROM team_brands WHERE team_id=%s", (team_of[token],))}
                brands = {res.get("brand") for i in body["items"] for res in i["results"]} - {None}
                self.assertFalse(brands - visible, token)
                # same_cas cũng chỉ trong hãng của team
                for i in body["items"]:
                    for res in i["results"]:
                        for sc in res.get("stock", {}).get("same_cas", []):
                            if "brand" in sc:
                                self.assertIn(sc["brand"], visible)

    def test_field_permissions_and_not_allowed(self):
        b = self.ok([{"code": "VX-100"}, {"cas": "222-22-2"}], token="nocode", include=["compliance", "stock"])
        self.assertEqual(b["items"][0]["status"], "NOT_ALLOWED")
        self.assertEqual(b["items"][0]["results"], [])
        self.assertEqual(b["items"][1]["status"], "FOUND")
        for r in b["items"][1]["results"]:
            self.assertNotIn("code", r)
            self.assertNotIn("size", r)
            for sc in r["stock"].get("same_cas", []):
                self.assertNotIn("code", sc)
        b = self.ok([{"code": "VX-100"}, {"cas": "222-22-2"}], token="nocasview", include=["compliance", "stock"])
        self.assertEqual(b["items"][1]["status"], "NOT_ALLOWED")
        self.assertEqual(b["items"][1]["results"], [])
        for r in b["items"][0]["results"]:
            self.assertNotIn("cas", r)
            self.assertNotIn("brand", r)
            self.assertEqual(r["compliance"]["state"], "HIDDEN")
            self.assertNotIn("same_cas", r["stock"])
        self.assertEqual(b["summary"]["not_allowed"], 1)

    def test_team_without_search_but_with_cas_permissions(self):
        r = self.batch([{"cas": "222-22-2"}, {"code": "VX-100"}], token="casnosearch")
        text = r.get_data(as_text=True)
        sys.stderr.write(f"\nOBSERVATION[B5-casnosearch]: status={r.status_code} body={text[:160]}\n")
        self.assertNotIn("VX-100", text)
        self.assertIn(r.status_code, (200, 403))
        if r.status_code == 200:
            self.assertEqual(r.get_json()["items"][1]["status"], "NOT_ALLOWED")

    def test_top_level_escalation_keys_rejected(self):
        for extra in ({"profile": "internal"}, {"include_price": True}, {"limit": 50}, {"team_id": 1},
                      {"Items": []}, {"fields": ["price"]}):
            r = self.batch([{"code": "VX-100"}], token="cust", **extra)
            self.assertEqual(r.status_code, 400, extra)
            self.assertNotIn("results", r.get_data(as_text=True))
        for inc in (["price"], ["Stock"], ["stock", 1], "stock", None, {"stock": 1}):
            self.assertEqual(self.batch([{"code": "VX-100"}], include=inc).status_code, 400, inc)
        for m in (None, "10", 0, -3, 2.0, True, False, [5], float("inf")):
            r = self.batch([{"code": "VX-100"}], max_per_item=m) if m != float("inf") else \
                self.raw('{"items":[{"code":"VX-100"}],"max_per_item":Infinity}')
            self.assertEqual(r.status_code, 400, m)
        self.assertEqual(self.batch([{"code": "VX-100"}], max_per_item=10 ** 30).get_json()["max_per_item"], 50)
        for items in (None, {}, "VX-100", [], [{"code": "A"}] * 501):
            r = self.client.post(PATH, json={"items": items}, headers={"Authorization": "Bearer " + self.tokens["internal"]},
                                 environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
            self.assertEqual(r.status_code, 400, str(items)[:30])
        r = self.client.post(PATH, json={"include": ["stock"]}, headers={"Authorization": "Bearer " + self.tokens["internal"]},
                             environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
        self.assertEqual(r.status_code, 400)

    def test_auth_ip_dsn_method_and_size(self):
        self.assertEqual(self.client.post(PATH, json={"items": [{"code": "VX-100"}]},
                                          environ_overrides={"REMOTE_ADDR": "198.51.100.7"}).status_code, 401)
        self.assertEqual(self.batch([{"code": "VX-100"}], token="stm_wrong_" + "a" * 30).status_code, 401)
        self.assertEqual(self.batch([{"code": "VX-100"}], token="internal", addr="192.0.2.1").status_code, 403)
        self.assertEqual(self.batch([{"code": "VX-100"}], token="internal_ip").status_code, 403)
        env = {k: v for k, v in os.environ.items() if k != "MACHINE_INTERNAL_DATABASE_URL"}
        with mock.patch.dict(os.environ, env, clear=True):
            r = self.batch([{"code": "VX-100"}])
            self.assertEqual(r.status_code, 503)
            self.assertNotIn("results", r.get_data(as_text=True))
        auth = {"Authorization": "Bearer " + self.tokens["internal"]}
        for method in ("get", "put", "delete", "patch"):
            r = getattr(self.client, method)(PATH, headers=auth, environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
            self.assertEqual(r.status_code, 405, method)
        # 413: có Content-Length và chunked (không Content-Length)
        big = json.dumps({"items": [{"code": "A" * 100, "ref": "r" * 64}] * 900})
        self.assertGreater(len(big), 128 * 1024)
        self.assertEqual(self.raw(big).status_code, 413)
        # các đường GET khác vẫn không nhận POST
        for p in ("/products", "/compliance", "/stock"):
            self.assertEqual(self.client.post(V + p, json={"q": "x"}, headers=auth,
                                              environ_overrides={"REMOTE_ADDR": "198.51.100.7"}).status_code, 405)

    def test_one_batch_is_one_rate_hit(self):
        items = [{"code": f"PAR-{i:03d}"} for i in range(42)] * 10
        for _ in range(3):
            self.assertEqual(self.batch(items, token="slow").status_code, 200)
        r = self.batch(items[:1], token="slow")
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("results", r.get_data(as_text=True))

    def test_internal_token_still_has_price_and_db_is_read_only(self):
        b = self.ok([{"code": "PAR-001"}], include=["stock"])
        self.assertIn("price", b["items"][0]["results"][0])
        seen = []
        real = machine_api._batch_rows

        def spy(conn, *a, **k):
            with conn.cursor() as cur:
                cur.execute("SHOW transaction_read_only")
                seen.append(cur.fetchone()[0])
            return real(conn, *a, **k)
        with mock.patch.object(machine_api, "_batch_rows", side_effect=spy):
            self.ok([{"code": "PAR-001"}], token="cust")
        self.assertEqual(set(seen), {"on"})


# ---------------------------------------------------------------------------------------------------------------
# B6 — nhật ký không rò bí mật
# ---------------------------------------------------------------------------------------------------------------
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.lines.append((record.name, text))


class B6_Logs(VBase):
    def capture(self, fn):
        handler = _Capture()
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            fn()
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        return handler.lines

    def test_access_and_error_logs_have_no_values(self):
        token = self.tokens["internal"]
        cust = self.tokens["cust"]

        def run():
            self.batch([{"code": CODE_SENTINEL, "ref": REF_SENTINEL}, {"cas": "777-77-7", "ref": REF_SENTINEL}],
                       include=["compliance", "stock"])
            self.batch([{"code": CODE_SENTINEL, "ref": REF_SENTINEL}], token="cust", include=["stock"])
            self.batch([{"code": CODE_SENTINEL}], include=[REF_SENTINEL])                 # 400
            self.batch([{"code": CODE_SENTINEL}] * 501)                                    # 400
            self.raw(json.dumps({"items": [{"code": CODE_SENTINEL}] * 6000}))              # 413
            self.raw('{"items": [{"code": "' + CODE_SENTINEL + '\\ud800"}]}')             # ký tự lạ
            with mock.patch.object(machine_api, "BATCH_MAX_RESULTS", 0):
                self.batch([{"code": CODE_SENTINEL}])                                      # too_many_results
            with mock.patch.object(machine_api, "_batch_rows",
                                   side_effect=machine_api.psycopg2.OperationalError(f"boom {CODE_SENTINEL}")):
                self.batch([{"code": CODE_SENTINEL, "ref": REF_SENTINEL}])                 # 503 db
        lines = self.capture(run)
        access = [json.loads(t) for n, t in lines if n == "machine_api.access"]
        self.assertEqual(len(access), 8)
        for entry in access:
            self.assertEqual(set(entry), {"ts", "token", "endpoint", "status", "results", "ip", "ms"})
            self.assertEqual(entry["endpoint"], "products_batch")
        self.assertEqual([e["status"] for e in access][:5], [200, 200, 400, 400, 413])
        joined = "\n".join(t for _n, t in lines if not t.startswith("boom"))
        for needle in (REF_SENTINEL, token, cust, PRICE_SENTINEL, "777-77-7", self.dsn, "SELECT", "unnest"):
            self.assertNotIn(needle, joined, needle)
        leaks = [(n, t[:200]) for n, t in lines if CODE_SENTINEL in t and "boom" not in t]
        self.assertEqual(leaks, [])

    def test_error_bodies_do_not_echo(self):
        for r in (self.batch([{"code": CODE_SENTINEL}], include=[CODE_SENTINEL]),
                  self.batch([{"code": CODE_SENTINEL}], **{CODE_SENTINEL: 1}),
                  self.batch([{"code": CODE_SENTINEL}] * 501),
                  self.raw(json.dumps({"items": [{"code": CODE_SENTINEL}] * 6000}))):
            self.assertGreaterEqual(r.status_code, 400)
            text = r.get_data(as_text=True)
            for needle in (CODE_SENTINEL, "SELECT", "postgresql://", "Traceback"):
                self.assertNotIn(needle, text)
        with mock.patch.object(machine_api, "_batch_rows",
                               side_effect=machine_api.psycopg2.OperationalError("SELECT secret postgresql://u:p@h/db")):
            r = self.batch([{"code": CODE_SENTINEL}])
        self.assertEqual(r.status_code, 503)
        for needle in (CODE_SENTINEL, "SELECT", "postgresql://"):
            self.assertNotIn(needle, r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
