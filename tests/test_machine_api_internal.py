"""Developer tests cho thẻ NỘI BỘ có giá và xếp hạng kết quả của cổng API máy (machine_api.py).

Bám theo specs/machine-api-internal-price/VERIFICATION.md (I1..I8). Dùng database thử tạm (pg_temp_db).
Chạy: PYTHONPATH=.:tests DATABASE_URL=... .venv/bin/python -m unittest tests.test_machine_api_internal
"""

import json
import os
import re
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psycopg2

os.environ.setdefault("FLASK_SECRET_KEY", "machine-api-test-only")

import pg_temp_db
import machine_api
import team_permissions
from currency_rates import load_currency_rate_resolver
from test_machine_api import MachineApiBase, token_entry, ALL_PERMISSIONS, LIMITED_PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]
TODAY = date.today()

PRICE_SENTINEL = "31415.9"
NOTE_SENTINEL = "GHI-CHU-NOI-BO-SENTINEL"
STOCK_PRICE_SENTINEL = 777777
STOCK_NOTE_SENTINEL = "GHI-CHU-KHO-SENTINEL"

BRANDS = {"ALPHA": "USD", "BETA": "EUR", "GAMMA": "VND", "DELTA": "USD", "EPSILON": "AUD", "ZETA": "GBP"}
RATES = {"USD": 25000, "EUR": 27000, "VND": 1, "AUD": 16500}  # GBP cố ý thiếu tỷ giá
PRICES = ["12.5", "100", "0", "", "-5", "abc", "12,5", "1e3", None, "  7 "]
SHIPS = ["1.3", "1", "0", "", "x", None, "1.25"]


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), "isolated local PostgreSQL required")
class InternalBase(MachineApiBase):
    @classmethod
    def seed(cls):
        with cls.conn.cursor() as cur:
            pg_temp_db.apply_brand_master_and_currency_migrations(cur)
        for brand, currency in BRANDS.items():
            cls.sql("INSERT INTO brand_master(name, normalized_name, currency_code) VALUES (%s,%s,%s) "
                    "ON CONFLICT (name) DO UPDATE SET currency_code = EXCLUDED.currency_code",
                    (brand, brand.casefold(), currency))
        # HANGLA: hãng đã ngừng (is_active=false) nên bộ phân giải coi là chưa gán tiền tệ (BRAND_UNKNOWN)
        cls.sql("INSERT INTO brand_master(name, normalized_name, currency_code, is_active) VALUES ('HANGLA','hangla','USD',false) "
                "ON CONFLICT (name) DO UPDATE SET is_active = false")
        for currency, rate in RATES.items():
            cls.sql("INSERT INTO currency_rates(currency_code, rate_vnd) VALUES (%s,%s) "
                    "ON CONFLICT (currency_code) DO UPDATE SET rate_vnd = EXCLUDED.rate_vnd", (currency, rate))
        cls.sql("DELETE FROM currency_rates WHERE currency_code = 'GBP'")

        cls.team_full = cls.sql("INSERT INTO teams(name, permission_keys) VALUES ('Khách-đầy-đủ', %s) RETURNING id",
                                (ALL_PERMISSIONS,))[0][0]
        cls.team_limited = cls.sql("INSERT INTO teams(name, permission_keys) VALUES ('Khách-hạn-chế', %s) RETURNING id",
                                   (LIMITED_PERMISSIONS,))[0][0]
        for team, brands in ((cls.team_full, ["ALPHA", "BETA"]), (cls.team_limited, ["ALPHA"])):
            for brand in brands:
                cls.sql("INSERT INTO team_brands(team_id, brand) VALUES (%s, %s)", (team, brand))
        statuses = dict(cls.sql("SELECT stable_key, id FROM regulatory_statuses"))
        cls.sql("INSERT INTO regulatory_rules(status_id, rule_type, rule_label, match_field, match_value, note) "
                "VALUES (%s, '', '', 'cas', '64-17-5', 'ghi chu etanol')", (statuses["CAN_GIAY_PHEP"],))

        def product(name, code, cas, brand, size, price=PRICE_SENTINEL, ship="1.3", note=NOTE_SENTINEL):
            cls.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (name, code, cas, brand, size, price, ship, note, brand))

        # Dữ liệu xếp hạng: 5 hãng × 3 quy cách tên trùng "Ethanol"; "Ethanol absolute" (bắt đầu bằng); "Methanol" (chứa)
        for brand in ("ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON"):
            for size in ("100mL", "500mL", "1L"):
                product("Ethanol", f"ETH-{brand[0]}{brand[1]}-{size}", "64-17-5", brand, size)
        for size in ("1L", "2L", "4L", "5L", "10L"):
            product("Ethanol absolute", f"ABS-{size}", "64-17-5", "ALPHA", size)
        for brand in ("ALPHA", "BETA", "GAMMA"):
            product("Methanol HPLC", f"MET-{brand[0]}", "67-56-1", brand, "1L")
        product("Zzz mã trùng", "ETHANOL", "", "ALPHA", "1L")           # trùng MÃ với từ khóa 'ethanol'
        product("Ca gần giống", "CAS-NEAR", "164-17-50", "ALPHA", "1L")  # CAS chỉ CHỨA '64-17-5'
        for index in range(60):
            product(f"Bulk acid {index:02d}", f"BLK-{index:03d}", "", "ALPHA", "1L")
        # Ma trận so khớp giá với ứng dụng nhân viên: ≥ 30 sản phẩm, nhiều hãng/tiền tệ/định dạng
        cls.parity_codes = []
        brand_cycle = list(BRANDS) + ["HANGLA"]  # HANGLA không có trong brand_master
        for index in range(42):
            code = f"PAR-{index:03d}"
            product(f"Parity item {index:03d}", code, "", brand_cycle[index % len(brand_cycle)], "1L",
                    price=PRICES[index % len(PRICES)], ship=SHIPS[(index * 3) % len(SHIPS)], note="")
            cls.parity_codes.append(code)

        # Giá hỏng: một sản phẩm bẩn không được làm hỏng cả truy vấn
        product("Poison clean", "NAN-002", "", "ALPHA", "1L", price="10", ship="1.1", note="")
        product("Poison nan", "NAN-001", "", "ALPHA", "1L", price="nan", ship="1.1", note="")
        product("Poison inf", "NAN-003", "", "ALPHA", "1L", price="10", ship="inf", note="")
        product("Poison huge", "NAN-004", "", "ALPHA", "1L", price="1e308", ship="1e308", note="")
        product("Many lots a", "LOT-025", "", "ALPHA", "1L")
        product("Many lots b", "LOT-205", "", "ALPHA", "1L")
        product("Big same cas", "ZZZ-BIG", "555-55-5", "ALPHA", "1L")

        snapshot = str(uuid.uuid4())
        cls.sql("INSERT INTO stock_snapshots(id, source_kind, actor, row_count, content_sha256) VALUES (%s,'IMPORT','test',1,'x')",
                (snapshot,))

        def stock(name, code, cas, brand, size, qty, expiry):
            cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                    "brand_norm,code_norm,size_norm,cas_norm,stock_note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (snapshot, name, code, cas, brand, size, STOCK_PRICE_SENTINEL, qty, expiry, brand.casefold(),
                     code.casefold(), size.casefold(), cas.casefold() or None, STOCK_NOTE_SENTINEL))
        stock("Ethanol", "ETH-AL-1L", "64-17-5", "ALPHA", "1L", 7, TODAY + timedelta(days=200))
        stock("Ethanol", "ETH-AL-1L", "64-17-5", "ALPHA", "1L", 3, TODAY + timedelta(days=20))
        stock("Ethanol", "ETH-GA-1L", "64-17-5", "GAMMA", "1L", 4, None)
        stock("Ethanol", "ETH-BE-500mL", "64-17-5", "BETA", "500mL", 0, None)
        for code, n in (("LOT-025", 25), ("LOT-205", 205)):
            cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                    "brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                    "SELECT %s, 'Many lots', %s, '', 'ALPHA', '1L', 1, 1, current_date + g, 'alpha', %s, '1l', NULL, '' "
                    "FROM generate_series(100, 99 + %s) AS g", (snapshot, code, code.casefold(), n))
        # > 5000 lô cùng CAS, mã cần hỏi (ZZZ-BIG) xếp sau cùng theo thứ tự chữ
        cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                "brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                "SELECT %s, 'Same cas', 'AAA-' || g, '555-55-5', 'ALPHA', '1L', 1, 1, NULL, 'alpha', 'aaa-' || g, '1l', '555-55-5', '' "
                "FROM generate_series(1, 5200) AS g", (snapshot,))
        cls.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,"
                "brand_norm,code_norm,size_norm,cas_norm,stock_note) VALUES (%s,'Big same cas','ZZZ-BIG','555-55-5','ALPHA','1L',1,40,NULL,"
                "'alpha','zzz-big','1l','555-55-5','')", (snapshot,))
        cls.sql("UPDATE stock_state SET active_snapshot_id=%s, revision=revision+1", (snapshot,))

        cls.tokens = {n: "stm_test_" + n + "_" + uuid.uuid4().hex for n in ("cust", "limited", "internal", "internal_ip")}
        cls.write_tokens([
            token_entry(cls.tokens["cust"], "c-full", cls.team_full),
            token_entry(cls.tokens["limited"], "c-limited", cls.team_limited),
            token_entry(cls.tokens["internal"], "i-main", None, profile="internal", allowed_ips=["198.51.100.0/24"]),
            token_entry(cls.tokens["internal_ip"], "i-ip", None, profile="internal", allowed_ips=["203.0.113.0/24"]),
        ])

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": self.dsn})
        patcher.start()
        self.addCleanup(patcher.stop)

    def internal(self, q, **extra):
        return self.products(q, token="internal", **extra)


