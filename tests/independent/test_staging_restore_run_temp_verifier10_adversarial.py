"""Independent adversarial verification, round 10, of scripts/staging_restore_run_temp.py (S1.2) on REAL
PostgreSQL 16 (disposable Docker container, --network none, fake data only).

Focus (new vs. earlier rounds)
  A. "temp DB not empty" gate: many more kinds of pre-existing user objects (operator, collation, text
     search objects, FDW/server, publication, cast, conversion, default privileges, ...) must be refused
     BEFORE the single pg_restore; positive control = DB created exactly like S1.1 passes.
  B. HOLD / error output must never carry a data/catalog canary, nor raw tool text, for many more error
     contexts (generated column, raising function in index/matview, enum, json, partition routing, event
     trigger, missing role, missing extension, invalid UTF-8, truncated archive).
  C. 033-name post-check variants (renamed/moved/other shape) + positive controls.
  D. Safety regressions: one pg_restore, argv option whitelist, source untouched, pre-write refusals.

Uses its OWN container name (default vrf-pg16-s12-v10) so it never collides with other test files.
Skips when Docker/postgres:16 is unavailable.
"""
import io
import os
from contextlib import redirect_stderr
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault("VRF_PG_CONTAINER", "vrf-pg16-s12-v10")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_staging_restore_run_temp_real_pg_adversarial as base  # noqa: E402

DOCKER, CONTAINER = base.DOCKER, base.CONTAINER
psql, sh = base.psql, base.sh

CAN = "CANARYDATA"  # 10 bytes, must never appear in any script output
XXX = "XXXXXXXXXX"  # 10 bytes planted placeholder, replaced by CAN in the (uncompressed) archive


def dump_z0(db, name):
    sh(DOCKER, "exec", "-u", "postgres", CONTAINER, "sh", "-c",
       "rm -f /tmp/%s.dump && pg_dump -Fc -Z0 -d %s -f /tmp/%s.dump && chmod 644 /tmp/%s.dump" % (name, db, name, name))


def variant(name, *sqls, z0=True, template="prechange_src"):
    if psql("postgres", "select 1 from pg_database where datname='%s'" % name):
        return
    psql("postgres", "create database %s owner vrf_app template %s" % (name, template))
    for s in sqls:
        psql(name, s)
    (dump_z0 if z0 else base.dump)(name, name)


NONEMPTY = {
    # every item: ordinary user-visible object in a DB that must count as "not empty"
    "schema_only": ["create schema junk"],
    "table_other_schema": ["create schema junk", "create table junk.t(id int)"],
    "sequence": ["create sequence public.sq"],
    "type_enum": ["create type public.e as enum ('a')"],
    "domain": ["create domain public.d as int"],
    "function": ["create function public.f() returns int language sql as 'select 1'"],
    "aggregate": ["create function public.sf(int,int) returns int language sql as 'select $1+$2'",
                  "create aggregate public.ag(int) (sfunc=public.sf, stype=int)"],
    "extension": ["create extension pg_trgm"],
    "event_trigger": ["create function public.ef() returns event_trigger language plpgsql as 'begin end'",
                      "create event trigger et on ddl_command_start execute function public.ef()"],
    "large_object": ["select lo_create(0)"],
    "operator_builtin_func": ["create operator public.=== (leftarg=int, rightarg=int, function=int4eq)"],
    "collation": ["create collation public.cx (provider=libc, locale='C')"],
    "ts_config": ["create text search configuration public.tsc (copy=english)"],
    "ts_dictionary": ["create text search dictionary public.tsd (template=simple)"],
    "fdw": ["create foreign data wrapper fdwx"],
    "foreign_server_via_fdw": ["create foreign data wrapper fdwy", "create server srvy foreign data wrapper fdwy"],
    "publication": ["create publication pubx"],
    "cast_builtin": ["create domain public.d2 as int", "create cast (public.d2 as text) with inout"],
    "conversion": ["create conversion public.cvx for 'UTF8' to 'LATIN1' from utf8_to_iso8859_1"],
    "default_privileges": ["alter default privileges in schema public grant select on tables to public"],
    "comment_on_public_schema": ["comment on schema public is 'user comment'"],
    "drop_public_schema": ["drop schema public"],
    "table_public_control": ["create table public.x(id int)"],
    "view": ["create view public.v as select 1 as a"],
    "matview": ["create materialized view public.mv as select 1 as a"],
    "policy": ["create table public.pt(id int)", "alter table public.pt enable row level security",
               "create policy pp on public.pt using (true)"],
    "trigger": ["create table public.tt(id int)",
                "create function public.tf() returns trigger language plpgsql as 'begin return new; end'",
                "create trigger tg before insert on public.tt for each row execute function public.tf()"],
    "stats_object": ["create table public.st(a int,b int)", "create statistics public.ss on a,b from public.st"],
}


