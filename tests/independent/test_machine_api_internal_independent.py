"""Test độc lập của VERIFIER cho thẻ NỘI BỘ có giá của cổng API máy (claim I1..I8 của
specs/machine-api-internal-price/VERIFICATION.md). Không dùng/không sửa tests/test_machine_api_internal.py.

Mọi ca là ca đối kháng do verifier tự nghĩ. Chạy trên database thử tạm (tests/pg_temp_db.py), không đụng .env.

Chạy lại:
  export DATABASE_URL=postgresql://searchlocal:searchlocal@localhost:5432/products_local
  PYTHONPATH=.:tests:tests/independent .venv/bin/python -m unittest tests.independent.test_machine_api_internal_independent
"""

import http.client
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse, urlunparse

import psycopg2

import machine_api_verifier_support as S
import machine_api
import pg_temp_db
from currency_rates import load_currency_rate_resolver

ROOT = Path(__file__).resolve().parents[2]
V = "/machine/v1"
HAS_PG = pg_temp_db.probe_postgres_reachable()
TODAY = date.today()
OLD_COMMIT = "351cf1a"

PRICE_S = "31415.9"          # giá canary (không được lọt ra thẻ khách / nhật ký / lỗi)
NOTE_S = "GHI-CHU-NOI-BO-VERIFIER"
SPRICE_S = 777123
SNOTE_S = "GHI-CHU-KHO-VERIFIER"
INTERNAL_IP = "198.51.100.7"
INTERNAL_NET = "198.51.100.0/24"

BRANDS = {"CATO": "USD", "ALPHA": "USD", "BETA": "EUR", "GAMMA": "VND", "DELTA": "AUD", "EPS": "USD", "ZETA": "GBP"}
RATES = {"USD": 25000, "EUR": 27000, "VND": 1, "AUD": 16500}   # GBP cố ý không có tỷ giá
ALL_PERMS = S.ALL_PERMS


def keys_deep(value, acc=None):
    acc = set() if acc is None else acc
    if isinstance(value, dict):
        for k, v in value.items():
            acc.add(str(k).lower())
            keys_deep(v, acc)
    elif isinstance(value, list):
        for v in value:
            keys_deep(v, acc)
    return acc


class IEnv(S.Env):
    """Database thử + dữ liệu của verifier (khác seed cũ): nhiều hãng, tiền tệ, xếp hạng, tồn kho."""

    def seed(self):
        with self.conn.cursor() as cur:
            pg_temp_db.apply_brand_master_and_currency_migrations(cur)
        for brand, cur_code in BRANDS.items():
            self.sql("INSERT INTO brand_master(name, normalized_name, currency_code) VALUES (%s,%s,%s) "
                     "ON CONFLICT (name) DO UPDATE SET currency_code=EXCLUDED.currency_code", (brand, brand.casefold(), cur_code))
        for code, rate in RATES.items():
            self.sql("INSERT INTO currency_rates(currency_code, rate_vnd) VALUES (%s,%s) "
                     "ON CONFLICT (currency_code) DO UPDATE SET rate_vnd=EXCLUDED.rate_vnd", (code, rate))
        self.sql("DELETE FROM currency_rates WHERE currency_code='GBP'")
        # HANG-LA: hãng ngừng hoạt động (is_active=false) => bộ phân giải coi là chưa gán tiền tệ
        self.sql("INSERT INTO brand_master(name, normalized_name, currency_code, is_active) VALUES ('HANG-LA','hang-la','USD',false)")
        self.spaced_brand_ok = True
        try:
            self.sql("INSERT INTO brand_master(name, normalized_name, currency_code) VALUES (' SPACED ','spaced','USD')")
        except psycopg2.Error:
            self.spaced_brand_ok = False
        self.team_full = self.new_team("ifull", ALL_PERMS, ["CATO", "ALPHA", "BETA"])
        self.team_limited = self.new_team("ilimited", ["SEARCH", "VIEW_NAME", "VIEW_CODE", "VIEW_BRAND"], ["CATO"])
        statuses = dict(self.sql("SELECT stable_key, id FROM regulatory_statuses"))
        self.statuses = statuses
        self.sql("INSERT INTO regulatory_rules(status_id, rule_type, rule_label, match_field, match_value, note) "
                 "VALUES (%s,'','','cas','50-99-7','ghi-chu-phap-che-glu')", (statuses["CAN_GIAY_PHEP"],))
        self.sql("INSERT INTO regulatory_rules(status_id, rule_type, rule_label, match_field, match_value, note) "
                 "VALUES (%s,'','','cas','64-17-5','cam-etanol')", (statuses["CAM_NHAP"],))
        self.sql("INSERT INTO brand_compliance_settings VALUES ('ALPHA', true, now())")

        def product(name, code, cas, brand, size="1L", price=PRICE_S, ship="1.3", note=NOTE_S, manual=None, mnote=None):
            self.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, manual_compliance, manual_compliance_note, source_brand) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (name, code, cas, brand, size, price, ship, note, manual, mnote, brand))
        self.product = product
        brands = list(BRANDS)
        # --- bộ xếp hạng từ khóa "glucose" ---------------------------------
        product("Zzz mã trùng", "GLUCOSE", "", "ZETA")
        product("Zzz mã trùng 2", "glucose", "", "CATO")
        for b in brands:                                  # trùng CAS 50-99-7
            product("Dextrose " + b, "DEX-" + b, "50-99-7", b)
        product("CAS chỉ chứa", "CASNEAR", "150-99-70", "CATO")
        for b in brands:                                  # trùng TÊN
            for size in ("100mL", "500mL", "1L"):
                product("Glucose", f"GN-{b}-{size}", "", b, size)
        for n in range(4):                                # mã BẮT ĐẦU bằng
            product("Pref code " + str(n), f"GLUCOSE-{n}", "", "CATO" if n < 3 else "BETA")
        for n in range(20):                               # tên bắt đầu bằng; CATO áp đảo
            product(f"Glucose anhydrous {n:02d}", f"GA-C{n:02d}", "", "CATO")
        for b in brands[1:]:
            for n in range(2):
                product(f"Glucose anhydrous {b}{n}", f"GA-{b}{n}", "", b)
        for n in range(20):                               # tên CHỨA; CATO áp đảo
            product(f"D-Glucose monohydrate {n:02d}", f"DM-C{n:02d}", "", "CATO")
        for b in brands[1:]:
            for n in range(2):
                product(f"D-Glucose monohydrate {b}{n}", f"DM-{b}{n}", "", b)
        # --- ký tự đặc biệt -------------------------------------------------
        product("Pure 100% sodium", "SP-001", "", "CATO")
        product("Pure 1000 sodium", "SP-002", "", "CATO")
        product("Under_score thing", "SP-003", "", "CATO")
        product("UnderXscore thing", "SP-004", "", "CATO")
        product("Back\\slash thing", "SP-005", "", "CATO")
        product("Bang!excl thing", "SP-006", "", "CATO")
        product("Quote'apos thing", "SP-007", "", "CATO")
        # --- code trùng, khác hãng -----------------------------------------
        product("Dupcode one", "DUP-1", "", "ALPHA")
        product("Dupcode two", "DUP-1", "", "GAMMA")
        # --- pháp chế / thủ công -------------------------------------------
        product("Override sample", "OVR-1", "64-17-5", "ALPHA", manual="Được bán", mnote="ghi chu thu cong")
        product("Override ignored", "OVR-2", "64-17-5", "CATO", manual="Được bán", mnote="phai-bo-qua")
        product("Norule sample", "NOR-1", "71-43-2", "CATO")
        # --- tồn kho -------------------------------------------------------
        snap = str(uuid.uuid4())
        self.sql("INSERT INTO stock_snapshots(id, source_kind, actor, row_count, content_sha256) VALUES (%s,'IMPORT','v','1','x')", (snap,))

        def stock(code, cas, brand, size, qty, expiry, price=SPRICE_S, note=SNOTE_S):
            self.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                     "VALUES (%s,'x',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (snap, code, cas, brand, size, price, qty, expiry, brand.casefold(), code.casefold(), size.casefold(), cas.casefold() or None, note))
        stock("GN-CATO-1L", "", "CATO", "1L", 5, TODAY + timedelta(days=200))
        stock("GN-CATO-1L", "", "CATO", "1L", 7, TODAY + timedelta(days=20))
        stock("GN-CATO-1L", "", "CATO", "1L", 100, TODAY - timedelta(days=3))     # hết hạn: không tính vào số lượng
        stock("GN-CATO-1L", "", "CATO", "1L", 0, None)
        stock("GN-ZETA-1L", "", "ZETA", "1L", 11, None)
        stock("OVR-1", "64-17-5", "ALPHA", "1L", 4, None)
        for i in range(25):                                                          # 25 lô cùng mã (kiểm cắt thầm lặng)
            stock("MANYLOT-1", "", "CATO", "1L", 1, TODAY + timedelta(days=100 + i))
        self.product("Many lots", "MANYLOT-1", "", "CATO")
        self.sql("UPDATE stock_state SET active_snapshot_id=%s, revision=revision+1", (snap,))
        # --- thẻ ------------------------------------------------------------
        self.add_token("cfull", self.team_full)
        self.add_token("climited", self.team_limited)
        self.add_internal("int")

    def raw_product(self, name, code, brand, price, ship, size="1L", cas="", note=""):
        self.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (name, code, cas, brand, size, price, ship, note, brand if brand is not None else ""))

    def add_internal(self, name, **extra):
        token = "stm_vi_" + name.replace("-", "_") + "_" + uuid.uuid4().hex
        self.tokens[name] = token
        ent = S.entry(token, "t-" + name, None, profile="internal", allowed_ips=[INTERNAL_NET], stock_detail="quantity")
        ent.pop("team_id")
        ent.update(extra)
        self.entries = [e for e in self.entries if e["id"] != "t-" + name]
        self.entries.append(ent)
        self.write_entries()
        return token


class Base(unittest.TestCase):
    env = None
    needs_role = False

    @classmethod
    def setUpClass(cls):
        cls.env = IEnv()
        cls.logs = S.LogCapture()
        cls._root = logging.getLogger()
        cls._old_level = cls._root.level
        cls._root.addHandler(cls.logs)
        cls._root.setLevel(logging.DEBUG)
        cls.bodies = []
        cls.base_entries = list(cls.env.entries)

    @classmethod
    def tearDownClass(cls):
        cls._root.removeHandler(cls.logs)
        cls._root.setLevel(cls._old_level)
        cls.env.close()

    def setUp(self):
        envvars = {"DATABASE_URL": self.env.dsn, "MACHINE_INTERNAL_DATABASE_URL": self.env.dsn,
                   "MACHINE_API_TOKENS_FILE": self.env.token_path}
        p = mock.patch.dict(os.environ, envvars)
        p.start()
        self.addCleanup(p.stop)
        t = mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0)
        t.start()
        self.addCleanup(t.stop)
        self.env.entries = list(self.base_entries)
        self.env.write_entries()
        machine_api.token_store.reset()
        machine_api.rate_limiter.reset()
        self.client = machine_api.app.test_client()

    def get(self, path, token="int", addr=INTERNAL_IP, headers=None, **query):
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = "Bearer " + self.env.tokens.get(token, token)
        resp = self.client.get(path, headers=h, query_string=query or None, environ_overrides={"REMOTE_ADDR": addr})
        self.bodies.append(resp.get_data(as_text=True))
        return resp

    def products(self, q, token="int", **extra):
        return self.get(V + "/products", token=token, q=q, **extra)

    def js(self, q, token="int", **extra):
        r = self.products(q, token=token, **extra)
        self.assertEqual(r.status_code, 200, (q, extra, r.get_data(as_text=True)[:300]))
        return r.get_json()

    def write_cfg(self, *entries):
        self.env.entries = list(entries)
        self.env.write_entries()
        machine_api.token_store.reset()


