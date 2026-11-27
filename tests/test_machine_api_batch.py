"""Developer tests cho tra hàng loạt POST /machine/v1/products/batch (machine_api.py).

Bám theo specs/machine-api-batch/VERIFICATION.md (B1..B6). Dùng database thử tạm (pg_temp_db) và bộ dữ liệu
của tests/test_machine_api_internal.py (nhiều hãng, giá bẩn, tỷ giá thiếu, lô tồn kho, >5000 lô cùng CAS).
Chạy: PYTHONPATH=.:tests DATABASE_URL=... .venv/bin/python -m unittest tests.test_machine_api_batch
"""

import json
import os
import unittest
from unittest import mock

import machine_api

from test_machine_api_internal import (InternalBase, walk_keys, NOTE_SENTINEL, PRICE_SENTINEL, STOCK_NOTE_SENTINEL,
                                       STOCK_PRICE_SENTINEL)

PATH = "/machine/v1/products/batch"
CUSTOMER_FORBIDDEN = {"price", "unit_price_vnd", "original_price", "ship_factor", "ship", "note", "stock_note",
                      "stock_price_vnd", "lots", "rate_vnd", "currency", "price_status"}


class BatchBase(InternalBase):
    @classmethod
    def seed(cls):
        super().seed()

        def product(name, code, cas, brand, size):
            cls.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'1.3',%s,%s)", (name, code, cas, brand, size, PRICE_SENTINEL,
                                                               NOTE_SENTINEL, brand))
        # Cùng một mã ở 3 hãng/quy cách, khác hoa/thường + khoảng trắng; một mã chỉ CHỨA giá trị (không được trả)
        product("Dup one", "DUP-1", "111-11-1", "ALPHA", "1L")
        product("Dup one", "dup-1 ", "111-11-1", "BETA", "500mL")
        product("Dup one", "  Dup-1", "111-11-1", "GAMMA", "1L")
        product("Contains dup", "XDUP-1", "111-11-1", "ALPHA", "1L")
        # Ký tự đại diện phải là ký tự thường
        product("Wild literal", "W%_!1", "", "ALPHA", "1L")
        product("Wild other", "WAB!1", "", "ALPHA", "1L")
        # Sản phẩm có mã trùng một mã trong >5000 lô cùng CAS 555-55-5 (cùng gói với ZZZ-BIG)
        product("Same cas sibling", "AAA-1", "555-55-5", "ALPHA", "1L")

    def batch(self, items, token="internal", addr="198.51.100.7", **extra):
        body = {"items": items, **extra}
        return self.client.post(PATH, json=body, headers={"Authorization": "Bearer " + self.tokens.get(token, token)},
                                environ_overrides={"REMOTE_ADDR": addr})

    def ok(self, items, **kw):
        response = self.batch(items, **kw)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        return response.get_json()