class Verifier10(unittest.TestCase):
    restore_inner = base.RealPg.restore
    archive = base.RealPg.archive
    derived = base.RealPg.derived
    put_archive_in_container = base.RealPg.put_archive_in_container
    tables_in_temp = base.RealPg.tables_in_temp
    live_writes = base.RealPg.live_writes

    def fresh_temp(self, harness, path):
        temp, oid = base.RealPg.fresh_temp(self, harness, path)
        for _ in range(50):
            if psql("postgres", "select count(*) from pg_stat_activity where datname='%s'" % temp) == "0":
                break
            time.sleep(0.2)
        return temp, oid

    def restore(self, *a, **k):
        err = io.StringIO()
        with redirect_stderr(err):
            r = self.restore_inner(*a, **k)
        r["err"] = err.getvalue()
        return r

    @classmethod
    def setUpClass(cls):
        if not base.docker_ok():
            raise unittest.SkipTest("docker/postgres:16 image unavailable")
        base.build_fixtures()
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="s12-r10-")
        cls.tmp = Path(cls.tmpdir.name).resolve()
        cls.archives = {}
        cls.build_variants()

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()
        if not os.environ.get("VRF_PG_KEEP"):
            subprocess.run([DOCKER, "rm", "-f", CONTAINER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def build_variants(cls):
        # --- leak fixtures: planted XXXXXXXXXX (valid in source) is later replaced by CANARYDATA in the archive
        raiser = ("create function public.rf(t text) returns text immutable language plpgsql as $$ begin "
                  "if t = 'CANARYDATA' then raise exception 'bad value %', t; end if; return t; end $$")
        variant("x_gencol2", "create table gc(id int primary key, t text)",
                "insert into gc values (1,'1234567890')",
                "alter table gc add column n int generated always as (t::int) stored")
        variant("x_raise_index", raiser, "create table ri(id int primary key, t text)",
                "insert into ri values (1,'%s')" % XXX, "create index ri_ix on ri(public.rf(t))")
        variant("x_raise_matview", raiser, "create table rm(id int primary key, t text)",
                "insert into rm values (1,'%s')" % XXX, "create materialized view rmv as select public.rf(t) as r from rm")
        variant("x_enum", "create type en as enum ('READY00001','OTHER00001')", "create table et(id int primary key, e en)",
                "insert into et values (1,'READY00001')")
        variant("x_json", "create table jt(id int primary key, j jsonb)", "insert into jt values (1,'{\"ab\":12345}')")
        variant("x_date", "create table dt(id int primary key, d date)", "insert into dt values (1,'2026-01-01')")
        variant("x_partition",
                "create table pp(k text, v int) partition by list (k)",
                "create table pp1 partition of pp for values in ('%s')" % XXX, "insert into pp values ('%s',1)" % XXX)
        variant("x_part_unattached", "create table pa(k text, v int) partition by list (k)",
                "create table pa1 partition of pa for values in ('AAAAAAAAAA')",
                "insert into pa values ('AAAAAAAAAA',1)")
        variant("x_notnull_text", "create table nn(id int primary key, c numeric(4,0))", "insert into nn values (1, 1234)")
        variant("x_exclusion", "create table ex(id int primary key, r text, exclude using btree (r with =))",
                "insert into ex values (1,'%s'),(2,'%s')" % (XXX, "YYYYYYYYYY"))
        variant("x_fk_deferred", "create table fp(p text primary key)",
                "create table fc(c text references fp(p) deferrable initially deferred)",
                "insert into fp values ('PARENT0001')", "insert into fc values ('PARENT0001')")
        variant("x_role_canary", "create role canaryrole_xxxxxx login", "create table rc(id int)",
                "alter table rc owner to canaryrole_xxxxxx", "grant select on rc to canaryrole_xxxxxx")
        # generic positive-ish: pristine pre-033 plus real-world extras
        variant("p_extras", "create schema reporting", "create table reporting.x(id int)",
                "create view public.some_view as select 1 as a", "create sequence public.some_seq",
                z0=False)
        # --- 033 name variants
        variant("n_renamed_keys_pkey", "create table public.regulatory_rule_manual_keys_old(id serial primary key)",
                "alter index public.regulatory_rule_manual_keys_old_pkey rename to regulatory_rule_manual_keys_pkey",
                z0=False)
        variant("n_type_only", "create type public.regulatory_rule_manual_keys as enum ('a')", z0=False)
        variant("n_view_with_revision", "create schema legacy",
                "create view legacy.regulatory_rules as select 1::bigint as revision", z0=False)
        variant("n_matview_manual_protected", "create schema legacy",
                "create materialized view legacy.regulatory_rules as select true as manual_protected", z0=False)
        variant("n_trigger_on_other_schema_table", "create schema legacy", "create table legacy.t(id int)",
                "create function legacy.tf() returns trigger language plpgsql as 'begin return new; end'",
                "create trigger zz_regulatory_rule_revision before update on legacy.t for each row "
                "execute function legacy.tf()", z0=False)
        variant("n_event_history_view", "create view public.regulatory_manual_event_history as select 1 as a", z0=False)
        variant("n_seq_named_033_index", "create sequence public.regulatory_manual_key_owner", z0=False)
        variant("n_proc_other_schema_diff_args", "create schema legacy",
                "create function legacy.update_regulatory_rule_revision(a text, b text) returns text "
                "language sql as 'select a'", z0=False)

    # ------------------------------------------------------------------ helpers
    def _pg_restores(self, r):
        return [a for a in r["h"].calls if any(x.endswith("/pg_restore") for x in a) and "--version" not in a]

    def _combined(self, r):
        return r["out"] + "\n" + r["err"]

    # ------------------------------------------------------------------ A. non-empty gate
    def test_a_nonempty_matrix_all_refused_before_write(self):
        seen, bad = {}, {}
        for name, sqls in NONEMPTY.items():
            def pre(temp, sqls=sqls):
                for s in sqls:
                    psql(temp, s)
            r = self.restore("pristine", pre=pre)
            wrote = bool(r["h"].restore_results)
            gate = [l for l in r["out"].splitlines() if l.startswith("DUNG")]
            seen[name] = (r["code"], wrote, gate)
            if wrote or r["code"] == 0:
                bad[name] = (r["code"], wrote, "COMPLETED" in r["out"])
        print("A. non-empty variants seen (code, pg_restore_ran, gate):")
        for k, v in seen.items():
            print("   ", k, v)
        print("A. BYPASSED the gate (pg_restore ran / exit 0):", bad)
        self.assertEqual(bad, {}, "pg_restore ran into a temp DB that was not empty")

    def test_a_config_only_changes_observed(self):
        """Not asserted: DB/schema level settings that are not objects. Records gate behaviour only."""
        cases = {
            "alter_database_set": 'alter database "%s" set work_mem=\'1MB\'',
            "schema_public_owner": "alter schema public owner to vrf_app",
            "schema_public_grant_public": "grant create on schema public to public",
        }
        seen = {}
        for name, sql in cases.items():
            def pre(temp, sql=sql):
                if "%s" in sql:
                    psql("postgres", sql % temp)
                else:
                    psql(temp, sql)
            r = self.restore("pristine", pre=pre)
            seen[name] = (r["code"], bool(r["h"].restore_results))
        print("A. config-only changes (code, pg_restore_ran):", seen)

    def test_a_positive_control_s11_created_db_passes_gate_and_restores(self):
        r = self.restore("pristine")
        self.assertEqual(r["code"], 0, r["out"])
        self.assertIn("PRERESTORE_READONLY_GATES_OK", r["out"])
        self.assertIn("temp_user_tables=0", r["out"])
        self.assertIn("STAGING_TEMP_RESTORE_COMPLETED", r["out"])
        self.assertEqual(len(self._pg_restores(r)), 1)

    def test_a_positive_control_precise_user_objects_zero_on_fresh_s11_db(self):
        h = base.Harness(self.tmp)
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        temp, oid = self.fresh_temp(h, path)
        n = psql(temp, "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                 "where n.nspname not in ('pg_catalog','information_schema') and n.nspname not like 'pg\\_toast%'")
        self.assertEqual(n, "0")

    # ------------------------------------------------------------------ B. leaks
    def _leak(self, label, name, edit=None, raw_edit=None, expect_hold=True, mutate_env=None):
        path = self.archive(name)
        if edit or raw_edit:
            raw = path.read_bytes()
            if raw_edit:
                raw = raw_edit(raw)
            else:
                old, new = edit
                self.assertEqual(len(old), len(new))
                self.assertGreaterEqual(raw.count(old), 1, "planted value missing in fixture")
                raw = raw.replace(old, new)
            path = self.derived(name + "_ed_" + label, raw)
        pre = mutate_env["pre"] if mutate_env else None
        r = self.restore(path, pre=pre)
        text = self._combined(r)
        lines = [l for l in text.splitlines() if l.startswith(("DUNG", "HOLD", "restore_error"))]
        print("LEAK[%s] code=%s lines=%r" % (label, r["code"], [l[:90] for l in lines]))
        self.assertEqual(r["code"], 1, text)
        self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", text)
        for needle in (CAN, "CANARY", "canaryrole", "canary", "pg_restore:", "ERROR:", "DETAIL:", "CONTEXT:",
                       "restore_error_first_line", "invalid input", "bad value", "violates", "psql:"):
            self.assertNotIn(needle, text, "tool text/data fragment %r reached script output" % needle)
        if expect_hold:
            self.assertIn("HOLD:", r["out"])
        self.assertEqual(len(self._pg_restores(r)), 1)
        self.assertEqual(self.tables_in_temp(r["temp"]), 0, "single-transaction rollback not observed")
        self.assertEqual(self.live_writes(), r["live_before"])
        return r

    def test_b_generated_column_input_error(self):
        # source valid '1234567890' (fits int); archive value replaced by non numeric canary
        self._leak("gencol", "x_gencol2", (b"1234567890", CAN.encode()))

    def test_b_raising_function_in_expression_index(self):
        self._leak("raise_index", "x_raise_index", (XXX.encode(), CAN.encode()))

    def test_b_raising_function_in_matview(self):
        self._leak("raise_matview", "x_raise_matview", (XXX.encode(), CAN.encode()))

    def test_b_enum_input_error(self):
        self._leak("enum", "x_enum", raw_edit=lambda raw: self._second(raw, b"READY00001", CAN.encode()))

    def test_b_json_input_error(self):
        self._leak("json", "x_json", (b'{"ab": 12345}', b"{CANARYDATAX}"))

    def test_b_date_input_error(self):
        self._leak("date", "x_date", (b"2026-01-01", CAN.encode()))

    def test_b_partition_routing_error(self):
        # value in the partition leaf no longer matches the partition bound once edited
        self._leak("partition", "x_partition", raw_edit=lambda raw: self._second(raw, XXX.encode(), CAN.encode()))

    def _second(self, raw, old, new):
        """replace only the LAST occurrence (the row data), keep the partition bound text intact"""
        i = raw.rindex(old)
        return raw[:i] + new + raw[i + len(old):]

    def test_b_numeric_overflow_error(self):
        self._leak("numeric", "x_notnull_text", (b"1234", b"CANA"))

    def test_b_exclusion_like_unique_violation(self):
        self._leak("excl", "x_exclusion", (b"YYYYYYYYYY", XXX.encode().replace(b"XXXXXXXXXX", b"XXXXXXXXXX")))
        # (the duplicate XXXXXXXXXX value now collides; value is a placeholder, not the canary)

    def test_b_unique_violation_with_canary_value(self):
        self._leak("excl_canary", "x_exclusion",
                   raw_edit=lambda raw: raw.replace(XXX.encode(), CAN.encode()).replace(b"YYYYYYYYYY", CAN.encode()))

    def test_b_deferred_fk_violation(self):
        raw = self.archive("x_fk_deferred").read_bytes()
        first = raw.index(b"PARENT0001")
        second = raw.index(b"PARENT0001", first + 1)
        self.assertGreater(second, first)
        self._leak("fk", "x_fk_deferred", raw_edit=lambda r: r[:second] + CAN.encode() + r[second + 10:])

    def test_b_missing_owner_role_name_not_leaked(self):
        def pre(temp):
            pass
        # role exists for the dump; drop it before the restore (harness executes pre AFTER fresh temp creation)
        psql("postgres", "drop owned by canaryrole_xxxxxx cascade", check=False)
        path = self.archive("x_role_canary")
        self.put_archive_in_container(path)
        psql("postgres", "drop database if exists x_role_canary with (force)")
        psql("postgres", "drop role if exists canaryrole_xxxxxx")
        r = self.restore(path)
        text = self._combined(r)
        print("LEAK[role] code=%s lines=%r" % (r["code"], [l[:100] for l in text.splitlines() if l.startswith(("DUNG", "restore_error"))]))
        self.assertEqual(r["code"], 1, text)
        self.assertNotIn("canaryrole", text.lower())
        self.assertNotIn("does not exist", text)
        self.assertNotIn("pg_restore:", text)

    def test_b_missing_extension_name_not_leaked(self):
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        sh(DOCKER, "exec", "-u", "root", CONTAINER, "sh", "-c",
           "mv /usr/share/postgresql/16/extension/pg_trgm.control /usr/share/postgresql/16/extension/pg_trgm.control.off")
        try:
            r = self.restore(path)
        finally:
            sh(DOCKER, "exec", "-u", "root", CONTAINER, "sh", "-c",
               "mv /usr/share/postgresql/16/extension/pg_trgm.control.off /usr/share/postgresql/16/extension/pg_trgm.control")
        text = self._combined(r)
        print("LEAK[ext] code=%s lines=%r" % (r["code"], [l[:100] for l in text.splitlines() if l.startswith(("DUNG", "restore_error"))]))
        self.assertEqual(r["code"], 1, text)
        self.assertNotIn("pg_trgm", text)
        self.assertNotIn("pg_restore:", text)
        self.assertIn("HOLD:", r["out"])
        self.assertEqual(self.tables_in_temp(r["temp"]), 0)

    def test_b_invalid_utf8_in_text_copy(self):
        # text column accepts every string but the bytes are not valid UTF-8 => COPY error, with context line
        self._leak("utf8", "x_date", (b"2026-01-01", b"CANA\xffRY\xfe\xfd1"))

    def test_b_truncated_archive_with_canary_row_inside(self):
        raw = self.archive("x_date").read_bytes()
        i = raw.index(b"2026-01-01")
        self._leak("trunc", "x_date", raw_edit=lambda r: r[: i + 4])

    def test_b_archive_header_corrupt(self):
        self._leak("hdr", "x_date", raw_edit=lambda r: b"PGDMX" + r[5:])

    # ------------------------------------------------------------------ C. 033 name variants
    def _held_pre033(self, name):
        r = self.restore(name)
        return r["code"] == 1 and "DUNG: TEMP_MUST_BE_PRE_033" in r["out"] and "STAGING_TEMP_RESTORE_COMPLETED" not in r["out"], r

    def test_c_033_name_variants_observed_and_asserted(self):
        seen = {}
        for name in ("n_renamed_keys_pkey", "n_type_only", "n_view_with_revision", "n_matview_manual_protected",
                     "n_trigger_on_other_schema_table", "n_event_history_view", "n_seq_named_033_index",
                     "n_proc_other_schema_diff_args"):
            ok, r = self._held_pre033(name)
            seen[name] = (r["code"], "COMPLETED" in r["out"])
        print("C. 033-named object variants (code, completed):", seen)
        # asserted: anything carrying one of the contract's named footprints (table/column/trigger/function/index
        # names) in ANY schema/kind must be held. Pure pkey-name / enum-type-name are NOT contract footprints.
        must_hold = ("n_view_with_revision", "n_matview_manual_protected", "n_trigger_on_other_schema_table",
                     "n_event_history_view", "n_seq_named_033_index", "n_proc_other_schema_diff_args")
        bad = {k: seen[k] for k in must_hold if seen[k] != (1, False)}
        self.assertEqual(bad, {}, "033-named footprint passed as pre-033")

    def test_c_positive_control_pre033_with_extras(self):
        r = self.restore("p_extras")
        self.assertEqual(r["code"], 0, r["out"])
        self.assertIn("STAGING_TEMP_RESTORE_COMPLETED", r["out"])
        self.assertIn("footprints_033=manual_keys_table=False", r["out"])

    # ------------------------------------------------------------------ D. safety regressions
    def test_d_single_restore_argv_whitelist_and_source_untouched(self):
        r = self.restore("pristine")
        self.assertEqual(r["code"], 0, r["out"])
        restores = self._pg_restores(r)
        self.assertEqual(len(restores), 1)
        argv = restores[0]
        i = [k for k, a in enumerate(argv) if a.endswith("/pg_restore")][0]
        opts = argv[i + 1:]
        self.assertEqual(opts, ["--exit-on-error", "--single-transaction", "-w", "-h", "/var/run/postgresql",
                                "-p", "5432", "-U", "postgres", "--dbname=" + r["temp"]])
        pre = argv[:i]
        self.assertEqual(pre[:5], ["sudo", "-n", "-u", "postgres", "env"])
        for a in argv:
            self.assertNotRegex(a, r"(?i)postgres(ql)?://|password|PGPASSWORD|DATABASE_URL")
        self.assertEqual(self.live_writes(), r["live_before"])
        # all other subprocess calls: psql read-only or version query, never a second writer
        for a in r["h"].calls:
            if a is argv:
                continue
            joined = " ".join(a)
            self.assertNotRegex(joined, r"createdb|dropdb|pg_dump|--clean|--create|systemctl\", \"(start|stop|restart)")
            if any(x.endswith("/psql") for x in a):
                self.assertIn("default_transaction_read_only=on", joined)

    def test_d_refusals_before_write(self):
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        h = base.Harness(self.tmp)
        temp, oid = self.fresh_temp(h, path)
        run = base.load("run_v10", "scripts/staging_restore_run_temp.py")
        # wrong OID
        code, out, exc = h.run_module(run, "--restore", archive=path, oid=oid + 7)
        self.assertEqual((code, h.restore_results), (1, []))
        self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", out)
        # tampered label
        psql("postgres", "comment on database \"%s\" is 'x'" % temp)
        code, out, exc = h.run_module(run, "--restore", archive=path, oid=oid)
        self.assertEqual((code, h.restore_results), (1, []))
        self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", out)
        self.assertEqual(self.tables_in_temp(temp), 0)

    def test_d_public_connect_granted_refused(self):
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        h = base.Harness(self.tmp)
        temp, oid = self.fresh_temp(h, path)
        psql("postgres", 'grant connect on database "%s" to public' % temp)
        run = base.load("run_v10b", "scripts/staging_restore_run_temp.py")
        code, out, exc = h.run_module(run, "--restore", archive=path, oid=oid)
        self.assertEqual((code, h.restore_results), (1, []))
        self.assertIn("TEMP_DATABASE_IDENTITY", out)


if __name__ == "__main__":
    unittest.main()
