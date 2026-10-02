"""Independent adversarial verification, round 2, of scripts/staging_restore_run_temp.py (S1.2) on REAL
PostgreSQL 16 (disposable Docker container, --network none, fake data only).

Focus
  A. V5 post-restore check: "partial / disguised / different-shape 033" variants must be HELD
     (exit 1, DUNG: TEMP_MUST_BE_PRE_033, no completed marker); legit pre-033 look-alikes and a valid
     pre-033 DB must NOT be blocked (positive controls).
  B. HOLD / error output must never carry data fragments or secrets (COPY, constraint, input errors,
     expression-index / materialized-view errors that do NOT go through COPY).
  C. Single-transaction atomicity for post-data failures, no retry, temp DB kept, source untouched.

Reuses the Harness / fixtures of test_staging_restore_run_temp_real_pg_adversarial.py (same container
name; set VRF_PG_KEEP=1 to keep the container between runs). Skips when Docker/postgres:16 is missing.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_staging_restore_run_temp_real_pg_adversarial as base  # noqa: E402

DOCKER, CONTAINER = base.DOCKER, base.CONTAINER
psql, sh = base.psql, base.sh

MARK = "SECRETMARK"  # 10 bytes: planted data fragment that must never reach the output
INT10 = "1234567890"  # 10 bytes: replaced by MARK inside an uncompressed archive


def dump_z0(db, name):
    base.sh(DOCKER, "exec", "-u", "postgres", CONTAINER, "sh", "-c",
            "rm -f /tmp/%s.dump && pg_dump -Fc -Z0 -d %s -f /tmp/%s.dump && chmod 644 /tmp/%s.dump"
            % (name, db, name, name))


def variant(name, *sqls, z0=False, template="prechange_src"):
    if psql("postgres", "select 1 from pg_database where datname='%s'" % name):
        return
    psql("postgres", "create database %s owner vrf_app template %s" % (name, template))
    for s in sqls:
        psql(name, s)
    (dump_z0 if z0 else base.dump)(name, name)


FUNC_TRIG = ("create function public.zz_f2() returns trigger language plpgsql as $$ begin return new; end $$")

# name -> sqls ; every one of these carries a 033-named object (any shape) => V5 oracle says HOLD
RESIDUE = {
    "r_func_overload": ["create function public.update_regulatory_rule_revision(integer) returns int "
                        "language sql as 'select 1'"],
    "r_func_procedure": ["create procedure public.update_regulatory_rule_revision() language sql as 'select 1'"],
    "r_trigger_other_table": [FUNC_TRIG, "create trigger zz_regulatory_rule_revision before update on "
                              "public.products for each row execute function public.zz_f2()"],
    "r_trigger_disabled": [FUNC_TRIG, "create trigger zz_regulatory_rule_revision before update on "
                           "public.regulatory_rules for each row execute function public.zz_f2()",
                           "alter table regulatory_rules disable trigger zz_regulatory_rule_revision"],
    "r_trigger_constraint": [FUNC_TRIG, "create constraint trigger zz_regulatory_rule_revision after insert on "
                             "public.products deferrable initially deferred for each row execute function public.zz_f2()"],
    "r_idx_identity_on_other_table": ["create index regulatory_manual_key_identity on public.products(id)"],
    "r_idx_owner_on_other_table": ["create index regulatory_manual_key_owner on public.products(id)"],
    "r_idx_history_on_other_table": ["create index regulatory_manual_event_history on public.products(id)"],
    "r_keys_matview": ["create materialized view public.regulatory_rule_manual_keys as select 1 as id"],
    "r_keys_sequence": ["create sequence public.regulatory_rule_manual_keys"],
    "r_keys_type": ["create type public.regulatory_rule_manual_keys as (a int)"],
    "r_keys_partitioned": ["create table public.regulatory_rule_manual_keys (id int) partition by range (id)"],
    "r_events_view": ["create view public.regulatory_rule_manual_events as select 1 as id"],
    "r_events_plain_table": ["create table public.regulatory_rule_manual_events (id int)"],
    "r_col_manual_protected_text": ["alter table regulatory_rules add column manual_protected text"],
    "r_col_manual_protected_nullable_bool": ["alter table regulatory_rules add column manual_protected boolean"],
    "r_col_revision_text": ["alter table regulatory_rules add column revision text"],
}

# 033 DDL present as ordinary tables but moved/renamed => no object has the exact public 033 name.
AMBIGUOUS = {
    "a_keys_other_schema": ["create schema legacy", "create table legacy.regulatory_rule_manual_keys (id int)"],
    "a_keys_renamed": ["create table public.regulatory_rule_manual_keys_old (id int)"],
    "a_func_other_schema": ["create schema legacy", "create function legacy.update_regulatory_rule_revision() "
                            "returns trigger language plpgsql as $$ begin return new; end $$"],
    "a_idx_other_schema": ["create schema legacy", "create table legacy.t (id int)",
                           "create index regulatory_manual_key_identity on legacy.t(id)"],
    "a_revision_col_other_schema": ["create schema legacy",
                                    "create table legacy.regulatory_rules (id int, revision bigint not null)"],
}

# legitimate pre-033 DBs that must NOT be blocked
POSITIVE = {
    "p_all_rules_active": ["update regulatory_rules set is_active=true"],
    "p_lookalikes_unrelated": [
        "alter table products add column revision bigint not null default 1",
        "alter table stock_items add column manual_protected boolean not null default false",
        FUNC_TRIG.replace("zz_f2", "update_regulatory_rule_revision_x"),
        "create trigger zz_regulatory_rule_revision_old before update on public.regulatory_rules "
        "for each row execute function public.update_regulatory_rule_revision_x()",
        "create index regulatory_manual_key_identity_old on public.products(id)",
        "create table public.regulatory_rule_manual_keys_archive_note (id int)",
    ],
    "p_extra_schema_objects": ["create schema reporting", "create table reporting.x (id int)",
                               "create index x_ix on reporting.x(id)"],
}


class ShapeAndLeak(unittest.TestCase):
    restore = base.RealPg.restore
    archive = base.RealPg.archive
    derived = base.RealPg.derived
    put_archive_in_container = base.RealPg.put_archive_in_container
    def fresh_temp(self, harness, path):
        # harness hygiene: wait until the S1.1 create script's own backend has really exited (race seen once)
        import time
        temp, oid = base.RealPg.fresh_temp(self, harness, path)
        for _ in range(50):
            if psql("postgres", "select count(*) from pg_stat_activity where datname='%s'" % temp) == "0":
                break
            time.sleep(0.2)
        return temp, oid
    tables_in_temp = base.RealPg.tables_in_temp
    live_writes = base.RealPg.live_writes

    @classmethod
    def setUpClass(cls):
        if not base.docker_ok():
            raise unittest.SkipTest("docker/postgres:16 image unavailable")
        base.build_fixtures()
        import tempfile
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="s12-r2-")
        cls.tmp = Path(cls.tmpdir.name).resolve()
        cls.archives = {}
        for group in (RESIDUE, AMBIGUOUS, POSITIVE):
            for name, sqls in group.items():
                variant("w_" + name, *sqls)
        # partial 033 built from the real post-033 schema: drop pieces so only some footprints remain
        variant("w_partial_no_trigger_no_function", "drop trigger zz_regulatory_rule_revision on regulatory_rules",
                "drop function update_regulatory_rule_revision()", template="search_tools_staging")
        variant("w_partial_columns_only", "drop trigger zz_regulatory_rule_revision on regulatory_rules",
                "drop function update_regulatory_rule_revision()", "drop table regulatory_rule_manual_events",
                "drop table regulatory_rule_manual_keys", template="search_tools_staging")
        variant("w_partial_tables_only", "drop trigger zz_regulatory_rule_revision on regulatory_rules",
                "drop function update_regulatory_rule_revision()",
                "alter table regulatory_rules drop column revision, drop column manual_protected",
                template="search_tools_staging")
        variant("w_partial_indexes_only_dropped", "drop index regulatory_manual_key_identity",
                "drop index regulatory_manual_key_owner", "drop index regulatory_manual_event_history",
                template="search_tools_staging")
        # leak fixtures (uncompressed archives so a planted 10-byte value can be edited in place)
        variant("w_leak_expr_index", "create table lk_a(id int primary key, t text)",
                "insert into lk_a values (1,'%s')" % INT10, "create index lk_a_ix on lk_a(((t)::integer))", z0=True)
        variant("w_leak_copy_int", "create table lk_b(id int primary key, n text)",
                "insert into lk_b values (1,'%s')" % INT10, "alter table lk_b alter column n type integer using n::integer",
                z0=True)
        variant("w_leak_check", "create table lk_c(id int primary key, nm text check (nm ~ '^[A-Z]+$'))",
                "insert into lk_c values (1,'%s')" % MARK.replace("K", "K"), z0=True)
        variant("w_leak_unique", "create table lk_e(id int primary key, u text unique)",
                "insert into lk_e values (1,'%s1'),(2,'%s2')" % (MARK, MARK), z0=True)
        variant("w_leak_fk", "create table lk_p(p text primary key)", "create table lk_c2(c text references lk_p(p))",
                "insert into lk_p values ('PARENTROW1')", "insert into lk_c2 values ('PARENTROW1')", z0=True)
        variant("w_leak_matview", "create table lk_g(id int primary key, t text)",
                "insert into lk_g values (1,'%s')" % INT10,
                "create materialized view lk_mv as select t::integer as n from lk_g", z0=True)
        variant("w_leak_domain", "create domain lk_dom as text check (value ~ '^[A-Z]+$')",
                "create table lk_h(id int primary key, d lk_dom)", "insert into lk_h values (1,'%s')" % MARK, z0=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()
        if not base.KEEP and not os.environ.get("VRF_PG_CONTAINER"):
            subprocess.run([DOCKER, "rm", "-f", CONTAINER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ A. V5 variants
    def _held_pre033(self, name):
        r = self.restore("w_" + name)
        ok = (r["code"] == 1 and "STAGING_TEMP_RESTORE_COMPLETED" not in r["out"]
              and "DUNG: TEMP_MUST_BE_PRE_033" in r["out"] and "HOLD:" in r["out"] and r["exc"] is None)
        return ok, r

    def test_v5_residue_variants_all_held(self):
        bad = {}
        for name in RESIDUE:
            ok, r = self._held_pre033(name)
            if not ok:
                bad[name] = (r["code"], [l for l in r["out"].splitlines() if l.startswith(("DUNG", "STAGING", "names_033"))])
        print("V5 residue variants not held:", bad)
        self.assertEqual(bad, {}, "033-named residue reported as pre-033 (script exit 0 / wrong gate)")

    def test_v5_partial_033_from_real_post033_schema_all_held(self):
        bad = {}
        for name in ("partial_no_trigger_no_function", "partial_columns_only", "partial_tables_only",
                     "partial_indexes_only_dropped"):
            ok, r = self._held_pre033(name)
            if not ok:
                bad[name] = (r["code"], [l for l in r["out"].splitlines() if l.startswith(("DUNG", "STAGING"))])
        print("V5 partial-033 variants not held:", bad)
        self.assertEqual(bad, {})

    def test_v5_full_033_archive_held_and_temp_kept(self):
        r = self.restore("post033")
        self.assertEqual(r["code"], 1, r["out"])
        self.assertIn("DUNG: TEMP_MUST_BE_PRE_033", r["out"])
        self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", r["out"])
        self.assertEqual(psql("postgres", "select count(*) from pg_database where datname='%s'" % r["temp"]), "1")

    def test_v5_ambiguous_variants_observed(self):
        """Not asserted as failure: records how the script treats 033-like objects that are moved/renamed."""
        seen = {}
        for name in AMBIGUOUS:
            r = self.restore("w_" + name)
            seen[name] = (r["code"], "STAGING_TEMP_RESTORE_COMPLETED" in r["out"])
        print("V5 ambiguous (moved/renamed 033-like objects) (code, completed):", seen)

    def test_positive_controls_not_blocked(self):
        bad = {}
        for name in POSITIVE:
            r = self.restore("w_" + name)
            if not (r["code"] == 0 and "STAGING_TEMP_RESTORE_COMPLETED" in r["out"] and "HOLD" not in r["out"]):
                bad[name] = (r["code"], [l for l in r["out"].splitlines() if l.startswith(("DUNG", "footprints", "names_033"))])
        print("positive controls wrongly blocked:", bad)
        self.assertEqual(bad, {})

    def test_positive_control_pristine_033_names_all_false_and_030_032_true(self):
        r = self.restore("pristine")
        self.assertEqual(r["code"], 0, r["out"])
        self.assertIn("footprints_030_032=footprint_030=True,footprint_031=True,footprint_032=True", r["out"])
        names = [l for l in r["out"].splitlines() if l.startswith("names_033=")][0]
        self.assertNotIn("True", names)

    # ------------------------------------------------------------------ B/C. leak + atomicity
    def _leak_case(self, name, edit=None):
        path = self.archive("w_" + name)
        if edit:
            raw = path.read_bytes()
            old, new = edit
            self.assertEqual(raw.count(old), 1, "fixture must contain the planted value exactly once")
            path = self.derived(name + "_edited", raw.replace(old, new))
        r = self.restore(path)
        return r

    def _assert_no_leak_and_atomic(self, r, label):
        out = r["out"]
        first = [l for l in out.splitlines() if l.startswith("restore_error_first_line=")]
        print("LEAK[%s] code=%s hint=%r" % (label, r["code"], first))
        self.assertEqual(r["code"], 1, out)
        self.assertIn("HOLD:", out)
        self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", out)
        self.assertNotIn(MARK, out, "data fragment reached the script output")
        self.assertNotIn("PARENTROW", out)
        restores = [a for a in r["h"].calls if any(x.endswith("/pg_restore") for x in a) and "--version" not in a]
        self.assertEqual(len(restores), 1)
        self.assertEqual(self.tables_in_temp(r["temp"]), 0, "single-transaction rollback not observed")
        self.assertEqual(psql("postgres", "select count(*) from pg_database where datname='%s'" % r["temp"]), "1")
        self.assertEqual(self.live_writes(), r["live_before"])

    def test_leak_expression_index_input_error(self):
        """Non-COPY, data-dependent input error (CREATE INDEX on ((t)::integer)) in post-data."""
        r = self._leak_case("leak_expr_index", (INT10.encode(), MARK.encode()))
        self._assert_no_leak_and_atomic(r, "expr_index")

    def test_leak_materialized_view_refresh_input_error(self):
        r = self._leak_case("leak_matview", (INT10.encode(), MARK.encode()))
        self._assert_no_leak_and_atomic(r, "matview")

    def test_leak_copy_integer_input_error(self):
        r = self._leak_case("leak_copy_int", (INT10.encode(), MARK.encode()))
        self._assert_no_leak_and_atomic(r, "copy_int")

    def test_leak_check_constraint_violation(self):
        r = self._leak_case("leak_check", (MARK.encode(), (MARK[:-1] + "1").encode()))
        self._assert_no_leak_and_atomic(r, "check")

    def test_leak_unique_violation(self):
        r = self._leak_case("leak_unique", ((MARK + "2").encode(), (MARK + "1").encode()))
        self._assert_no_leak_and_atomic(r, "unique")

    def test_leak_foreign_key_violation(self):
        raw = self.archive("w_leak_fk").read_bytes()
        self.assertEqual(raw.count(b"PARENTROW1"), 2)
        first = raw.index(b"PARENTROW1")
        second = raw.index(b"PARENTROW1", first + 1)
        # change the child value (second occurrence) so the FK validation fails
        edited = raw[:second] + MARK.encode() + raw[second + 10:]
        r = self.restore(self.derived("leak_fk_edited", edited))
        self._assert_no_leak_and_atomic(r, "fk")

    def test_leak_domain_check_violation(self):
        r = self._leak_case("leak_domain", (MARK.encode(), (MARK[:-1] + "1").encode()))
        self._assert_no_leak_and_atomic(r, "domain")

    def test_leak_invalid_utf8_in_copy(self):
        raw = self.archive("w_leak_copy_int").read_bytes()
        self.assertEqual(raw.count(INT10.encode()), 1)
        r = self.restore(self.derived("leak_utf8", raw.replace(INT10.encode(), b"\xff\xfe" + MARK.encode()[:8])))
        self._assert_no_leak_and_atomic(r, "utf8")

    # ------------------------------------------------------------------ D. "temp DB not empty" in forms other
    # than a relation in schema public (gate must refuse before the single write)
    NONEMPTY = {
        "other_schema_table": ["create schema junk", "create table junk.t (id int)"],
        "empty_extra_schema": ["create schema junk"],
        "public_function": ["create function public.f() returns int language sql as 'select 1'"],
        "public_sequence": ["create sequence public.sq"],
        "public_type": ["create type public.ty as (a int)"],
        "extension": ["create extension if not exists pg_trgm"],
        "control_public_table_refused": ["create table public.x(id int)"],
    }

    def test_nonempty_temp_variants_refused_before_write(self):
        seen = {}
        for name, sqls in self.NONEMPTY.items():
            r = self.restore("pristine", pre=lambda temp, sqls=sqls: [psql(temp, s) for s in sqls])
            wrote = bool(r["h"].restore_results)
            seen[name] = (r["code"], wrote, [l for l in r["out"].splitlines() if l.startswith("DUNG")])
        print("non-empty temp variants (code, pg_restore_ran, gate):", seen)
        bad = {k: v for k, v in seen.items() if v[1]}
        self.assertEqual(bad, {}, "pg_restore ran into a temp DB that was not empty")


if __name__ == "__main__":
    unittest.main()