def walk_keys(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key.lower())
            walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            walk_keys(item, found)
    return found


class TokenConfigTests(InternalBase):
    """I2"""

    def entries(self, *raw):
        path = os.path.join(self.tmp.name, "cfg.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"tokens": list(raw)}, handle)
        os.chmod(path, 0o600)
        return {e["id"]: e for e in machine_api.load_token_entries(path)}

    def test_internal_needs_allowed_ips_and_valid_profile(self):
        good = token_entry("tok-a" * 6, "i-ok", None, profile="internal", allowed_ips=["198.51.100.0/24"])
        no_ip = token_entry("tok-b" * 6, "i-noip", None, profile="internal")
        empty_ip = token_entry("tok-c" * 6, "i-empty", None, profile="internal", allowed_ips=[])
        bad_profile = token_entry("tok-d" * 6, "i-bad", None, profile="Internal", allowed_ips=["198.51.100.0/24"])
        spaced = token_entry("tok-e" * 6, "i-sp", None, profile="internal ", allowed_ips=["198.51.100.0/24"])
        cust_no_team = token_entry("tok-f" * 6, "c-noteam", None)
        loaded = self.entries(good, no_ip, empty_ip, bad_profile, spaced, cust_no_team)
        self.assertEqual(set(loaded), {"i-ok"})
        self.assertEqual(loaded["i-ok"]["profile"], "internal")
        self.assertEqual(loaded["i-ok"]["stock_detail"], "quantity")
        self.assertIsNone(loaded["i-ok"]["team_id"])

    def test_default_profile_is_customer(self):
        loaded = self.entries(token_entry("tok-z" * 6, "c-ok", self.team_full))
        self.assertEqual(loaded["c-ok"]["profile"], "customer")

    def test_internal_without_internal_dsn_is_503_and_never_falls_back(self):
        env = {k: v for k, v in os.environ.items() if k != "MACHINE_INTERNAL_DATABASE_URL"}
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.internal("ethanol")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("results", response.get_data(as_text=True))
        # thẻ khách vẫn chạy bình thường khi không có DSN nội bộ
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.products("ethanol", token="cust").status_code, 200)

    def test_internal_token_ip_allowlist_and_auth_failures(self):
        self.assertEqual(self.products("ethanol", token="internal_ip").status_code, 403)  # 198.51.100.7 ngoài dải
        ok = self.get("/machine/v1/products", token="internal_ip", addr="203.0.113.9", q="ethanol")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.products("ethanol", token="nope").status_code, 401)

    def test_customer_connection_never_uses_internal_dsn(self):
        seen = []
        real = psycopg2.connect

        def spy(dsn, *args, **kwargs):
            seen.append((dsn, kwargs.get("options", "")))
            return real(dsn, *args, **kwargs)
        other = self.dsn + ("&" if "?" in self.dsn else "?") + "application_name=ignored"
        with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": other}):
            with mock.patch.object(psycopg2, "connect", spy):
                self.assertEqual(self.products("ethanol", token="cust").status_code, 200)
                self.assertEqual(self.internal("ethanol").status_code, 200)
        customer_dsns = {dsn for dsn, opt in seen if "machine-api-internal" not in opt}
        internal_dsns = {dsn for dsn, opt in seen if "machine-api-internal" in opt}
        self.assertEqual(customer_dsns, {self.dsn})
        self.assertEqual(internal_dsns, {other})