class OrderAndStatusTests(BatchBase):
    """B2"""

    def test_every_item_answered_in_order_with_status_and_summary(self):
        items = [{"code": "ETH-AL-1L", "ref": "r1"}, {"code": "KHONG-CO-MA"}, {"cas": "64-17-5", "ref": "r3"},
                 {"code": "  eth-al-1l  "}, {"code": "ETH-AL-1L", "ref": "r5"}, {"bad": 1}, {"code": ""},
                 {"code": "x" * 101}, {"code": 123}, {"code": "A", "cas": "B"}, {}, "ETH-AL-1L", None, [],
                 {"code": "a\x00b"}, {"code": "ETH-AL-1L", "ref": "x" * 65}, {"code": "ETH-AL-1L", "ref": 7}]
        body = self.ok(items)
        self.assertEqual(body["count"], len(items))
        self.assertEqual([it["position"] for it in body["items"]], list(range(1, len(items) + 1)))
        statuses = [it["status"] for it in body["items"]]
        self.assertEqual(statuses[:5], ["FOUND", "NOT_FOUND", "FOUND", "FOUND", "FOUND"])
        self.assertEqual(set(statuses[5:]), {"INVALID"})
        self.assertEqual(body["items"][0]["ref"], "r1")
        self.assertEqual(body["items"][2]["ref"], "r3")
        self.assertNotIn("ref", body["items"][1])
        self.assertEqual(body["items"][3]["value"], "eth-al-1l")
        self.assertEqual(body["items"][3]["results"], body["items"][0]["results"])
        for item in body["items"][5:]:
            self.assertTrue(item["message"])
            self.assertEqual((item["results"], item["matches_total"], item["truncated"]), ([], 0, False))
        nf = body["items"][1]
        self.assertEqual((nf["results"], nf["matches_total"], nf["truncated"]), ([], 0, False))
        self.assertEqual(body["summary"], {"found": 4, "not_found": 1, "invalid": len(items) - 5,
                                           "not_allowed": 0, "truncated": 0})

    def test_unencodable_and_control_chars_only_invalidate_their_item(self):
        raw = ('{"items": [{"code": "ETH-AL-1L"}, {"code": "AB\\ud800CD"}, {"code": "ETH-AL-1L", "ref": "x\\udc00"},'
               ' {"code": "A\\u0085B"}, {"cas": "\\u009b64-17-5"}]}')
        r = self.client.post(PATH, data=raw, content_type="application/json",
                             headers={"Authorization": "Bearer " + self.tokens["internal"]},
                             environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        statuses = [it["status"] for it in r.get_json()["items"]]
        self.assertEqual(statuses, ["FOUND", "INVALID", "INVALID", "INVALID", "INVALID"])
        self.assertIsNone(r.get_json()["items"][1]["value"])
        self.assertNotIn("ref", r.get_json()["items"][2])

    def test_deeply_nested_json_is_400_not_500(self):
        deep = '{"items": [' + "[" * 60000 + "]" * 60000 + "]}"
        r = self.client.post(PATH, data=deep, content_type="application/json",
                             headers={"Authorization": "Bearer " + self.tokens["internal"]},
                             environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
        self.assertEqual(r.status_code, 400)

    def test_500_items_all_same_and_all_invalid(self):
        body = self.ok([{"code": "PAR-001"}] * 500)
        self.assertEqual(body["count"], 500)
        self.assertEqual(body["summary"]["found"], 500)
        self.assertEqual(len({json.dumps(it["results"]) for it in body["items"]}), 1)
        body = self.ok([{"code": "   "}] * 500)
        self.assertEqual(body["summary"]["invalid"], 500)


class ExactMatchTests(BatchBase):
    """B3"""

    def test_exact_case_insensitive_trimmed_all_matches_no_contains(self):
        item = self.ok([{"code": "dup-1"}])["items"][0]
        self.assertEqual(item["status"], "FOUND")
        self.assertEqual(item["matches_total"], 3)
        self.assertFalse(item["truncated"])
        self.assertEqual(sorted(r["brand"] for r in item["results"]), ["ALPHA", "BETA", "GAMMA"])
        self.assertNotIn("XDUP-1", [r["code"] for r in item["results"]])
        cas_item = self.ok([{"cas": " 111-11-1 "}])["items"][0]
        self.assertEqual(cas_item["matches_total"], 4)  # CAS khớp cả XDUP-1 (cùng CAS)

    def test_wildcards_are_literal(self):
        item = self.ok([{"code": "w%_!1"}])["items"][0]
        self.assertEqual([r["code"] for r in item["results"]], ["W%_!1"])
        self.assertEqual(self.ok([{"code": "W%"}])["items"][0]["status"], "NOT_FOUND")

    def test_truncation_flag_total_and_brand_interleave(self):
        # 64-17-5: 15 'Ethanol' (5 hãng × 3) + 5 'Ethanol absolute' (ALPHA) = 20 sản phẩm
        item = self.ok([{"cas": "64-17-5"}], max_per_item=6)["items"][0]
        self.assertEqual((item["matches_total"], len(item["results"]), item["truncated"]), (20, 6, True))
        self.assertEqual(len({r["brand"] for r in item["results"][:5]}), 5)
        full = self.ok([{"cas": "64-17-5"}], max_per_item=50)["items"][0]
        self.assertEqual((full["matches_total"], len(full["results"]), full["truncated"]), (20, 20, False))
        self.assertEqual(self.ok([{"cas": "64-17-5"}])["max_per_item"], 20)
        self.assertEqual(self.ok([{"cas": "64-17-5"}], max_per_item=999)["max_per_item"], 50)
        again = self.ok([{"cas": "64-17-5"}], max_per_item=6)["items"][0]
        self.assertEqual(again["results"], item["results"])  # ổn định
        body = self.ok([{"cas": "64-17-5"}], max_per_item=6)
        self.assertEqual(body["summary"]["truncated"], 1)


class ParityTests(BatchBase):
    """B4 — mỗi sản phẩm của batch y hệt phần tử tương ứng của /products"""

    def assert_parity(self, token, codes, include=("compliance", "stock")):
        body = self.ok([{"code": c} for c in codes], token=token, include=list(include), max_per_item=50)
        checked = 0
        for item in body["items"]:
            for result in item["results"]:
                single = self.get("/machine/v1/products", token=token, q=item["value"], limit="50").get_json()
                same = [r for r in single["results"]
                        if all(r.get(k) == result.get(k) for k in ("name", "code", "brand", "size"))]
                self.assertEqual(len(same), 1, (token, item["value"], result.get("brand")))
                self.assertEqual(result, same[0], (token, item["value"]))
                checked += 1
        return checked

    def test_internal_parity_price_compliance_stock(self):
        codes = (list(self.parity_codes) + ["ETH-AL-1L", "ETH-GA-1L", "ETH-BE-500mL", "ETH-DE-1L", "ABS-1L",
                 "MET-A", "LOT-025", "LOT-205", "ZZZ-BIG", "NAN-001", "NAN-002", "NAN-003", "NAN-004", "DUP-1"])
        self.assertGreaterEqual(self.assert_parity("internal", codes), 50)

    def test_customer_parity_full_and_limited(self):
        codes = ["ETH-AL-1L", "ETH-BE-500mL", "ETH-GA-1L", "ABS-1L", "MET-A", "MET-B", "DUP-1", "PAR-000", "PAR-001"]
        self.assertGreaterEqual(self.assert_parity("cust", codes), 6)
        self.assertGreaterEqual(self.assert_parity("limited", codes), 3)

    def test_cas_items_parity(self):
        body = self.ok([{"cas": "64-17-5"}, {"cas": "555-55-5"}], include=["compliance", "stock"], max_per_item=50)
        for item in body["items"]:
            for result in item["results"]:
                single = self.internal(result["code"], limit="50").get_json()
                same = [r for r in single["results"] if r["code"] == result["code"] and r["brand"] == result["brand"]
                        and r["size"] == result["size"] and r["name"] == result["name"]]
                self.assertEqual([result], same)

    def test_same_cas_window_is_per_product_not_per_batch(self):
        # Hai sản phẩm cùng CAS có > 5000 lô trong CÙNG một gói: mỗi sản phẩm vẫn y hệt /products tra riêng.
        for token in ("internal", "cust"):
            body = self.ok([{"code": "ZZZ-BIG"}, {"code": "AAA-1"}, {"cas": "555-55-5"}], token=token,
                           include=["stock"], max_per_item=50)
            for item in body["items"]:
                for result in item["results"]:
                    single = self.get("/machine/v1/products", token=token, q=result["code"], limit="50").get_json()
                    same = [r for r in single["results"] if r["code"] == result["code"]]
                    self.assertEqual(len(same), 1)
                    self.assertEqual(result["stock"], same[0]["stock"], (token, result["code"]))

    def test_item_result_does_not_depend_on_other_items(self):
        # Hợp đồng B4 v2: mọi khối của một mục giống hệt khi gửi riêng mục đó.
        mixed = [{"code": "LOT-205"}, {"code": "ZZZ-BIG"}, {"code": "AAA-1"}, {"cas": "555-55-5"},
                 {"code": "ETH-AL-1L"}, {"cas": "64-17-5"}, {"code": "LOT-025"}, {"code": "DUP-1"}]
        for token in ("internal", "cust"):
            together = self.ok(mixed, token=token, include=["compliance", "stock"], max_per_item=50)["items"]
            for item, sent in zip(together, mixed):
                alone = self.ok([sent], token=token, include=["compliance", "stock"], max_per_item=50)["items"][0]
                self.assertEqual(item["results"], alone["results"], (token, sent))

    def test_same_cas_equals_products_single_product_page(self):
        # Hợp đồng B4 v2: same_cas = /products?q=<mã>&limit=1 (trang chỉ có đúng sản phẩm đó).
        for token in ("internal", "cust"):
            for code in ("ZZZ-BIG", "AAA-1", "ETH-AL-1L", "ETH-BE-500mL", "MET-A", "LOT-205"):
                batch = self.ok([{"code": code}], token=token, include=["stock"])["items"][0]["results"]
                single = self.get("/machine/v1/products", token=token, q=code, limit="1").get_json()["results"]
                if not batch:
                    continue
                self.assertEqual(single[0]["code"], code)
                self.assertEqual(batch[0]["stock"], single[0]["stock"], (token, code))

    def test_include_controls_blocks(self):
        bare = self.ok([{"code": "ETH-AL-1L"}])["items"][0]["results"][0]
        self.assertIn("price", bare)
        self.assertNotIn("compliance", bare)
        self.assertNotIn("stock", bare)
        only_c = self.ok([{"code": "ETH-AL-1L"}], include=["compliance"])["items"][0]["results"][0]
        self.assertIn("compliance", only_c)
        self.assertNotIn("stock", only_c)
        self.assertEqual(self.ok([{"code": "ETH-AL-1L"}], include=["stock", "compliance"])["include"],
                         ["compliance", "stock"])


class SecurityTests(BatchBase):
    """B5, B6"""

    def test_customer_never_sees_price_and_brand_scope_applies(self):
        calls = []
        for token in ("cust", "limited"):
            for include in ([], ["compliance", "stock"]):
                calls.append(self.batch([{"code": "ETH-AL-1L"}, {"code": "ETH-GA-1L"}, {"cas": "64-17-5"},
                                         {"code": "DUP-1"}, {"code": "PAR-005"}], token=token, include=include))
        for response in calls:
            self.assertEqual(response.status_code, 200)
            text = response.get_data(as_text=True)
            for sentinel in (PRICE_SENTINEL, NOTE_SENTINEL, STOCK_NOTE_SENTINEL, str(STOCK_PRICE_SENTINEL)):
                self.assertNotIn(sentinel, text)
            body = response.get_json()
            self.assertFalse(walk_keys(body) & CUSTOMER_FORBIDDEN)
            self.assertTrue(body["price_notice"])
            brands = {r.get("brand") for it in body["items"] for r in it["results"]}
            self.assertFalse(brands - {"ALPHA", "BETA"})
        self.assertEqual(calls[0].get_json()["items"][1]["status"], "NOT_FOUND")  # GAMMA ngoài team

    def test_limited_team_cas_not_allowed_and_compliance_hidden(self):
        body = self.ok([{"cas": "64-17-5"}, {"code": "ETH-AL-1L"}], token="limited", include=["compliance", "stock"])
        self.assertEqual(body["items"][0]["status"], "NOT_ALLOWED")
        self.assertEqual(body["items"][0]["results"], [])
        result = body["items"][1]["results"][0]
        self.assertEqual(result["compliance"]["state"], "HIDDEN")
        self.assertNotIn("cas", result)
        self.assertEqual(body["summary"]["not_allowed"], 1)

    def test_customer_cap_10_and_no_escalation(self):
        body = self.ok([{"cas": "64-17-5"}], token="cust", max_per_item=50)
        self.assertEqual(body["max_per_item"], 10)
        for extra in ({"profile": "internal"}, {"include_price": True}, {"fields": ["price"]}):
            self.assertEqual(self.batch([{"code": "ETH-AL-1L"}], token="cust", **extra).status_code, 400)
        response = self.client.post(PATH, json={"items": [{"code": "ETH-AL-1L"}]},
                                    headers={"Authorization": "Bearer " + self.tokens["cust"], "X-Profile": "internal"},
                                    environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
        self.assertFalse(walk_keys(response.get_json()) & CUSTOMER_FORBIDDEN)

    def test_request_validation_and_auth(self):
        self.assertEqual(self.batch([{"code": "A"}] * 501).status_code, 400)
        self.assertEqual(self.batch([{"code": "A"}] * 501).get_json()["error"], "too_many_items")
        self.assertEqual(self.batch([]).status_code, 400)
        self.assertEqual(self.batch([{"code": "A"}], include=["price"]).status_code, 400)
        self.assertEqual(self.batch([{"code": "A"}], include="stock").status_code, 400)
        for bad in (0, -1, "5", True, 1.5):
            self.assertEqual(self.batch([{"code": "A"}], max_per_item=bad).status_code, 400, bad)
        auth = {"Authorization": "Bearer " + self.tokens["internal"]}
        env = {"REMOTE_ADDR": "198.51.100.7"}
        for data, ctype in (("not json", "application/json"), ('{"items": [{"code": "A"}]}', "text/plain"),
                            ("[1,2]", "application/json")):
            r = self.client.post(PATH, data=data, content_type=ctype, headers=auth, environ_overrides=env)
            self.assertEqual(r.status_code, 400, data)
        big = json.dumps({"items": [{"code": "A" * 100, "ref": "r" * 64}] * 500 + [{"code": "x" * 100}] * 600})
        self.assertGreater(len(big), 128 * 1024)
        r = self.client.post(PATH, data=big, content_type="application/json", headers=auth, environ_overrides=env)
        self.assertEqual(r.status_code, 413)
        full = json.dumps({"items": [{"code": "A" * 100, "ref": "r" * 64}] * 500})
        self.assertLess(len(full), 128 * 1024)
        r = self.client.post(PATH, data=full, content_type="application/json", headers=auth, environ_overrides=env)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(PATH, headers=auth, environ_overrides=env).status_code, 405)
        self.assertEqual(self.client.post(PATH, json={"items": [{"code": "A"}]}).status_code, 401)
        self.assertEqual(self.batch([{"code": "A"}], token="wrong-token").status_code, 401)
        self.assertEqual(self.batch([{"code": "A"}], token="internal_ip").status_code, 403)
        environ = {k: v for k, v in os.environ.items() if k != "MACHINE_INTERNAL_DATABASE_URL"}
        with mock.patch.dict(os.environ, environ, clear=True):
            self.assertEqual(self.batch([{"code": "A"}]).status_code, 503)

    def test_too_many_results_is_explicit(self):
        with mock.patch.object(machine_api, "BATCH_MAX_RESULTS", 5):
            r = self.batch([{"cas": "64-17-5"}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "too_many_results")
        self.assertNotIn("results", r.get_json())

    def test_one_batch_counts_one_rate_hit(self):
        with mock.patch.object(machine_api.rate_limiter, "check", wraps=machine_api.rate_limiter.check) as check:
            self.ok([{"code": "PAR-%03d" % i} for i in range(40)])
        self.assertEqual(check.call_count, 1)

    def test_access_log_has_no_values_refs_or_prices(self):
        with self.assertLogs("machine_api.access", level="INFO") as captured:
            self.ok([{"code": "ETH-AL-1L", "ref": "REF-SENTINEL-9"}, {"cas": "64-17-5"}], include=["stock"])
        text = "\n".join(captured.output)
        for needle in ("ETH-AL-1L", "64-17-5", "REF-SENTINEL-9", PRICE_SENTINEL, self.tokens["internal"]):
            self.assertNotIn(needle, text)
        entry = json.loads(captured.records[-1].getMessage())
        self.assertEqual(set(entry), {"ts", "token", "endpoint", "status", "results", "ip", "ms"})
        self.assertEqual(entry["endpoint"], "products_batch")

    def test_errors_do_not_echo_input(self):
        r = self.batch([{"code": "A"}], include=["SECRET-INCLUDE-XYZ"])
        self.assertNotIn("SECRET-INCLUDE-XYZ", r.get_data(as_text=True))
        r = self.batch([{"code": "A"}], **{"SECRET-KEY-XYZ": 1})
        self.assertNotIn("SECRET-KEY-XYZ", r.get_data(as_text=True))

    def test_statement_timeout_is_local_to_batch(self):
        captured = []
        real = machine_api._batch_rows

        def spy(conn, *args, **kwargs):
            with conn.cursor() as cur:
                cur.execute("SHOW statement_timeout")
                captured.append(cur.fetchone()[0])
                cur.execute("SHOW transaction_read_only")
                captured.append(cur.fetchone()[0])
            return real(conn, *args, **kwargs)

        with mock.patch.object(machine_api, "_batch_rows", side_effect=spy):
            self.ok([{"code": "ETH-AL-1L"}])
        self.assertEqual(captured[:2], ["8s", "on"])


if __name__ == "__main__":
    unittest.main()