def staff_tier(q, row):
    """Oracle độc lập: mức xếp hạng 0..4 từ chính dữ liệu (không dùng SQL của cổng).
    LƯỢT 2: theo làm rõ oracle I7 (Change request log 2026-11-22) 'bắt đầu bằng' là MỘT mức chung cho mã và tên
    (trước đó oracle này tự tách mã-trước-tên thành hai mức 3 và 4 — kỳ vọng đó đã lỗi thời, không phải hạ chuẩn)."""
    u = q.strip().upper()
    code, cas, name = (row.get("code") or "").strip().upper(), (row.get("cas") or "").strip().upper(), (row.get("name") or "").strip().upper()
    if code == u:
        return 0
    if cas == u:
        return 1
    if name == u:
        return 2
    if code.startswith(u) or name.startswith(u):
        return 3
    return 4


# ======================================================================
# I1
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I1_CustomerUnchanged(Base):
    FORBID = {"price", "unit_price_vnd", "original_price", "ship_factor", "ship", "note", "stock_note", "stock_price_vnd", "lots",
              "rate_vnd", "currency", "price_status", "unit_price", "regulatory_price", "id", "product_id", "stock_item_id"}
    SENTINELS = (PRICE_S, "31415", NOTE_S, SNOTE_S, str(SPRICE_S))

    def scan(self, resp):
        text = resp.get_data(as_text=True)
        for s in self.SENTINELS:
            self.assertNotIn(s, text)
        if resp.status_code == 200:
            self.assertFalse(keys_deep(resp.get_json()) & self.FORBID)

    def test_customer_tokens_never_see_price_under_any_trick(self):
        calls = []
        tricks = [{}, {"profile": "internal"}, {"include_price": "1"}, {"fields": "price,ship,note"}, {"debug": "1"}, {"internal": "true"},
                  {"profile": "internal", "include_price": "1"}, {"as": "internal"}, {"expand": "price"}, {"limit": "50"}]
        headers = [{}, {"X-Profile": "internal"}, {"X-Machine-Profile": "internal"}, {"X-Include-Price": "1"},
                   {"X-Forwarded-For": INTERNAL_IP}, {"X-Real-IP": INTERNAL_IP}, {"Accept": "application/json; profile=internal"}]
        for token in ("cfull", "climited"):
            for q in ("glucose", "GN-CATO-1L", "50-99-7", "dextrose", "OVR-1"):
                for trick in tricks[:6]:
                    calls.append(self.products(q, token=token, **trick))
            for h in headers:
                calls.append(self.get(V + "/products", token=token, headers=h, q="glucose"))
            for trick in tricks[6:]:
                calls.append(self.products("glucose", token=token, **trick))
            calls.append(self.get(V + "/stock", token=token, code="GN-CATO-1L", profile="internal"))
            calls.append(self.get(V + "/stock", token=token, cas="50-99-7", profile="internal"))
            calls.append(self.get(V + "/compliance", token=token, cas="50-99-7", profile="internal"))
        self.assertGreaterEqual(len(calls), 60)
        for r in calls:
            self.scan(r)
        for r in calls:
            if r.status_code == 200 and r.get_json().get("results"):
                self.assertIn("price_notice", r.get_json())
        # giới hạn của thẻ khách vẫn là 10 dù xin 50
        self.assertLessEqual(self.js("glucose", token="cfull", limit="50")["count"], 10)

    def test_customer_entry_with_explicit_profile_customer_and_stray_fields(self):
        token = "stm_vi_x_" + uuid.uuid4().hex
        ent = S.entry(token, "t-cx", self.env.team_full, profile="customer", allowed_ips=[INTERNAL_NET])
        ent2 = S.entry("stm_vi_y_" + uuid.uuid4().hex, "t-cy", self.env.team_full, include_price=True, price=True, scope="internal")
        self.env.tokens["cx"], self.env.tokens["cy"] = token, ent2["id"]
        self.env.tokens["cy"] = "stm_vi_y_" + uuid.uuid4().hex
        ent2["sha256"] = machine_api.hash_token(self.env.tokens["cy"])
        self.write_cfg(ent, ent2)
        for tok in ("cx", "cy"):
            r = self.products("glucose", token=tok)
            self.assertEqual(r.status_code, 200)
            self.scan(r)

    def test_customer_never_uses_internal_dsn_and_internal_never_uses_customer_dsn(self):
        seen = []
        real = psycopg2.connect

        def spy(dsn, *a, **k):
            seen.append((dsn, k.get("options", "")))
            return real(dsn, *a, **k)
        cust_dsn = self.env.dsn + "?application_name=custdsn"
        int_dsn = self.env.dsn + "?application_name=intdsn"
        with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": cust_dsn, "MACHINE_INTERNAL_DATABASE_URL": int_dsn, "DATABASE_URL": self.env.dsn}):
            with mock.patch.object(psycopg2, "connect", spy):
                self.assertEqual(self.products("glucose", token="cfull").status_code, 200)
                self.assertEqual(self.products("glucose", token="int").status_code, 200)
                # chỉ có MACHINE_DATABASE_URL (không có DSN nội bộ): thẻ nội bộ KHÔNG được lùi về DSN khách
            with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": ""}):
                with mock.patch.object(psycopg2, "connect", spy):
                    r = self.products("glucose", token="int")
                    self.assertEqual(r.status_code, 503)
        cust = [d for d, o in seen if "internal" not in o]
        inter = [d for d, o in seen if "machine-api-internal" in o]
        self.assertEqual(set(cust), {cust_dsn})
        self.assertEqual(set(inter), {int_dsn})
        self.assertNotIn(cust_dsn, inter)

    def test_customer_db_role_cannot_read_price_even_with_internal_gateway_present(self):
        """Dùng ĐÚNG sql/machine_readonly_role.sql: vai khách vẫn bị từ chối đọc giá; cổng chạy thẻ khách dưới vai đó vẫn 200."""
        role = RoleHelper(self.env, "sql/machine_readonly_role.sql", "machine_api_ro")
        try:
            with role.connect() as conn, conn.cursor() as cur:
                for stmt in ("SELECT price FROM products", "SELECT ship FROM products", "SELECT note FROM products",
                             "SELECT stock_price_vnd FROM stock_items", "SELECT stock_note FROM stock_items",
                             "SELECT * FROM currency_rates", "SELECT * FROM brand_master"):
                    conn.rollback()
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege, msg=stmt):
                        cur.execute(stmt)
            with mock.patch.dict(os.environ, {"MACHINE_DATABASE_URL": role.dsn}):
                r = self.products("glucose", token="cfull")
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
                self.scan(r)
        finally:
            role.drop()