class RepairTests(InternalBase):
    """Các lỗi verifier độc lập tìm thấy (I2, I3, I4, I8) — sửa lần 1 (2026-11-22)"""

    def test_dirty_prices_do_not_poison_the_whole_query(self):
        response = self.internal("Poison")
        self.assertEqual(response.status_code, 200)
        by_code = {r["code"]: r["price"] for r in response.get_json()["results"]}
        self.assertEqual(set(by_code), {"NAN-001", "NAN-002", "NAN-003", "NAN-004"})
        self.assertEqual(by_code["NAN-002"]["price_status"], "OK")
        self.assertEqual(by_code["NAN-002"]["unit_price_vnd"], round(10 * 1.1 * 25000, -3))
        for code in ("NAN-001", "NAN-003", "NAN-004"):
            self.assertIsNone(by_code[code]["unit_price_vnd"], code)
            self.assertEqual(by_code[code]["price_status"], "INVALID_PRICE_DATA", code)

    def test_lots_are_not_silently_truncated(self):
        small = self.internal("LOT-025").get_json()["results"][0]["stock"]
        self.assertEqual((len(small["lots"]), small["lots_total"], small["lots_truncated"]), (25, 25, False))
        self.assertEqual(small["quantity"], 25)
        big = self.internal("LOT-205").get_json()["results"][0]["stock"]
        self.assertEqual((len(big["lots"]), big["lots_total"], big["lots_truncated"]), (200, 205, True))
        self.assertEqual(big["quantity"], 205)  # số tổng vẫn đủ
        item = self.get("/machine/v1/stock", token="internal", code="LOT-205").get_json()["items"][0]
        self.assertTrue(item["lots_truncated"])

    def test_exact_code_stock_survives_more_than_5000_same_cas_lots(self):
        item = self.internal("ZZZ-BIG").get_json()["results"][0]
        self.assertEqual((item["stock"]["status"], item["stock"]["quantity"]), ("IN_STOCK", 40))

    def test_internal_compliance_has_no_no_price_notice(self):
        body = self.get("/machine/v1/compliance", token="internal", cas="64-17-5").get_json()
        self.assertNotIn("price_notice", body)
        customer = self.get("/machine/v1/compliance", token="cust", cas="64-17-5").get_json()
        self.assertIn("price_notice", customer)

    def test_wide_ip_ranges_and_long_expiry_rejected_for_internal(self):
        far = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        ok = (datetime.now(timezone.utc) + timedelta(days=89)).isoformat()
        entries = [
            token_entry("w1" * 12, "i-wide4", None, profile="internal", allowed_ips=["0.0.0.0/0"]),
            token_entry("w2" * 12, "i-wide6", None, profile="internal", allowed_ips=["::/0"]),
            token_entry("w3" * 12, "i-slash8", None, profile="internal", allowed_ips=["10.0.0.0/8"]),
            token_entry("w4" * 12, "i-mixed", None, profile="internal", allowed_ips=["203.0.113.5", "0.0.0.0/0"]),
            token_entry("w5" * 12, "i-far", None, profile="internal", allowed_ips=["203.0.113.5"], expires_at=far),
            token_entry("w6" * 12, "i-ok24", None, profile="internal", allowed_ips=["203.0.113.0/24"], expires_at=ok),
            token_entry("w7" * 12, "i-ok-host", None, profile="internal", allowed_ips=["203.0.113.5/32"]),
        ]
        path = os.path.join(self.tmp.name, "wide.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"tokens": entries}, handle)
        os.chmod(path, 0o600)
        loaded = {e["id"] for e in machine_api.load_token_entries(path)}
        self.assertEqual(loaded, {"i-ok24", "i-ok-host"})

    def test_cli_rejects_zero_or_negative_days(self):
        from scripts import machine_token
        path = os.path.join(self.tmp.name, "cli.json")
        for days in ("0", "-5"):
            with self.assertRaises(SystemExit):
                machine_token.main(["new", "--file", path, "--id", "x-days", "--profile", "internal",
                                    "--allowed-ip", "203.0.113.5", "--days", days])


class CustomerUnchangedTests(InternalBase):
    """I1"""

    FORBIDDEN = {"price", "unit_price_vnd", "original_price", "ship_factor", "ship", "note", "stock_note",
                 "stock_price_vnd", "lots", "rate_vnd", "currency", "price_status"}

    def test_customer_never_sees_price_or_internal_notes(self):
        calls = []
        for token in ("cust", "limited"):
            for q in ("ethanol", "ETH-AL-1L", "64-17-5", "methanol", "Parity item 001", "PAR-005", "bulk"):
                calls.append(self.products(q, token=token))
            calls.append(self.products("ethanol", token=token, profile="internal", include_price="1", fields="price", debug="1"))
            calls.append(self.get("/machine/v1/products", token=token, q="ethanol", headers={"X-Profile": "internal"}))
        calls += [self.get("/machine/v1/stock", token="cust", code="ETH-AL-1L"),
                  self.get("/machine/v1/stock", token="cust", cas="64-17-5"),
                  self.get("/machine/v1/compliance", token="cust", cas="64-17-5")]
        self.assertGreaterEqual(len(calls), 20)
        for response in calls:
            text = response.get_data(as_text=True)
            for sentinel in (PRICE_SENTINEL, NOTE_SENTINEL, STOCK_NOTE_SENTINEL, str(STOCK_PRICE_SENTINEL)):
                self.assertNotIn(sentinel, text)
            if response.status_code == 200:
                self.assertFalse(walk_keys(response.get_json()) & self.FORBIDDEN, text[:200])

    def test_customer_limit_cap_is_still_10(self):
        response = self.products("bulk", token="cust", limit="500")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual((body["count"], body["more"]), (10, True))  # team_full thấy hãng ALPHA có 60 'Bulk acid'
        self.assertTrue(body["price_notice"])


class InternalVisibilityTests(InternalBase):
    """I3, I6, I8"""

    def test_internal_sees_every_brand_and_all_fields(self):
        body = self.internal("ETH-GA-1L").get_json()
        self.assertEqual(body["count"], 1)
        item = body["results"][0]
        self.assertEqual(item["brand"], "GAMMA")  # GAMMA không thuộc team nào
        self.assertEqual(item["note"], NOTE_SENTINEL)
        self.assertEqual(item["price"]["original_price"], PRICE_SENTINEL)
        self.assertEqual(item["price"]["ship_factor"], "1.3")
        self.assertEqual(item["price"]["currency"], "VND")
        self.assertEqual(item["price"]["unit_price_vnd"], round(31415.9 * 1.3 * 1, -3))
        self.assertEqual(item["compliance"]["state"], "RULE")
        self.assertEqual(item["compliance"]["regulatory_note"], "ghi chu etanol")
        stock = item["stock"]
        self.assertEqual((stock["status"], stock["quantity"]), ("IN_STOCK", 4))
        lot = stock["lots"][0]
        self.assertEqual((lot["quantity"], lot["stock_price_vnd"], lot["stock_note"]), (4, STOCK_PRICE_SENTINEL, STOCK_NOTE_SENTINEL))
        self.assertNotIn("price_notice", body)

    def test_customer_team_still_filters_brands_but_internal_does_not(self):
        cust = {r["brand"] for r in self.products("ethanol", token="limited", limit="10").get_json()["results"]}
        self.assertEqual(cust, {"ALPHA"})
        internal = {r["brand"] for r in self.internal("ethanol", limit="50").get_json()["results"]}
        self.assertTrue({"GAMMA", "DELTA", "EPSILON"} <= internal)

    def test_no_technical_ids_and_lots_detail(self):
        body = self.internal("ETH-AL-1L").get_json()
        keys = walk_keys(body)
        self.assertFalse(keys & {"id", "product_id", "stock_item_id", "sha256", "token", "password"})
        lots = body["results"][0]["stock"]["lots"]
        self.assertEqual(sorted(lot["quantity"] for lot in lots), [3, 7])
        self.assertEqual(body["results"][0]["stock"]["quantity"], 10)

    def test_no_stock_means_out_of_stock_and_empty_lots(self):
        item = self.internal("ETH-DE-1L").get_json()["results"][0]
        self.assertEqual(item["stock"]["status"], "OUT_OF_STOCK")
        self.assertEqual(item["stock"]["lots"], [])

    def test_stock_endpoint_internal_has_lots_and_quantities(self):
        by_code = self.get("/machine/v1/stock", token="internal", code="ETH-AL-1L").get_json()
        self.assertEqual(by_code["quantity"], 10)
        self.assertEqual(len(by_code["items"][0]["lots"]), 2)
        by_cas = self.get("/machine/v1/stock", token="internal", cas="64-17-5").get_json()
        self.assertGreaterEqual(len(by_cas["items"]), 3)
        self.assertTrue(all("lots" in item for item in by_cas["items"]))
        self.assertNotIn("price_notice", by_cas)

    def test_compliance_endpoint_full_for_internal(self):
        block = self.get("/machine/v1/compliance", token="internal", cas="64-17-5").get_json()["compliance"]
        self.assertEqual((block["state"], block["label"], block["regulatory_note"]), ("RULE", "Cần giấy phép", "ghi chu etanol"))

    def test_internal_limits(self):
        default = self.internal("bulk").get_json()
        self.assertEqual((default["count"], default["more"]), (20, True))
        maximum = self.internal("bulk", limit="500").get_json()
        self.assertEqual((maximum["count"], maximum["more"]), (50, True))
        for bad in ("0", "abc", "-1", "1.5", ""):
            if bad == "":
                self.assertEqual(self.internal("bulk", limit=bad).status_code, 200)
            else:
                self.assertEqual(self.internal("bulk", limit=bad).status_code, 400, bad)

    def test_access_log_has_no_price_or_keyword(self):
        with self.assertLogs("machine_api.access", level="INFO") as captured:
            self.internal("ETH-AL-1L")
        text = "\n".join(captured.output)
        for needle in (PRICE_SENTINEL, "ETH-AL-1L", self.tokens["internal"], "31415", "unit_price"):
            self.assertNotIn(needle, text)
        entry = json.loads(captured.records[-1].getMessage())
        self.assertEqual(set(entry), {"ts", "token", "endpoint", "status", "results", "ip", "ms"})
        self.assertEqual(entry["token"], "i-main")


class PriceParityTests(InternalBase):
    """I4 — khớp từng đồng với hàm tính giá của ứng dụng nhân viên"""

    def test_unit_price_matches_staff_function_for_matrix(self):
        import search  # noqa: PLC0415  (nạp chậm: cần FLASK_SECRET_KEY)
        conn = psycopg2.connect(self.dsn)
        try:
            resolver = load_currency_rate_resolver(conn, str(ROOT))
            rows = {r[0]: r[1:] for r in self.sql(
                "SELECT code, price, ship, brand FROM products WHERE code LIKE 'PAR-%%'")}
            self.assertGreaterEqual(len(rows), 30)
            checked = statuses = 0
            seen = set()
            for code in self.parity_codes:
                body = self.internal(code).get_json()
                self.assertEqual(body["count"], 1, code)
                block = body["results"][0]["price"]
                price, ship, brand = rows[code]
                value, _display, valid, status = search._compute_unit_price_details(price, ship, brand, resolver)
                if valid:
                    self.assertEqual(block["unit_price_vnd"], int(value), (code, price, ship, brand))
                    self.assertIn(block["price_status"], ("OK", "NO_PRICE_DATA"))
                    self.assertEqual(block["price_status"] == "OK", _num(price) > 0 and _num(ship) > 0 and block["unit_price_vnd"] > 0, (code, price, ship))
                else:
                    self.assertIsNone(block["unit_price_vnd"], code)  # không bao giờ 0 hay tỷ giá 1 ngầm
                    self.assertEqual(block["price_status"], str(status), code)
                    statuses += 1
                seen.add(block["price_status"])
                self.assertEqual(block["original_price"], price)
                self.assertEqual(block["ship_factor"], ship)
                checked += 1
            self.assertEqual(checked, len(self.parity_codes))
            self.assertGreater(statuses, 0, "ma trận phải có ca lỗi tỷ giá (hãng ZETA thiếu tỷ giá, HANGLA ngừng hoạt động)")
            self.assertTrue({"OK", "NO_PRICE_DATA"} <= seen)
        finally:
            conn.close()

    def test_rate_change_applies_to_next_call(self):
        before = self.internal("ETH-DE-1L").get_json()["results"][0]["price"]["unit_price_vnd"]
        self.sql("UPDATE currency_rates SET rate_vnd = 26000 WHERE currency_code = 'USD'")
        try:
            after = self.internal("ETH-DE-1L").get_json()["results"][0]["price"]["unit_price_vnd"]
        finally:
            self.sql("UPDATE currency_rates SET rate_vnd = 25000 WHERE currency_code = 'USD'")
        self.assertEqual(before, round(31415.9 * 1.3 * 25000, -3))
        self.assertEqual(after, round(31415.9 * 1.3 * 26000, -3))

    def test_missing_rate_brand_is_null_with_status(self):
        zeta = self.sql("SELECT code FROM products WHERE code LIKE 'PAR-%%' AND brand = 'ZETA' ORDER BY code LIMIT 1")[0][0]
        block = self.internal(zeta).get_json()["results"][0]["price"]
        self.assertIsNone(block["unit_price_vnd"])
        self.assertNotEqual(block["price_status"], "OK")


def _num(value):
    try:
        return float(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


class RankingTests(InternalBase):
    """I7"""

    def brands(self, body):
        return [r["brand"] for r in body["results"]]

    def test_exact_code_then_exact_name_then_prefix_then_contains(self):
        body = self.internal("ethanol", limit="50").get_json()
        names = [(r["code"], r["name"]) for r in body["results"]]
        self.assertEqual(names[0][0], "ETHANOL")  # trùng MÃ đứng đầu
        exact = [n for n in names[1:] if n[1] == "Ethanol"]
        self.assertEqual(names[1:1 + len(exact)], exact)  # rồi tới trùng TÊN
        first_prefix = next(i for i, n in enumerate(names) if n[1] == "Ethanol absolute")
        last_exact = max(i for i, n in enumerate(names) if n[1] == "Ethanol")
        self.assertLess(last_exact, first_prefix)
        first_contains = next(i for i, n in enumerate(names) if n[1] == "Methanol HPLC")
        self.assertLess(max(i for i, n in enumerate(names) if n[1] == "Ethanol absolute"), first_contains)

    def test_brands_are_interleaved_not_alphabetical_block(self):
        body = self.internal("ethanol", limit="6").get_json()  # 1 mã + 5 tên trùng (5 hãng)
        self.assertTrue(body["more"])
        top = self.brands(body)[1:6]
        self.assertEqual(len(set(top)), 5, top)  # 5 hãng khác nhau, không dồn một hãng
        body = self.internal("ethanol", limit="11").get_json()
        second_round = self.brands(body)[6:11]
        self.assertEqual(len(set(second_round)), 5, second_round)

    def test_customer_brand_diversity_with_team_scope(self):
        body = self.products("ethanol", token="cust", limit="10").get_json()
        brands = self.brands(body)
        self.assertEqual({b for b in brands}, {"ALPHA", "BETA"})
        self.assertEqual(brands[:3].count("ALPHA"), 2)  # mã ETHANOL (ALPHA), rồi xen BETA/ALPHA

    def test_exact_cas_before_cas_contains(self):
        body = self.internal("64-17-5", limit="50").get_json()
        codes = [r["code"] for r in body["results"]]
        self.assertEqual(codes[-1], "CAS-NEAR")
        self.assertEqual(body["results"][0]["cas"], "64-17-5")

    def test_order_is_stable_and_more_is_accurate(self):
        first = self.internal("ethanol", limit="7").get_json()
        second = self.internal("ethanol", limit="7").get_json()
        self.assertEqual([r["code"] for r in first["results"]], [r["code"] for r in second["results"]])
        total = self.internal("ethanol", limit="50").get_json()
        self.assertFalse(total["more"])
        self.assertTrue(first["more"])

    def test_wildcards_are_literal(self):
        self.assertEqual(self.internal("%%%").get_json()["count"], 0)
        self.assertEqual(self.internal("100%").get_json()["count"], 0)
        self.assertEqual(self.internal("_th_nol").get_json()["count"], 0)


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), "isolated local PostgreSQL required")
class InternalRoleTests(InternalBase):
    """I5 — role riêng machine_api_internal_ro (sql/machine_internal_role.sql)"""

    @classmethod
    def seed(cls):
        super().seed()
        cls.role = "machine_api_internal_ro_t" + os.urandom(3).hex()
        cls.password = "pw" + os.urandom(6).hex()
        sql = (ROOT / "sql/machine_internal_role.sql").read_text(encoding="utf-8")
        sql = sql.replace("{{DB_NAME}}", cls.db_name).replace("{{PASSWORD}}", cls.password).replace(
            "machine_api_internal_ro", cls.role)
        cls.sql(sql)

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "role", None):
                with cls.conn.cursor() as cur:
                    cur.execute(f'DROP OWNED BY "{cls.role}"')
                admin = psycopg2.connect(pg_temp_db.maintenance_dsn())
                admin.autocommit = True
                try:
                    with admin.cursor() as cur:
                        cur.execute(f'DROP ROLE IF EXISTS "{cls.role}"')
                finally:
                    admin.close()
        finally:
            super().tearDownClass()

    @classmethod
    def role_dsn(cls):
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(cls.dsn)
        return urlunparse(parsed._replace(netloc=f"{cls.role}:{cls.password}@{parsed.hostname}:{parsed.port or 5432}"))

    def run_sql(self, query):
        conn = psycopg2.connect(self.role_dsn())
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(query)
                return cur.fetchall() if cur.description else None
        finally:
            conn.close()

    def test_role_reads_only_the_three_data_kinds(self):
        for query in ("SELECT price, ship, note FROM products LIMIT 1", "SELECT stock_price_vnd, stock_note FROM stock_items LIMIT 1",
                      "SELECT * FROM regulatory_rules LIMIT 1", "SELECT name, currency_code FROM brand_master LIMIT 1",
                      "SELECT currency_code, rate_vnd FROM currency_rates LIMIT 1"):
            self.run_sql(query)
        denied = ["SELECT updated_by FROM currency_rates", "SELECT created_at FROM brand_master"]
        for table in ("teams", "team_brands", "app_users", "login_audit_events", "product_admin_events"):
            if self.sql("SELECT to_regclass(%s)", (table,))[0][0] is not None:
                denied.append(f"SELECT * FROM {table}")
        self.assertGreaterEqual(len(denied), 5)
        for query in denied:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege, msg=query):
                self.run_sql(query)

    def test_role_cannot_write_or_change_schema(self):
        for query in ("INSERT INTO products(name) VALUES ('x')", "UPDATE products SET price = '1'", "DELETE FROM products",
                      "UPDATE currency_rates SET rate_vnd = 1", "CREATE TABLE zz(a int)", "DROP TABLE products",
                      "TRUNCATE products"):
            with self.assertRaises(psycopg2.Error, msg=query):
                self.run_sql(query)

    def test_gateway_under_role_equals_gateway_under_owner(self):
        queries = ["ethanol", "ETH-AL-1L", "64-17-5", "PAR-003", "PAR-005", "bulk", "methanol"]
        owner = {q: self.internal(q, limit="50").get_data(as_text=True) for q in queries}
        with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": self.role_dsn()}):
            role = {q: self.internal(q, limit="50").get_data(as_text=True) for q in queries}
            stock = self.get("/machine/v1/stock", token="internal", cas="64-17-5").get_data(as_text=True)
            comp = self.get("/machine/v1/compliance", token="internal", cas="64-17-5").get_data(as_text=True)
        self.assertEqual(owner, role)
        self.assertIn("lots", stock)
        self.assertIn("RULE", comp)


if __name__ == "__main__":
    unittest.main()