# ======================================================================
# I2
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I2_FailClosed(Base):
    def internal_entry(self, token, ident, **extra):
        ent = S.entry(token, ident, None, profile="internal", allowed_ips=[INTERNAL_NET])
        ent.pop("team_id")
        ent.update(extra)
        return ent

    def loaded(self, *entries):
        self.write_cfg(*entries)
        return {e["id"]: e for e in machine_api.load_token_entries(self.env.token_path)}

    def test_unsafe_internal_entries_are_ignored_with_warning_and_cannot_authenticate(self):
        cases = {}
        specs = {
            "i-noips": dict(allowed_ips=None), "i-emptyips": dict(allowed_ips=[]), "i-nonlist": dict(allowed_ips=INTERNAL_NET),
            "i-upper": dict(profile="Internal"), "i-upper2": dict(profile="INTERNAL"), "i-space": dict(profile="internal "),
            "i-lead": dict(profile=" internal"), "i-none": dict(profile=None), "i-list": dict(profile=["internal"]),
            "i-int": dict(profile=1), "i-bool": dict(profile=True), "i-other": dict(profile="admin"), "i-super": dict(profile="superuser"),
            "i-empty": dict(profile=""), "i-badip": dict(allowed_ips=["not-an-ip"]), "i-intip": dict(allowed_ips=[5]),
        }
        entries = []
        for ident, extra in specs.items():
            tok = "stm_vi_" + ident + "_" + uuid.uuid4().hex
            ent = self.internal_entry(tok, ident, **extra)
            if extra.get("allowed_ips", 1) is None:
                ent.pop("allowed_ips")
            cases[ident] = tok
            entries.append(ent)
        good = "stm_vi_good_" + uuid.uuid4().hex
        entries.append(self.internal_entry(good, "i-good"))
        self.write_cfg(*entries)
        start = len(self.logs.records)
        for ident, tok in cases.items():
            r = self.get(V + "/products", token=tok, q="glucose")
            self.assertEqual(r.status_code, 401, (ident, r.status_code))
            self.assertNotIn("results", r.get_data(as_text=True))
        self.assertEqual(self.get(V + "/products", token=good, q="glucose").status_code, 200)
        warns = "\n".join(t for _, lvl, t in self.logs.records[start:] if lvl == "WARNING")
        self.assertGreaterEqual(warns.count("ignored"), len(specs), warns[:500])

    def test_wide_open_allowed_ips_on_internal_token_is_accepted_residual_risk(self):
        """THĂM DÒ: allowed_ips=['0.0.0.0/0'] vẫn 'có allowed_ips' nên thẻ có giá hoạt động từ MỌI IP."""
        tok = "stm_vi_wide_" + uuid.uuid4().hex
        self.write_cfg(self.internal_entry(tok, "i-wide", allowed_ips=["0.0.0.0/0", "::/0"]))
        r = self.get(V + "/products", token=tok, addr="203.0.113.99", q="glucose")
        self.__class__.wide_open_status = r.status_code
        sys.stderr.write(f"\nOBSERVATION[I2]: thẻ nội bộ allowed_ips=0.0.0.0/0 từ IP lạ 203.0.113.99 -> HTTP {r.status_code}\n")
        tok2 = "stm_vi_long_" + uuid.uuid4().hex
        self.write_cfg(self.internal_entry(tok2, "i-long", expires_at="2099-12-31T00:00:00+00:00", rate_per_minute=600))
        r2 = self.get(V + "/products", token=tok2, q="glucose")
        sys.stderr.write(f"\nOBSERVATION[I2]: file thẻ nội bộ hạn 2099 (không giới hạn 90 ngày ở phía cổng) -> HTTP {r2.status_code}\n")

    def test_missing_or_empty_internal_dsn_is_503_not_200_and_no_leak(self):
        base_env = {k: v for k, v in os.environ.items() if k != "MACHINE_INTERNAL_DATABASE_URL"}
        base_env["MACHINE_DATABASE_URL"] = self.env.dsn   # DSN khách có sẵn: không được lùi sang nó
        for value in (None, "", "   "):
            env = dict(base_env)
            if value is not None:
                env["MACHINE_INTERNAL_DATABASE_URL"] = value
            with mock.patch.dict(os.environ, env, clear=True):
                for path, q in ((V + "/products", {"q": "glucose"}), (V + "/stock", {"code": "GN-CATO-1L"}), (V + "/compliance", {"cas": "50-99-7"})):
                    r = self.get(path, token="int", **q)
                    self.assertEqual(r.status_code, 503, (value, path, r.get_data(as_text=True)[:200]))
                    text = r.get_data(as_text=True)
                    for needle in ("results", PRICE_S, "postgres", "psycopg", "SELECT", self.env.dsn, "/"):
                        if needle == "/":
                            continue
                        self.assertNotIn(needle, text)
                # thẻ khách vẫn chạy
                self.assertEqual(self.products("glucose", token="cfull").status_code, 200)

    def test_wrong_internal_dsn_generic_503_and_logs_do_not_contain_password(self):
        parsed = urlparse(self.env.dsn)
        secret_pw = "SECRETPW" + uuid.uuid4().hex
        bad = [
            urlunparse(parsed._replace(netloc=f"nouser:{secret_pw}@{parsed.hostname}:{parsed.port}")),
            urlunparse(parsed._replace(netloc=f"{parsed.username}:{secret_pw}@{parsed.hostname}:{parsed.port}")),
            urlunparse(parsed._replace(netloc=f"x:{secret_pw}@127.0.0.1:1")),
            "postgresql://x:" + secret_pw + "@nonexistent.invalid:5432/db",
            "garbage dsn with password=" + secret_pw,
        ]
        start = len(self.logs.records)
        for dsn in bad:
            with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": dsn}):
                r = self.products("glucose", token="int")
                self.assertEqual(r.status_code, 503, dsn.split("@")[-1])
                text = r.get_data(as_text=True)
                self.assertEqual(r.get_json()["error"], "service_unavailable")
                for needle in (secret_pw, "nouser", "127.0.0.1", "invalid", "password", "psycopg", "Traceback", "FATAL", ".py"):
                    self.assertNotIn(needle, text)
        logtext = "\n".join(t for _, _, t in self.logs.records[start:])
        self.assertNotIn(secret_pw, logtext, "mật khẩu DSN lọt vào nhật ký")

    def test_auth_matrix_and_methods(self):
        now = datetime.now(timezone.utc)
        toks = {n: "stm_vi_m_" + n + "_" + uuid.uuid4().hex for n in ("expired", "revoked", "future", "ok")}
        self.write_cfg(
            self.internal_entry(toks["expired"], "i-expired", expires_at=(now - timedelta(seconds=1)).isoformat()),
            self.internal_entry(toks["revoked"], "i-revoked", enabled=False),
            self.internal_entry(toks["ok"], "i-ok"),
        )
        for name in ("expired", "revoked"):
            r = self.get(V + "/products", token=toks[name], q="glucose")
            self.assertEqual(r.status_code, 401, name)
        self.assertEqual(self.get(V + "/products", token=toks["ok"], addr="203.0.113.1", q="glucose").status_code, 403)
        self.assertEqual(self.get(V + "/products", token=toks["ok"], addr="198.51.101.7", q="glucose").status_code, 403)
        self.assertEqual(self.get(V + "/products", token=toks["ok"], addr="::ffff:198.51.100.7", q="glucose").status_code, 200)
        self.assertEqual(self.get(V + "/products", token=toks["ok"] + "x", q="glucose").status_code, 401)
        # giả IP bằng header (ProxyFix lấy phần tử cuối; Nginx ghi đè — ở đây chỉ kiểm không có cửa nào khác)
        for h in ({"X-Real-IP": INTERNAL_IP}, {"Forwarded": "for=198.51.100.7"}, {"X-Client-IP": INTERNAL_IP}):
            self.assertEqual(self.get(V + "/products", token=toks["ok"], addr="203.0.113.1", headers=h, q="glucose").status_code, 403, h)
        # chỉ GET
        for method in ("post", "put", "delete", "patch"):
            r = getattr(self.client, method)(V + "/products?q=glucose", headers={"Authorization": "Bearer " + toks["ok"]},
                                              environ_overrides={"REMOTE_ADDR": INTERNAL_IP}, data=b"{}")
            self.assertEqual(r.status_code, 405, method)
            self.assertNotIn("results", r.get_data(as_text=True))
        r = self.client.options(V + "/products", headers={"Authorization": "Bearer " + toks["ok"]}, environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
        self.assertNotIn("results", r.get_data(as_text=True))

    def test_duplicate_entries_are_dropped_both(self):
        tok = "stm_vi_dup_" + uuid.uuid4().hex
        a = self.internal_entry(tok, "i-dup-a")
        b = self.internal_entry(tok, "i-dup-b")                       # trùng băm
        c = self.internal_entry("stm_vi_zz_" + uuid.uuid4().hex, "i-dup-c")
        d = dict(c)
        d["sha256"] = machine_api.hash_token("stm_vi_other_" + uuid.uuid4().hex)   # trùng id
        d["profile"] = "customer"
        d["team_id"] = self.env.team_full
        loaded = self.loaded(a, b, c, d)
        self.assertEqual(loaded, {})
        self.assertEqual(self.get(V + "/products", token=tok, q="glucose").status_code, 401)
        # thẻ khách và thẻ nội bộ trùng băm cũng bị bỏ cả hai
        custom = S.entry(tok, "c-same", self.env.team_full)
        self.assertEqual(self.loaded(a, custom), {})

    def test_internal_cannot_be_downgraded_to_customer_dsn_when_entry_flips(self):
        """Thẻ đổi profile trong file (internal -> customer thiếu team) phải bị bỏ qua, không 'rơi' về quyền khác."""
        tok = "stm_vi_flip_" + uuid.uuid4().hex
        ent = self.internal_entry(tok, "i-flip")
        self.write_cfg(ent)
        self.assertEqual(self.get(V + "/products", token=tok, q="glucose").status_code, 200)
        ent2 = dict(ent)
        ent2["profile"] = "customer"
        ent2.pop("team_id", None)
        self.write_cfg(ent2)
        self.assertEqual(self.get(V + "/products", token=tok, q="glucose").status_code, 401)


# ======================================================================
# I3 + I8
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I3_I8_Visibility(Base):
    def test_internal_sees_every_brand_every_field(self):
        seen_brands = set()
        for b in BRANDS:
            body = self.js(f"GN-{b}-1L")
            self.assertEqual(body["count"], 1, b)
            item = body["results"][0]
            seen_brands.add(item["brand"])
            self.assertEqual(item["note"], NOTE_S)
            self.assertEqual(item["price"]["original_price"], PRICE_S)
            self.assertEqual(item["price"]["ship_factor"], "1.3")
            self.assertEqual(set(item["price"]) >= {"original_price", "ship_factor", "currency", "rate_vnd", "unit_price_vnd", "price_status"}, True)
            self.assertEqual(set(item) >= {"name", "code", "cas", "brand", "size", "note", "price", "compliance", "stock"}, True)
        self.assertEqual(seen_brands, set(BRANDS))        # kể cả GAMMA/DELTA/EPS/ZETA (không team nào có)
        self.assertEqual(self.js("GN-GAMMA-1L", token="cfull")["count"], 0)
        self.assertEqual(self.js("GN-DELTA-1L", token="climited")["count"], 0)

    def test_stock_exact_quantity_and_lots_no_technical_ids(self):
        body = self.js("GN-CATO-1L")
        item = body["results"][0]
        stock = item["stock"]
        self.assertEqual(stock["status"], "IN_STOCK")
        self.assertEqual(stock["quantity"], 12)             # 5+7 (lô hết hạn 100 và lô 0 không tính)
        lots = stock["lots"]
        self.assertEqual(sorted(l["quantity"] for l in lots), [0, 5, 7, 100])
        for lot in lots:
            self.assertEqual(lot["stock_price_vnd"], SPRICE_S)
            self.assertEqual(lot["stock_note"], SNOTE_S)
        expired = [l for l in lots if l["quantity"] == 100][0]
        self.assertEqual((expired["expiry_state"], expired["available"]), ("expired", False))
        self.assertFalse(keys_deep(body) & {"id", "product_id", "stock_item_id", "sha256", "token", "password"})
        oracle = self.env.sql("SELECT coalesce(sum(i.quantity),0) FROM stock_state s JOIN stock_items i ON i.snapshot_id=s.active_snapshot_id "
                              "WHERE i.code_norm='gn-cato-1l' AND (i.expiry_date IS NULL OR i.expiry_date >= current_date) AND i.quantity>0")[0][0]
        self.assertEqual(stock["quantity"], oracle)

    def test_out_of_stock_has_empty_lots(self):
        item = self.js("GN-DELTA-1L")["results"][0]
        self.assertEqual((item["stock"]["status"], item["stock"]["lots"]), ("OUT_OF_STOCK", []))
        self.assertEqual(item["stock"]["quantity"], 0)

    def test_lots_list_not_silently_truncated(self):
        """25 lô cùng mã: 'từng lô' phải đủ 25 (hoặc có cờ cắt)."""
        item = self.js("MANYLOT-1")["results"][0]
        stock = item["stock"]
        self.assertEqual(stock["quantity"], 25)
        lots = stock["lots"]
        flagged = any(k for k in stock if "more" in k or "trunc" in k) and "lots_more" in stock
        sys.stderr.write(f"\nOBSERVATION[I3]: 25 lô -> {len(lots)} lô trong 'lots'; khóa stock = {sorted(stock)}\n")
        self.__class__.lots_returned_for_25 = (len(lots), flagged)
        self.assertTrue(len(lots) == 25 or flagged, f"lots bị cắt còn {len(lots)}/25 mà không có cờ")

    def test_stock_endpoint_and_compliance_endpoint_internal(self):
        by_code = self.get(V + "/stock", code="GN-CATO-1L").get_json()
        self.assertEqual(by_code["quantity"], 12)
        self.assertEqual(len(by_code["items"][0]["lots"]), 4)
        self.assertNotIn("price_notice", by_code)
        comp = self.get(V + "/compliance", cas="50-99-7").get_json()
        self.assertEqual(comp["compliance"]["regulatory_note"], "ghi-chu-phap-che-glu")
        self.__class__.compliance_price_notice = comp.get("price_notice")
        sys.stderr.write(f"\nOBSERVATION[I3]: /compliance của thẻ nội bộ trả price_notice = {comp.get('price_notice')!r}\n")

    def test_compliance_semantics_unchanged_I8(self):
        # NO_RULE tường minh
        item = self.js("NOR-1")["results"][0]
        self.assertEqual(item["compliance"]["state"], "NO_RULE")
        self.assertIn("KHÔNG có nghĩa là được phép", item["compliance"]["message"])
        self.assertNotIn("blocked", item["compliance"])
        # ngoại lệ thủ công: ALPHA bật ưu tiên thủ công => 'Được bán' ; CATO không bật => bị bỏ qua, vẫn Cấm nhập
        ovr1 = self.js("OVR-1")["results"][0]["compliance"]
        ovr2 = self.js("OVR-2")["results"][0]["compliance"]
        self.assertEqual(ovr1["label"], "Được bán")
        self.assertEqual(ovr2["state"], "RULE")
        self.assertNotEqual(ovr2["label"], "Được bán")
        self.assertTrue(ovr2["blocked"])
        self.assertNotIn("phai-bo-qua", json.dumps(ovr2))
        for item in (self.js("OVR-1")["results"][0], self.js("OVR-2")["results"][0]):
            self.assertIn("regulatory_note", item["compliance"])

    def test_pack_confirmed_and_same_cas_for_customer_like_before(self):
        item = self.js("OVR-1", token="cfull")["results"][0]
        self.assertEqual(item["stock"]["status"], "IN_STOCK")
        self.assertTrue(item["stock"]["pack_confirmed"])
        self.assertNotIn("quantity", item["stock"])
        self.assertNotIn("lots", item["stock"])
        # thẻ nội bộ cùng sản phẩm: có thêm số lượng chính xác
        inner = self.js("OVR-1")["results"][0]["stock"]
        self.assertEqual(inner["quantity"], 4)
        self.assertTrue(inner["pack_confirmed"])

    def test_team_restriction_does_not_affect_internal(self):
        lim = {r["brand"] for r in self.js("glucose", token="climited")["results"]}
        self.assertEqual(lim, {"CATO"})
        inner = {r["brand"] for r in self.js("glucose", limit="50")["results"]}
        self.assertGreaterEqual(len(inner), 5)
        # team lưu trữ / thay đổi không ảnh hưởng thẻ nội bộ
        saved = self.env.sql("SELECT team_id, brand FROM team_brands")
        self.env.sql("DELETE FROM team_brands")
        try:
            self.assertEqual(self.js("glucose", limit="50")["count"], 50)
            self.assertEqual(self.js("glucose", token="cfull")["count"], 0)
        finally:
            for t, b in saved:
                self.env.sql("INSERT INTO team_brands(team_id, brand) VALUES (%s,%s)", (t, b))


# ======================================================================
# I4
# ======================================================================
PRICE_CASES = ["12.5", "100", "0", "", "-5", "-0.0", "abc", "12,5", "1,234.5", "1e3", None, "  7 ", " ", "nan", "NaN", "inf", "-inf",
               "Infinity", "1e308", "1e-9", "9" * 400, "1_000", "٣٤", "0x10", "1e5000", "٣٤.٥", "1.0000000000000001",
               "123456789012345678901234567890", "+5", "5.", ".5", "1e2147483648", "  8", "12.5kg", "$12", "0.0001", "99999999.99"]
SHIP_CASES = ["1.3", "1", "0", "", "x", None, "1.25", "-1", "nan", "inf", "1,3", " 2 ", "1e308", "0.0", "3"]


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I4_PriceParity(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cases = []
        brand_cycle = list(BRANDS) + ["HANG-LA", None] + ([" SPACED "] if cls.env.spaced_brand_ok else [])
        n = 0
        for price in PRICE_CASES:
            ship = SHIP_CASES[n % len(SHIP_CASES)] if price not in ("12.5", "100") else "1.3"
            brand = brand_cycle[n % len(brand_cycle)]
            if price in ("12.5", "100", "1e308", "nan", "inf", "-5"):
                brand = "ALPHA"
            code = f"PRC-{n:03d}"
            cls.env.raw_product(f"Parity probe {n:03d}", code, brand, price, ship)
            cls.cases.append((code, price, ship, brand))
            n += 1
        for ship in SHIP_CASES:
            code = f"PRS-{n:03d}"
            cls.env.raw_product(f"Parity probe {n:03d}", code, "ALPHA", "10", ship)
            cls.cases.append((code, "10", ship, "ALPHA"))
            n += 1

    def test_unit_price_equals_staff_function_over_matrix(self):
        import search  # noqa: PLC0415
        conn = psycopg2.connect(self.env.dsn)
        failures = []
        checked = 0
        try:
            resolver = load_currency_rate_resolver(conn, str(ROOT))
            for code, price, ship, brand in self.cases:
                r = self.products(code)
                db_price, db_ship, db_brand = self.env.sql("SELECT price, ship, brand FROM products WHERE code=%s", (code,))[0]
                value, _disp, valid, status = search._compute_unit_price_details(db_price, db_ship, db_brand, resolver)
                if r.status_code != 200:
                    failures.append((code, price, ship, brand, "HTTP", r.status_code))
                    continue
                block = r.get_json()["results"][0]["price"]
                checked += 1
                if not valid:
                    if block["unit_price_vnd"] is not None or block["price_status"] != str(status):
                        failures.append((code, price, ship, brand, "invalid-rate", block))
                elif isinstance(value, float) and not math.isfinite(value):
                    failures.append((code, price, ship, brand, "non-finite staff value", block))   # không crash là đủ; ghi lại
                elif block["unit_price_vnd"] != int(value):
                    failures.append((code, price, ship, brand, "mismatch", block, value))
        finally:
            conn.close()
        self.assertGreaterEqual(len(self.cases), 50)
        crashed = [f for f in failures if f[4] == "HTTP"]
        mism = [f for f in failures if f[4] in ("mismatch", "invalid-rate")]
        sys.stderr.write(f"\nOBSERVATION[I4]: {len(self.cases)} ca; HTTP lỗi={len(crashed)}; sai lệch={len(mism)}; "
                         f"giá trị staff không hữu hạn={len([f for f in failures if f[4].startswith('non-finite')])}\n")
        for f in crashed[:12]:
            sys.stderr.write(f"   HTTP {f[5]} price={f[1]!r} ship={f[2]!r} brand={f[3]!r}\n")
        self.assertEqual(mism, [], mism[:5])
        self.assertEqual(crashed, [], "cổng trả lỗi cho dữ liệu giá lạ (một sản phẩm bẩn làm hỏng cả truy vấn)")

    def test_one_dirty_product_does_not_poison_the_whole_query(self):
        """Truy vấn khớp cả sản phẩm giá 'nan' lẫn sản phẩm sạch: phải vẫn 200 và vẫn trả sản phẩm sạch."""
        self.env.raw_product("Poison clean", "POI-1", "ALPHA", "10", "1")
        self.env.raw_product("Poison dirty", "POI-2", "ALPHA", "nan", "1")
        r = self.products("Poison")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertEqual({x["code"] for x in r.get_json()["results"]}, {"POI-1", "POI-2"})

    def test_rate_change_between_calls_and_brand_spelling(self):
        code = "PRC-000"
        self.env.raw_product("Spell c", "SPL-3", "ALPHA", "10", "2")
        b1 = {c: self.js(c)["results"][0]["price"] for c in ("SPL-3",)}
        self.assertEqual(b1["SPL-3"]["unit_price_vnd"], 500000)
        self.env.sql("UPDATE currency_rates SET rate_vnd=26000 WHERE currency_code='USD'")
        try:
            self.assertEqual(self.js("SPL-3")["results"][0]["price"]["unit_price_vnd"], 520000)
        finally:
            self.env.sql("UPDATE currency_rates SET rate_vnd=25000 WHERE currency_code='USD'")
        self.assertEqual(self.js("SPL-3")["results"][0]["price"]["unit_price_vnd"], 500000)

    def test_rate_status_failures_are_null_never_zero_or_one(self):
        # ZETA: có hãng nhưng tiền tệ GBP thiếu tỷ giá; HANG-LA: hãng không có trong brand_master
        self.env.raw_product("Z item", "ZZ-1", "ZETA", "10", "2")
        self.env.raw_product("H item", "HH-1", "HANG-LA", "10", "2")
        for code in ("ZZ-1", "HH-1"):
            block = self.js(code)["results"][0]["price"]
            self.assertIsNone(block["unit_price_vnd"], block)
            self.assertNotIn(block["price_status"], ("OK", "NO_PRICE_DATA"))
        # đổi tiền tệ sang VND chưa gán tỷ giá cho tiền khác: bảng tỷ giá trống hoàn toàn
        self.env.sql("CREATE TEMP TABLE IF NOT EXISTS _x(a int)")
        saved = self.env.sql("SELECT currency_code, rate_vnd FROM currency_rates")
        self.env.sql("DELETE FROM currency_rates")
        try:
            for code in ("GN-CATO-1L", "GN-GAMMA-1L"):
                block = self.js(code)["results"][0]["price"]
                self.assertIsNone(block["unit_price_vnd"], block)
        finally:
            for c, r in saved:
                self.env.sql("INSERT INTO currency_rates(currency_code, rate_vnd) VALUES (%s,%s)", (c, r))

    def test_partial_schema_fails_closed_status_matches_staff(self):
        import search  # noqa: PLC0415
        self.env.sql("ALTER TABLE currency_rates RENAME TO currency_rates_hidden")
        try:
            conn = psycopg2.connect(self.env.dsn)
            try:
                resolver = load_currency_rate_resolver(conn, str(ROOT))
                v, _d, valid, status = search._compute_unit_price_details("10", "2", "ALPHA", resolver)
            finally:
                conn.close()
            self.assertFalse(valid)
            block = self.js("GN-ALPHA-1L")["results"][0]["price"]
            self.assertIsNone(block["unit_price_vnd"])
            self.assertEqual(block["price_status"], str(status))
        finally:
            self.env.sql("ALTER TABLE currency_rates_hidden RENAME TO currency_rates")


# ======================================================================
# I5
# ======================================================================
class RoleHelper:
    def __init__(self, env, sql_path, role_name):
        self.env = env
        self.role = role_name + "_v" + os.urandom(3).hex()
        self.password = "pw" + os.urandom(6).hex()
        sql = (ROOT / sql_path).read_text(encoding="utf-8")
        sql = sql.replace("{{DB_NAME}}", env.db_name).replace("{{PASSWORD}}", self.password).replace(role_name, self.role)
        with env.conn.cursor() as cur:
            cur.execute(sql)
        parsed = urlparse(env.dsn)
        self.dsn = urlunparse(parsed._replace(netloc=f"{self.role}:{self.password}@{parsed.hostname}:{parsed.port or 5432}"))

    def connect(self):
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = True
        return conn

    def drop(self):
        try:
            with self.env.conn.cursor() as cur:
                cur.execute(f'DROP OWNED BY "{self.role}"')
        except Exception:  # noqa: BLE001
            pass
        admin = psycopg2.connect(pg_temp_db.maintenance_dsn())
        admin.autocommit = True
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP ROLE IF EXISTS "{self.role}"')
        finally:
            admin.close()


GRANTED_TABLES = {"products", "stock_items", "stock_state", "regulatory_rules", "regulatory_statuses", "brand_compliance_settings",
                  "brand_master", "currency_rates"}


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I5_InternalRole(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role = RoleHelper(cls.env, "sql/machine_internal_role.sql", "machine_api_internal_ro")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.role.drop()
        finally:
            super().tearDownClass()

    def run_sql(self, stmt):
        conn = self.role.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
                return cur.fetchall() if cur.description else None
        finally:
            conn.close()

    def test_privilege_inventory_exact(self):
        owner = self.env.conn
        with owner.cursor() as cur:
            cur.execute("""SELECT c.relname, a.attname, has_column_privilege(%s, c.oid, a.attnum, 'SELECT'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'INSERT'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'UPDATE'),
                                  has_column_privilege(%s, c.oid, a.attnum, 'REFERENCES')
                           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                           WHERE n.nspname='public' AND c.relkind IN ('r','v','m','p','f')""", (self.role.role,) * 4)
            readable = {}
            for table, col, sel, ins, upd, ref in cur.fetchall():
                self.assertFalse(ins or upd or ref, f"quyền ghi/tham chiếu: {table}.{col}")
                if sel:
                    readable.setdefault(table, set()).add(col)
            self.assertEqual(set(readable), GRANTED_TABLES, sorted(set(readable) ^ GRANTED_TABLES))
            for table in ("app_users", "teams", "team_brands", "login_audit_events", "quote_templates", "exchange_rates", "brand_aliases",
                          "admin_menu_grants", "office_ip_allowlist", "currency_rate_history", "brand_currency_history",
                          "stock_snapshots", "stock_import_jobs", "regulatory_import_rows"):
                self.assertNotIn(table, readable)
            # cột không có lý do cần cho báo giá
            self.assertFalse(readable["products"] & {"preparation_type", "source_brand"}, readable["products"])
            self.assertFalse(readable["stock_items"] & {"name", "cas", "id", "created_at"}, readable["stock_items"])
            self.assertFalse(readable["brand_master"] & {"id", "normalized_name", "created_at", "updated_at"}, readable["brand_master"])
            self.assertFalse(readable["currency_rates"] & {"updated_by", "update_source", "updated_at"}, readable["currency_rates"])
            sys.stderr.write("\nOBSERVATION[I5]: cột đọc được: " + json.dumps({k: sorted(v) for k, v in readable.items()}, ensure_ascii=False) + "\n")
            cur.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE n.nspname='public' AND c.relkind IN ('r','v','m','p','S')
                             AND (has_table_privilege(%s,c.oid,'INSERT') OR has_table_privilege(%s,c.oid,'UPDATE') OR has_table_privilege(%s,c.oid,'DELETE')
                                  OR has_table_privilege(%s,c.oid,'TRUNCATE') OR has_table_privilege(%s,c.oid,'TRIGGER') OR has_table_privilege(%s,c.oid,'REFERENCES')
                                  OR (c.relkind='S' AND (has_sequence_privilege(%s,c.oid,'USAGE') OR has_sequence_privilege(%s,c.oid,'UPDATE'))))""",
                        (self.role.role,) * 8)
            self.assertEqual(cur.fetchall(), [])
            cur.execute("SELECT has_schema_privilege(%s,'public','CREATE'), has_database_privilege(%s,current_database(),'CREATE'), "
                        "has_database_privilege(%s,current_database(),'TEMP')", (self.role.role,) * 3)
            create_schema, create_db, temp = cur.fetchone()
            self.assertFalse(create_schema or create_db)
            sys.stderr.write(f"\nOBSERVATION[I5]: quyền TEMP trên database = {temp}\n")
            cur.execute("""SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                           WHERE n.nspname='public' AND p.prosecdef AND has_function_privilege(%s,p.oid,'EXECUTE')""", (self.role.role,))
            self.assertEqual(cur.fetchall(), [])
            cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls, rolconnlimit FROM pg_roles WHERE rolname=%s", (self.role.role,))
            self.assertEqual(cur.fetchone(), (False, False, False, False, False, 10))
            cur.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s", (self.role.role,))
            self.assertEqual(cur.fetchone()[0], 0, "vai là thành viên của vai khác")
            cur.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND relrowsecurity")
            self.assertEqual(cur.fetchall(), [])

    def test_forbidden_reads_and_all_writes_denied(self):
        denied = ["SELECT * FROM app_users", "SELECT * FROM teams", "SELECT * FROM team_brands", "SELECT * FROM login_audit_events",
                  "SELECT password_hash FROM app_users", "SELECT * FROM quote_templates", "SELECT * FROM exchange_rates",
                  "SELECT * FROM products", "SELECT p FROM products p", "SELECT to_jsonb(p) FROM products p", "SELECT preparation_type FROM products",
                  "SELECT * FROM stock_items", "SELECT name FROM stock_items", "SELECT cas FROM stock_items",
                  "SELECT updated_by FROM currency_rates", "SELECT * FROM currency_rates", "SELECT * FROM brand_master", "SELECT id FROM brand_master",
                  "SELECT * FROM pg_authid", "SELECT pg_read_file('/etc/passwd')", "SELECT * FROM pg_ls_dir('.')",
                  "COPY (SELECT * FROM app_users) TO STDOUT", "COPY products TO '/tmp/x'",
                  "SELECT (SELECT username FROM app_users LIMIT 1)", "WITH x AS (SELECT * FROM teams) SELECT * FROM x",
                  "SELECT count(*) FROM login_audit_events", "SELECT * FROM brand_aliases", "SELECT * FROM currency_rate_history"]
        for stmt in denied:
            with self.assertRaises(psycopg2.Error, msg=stmt) as ctx:
                self.run_sql(stmt)
            self.assertIsInstance(ctx.exception, psycopg2.errors.InsufficientPrivilege, (stmt, ctx.exception))
        writes = ["INSERT INTO products(name) VALUES ('x')", "UPDATE products SET price='1'", "DELETE FROM products", "TRUNCATE products",
                  "UPDATE currency_rates SET rate_vnd=1", "INSERT INTO currency_rates(currency_code, rate_vnd) VALUES ('ZZZ',1)",
                  "UPDATE stock_state SET revision=0", "DELETE FROM regulatory_rules", "CREATE TABLE zz(a int)", "CREATE TEMP TABLE zz(a int)",
                  "DROP TABLE products", "ALTER TABLE products ADD COLUMN zz int", "CREATE FUNCTION zz() RETURNS int AS 'select 1' LANGUAGE sql",
                  "GRANT SELECT ON app_users TO PUBLIC", "ALTER ROLE machine_api_internal_ro_x SUPERUSER", "CREATE ROLE zzz", "DROP DATABASE postgres",
                  "COMMENT ON TABLE products IS 'x'", "LOCK TABLE products IN ACCESS EXCLUSIVE MODE",
                  "SELECT lo_import('/etc/passwd')", "SELECT set_config('default_transaction_read_only','off',false)"]
        for stmt in writes:
            conn = self.role.connect()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(stmt)
                        if stmt.startswith("SELECT set_config"):
                            continue   # chỉ đổi cờ phiên; bước tiếp theo phải vẫn bị chặn
                        self.fail(f"vai chạy được: {stmt}")
                    except psycopg2.Error:
                        pass
            finally:
                conn.close()
        # kể cả sau khi tắt cờ read-only của phiên, vẫn không ghi được (không có quyền)
        conn = self.role.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SET default_transaction_read_only = off")
                cur.execute("SET transaction_read_only = off")
                for stmt in ("UPDATE products SET price='1'", "INSERT INTO products(name) VALUES ('x')", "CREATE TABLE zz(a int)"):
                    with self.assertRaises(psycopg2.Error, msg=stmt):
                        cur.execute(stmt)
        finally:
            conn.close()
        self.assertEqual(self.env.sql("SELECT count(*) FROM products WHERE price='1'")[0][0], 0)

    def test_session_settings_of_role_and_gateway_connection_are_readonly(self):
        rows = self.run_sql("SHOW default_transaction_read_only")
        self.assertEqual(rows[0][0], "on")
        self.assertEqual(self.run_sql("SHOW statement_timeout")[0][0], "3s")
        with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": self.role.dsn}):
            captured = []
            real = psycopg2.connect

            def spy(dsn, *a, **k):
                captured.append(k.get("options", ""))
                return real(dsn, *a, **k)
            with mock.patch.object(psycopg2, "connect", spy):
                self.assertEqual(self.products("glucose").status_code, 200)
            self.assertTrue(captured and all("default_transaction_read_only=on" in o and "statement_timeout=3000" in o for o in captured), captured)

    def test_gateway_under_role_equals_owner_across_matrix(self):
        queries = ["glucose", "GN-CATO-1L", "50-99-7", "OVR-1", "PRC-000", "MANYLOT-1", "Pure 100%", "dup-1", "nor-1", "Bang!excl"]
        out = {}
        for tag, dsn in (("owner", self.env.dsn), ("role", self.role.dsn)):
            with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": dsn}):
                machine_api.rate_limiter.reset()
                out[tag] = {q: self.products(q, limit="50").get_data(as_text=True) for q in queries}
                out[tag]["stock"] = self.get(V + "/stock", cas="50-99-7").get_data(as_text=True)
                out[tag]["stock2"] = self.get(V + "/stock", code="GN-CATO-1L").get_data(as_text=True)
                out[tag]["comp"] = self.get(V + "/compliance", cas="50-99-7").get_data(as_text=True)
        self.assertEqual(out["owner"], out["role"])
        for q in queries:
            self.assertNotIn('"error"', out["role"][q][:30], q)

    def test_statement_timeout_applies(self):
        conn = psycopg2.connect(self.role.dsn)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                with self.assertRaises(psycopg2.errors.QueryCanceled):
                    cur.execute("SELECT pg_sleep(5)")
        finally:
            conn.close()


# ======================================================================
# SQL injection / hostile input (cũng thuộc I5)
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I5_Injection(Base):
    PAYLOADS = ["'; DROP TABLE products; --", "' OR '1'='1", "glucose' UNION SELECT price,price,price,price,price FROM products--",
                "%' ; DELETE FROM stock_items; --", "\\' OR 1=1 --", "glu%", "!%", "!!", "a!b", "'||(SELECT price FROM products LIMIT 1)||'",
                "$$;DROP TABLE teams;$$", "\"; select pg_sleep(10); --", "glucose\u0000x", "glucose" * 20, "{0}{1}", "%s%s%s", "%(x)s", "glu\ncose",
                "ß", "İstanbul", "Ǆ", "‮gnirts", "'" * 50]

    def test_q_payloads_are_data_not_sql(self):
        before = self.env.sql("SELECT (SELECT count(*) FROM products), (SELECT count(*) FROM stock_items), (SELECT count(*) FROM teams), "
                              "(SELECT count(*) FROM currency_rates)")[0]
        t0 = time.monotonic()
        for payload in self.PAYLOADS:
            for token in ("int", "cfull"):
                r = self.products(payload, token=token)
                self.assertIn(r.status_code, (200, 400), (payload, token, r.status_code, r.get_data(as_text=True)[:200]))
                text = r.get_data(as_text=True)
                for needle in ("psycopg", "syntax error", "Traceback", "relation \"", "permission denied"):
                    self.assertNotIn(needle, text)
                if token == "cfull":
                    self.assertNotIn(PRICE_S, text)
        self.assertLess(time.monotonic() - t0, 20, "pg_sleep chạy được?")
        after = self.env.sql("SELECT (SELECT count(*) FROM products), (SELECT count(*) FROM stock_items), (SELECT count(*) FROM teams), "
                             "(SELECT count(*) FROM currency_rates)")[0]
        self.assertEqual(before, after)

    def test_literal_wildcards_match_literally_for_internal(self):
        self.assertEqual({r["code"] for r in self.js("100%")["results"]}, {"SP-001"})            # không khớp 'Pure 1000'
        self.assertEqual({r["code"] for r in self.js("Under_score")["results"]}, {"SP-003"})      # không khớp 'UnderXscore'
        self.assertEqual({r["code"] for r in self.js("Back\\slash")["results"]}, {"SP-005"})
        self.assertEqual({r["code"] for r in self.js("Bang!excl")["results"]}, {"SP-006"})
        self.assertEqual({r["code"] for r in self.js("Quote'apos")["results"]}, {"SP-007"})
        for wild in ("%%%", "___", "!!!", "%_%"):
            self.assertEqual(self.js(wild)["count"], 0, wild)

    def test_limit_cas_code_parameters_hostile(self):
        for bad in ("0", "-1", "abc", "1.5", "1e1", " 5", "5 ", "٥", "+5", "0x5", "1;DROP TABLE products", "5 OR 1=1", "٥", "99999999999999999999999"[:0] + "x", "--"):
            r = self.products("glucose", limit=bad)
            if bad in ("99999999999999999999999"[:0] + "x",):
                self.assertEqual(r.status_code, 400, bad)
                continue
            self.assertIn(r.status_code, (200, 400), (bad, r.status_code))
            if bad in ("0", "-1", "abc", "1.5", "1e1", "٥", "+5", "0x5", "1;DROP TABLE products", "5 OR 1=1", "٥", "--"):
                self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.products("glucose", limit="00005").get_json()["count"], 5)
        self.assertEqual(self.products("glucose", limit="9" * 30).status_code, 200)
        r = self.client.get(V + "/products?q=glucose&limit=5&limit=6", headers={"Authorization": "Bearer " + self.env.tokens["int"]},
                            environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
        self.assertEqual(r.status_code, 400)
        for bad in ("1;DROP TABLE products", "' OR 1=1--", "64-17-5'", "64-17-5\n", "64-17-5 ", "٦٤-١٧-٥", "00-00-0" + "0" * 50):
            r = self.get(V + "/compliance", cas=bad)
            self.assertIn(r.status_code, (200, 400), (bad, r.status_code))
            r = self.get(V + "/stock", cas=bad)
            self.assertIn(r.status_code, (200, 400), (bad, r.status_code))
        for bad in ("x' OR '1'='1", "GN-CATO-1L'; DROP TABLE stock_items;--", "%", "_", "a" * 101, "\x00"):
            r = self.get(V + "/stock", code=bad)
            self.assertIn(r.status_code, (200, 400), (bad, r.status_code))
        self.assertEqual(self.env.sql("SELECT count(*) FROM stock_items")[0][0] > 0, True)


# ======================================================================
# I6
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I6_NoLeaks(Base):
    def test_access_lines_have_no_secrets_and_profile_label(self):
        start = len(self.logs.records)
        token = self.env.tokens["int"]
        self.js("GN-CATO-1L")
        self.js("glucose", limit="50")
        self.get(V + "/stock", code="GN-CATO-1L")
        self.get(V + "/compliance", cas="50-99-7")
        self.products("zzz-no-match-keyword-xyz")
        self.products("zz")                                        # 400
        self.get(V + "/products", token="nope", q="glucose")        # 401
        self.get(V + "/products", addr="203.0.113.5", q="glucose")  # 403
        lines = self.logs.access_lines()[-8:]
        self.assertEqual(len(lines), 8)
        text = "\n".join(t for _, _, t in self.logs.records[start:])
        for needle in (PRICE_S, "31415", "GN-CATO-1L", "glucose", "50-99-7", "zzz-no-match", token, machine_api.hash_token(token), NOTE_S, str(SPRICE_S), "unit_price"):
            self.assertNotIn(needle, text, needle)
        entries = [json.loads(l) for l in lines]
        keysets = {frozenset(e) for e in entries}
        sys.stderr.write(f"\nOBSERVATION[I6]: khóa của dòng truy cập = {sorted(keysets.pop())}\n")
        for e in entries:
            self.assertTrue(set(e) <= {"ts", "token", "endpoint", "status", "results", "ip", "ms", "profile"})
        self.__class__.access_has_profile = all("profile" in e for e in entries if e["token"] != "anonymous")

    def test_error_bodies_and_logs_no_dsn_sql_paths_prices(self):
        start = len(self.logs.records)
        bodies = []
        bodies.append(self.products("zz"))
        bodies.append(self.products("glucose", limit="x"))
        bodies.append(self.get(V + "/stock"))
        bodies.append(self.get(V + "/stock", code="a", cas="50-99-7"))
        bodies.append(self.get(V + "/compliance", cas="zzz"))
        bodies.append(self.get(V + "/nope"))
        bodies.append(self.get(V + "/products", token="bad", q="glucose"))
        with mock.patch.dict(os.environ, {"MACHINE_INTERNAL_DATABASE_URL": "postgresql://u:pw@127.0.0.1:1/db"}):
            bodies.append(self.products("glucose"))
        with mock.patch.dict(os.environ, {"MACHINE_API_TOKENS_FILE": "/nonexistent/dir/tokens.json"}):
            machine_api.token_store.reset()
            bodies.append(self.products("glucose"))
        machine_api.token_store.reset()
        # lỗi 500 cố tình. LƯỢT 2: giá 'inf' không còn gây 500 (hợp đồng: trả null + INVALID_PRICE_DATA, 200), nên ép lỗi
        # bất ngờ bằng cách làm hàm tính giá ném ngoại lệ; thân phản hồi 500 vẫn phải là JSON chung, không lộ gì.
        self.env.raw_product("Err dirty", "ERR-1", "ALPHA", "10", "1")
        with mock.patch.object(machine_api, "_price_block", side_effect=RuntimeError("boom")):
            r500 = self.products("ERR-1")
        self.assertEqual(r500.status_code, 500)
        bodies.append(r500)
        # lỗi DB giữa chừng: chặn bảng products tạm thời bằng khóa
        for r in bodies:
            text = r.get_data(as_text=True)
            for needle in (self.env.dsn, "searchlocal", "postgres", "SELECT", "FROM ", ".py", "Traceback", "/Volumes", "/srv", "/etc", PRICE_S, "inf", "nan", NOTE_S,
                           "psycopg", "File \"", str(ROOT)):
                if needle in ("inf", "nan") and needle not in text:
                    continue
                self.assertNotIn(needle, text, (r.status_code, needle))
        logtext = "\n".join(t for _, _, t in self.logs.records[start:])
        for needle in (PRICE_S, NOTE_S, SNOTE_S, "searchlocal"):
            self.assertNotIn(needle, logtext, needle)

    def test_no_secrets_in_openapi_and_health(self):
        for path in ("/openapi.json", "/health"):
            r = self.client.get(V + path)
            self.assertEqual(r.status_code, 200)
            text = r.get_data(as_text=True)
            for needle in (PRICE_S, "searchlocal", self.env.tokens["int"]):
                self.assertNotIn(needle, text)
        doc = self.client.get(V + "/openapi.json").get_json()
        desc = doc["info"]["description"]
        sys.stderr.write(f"\nOBSERVATION[I6]: openapi mô tả: 'API KHÔNG trả giá' có mặt = {'KHÔNG trả giá' in desc}; limit max = "
                         f"{doc['paths']['/products']['get']['parameters'][1]['schema']['maximum']}\n")


# ======================================================================
# I7
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I7_Ranking(Base):
    def full_rows(self, q):
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = self.env.sql("SELECT name, code, cas, brand, size FROM products WHERE name ILIKE %s ESCAPE '!' OR code ILIKE %s ESCAPE '!' OR cas ILIKE %s ESCAPE '!'",
                            (like, like, like))
        return [dict(zip(("name", "code", "cas", "brand", "size"), r)) for r in rows]

    def check_ranking(self, q, limit, token="int", **kw):
        body = self.js(q, token=token, limit=str(limit))
        results = body["results"]
        full = self.full_rows(q)
        if token != "int":
            return body
        # (a) mức không giảm
        tiers = [staff_tier(q, r) for r in results]
        self.assertEqual(tiers, sorted(tiers), (q, tiers))
        # mức đầy đủ trước mức thấp hơn không bị bỏ sót: số kết quả mỗi mức trong top = min(...)
        all_tiers = sorted(staff_tier(q, r) for r in full)
        self.assertEqual(tiers, all_tiers[:len(results)], (q, "tập top không phải các mức tốt nhất"))
        # (b) xen đều hãng trong từng mức
        for t in set(tiers):
            seg = [r for r, tt in zip(results, tiers) if tt == t]
            brands_in_tier = {r["brand"].strip().upper() for r in full if staff_tier(q, r) == t}
            distinct_first = len({r["brand"].strip().upper() for r in seg[:min(len(seg), len(brands_in_tier))]})
            self.assertEqual(distinct_first, min(len(seg), len(brands_in_tier)), (q, t, [r["brand"] for r in seg]))
        # (d) more đúng
        self.assertEqual(body["more"], len(full) > limit, (q, limit, len(full)))
        self.assertEqual(body["count"], min(limit, len(full)))
        return body

    def test_tiers_and_brand_interleave_for_glucose_for_many_limits(self):
        for limit in (1, 2, 3, 5, 7, 8, 10, 15, 20, 30, 50):
            self.check_ranking("glucose", limit)

    def test_cas_keyword_and_code_collision(self):
        body = self.check_ranking("50-99-7", 50)
        self.assertTrue(all(r["cas"] == "50-99-7" for r in body["results"][:len(BRANDS)]))
        body = self.check_ranking("DUP-1", 10)
        self.assertEqual({r["brand"] for r in body["results"]}, {"ALPHA", "GAMMA"})   # code trùng, khác hãng: cả hai đều lên

    def test_cato_alphabetical_dominance_bug_does_not_recur(self):
        """Lỗi gốc: kết quả xếp theo chữ cái hãng rồi lấy 5 dòng đầu => chỉ CATO. Từ khóa chỉ trùng 'chứa'."""
        for q in ("monohydrate", "anhydrous"):
            body = self.js(q, limit="5")
            brands = [r["brand"] for r in body["results"]]
            self.assertEqual(len(set(brands)), 5, (q, brands))
        body = self.js("glucose", token="int", limit="5")
        self.assertGreaterEqual(len({r["brand"] for r in body["results"]}), 3)

    def test_stability_and_independence_from_physical_order(self):
        runs = [[r["code"] for r in self.js("glucose", limit="30")["results"]] for _ in range(3)]
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])
        self.env.sql("UPDATE products SET name = name")                # đổi vị trí vật lý các dòng
        self.env.sql("VACUUM products")
        again = [r["code"] for r in self.js("glucose", limit="30")["results"]]
        self.assertEqual(runs[0], again)
        a = [r["code"] for r in self.js("glucose", limit="50")["results"]]
        b = [r["code"] for r in self.js("glucose", limit="50")["results"]]
        self.assertEqual(a, b)

    def test_limits_caps_and_defaults(self):
        self.assertEqual(self.js("glucose")["count"], 20)                                       # mặc định nội bộ 20
        self.assertEqual(self.js("glucose", limit="500")["count"], 50)
        self.assertEqual(self.js("glucose", limit="50")["count"], 50)
        self.assertEqual(self.js("glucose", limit="51")["count"], 50)
        self.assertEqual(self.js("glucose", token="cfull")["count"], 10)                         # mặc định khách 10
        self.assertEqual(self.js("glucose", token="cfull", limit="500")["count"], 10)
        self.assertEqual(self.js("glucose", token="climited", limit="500")["count"], 10)
        for bad in ("abc", "0", "-3", "2.5"):
            self.assertEqual(self.products("glucose", limit=bad).status_code, 400)
            self.assertEqual(self.products("glucose", token="cfull", limit=bad).status_code, 400)

    def test_customer_ranking_tiers_and_team_scope(self):
        """Thẻ khách cũng xếp đúng (trong phạm vi hãng của team) và không lộ hãng ngoài team."""
        body = self.js("glucose", token="cfull", limit="10")
        brands = {r["brand"] for r in body["results"]}
        self.assertTrue(brands <= {"CATO", "ALPHA", "BETA"}, brands)
        tiers = [staff_tier("glucose", r) for r in body["results"]]
        self.assertEqual(tiers, sorted(tiers))
        top = [r["brand"] for r in body["results"]]
        self.assertGreaterEqual(len(set(top[:3])), 2)
        lim = self.js("glucose", token="climited", limit="10")
        self.assertEqual({r["brand"] for r in lim["results"]}, {"CATO"})

    def test_special_characters_and_tiers_with_prefix(self):
        for q in ("100%", "Under_score", "Bang!excl", "Back\\slash"):
            self.assertEqual(self.js(q)["count"], 1, q)
        # tiền tố có ký tự đặc biệt không làm lệch mức
        self.env.raw_product("50%off gadget", "PCT-9", "ALPHA", "1", "1")
        self.env.raw_product("Big 50%off gadget", "PCT-10", "ALPHA", "1", "1")
        codes = [r["code"] for r in self.js("50%off")["results"]]
        self.assertEqual(codes, ["PCT-9", "PCT-10"])

    def test_whitespace_case_variants_of_brand_are_merged_for_interleave_but_not_dropped(self):
        for i in range(3):
            self.env.raw_product(f"Zymurgy item {i}", f"ZY-{i}", "CATO", "1", "1")
        self.env.raw_product("Zymurgy other", "ZY-9", "ALPHA", "1", "1")
        r = self.js("zymurgy")
        self.assertEqual(r["count"], 4)

    def test_more_boundary(self):
        total = len(self.full_rows("glucose"))
        self.assertGreater(total, 50)
        self.assertTrue(self.js("glucose", limit=str(total - 1))["more"] if total <= 50 else True)
        exact = self.js("Pref code", limit="4")
        self.assertEqual((exact["count"], exact["more"]), (4, False))
        self.assertEqual(self.js("Pref code", limit="3")["more"], True)


# ======================================================================
# Hồi quy thẻ khách: so sánh trực tiếp với bản cũ (351cf1a) trong tiến trình riêng
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I1_I8_DifferentialAgainstOldCommit(unittest.TestCase):
    """Cùng DB, cùng thẻ khách, cùng lời gọi: bản mới phải trả đúng như bản cũ (trừ thứ tự xếp hạng khi có > limit kết quả)."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()          # seed cũ giàu ca: pháp chế, tồn kho, hãng ẩn, ghi chú tiêm, hạn dùng...
        cls.tmp = tempfile.mkdtemp(prefix="machine-old-")
        src = subprocess.run(["git", "show", f"{OLD_COMMIT}:machine_api.py"], capture_output=True, text=True, cwd=str(ROOT))
        if src.returncode != 0:
            raise unittest.SkipTest("không đọc được bản cũ từ Git: " + src.stderr[:200])
        Path(cls.tmp, "machine_api.py").write_text(src.stdout, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        cls.env.close()

    def run_old(self, requests):
        path = os.path.join(self.tmp, "req.json")
        Path(path).write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("MACHINE_INTERNAL_DATABASE_URL",)}
        env.update(DATABASE_URL=self.env.dsn, MACHINE_API_TOKENS_FILE=self.env.token_path, PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("machine_api_internal_old_runner.py")), self.tmp, path],
                              capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def run_new(self, requests):
        with mock.patch.dict(os.environ, {"DATABASE_URL": self.env.dsn, "MACHINE_API_TOKENS_FILE": self.env.token_path}):
            with mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0):
                machine_api.token_store.reset()
                client = machine_api.app.test_client()
                out = []
                for req in requests:
                    machine_api.rate_limiter.reset()
                    resp = client.get(req["path"], headers={"Authorization": "Bearer " + req["token"]}, query_string=req.get("query") or None,
                                      environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
                    out.append({"status": resp.status_code, "body": resp.get_json()})
                return out

    def test_customer_responses_identical_to_old_commit(self):
        reqs = []
        qs = ["methanol", "ethanol", "MET-001", "ETH-002", "64-17-5", "50-00-0", "Override", "Percent 100%", "Axit", "SEC-001", "Carbon", "Bulk",
              "Acid", "toluene", "TOL-001", "dichloro", "Same cas", "1333-74-0", "formaldehyde", "ETH", "zzzzzz", "Unknown status", "%%%", "_th_"]
        for name in ("full", "quantity", "limited"):
            tok = self.env.tokens[name]
            for q in qs:
                reqs.append({"path": V + "/products", "token": tok, "query": {"q": q}})
                reqs.append({"path": V + "/products", "token": tok, "query": {"q": q, "limit": "3"}})
            for c in ("MET-001", "ETH-002", "SEC-001", "TOL-001", "NOPE-1", "STK-ONLY"):
                reqs.append({"path": V + "/stock", "token": tok, "query": {"code": c}})
            for c in ("1333-74-0", "64-17-5", "50-00-0", "71-43-2"):
                reqs.append({"path": V + "/stock", "token": tok, "query": {"cas": c}})
                reqs.append({"path": V + "/compliance", "token": tok, "query": {"cas": c}})
        self.assertGreaterEqual(len(reqs), 150)
        old = self.run_old(reqs)
        new = self.run_new(reqs)
        diffs, compared_sets, compared_items = [], 0, 0
        for req, o, n in zip(reqs, old, new):
            self.assertEqual(o["status"], n["status"], req)
            if o["status"] != 200:
                self.assertEqual(o["body"], n["body"], req)
                continue
            if req["path"].endswith("/products"):
                self.assertEqual(set(o["body"]), set(n["body"]), req)
                for k in ("query", "price_notice", "disclaimer", "count", "more"):
                    self.assertEqual(o["body"].get(k), n["body"].get(k), (req, k))
                key = lambda r: (r.get("code"), r.get("brand"), r.get("size"), r.get("name"))  # noqa: E731
                omap, nmap = {key(r): r for r in o["body"]["results"]}, {key(r): r for r in n["body"]["results"]}
                for k, item in nmap.items():
                    if k in omap:
                        compared_items += 1
                        if item != omap[k]:
                            diffs.append((req, k, item, omap[k]))
                if not o["body"]["more"]:
                    compared_sets += 1
                    if set(omap) != set(nmap):
                        diffs.append((req, "set", sorted(omap), sorted(nmap)))
            else:
                if o["body"] != n["body"]:
                    diffs.append((req, "body"))
        sys.stderr.write(f"\nOBSERVATION[I1/I8]: so với {OLD_COMMIT}: {len(reqs)} lời gọi, {compared_items} mục so từng trường, "
                         f"{compared_sets} tập kết quả so đầy đủ, khác biệt={len(diffs)}\n")
        self.assertEqual(diffs, [], diffs[:3])


# ======================================================================
# Công cụ cấp thẻ + so sánh nội bộ/khách trên seed cũ giàu ca (I2, I8)
# ======================================================================
CLI = str(ROOT / "scripts/machine_token.py")


class I2_TokenCli(unittest.TestCase):
    def run_cli(self, *args):
        d = tempfile.mkdtemp(prefix="mtok-v-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "tokens.json")
        proc = subprocess.run([sys.executable, CLI, *args[:1], "--file", path, *args[1:]], capture_output=True, text=True, cwd=str(ROOT),
                              env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)})
        return proc, path

    def test_internal_issuance_rules(self):
        bad = [
            ["new", "--id", "i-a", "--profile", "internal", "--days", "30"],                                   # thiếu IP
            ["new", "--id", "i-b", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "91"],   # quá 90 ngày
            ["new", "--id", "i-c", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--team-id", "3"],  # có team
            ["new", "--id", "c-a", "--days", "30"],                                                              # khách thiếu team
            ["new", "--id", "i-d", "--profile", "internal", "--allowed-ip", "bogus", "--days", "30"],
            ["new", "--id", "i-e", "--profile", "Internal", "--allowed-ip", "198.51.100.7"],
        ]
        for args in bad:
            proc, path = self.run_cli(*args)
            self.assertNotEqual(proc.returncode, 0, (args, proc.stdout[-200:]))
            self.assertFalse(os.path.exists(path) and '"tokens": [{' in open(path).read().replace("\n", ""), args)
        proc, path = self.run_cli("new", "--id", "i-ok", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", "30", "--stock-detail", "status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.load(open(path))
        entry = doc["tokens"][0]
        self.assertEqual((entry["profile"], entry["stock_detail"]), ("internal", "quantity"))
        self.assertNotIn("team_id", entry)
        self.assertEqual(stat_mode(path), 0o600)
        loaded = machine_api.load_token_entries(path)
        self.assertEqual([e["id"] for e in loaded], ["i-ok"])
        # thẻ khách tạo bằng CLI vẫn hoạt động như cũ
        proc, path2 = self.run_cli("new", "--id", "c-ok", "--team-id", "3", "--days", "30")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("profile", json.load(open(path2))["tokens"][0])

    def test_zero_or_negative_days_observation(self):
        for days in ("0", "-5"):
            proc, path = self.run_cli("new", "--id", "i-neg", "--profile", "internal", "--allowed-ip", "198.51.100.7", "--days", days)
            sys.stderr.write(f"\nOBSERVATION[I2]: CLI --days {days} (thẻ nội bộ) -> exit {proc.returncode}\n")

    def test_wide_open_ip_accepted_by_cli_observation(self):
        proc, path = self.run_cli("new", "--id", "i-wide", "--profile", "internal", "--allowed-ip", "0.0.0.0/0", "--days", "30")
        sys.stderr.write(f"\nOBSERVATION[I2]: CLI cấp thẻ nội bộ với --allowed-ip 0.0.0.0/0 -> exit {proc.returncode}\n")


def stat_mode(path):
    import stat as _stat
    return _stat.S_IMODE(os.stat(path).st_mode)


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I8_InternalVsCustomerOnOldRichSeed(unittest.TestCase):
    """Seed cũ (injection note, ghi chú rất dài, quy tắc không hoạt động, trạng thái chưa xác định, hạn dùng...):
    pháp chế + trạng thái tồn kho của thẻ nội bộ phải trùng thẻ khách (stock_detail=quantity) trên mọi sản phẩm cả hai cùng thấy."""

    @classmethod
    def setUpClass(cls):
        cls.env = S.Env()
        tok = "stm_vi_old_" + uuid.uuid4().hex
        ent = S.entry(tok, "t-oldint", None, profile="internal", allowed_ips=[INTERNAL_NET], stock_detail="quantity")
        ent.pop("team_id")
        cls.env.tokens["oldint"] = tok
        cls.env.entries.append(ent)
        cls.env.write_entries()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_compliance_and_stock_blocks_equal(self):
        envv = {"DATABASE_URL": self.env.dsn, "MACHINE_INTERNAL_DATABASE_URL": self.env.dsn, "MACHINE_API_TOKENS_FILE": self.env.token_path}
        qs = ["methanol", "ethanol", "ETH-00", "MET-001", "64-17-5", "50-00-0", "Override", "Percent", "Axit", "SEC-001", "Carbon", "Acetonitrile",
              "Trichloro", "toluene", "TOL-001", "dichloro", "Same cas", "1333-74-0", "formaldehyde", "Unknown status", "Benzene", "sulfuric", "hydrochloric", "ACD-00"]
        compared = 0
        diffs = []
        with mock.patch.dict(os.environ, envv), mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0):
            machine_api.token_store.reset()
            client = machine_api.app.test_client()

            def call(tok, q):
                machine_api.rate_limiter.reset()
                r = client.get(V + "/products", query_string={"q": q, "limit": "10"},
                               headers={"Authorization": "Bearer " + self.env.tokens[tok]}, environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
                self.assertEqual(r.status_code, 200, (tok, q, r.get_data(as_text=True)[:200]))
                return r.get_json()["results"]
            for q in qs:
                cust = {(r["code"], r["brand"], r["size"]): r for r in call("quantity", q)}
                inner = {(r["code"], r["brand"], r["size"]): r for r in call("oldint", q)}
                for key, c in cust.items():
                    i = inner.get(key)
                    if i is None:
                        continue   # (tập top-10 có thể khác do hãng ngoài team làm đầy)
                    compared += 1
                    if c["compliance"] != i["compliance"]:
                        diffs.append((q, key, "compliance", c["compliance"], i["compliance"]))
                    for f in ("status", "pack_confirmed", "expiry_warning", "quantity"):
                        if c["stock"].get(f) != i["stock"].get(f):
                            diffs.append((q, key, f, c["stock"].get(f), i["stock"].get(f)))
                    if (c["name"], c["cas"]) != (i["name"], i["cas"]):
                        diffs.append((q, key, "identity"))
                    self.assertIn("price", i)
                    self.assertNotIn("price", c)
        sys.stderr.write(f"\nOBSERVATION[I8]: {compared} sản phẩm so pháp chế/tồn kho nội bộ vs khách; khác biệt={len(diffs)}\n")
        self.assertGreaterEqual(compared, 25)
        self.assertEqual(diffs, [], diffs[:3])

    def test_legacy_schema_without_brand_tables_gives_null_price_with_status(self):
        envv = {"DATABASE_URL": self.env.dsn, "MACHINE_INTERNAL_DATABASE_URL": self.env.dsn, "MACHINE_API_TOKENS_FILE": self.env.token_path}
        with mock.patch.dict(os.environ, envv), mock.patch.object(machine_api, "CONFIG_RECHECK_SECONDS", 0.0):
            machine_api.token_store.reset()
            machine_api.rate_limiter.reset()
            r = machine_api.app.test_client().get(V + "/products", query_string={"q": "MET-001"}, headers={"Authorization": "Bearer " + self.env.tokens["oldint"]},
                                                  environ_overrides={"REMOTE_ADDR": INTERNAL_IP})
            block = r.get_json()["results"][0]["price"]
            self.assertIsNone(block["unit_price_vnd"])
            self.assertIn(block["price_status"], ("LEGACY_RATE_MISSING", "LEGACY_SCHEMA", "CURRENCY_SCHEMA_INCOMPLETE"))


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I3_StockScanCap(Base):
    def test_exact_quantity_survives_thousands_of_lots_of_same_cas(self):
        """THĂM DÒ: truy vấn tồn kho có LIMIT 5000 lô; nếu CAS chung có > 5000 lô, số lượng chính xác của mã đang hỏi có thể sai thầm lặng."""
        snap = self.env.sql("SELECT active_snapshot_id FROM stock_state")[0][0]
        self.env.raw_product("Cap target", "ZZZ-CAP-1", "GAMMA", "10", "1", cas="99-99-9")
        self.env.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                     "VALUES (%s,'x','ZZZ-CAP-1','99-99-9','GAMMA','1L',1,40,NULL,'gamma','zzz-cap-1','1l','99-99-9','')", (snap,))
        self.env.sql("INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,brand_norm,code_norm,size_norm,cas_norm,stock_note) "
                     "SELECT %s,'x','AAA-' || g,'99-99-9','ALPHA','1L',1,1,NULL,'alpha','aaa-' || g,'1l','99-99-9','' FROM generate_series(1,5200) g", (snap,))
        try:
            stock = self.js("ZZZ-CAP-1")["results"][0]["stock"]
            sys.stderr.write(f"\nOBSERVATION[I3]: 5.200 lô cùng CAS + 1 lô mã đang hỏi (SL 40) -> status={stock['status']} quantity={stock.get('quantity')}\n")
            self.__class__.cap_result = (stock["status"], stock.get("quantity"))
            self.assertEqual((stock["status"], stock["quantity"]), ("IN_STOCK", 40))
        finally:
            self.env.sql("DELETE FROM stock_items WHERE cas='99-99-9'")
            self.env.sql("DELETE FROM products WHERE code='ZZZ-CAP-1'")


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I1_RealGunicornConcurrency(unittest.TestCase):
    """Dưới Gunicorn thật (8 luồng), trộn song song thẻ nội bộ và thẻ khách: thẻ khách KHÔNG bao giờ thấy giá (không rò trạng thái giữa các lời gọi)."""

    @classmethod
    def setUpClass(cls):
        cls.env = IEnv()
        cls.env.entries = []
        cls.env.tokens = {}
        cls.tok_int = cls.env.add_internal("gint", allowed_ips=["127.0.0.1/32"], rate_per_minute=600)
        cls.tok_cust = cls.env.add_token("gcust", cls.env.team_full, rate_per_minute=600)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_mixed_parallel_calls_no_price_for_customer(self):
        from concurrent.futures import ThreadPoolExecutor
        port = free_port()
        out = tempfile.TemporaryFile("w+b")
        envv = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "FLASK_SECRET_KEY", "PYTHONPATH")}
        envv.update(MACHINE_DATABASE_URL=self.env.dsn, MACHINE_INTERNAL_DATABASE_URL=self.env.dsn, MACHINE_API_TOKENS_FILE=self.env.token_path,
                    PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        flags = unit_flags(port)
        proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *flags], cwd=str(ROOT), env=envv, stdout=out, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    c.request("GET", V + "/health")
                    if c.getresponse().status == 200:
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.2)
            else:
                self.fail("gunicorn không khởi động")

            def one(i):
                token = self.tok_int if i % 2 == 0 else self.tok_cust
                path = [V + "/products?q=glucose", V + "/products?q=GN-CATO-1L", V + "/stock?code=GN-CATO-1L", V + "/products?q=Dextrose&limit=10"][i % 4]
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                c.request("GET", path, headers={"Authorization": "Bearer " + token})
                r = c.getresponse()
                body = r.read().decode("utf-8")
                c.close()
                return i % 2 == 0, r.status, body
            with ThreadPoolExecutor(16) as pool:
                results = list(pool.map(one, range(400)))
            cust = [(s, b) for internal, s, b in results if not internal]
            inner = [(s, b) for internal, s, b in results if internal]
            self.assertTrue(all(s == 200 for s, _ in cust + inner), {s for s, _ in cust + inner})
            for status, body in cust:
                for needle in (PRICE_S, "31415", NOTE_S, SNOTE_S, '"price"', '"lots"', "unit_price_vnd"):
                    self.assertNotIn(needle, body)
            self.assertTrue(any("unit_price_vnd" in b for _, b in inner if "/stock" not in b))
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:  # noqa: BLE001
                proc.kill()
            out.close()


# ======================================================================
# Gunicorn thật (đúng cờ của unit) với vai PG nội bộ thật
# ======================================================================
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def unit_flags(port):
    unit = (ROOT / "deploy/search-tools-machine-api.service").read_text(encoding="utf-8")
    match = re.search(r"ExecStart=(.*?)(?:\n[A-Z][A-Za-z]+=|\Z)", unit, re.S)
    flags = match.group(1).replace("\\\n", " ").split()[1:]
    flags[flags.index("--bind") + 1] = f"127.0.0.1:{port}"
    return flags


@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I2_I5_I6_RealGunicorn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = IEnv()
        cls.role = RoleHelper(cls.env, "sql/machine_internal_role.sql", "machine_api_internal_ro")
        cls.cust_role = RoleHelper(cls.env, "sql/machine_readonly_role.sql", "machine_api_ro")
        cls.env.entries = []
        cls.env.tokens = {}
        cls.tok_int = cls.env.add_internal("gint", allowed_ips=["127.0.0.1/32"])
        cls.tok_cust = cls.env.add_token("gcust", cls.env.team_full)
        cls.port = free_port()

    @classmethod
    def tearDownClass(cls):
        for r in (cls.role, cls.cust_role):
            r.drop()
        cls.env.close()

    def start(self, extra_env):
        self.out = tempfile.TemporaryFile("w+b")
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "MACHINE_DATABASE_URL", "MACHINE_INTERNAL_DATABASE_URL", "FLASK_SECRET_KEY", "PYTHONPATH")}
        env.update(MACHINE_API_TOKENS_FILE=self.env.token_path, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        env.update(extra_env)
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "gunicorn", *unit_flags(self.port)], cwd=str(ROOT), env=env, stdout=self.out, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                c.request("GET", V + "/health")
                if c.getresponse().status == 200:
                    return
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("gunicorn không khởi động")

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(10)
        except Exception:  # noqa: BLE001
            self.proc.kill()

    def log(self):
        time.sleep(0.4)
        self.out.seek(0)
        return self.out.read().decode("utf-8", "replace")

    def hit(self, path, token, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        if token:
            h["Authorization"] = "Bearer " + token
        c.request("GET", path, headers=h)
        r = c.getresponse()
        body = r.read().decode("utf-8")
        c.close()
        return r.status, body

    def test_real_gunicorn_with_real_roles_and_log_scan(self):
        self.start({"MACHINE_DATABASE_URL": self.cust_role.dsn, "MACHINE_INTERNAL_DATABASE_URL": self.role.dsn})
        try:
            s, b = self.hit(V + "/products?q=GN-CATO-1L", self.tok_int)
            self.assertEqual(s, 200, b[:300])
            item = json.loads(b)["results"][0]
            self.assertEqual(item["price"]["original_price"], PRICE_S)
            self.assertEqual(item["stock"]["quantity"], 12)
            s, b = self.hit(V + "/products?q=GN-CATO-1L&profile=internal", self.tok_cust)
            self.assertEqual(s, 200)
            self.assertNotIn(PRICE_S, b)
            self.assertNotIn("lots", b)
            self.assertEqual(self.hit(V + "/products?q=glucose", None)[0], 401)
            self.assertEqual(self.hit(V + "/products?q=glucose", self.tok_int, {"X-Forwarded-For": "203.0.113.9"})[0], 403)
            self.assertEqual(self.hit(V + "/products?q=zz", self.tok_int)[0], 400)
            self.assertEqual(self.hit(V + "/stock?code=GN-CATO-1L", self.tok_int)[0], 200)
            self.assertEqual(self.hit(V + "/compliance?cas=50-99-7", self.tok_int)[0], 200)
            text = self.log()
            for needle in (PRICE_S, "31415", "GN-CATO-1L", "glucose", "50-99-7", self.tok_int, self.tok_cust, self.role.password, self.cust_role.password,
                           machine_api.hash_token(self.tok_int), NOTE_S, SNOTE_S):
                self.assertNotIn(needle, text, needle)
            self.assertIn('"token": "t-gint"', text)
        finally:
            self.stop()

    def test_real_gunicorn_missing_internal_dsn_and_wrong_dsn(self):
        self.start({"MACHINE_DATABASE_URL": self.cust_role.dsn})
        try:
            s, b = self.hit(V + "/products?q=glucose", self.tok_int)
            self.assertEqual(s, 503, b[:200])
            self.assertEqual(self.hit(V + "/products?q=glucose", self.tok_cust)[0], 200)
        finally:
            self.stop()
        bad_pw = "WRONGPW" + uuid.uuid4().hex
        parsed = urlparse(self.role.dsn)
        bad = urlunparse(parsed._replace(netloc=f"{self.role.role}:{bad_pw}@{parsed.hostname}:{parsed.port}"))
        self.start({"MACHINE_DATABASE_URL": self.cust_role.dsn, "MACHINE_INTERNAL_DATABASE_URL": bad})
        try:
            s, b = self.hit(V + "/products?q=glucose", self.tok_int)
            self.assertEqual(s, 503)
            for needle in (bad_pw, self.role.role, "FATAL", "password", "psycopg", "Traceback"):
                self.assertNotIn(needle, b)
            text = self.log()
            self.assertNotIn(bad_pw, text)
            self.assertNotIn(self.role.password, text)
        finally:
            self.stop()

    def test_dirty_price_under_real_gunicorn_returns_json_not_html_500(self):
        self.env.raw_product("Dirty real", "DIRTY-1", "ALPHA", "nan", "1")
        self.start({"MACHINE_DATABASE_URL": self.cust_role.dsn, "MACHINE_INTERNAL_DATABASE_URL": self.role.dsn})
        try:
            s, b = self.hit(V + "/products?q=DIRTY-1", self.tok_int)
            sys.stderr.write(f"\nOBSERVATION[I4]: giá 'nan' qua Gunicorn thật -> HTTP {s}\n")
            self.assertEqual(s, 200, b[:200])
        finally:
            self.stop()
            self.env.sql("DELETE FROM products WHERE code='DIRTY-1'")


# ======================================================================
# Quy mô: truy vấn xếp hạng trên bảng lớn không vượt statement_timeout
# ======================================================================
@unittest.skipUnless(HAS_PG, "cần PostgreSQL local")
class I7_Scale(Base):
    def test_hot_keyword_on_large_table_stays_under_timeout(self):
        self.env.sql("INSERT INTO products(name, code, cas, brand, size, price, ship, note, source_brand) "
                     "SELECT 'Hot reagent ' || g, 'HOT-' || g, '', (ARRAY['CATO','ALPHA','BETA','GAMMA','DELTA'])[1 + mod(g, 5)], '1L', '10', '1.2', '', (ARRAY['CATO','ALPHA','BETA','GAMMA','DELTA'])[1 + mod(g, 5)] "
                     "FROM generate_series(1, 120000) g")
        self.env.sql("ANALYZE products")
        try:
            times = []
            for tok in ("int", "cfull"):
                t0 = time.monotonic()
                r = self.products("reagent", token=tok, limit="50")
                times.append(round(time.monotonic() - t0, 2))
                self.assertEqual(r.status_code, 200, (tok, r.status_code, r.get_data(as_text=True)[:200]))
                if tok == "int":
                    self.assertEqual(r.get_json()["count"], 50)
            sys.stderr.write(f"\nOBSERVATION[I7]: 120.000 sản phẩm khớp từ khóa -> thời gian (nội bộ, khách) = {times}s\n")
        finally:
            self.env.sql("DELETE FROM products WHERE left(code, 4) = 'HOT-'")


if __name__ == "__main__":
    unittest.main()
