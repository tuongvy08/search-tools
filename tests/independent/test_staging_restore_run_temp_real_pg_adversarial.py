"""Independent adversarial verification of scripts/staging_restore_run_temp.py (S1.2) against a
REAL PostgreSQL 16.15 server + real pg_restore/psql/createdb 16.15 binaries running in a
disposable Docker container (--network none, fake data built from the repo's own sql/ migrations).

What is real: PostgreSQL server, pg_dump/pg_restore/psql/createdb argv parsing and behaviour, the
S1.1 create script's real main(), the S1.2 restore script's real main(), its real SQL gates, and
real custom-format archives (stdin as a regular file AND as a pipe).
What is shimmed (cannot exist on a Mac/Docker test bed): sudo/env prefix (translated to
`docker exec -u postgres`), systemctl/git/ss runtime gates (runtime_guard patched, ss faked from a
fixture postmaster.pid), data directory / free-space path (fixture dir), pinned archive path/hash/
size/mtime and the pinned OID (patched to the fixture values), `Debian` -> `Ubuntu` in version text.
Skipped automatically when Docker or the postgres:16 image is unavailable.
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker") or "/usr/local/bin/docker"
IMAGE = "postgres:16"
CONTAINER = os.environ.get("VRF_PG_CONTAINER", "vrf-pg16-s12")
KEEP = bool(os.environ.get("VRF_PG_KEEP"))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(*args, stdin=None, check=True, timeout=300):
    r = subprocess.run(list(args), stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError("cmd failed: %s\n%s" % (args[:6], r.stderr[-800:]))
    return r


def psql(db, *sqls, user="postgres", check=True, tuples=True):
    cmd = [DOCKER, "exec", "-i", "-u", user, CONTAINER, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", db]
    if tuples:
        cmd += ["-A", "-t"]
    for s in sqls:
        cmd += ["-c", s]
    return sh(*cmd, check=check).stdout.strip()


def docker_ok():
    try:
        r = subprocess.run([DOCKER, "image", "inspect", IMAGE], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def apply_sql(db, path, role="vrf_app"):
    with open(path, "rb") as fh:
        r = subprocess.run([DOCKER, "exec", "-i", "-u", "postgres", CONTAINER, "psql", "-X", "-d", db,
                            "-v", "ON_ERROR_STOP=1", "-q", "-c", "set role " + role, "-f", "-"],
                           stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError("apply %s: %s" % (path, r.stderr[-500:]))


def dump(db, name):
    sh(DOCKER, "exec", "-u", "postgres", CONTAINER, "sh", "-c",
       "rm -f /tmp/%s.dump && pg_dump -Fc -d %s -f /tmp/%s.dump && chmod 644 /tmp/%s.dump" % (name, db, name, name))


def build_fixtures():
    """Idempotent. Fake data only. Builds pre-033 / 033 / mutated databases and archives."""
    exists = subprocess.run([DOCKER, "ps", "-a", "--filter", "name=^%s$" % CONTAINER, "--format", "{{.Names}}"],
                            stdout=subprocess.PIPE, text=True).stdout.strip()
    if not exists:
        sh(DOCKER, "run", "-d", "--name", CONTAINER, "--network", "none", "-e", "POSTGRES_PASSWORD=x",
           "-e", "POSTGRES_HOST_AUTH_METHOD=trust", IMAGE)
    else:
        sh(DOCKER, "start", CONTAINER, check=False)
    for _ in range(60):
        r = subprocess.run([DOCKER, "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            time.sleep(2)
            r2 = subprocess.run([DOCKER, "exec", CONTAINER, "psql", "-U", "postgres", "-Atc", "select 1"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r2.returncode == 0:
                break
        time.sleep(1)
    if psql("postgres", "select 1 from pg_database where datname='vrf_fixtures_done'"):
        return
    psql("postgres", "create role vrf_app login")
    psql("postgres", "create database prechange_src owner vrf_app")
    psql("postgres", "create database search_tools_staging owner vrf_app encoding 'UTF8' "
         "lc_collate 'en_US.UTF-8' lc_ctype 'en_US.UTF-8' template template0")
    sqls = [ROOT / "sql" / "schema.sql"] + sorted((ROOT / "sql").glob("migration_0[0-2][0-9]_*.sql")) \
        + sorted((ROOT / "sql").glob("migration_03[0-2]_*.sql"))
    sqls = [p for p in sqls if not p.name.startswith("migration_001")]
    for db in ("prechange_src", "search_tools_staging"):
        for p in sqls:
            apply_sql(db, p)
    apply_sql("search_tools_staging", ROOT / "sql" / "migration_033_regulatory_manual_edit.sql")
    seed = """set role vrf_app;
insert into products(name,code,cas,brand,size,ship,price,note,source_brand)
  select 'n'||g,'c'||g,'cas'||g,'A2S','1g','s','1','x','A2S' from generate_series(1,5000) g;
insert into stock_snapshots(id,source_kind,actor,row_count,content_sha256)
  values ('00000000-0000-0000-0000-000000000001','MANUAL','t',3,'h');
insert into stock_items(snapshot_id,name,code,brand,size,quantity,brand_norm,code_norm,size_norm)
  select '00000000-0000-0000-0000-000000000001','n'||g,'c'||g,'A2S','1g',1,'a2s','c'||g,'1g' from generate_series(1,30) g;
insert into regulatory_rules(rule_type,rule_label,match_field,match_value,is_active,status_id)
  select 't','l','cas','v'||g, g%7<>0, (select min(id) from regulatory_statuses) from generate_series(1,60) g;"""
    for db in ("prechange_src",):
        psql(db, seed)
    # live source: same data on top of 033 schema (revision/manual_protected have defaults)
    psql("search_tools_staging", seed)
    dump("prechange_src", "pristine")
    dump("search_tools_staging", "post033")
    # mutated copies (template copy needs no sessions)
    def variant(name, *sqls):
        psql("postgres", "create database %s owner vrf_app template prechange_src" % name)
        for s in sqls:
            psql(name, s)
        dump(name, name)
    variant("v_nullable_rev", "alter table regulatory_rules add column revision bigint")
    variant("v_func_only", "create function public.update_regulatory_rule_revision() returns trigger "
            "language plpgsql as $$ begin return new; end $$")
    variant("v_keys_view", "create view public.regulatory_rule_manual_keys as select 1 as id")
    variant("v_wrong_trigger", "create function public.zz_f() returns trigger language plpgsql as $$ begin return new; end $$",
            "create trigger zz_regulatory_rule_revision after insert on public.regulatory_rules "
            "for each row execute function public.zz_f()")
    variant("v_empty_products", "truncate products cascade")
    variant("v_no_statuses", "drop table regulatory_statuses cascade")
    variant("v_no_030", "drop table admin_menu_grants cascade")
    variant("v_no_032", "drop table stock_manual_requests cascade")
    # heavy archive for the timeout experiment
    variant("v_heavy", "create table big as select g as id, md5(g::text) as t from generate_series(1,2500000) g",
            "create index big_t on big(t)")
    # ghost-owner archive (owner role will not exist at restore time)
    psql("postgres", "create role vrf_ghost login")
    psql("postgres", "create database ghost_src owner vrf_ghost template prechange_src")
    psql("ghost_src", "reassign owned by vrf_app to vrf_ghost")  # cluster-wide: also moved database owners
    dump("ghost_src", "ghost")
    for other in psql("postgres", "select datname from pg_database where datdba=(select oid from pg_roles "
                      "where rolname='vrf_ghost') and datname<>'ghost_src'").splitlines():
        psql("postgres", 'alter database "%s" owner to vrf_app' % other)
    psql("postgres", "drop database ghost_src")
    psql("postgres", "drop role vrf_ghost")
    psql("postgres", "create database vrf_fixtures_done")


class Harness:
    """Runs the real S1.1/S1.2 main() against the container through a translating subprocess.run."""

    def __init__(self, tmp):
        self.tmp = Path(tmp).resolve()
        self.data = self.tmp / "data"
        (self.data / "base").mkdir(parents=True, exist_ok=True)
        (self.data / "postmaster.pid").write_bytes(b"4242\nfixture\n")
        self.real_run = subprocess.run
        self.calls = []
        self.restore_results = []
        self.mode = "file"

    def translate(self, args, kw):
        args = list(args)
        self.calls.append(list(args))
        if args[0] == "/usr/bin/ss":
            return subprocess.CompletedProcess(
                args, 0, 'LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=4242,fd=7))\n', "")
        user = []
        rest = args
        if args[:5] == ["sudo", "-n", "-u", "postgres", "env"]:
            user = ["-u", "postgres"]
            rest = args[4:]
        elif args[0].startswith("/usr/lib/postgresql/16/bin/") or args[0] == "/usr/bin/locale":
            rest = args
        else:
            raise AssertionError("unexpected executable: %r" % (args[:3],))
        is_restore = any(a.endswith("/pg_restore") for a in rest[:12]) and "--version" not in rest
        cmd = [DOCKER, "exec"] + user
        stdin = kw.get("stdin")
        if is_restore and self.mode == "file":
            cmd += [CONTAINER, "sh", "-c", 'exec "$@" < /tmp/archive_in_use.dump', "sh"] + rest
            stdin = None
        elif is_restore:
            cmd += ["-i", CONTAINER] + rest
        else:
            cmd += [CONTAINER] + rest
        res = self.real_run(cmd, env=os.environ.copy(), stdin=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=kw.get("timeout"))
        out = res.stdout.replace("(Debian", "(Ubuntu")
        res = subprocess.CompletedProcess(args, res.returncode, out, res.stderr)
        if is_restore:
            self.restore_results.append((res.returncode, res.stdout, res.stderr))
        return res

    def run_module(self, mod, flag, archive=None, oid=None, patches=(), stdout_obj=None, timeout=None):
        """patches: list of (obj, attr, value)."""
        if archive is not None:
            st = archive.stat()
            mod.ARCHIVE, mod.ARCHIVE_BYTES = archive, st.st_size
            mod.ARCHIVE_SHA = hashlib.sha256(archive.read_bytes()).hexdigest()
            mod.ARCHIVE_MTIME_NS = st.st_mtime_ns
            mod.LABEL = "staging-restore-rehearsal|snapshot=20261001T064530Z|archive=" + mod.ARCHIVE_SHA
        out = stdout_obj or io.StringIO()
        harness = self
        data_for_source = Path("/var/lib/postgresql/data")
        orig_instance = mod.instance_guard

        def instance_guard():
            keep = mod.DATA
            mod.DATA = harness.data
            try:
                return orig_instance()
            finally:
                mod.DATA = keep

        with ExitStack() as st:
            st.enter_context(patch.object(mod.os, "geteuid", return_value=0))
            st.enter_context(patch.object(mod.sys, "argv", ["-", flag]))
            st.enter_context(patch.object(mod, "runtime_guard"))
            st.enter_context(patch.object(mod, "DATA", data_for_source))
            st.enter_context(patch.object(mod, "BASE", self.data / "base"))
            st.enter_context(patch.object(mod, "instance_guard", instance_guard))
            st.enter_context(patch.object(mod.subprocess, "run", side_effect=lambda a, **k: self.translate(a, k)))
            if oid is not None:
                st.enter_context(patch.object(mod, "TEMP_OID", oid))
            if timeout is not None:
                st.enter_context(patch.object(mod, "RESTORE_TIMEOUT", timeout))
            for obj, attr, val in patches:
                st.enter_context(patch.object(obj, attr, val))
            st.enter_context(redirect_stdout(out))
            code = None
            exc = None
            try:
                code = mod.main()
            except BaseException as e:  # unhandled exception escaping main()
                exc = e
        return code, out.getvalue() if hasattr(out, "getvalue") else "", exc


class RealPg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not docker_ok():
            raise unittest.SkipTest("docker/postgres:16 image unavailable")
        build_fixtures()
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="s12-real-")
        cls.tmp = Path(cls.tmpdir.name).resolve()
        cls.archives = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()
        if not KEEP and not os.environ.get("VRF_PG_CONTAINER"):
            subprocess.run([DOCKER, "rm", "-f", CONTAINER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- helpers
    def archive(self, name):
        if name not in self.archives:
            dst = self.tmp / (name + ".dump")
            sh(DOCKER, "cp", "%s:/tmp/%s.dump" % (CONTAINER, name), str(dst))
            os.utime(dst, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))
            self.archives[name] = dst
        return self.archives[name]

    def derived(self, name, data):
        dst = self.tmp / (name + ".dump")
        dst.write_bytes(data)
        os.utime(dst, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))
        return dst

    def put_archive_in_container(self, path):
        sh(DOCKER, "cp", str(path), CONTAINER + ":/tmp/archive_in_use.dump")
        sh(DOCKER, "exec", "-u", "root", CONTAINER, "chmod", "644", "/tmp/archive_in_use.dump")

    def fresh_temp(self, harness, path):
        """Create the empty temp DB through the REAL S1.1 script (integration of S1.1 -> S1.2)."""
        create = load("create_mod", "scripts/staging_restore_create_temp.py")
        psql("postgres", 'drop database if exists "%s" with (force)' % create.TEMP)  # harness-side hygiene only
        code, out, exc = harness.run_module(create, "--create", archive=path)
        self.assertEqual(code, 0, out)
        self.assertIn("STAGING_TEMP_DATABASE_CREATED", out)
        oid = int(psql("postgres", "select oid from pg_database where datname='%s'" % create.TEMP))
        return create.TEMP, oid

    def tables_in_temp(self, temp):
        return int(psql(temp, "select count(*) from pg_class where relnamespace='public'::regnamespace "
                        "and relkind in ('r','p','v','m','f')"))

    def live_writes(self):
        return psql("postgres", "select tup_inserted, tup_updated, tup_deleted from pg_stat_database "
                    "where datname in ('search_tools_staging')")

    def restore(self, name_or_path, mode="file", flag="--restore", tweak=None, patches=(), timeout=None,
                stdout_obj=None, pre=None):
        path = self.archive(name_or_path) if isinstance(name_or_path, str) else name_or_path
        self.put_archive_in_container(path)
        h = Harness(self.tmp)
        temp, oid = self.fresh_temp(h, path)
        if pre:
            pre(temp)
        h.calls.clear()
        h.restore_results.clear()
        h.mode = mode
        run = load("run_mod", "scripts/staging_restore_run_temp.py")
        self.assertEqual(run.TEMP, temp)
        before = self.live_writes()
        code, out, exc = h.run_module(run, flag, archive=path, oid=oid, patches=patches, timeout=timeout,
                                      stdout_obj=stdout_obj)
        return dict(h=h, run=run, temp=temp, code=code, out=out, exc=exc, live_before=before)

    # ---- V2/V3 happy path against the real binaries
    def _happy(self, mode):
        r = self.restore("pristine", mode=mode)
        self.assertEqual(r["code"], 0, r["out"])
        self.assertIn("STAGING_TEMP_RESTORE_COMPLETED", r["out"])
        self.assertNotIn("HOLD", r["out"])
        self.assertIn("temp_count_products=5000", r["out"])
        self.assertIn("temp_count_stock_items=30", r["out"])
        self.assertIn("temp_count_regulatory_rules=60", r["out"])
        self.assertIn("temp_count_regulatory_statuses=6", r["out"])
        self.assertIn("temp_count_regulatory_rules_inactive=8", r["out"])
        # real pg_restore produced EMPTY stdout/stderr and exit 0 (observed)
        self.assertEqual(r["h"].restore_results, [(0, "", "")], r["h"].restore_results)
        # restored schema/data identical to the dump source
        src = psql("prechange_src", "select md5(string_agg(t,'|' order by t)) from "
                   "(select (p.*)::text t from products p) s")
        got = psql(r["temp"], "select md5(string_agg(t,'|' order by t)) from "
                   "(select (p.*)::text t from products p) s")
        self.assertEqual(src, got)
        self.assertEqual(self.live_writes(), r["live_before"])
        return r

    def test_happy_restore_archive_as_regular_file_on_stdin(self):
        self._happy("file")

    def test_happy_restore_archive_as_pipe_on_stdin(self):
        self._happy("pipe")

    def test_restore_argv_is_exactly_one_pgrestore_to_temp_only(self):
        r = self.restore("pristine")
        restores = [a for a in r["h"].calls if any(x.endswith("/pg_restore") for x in a) and "--version" not in a]
        self.assertEqual(len(restores), 1)
        joined = " ".join(restores[0])
        self.assertIn("--dbname=" + r["temp"], joined)
        self.assertNotIn("search_tools_staging", joined)
        self.assertNotIn("--clean", joined)
        self.assertNotIn("--create", joined)
        # every psql the script ran after the restore started targets the temp DB or live DB read-only
        for a in r["h"].calls:
            if any(x.endswith("/psql") for x in a):
                opts = [x for x in a if x.startswith("PGOPTIONS=")][0]
                db = a[a.index("-d") + 1]
                self.assertIn("default_transaction_read_only=on", opts, a)
                self.assertIn(db, (r["temp"], "search_tools_staging"))

    # ---- failure modes: restore error => HOLD, nothing cleaned, no retry, temp rolled back
    def _assert_hold(self, r, gate=None, populated=None):
        self.assertEqual(r["code"], 1, r["out"])
        self.assertIsNone(r["exc"])
        self.assertIn("HOLD:", r["out"])
        self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", r["out"])
        if gate:
            self.assertIn("DUNG: " + gate, r["out"])
        # never retried, never dropped
        restores = [a for a in r["h"].calls if any(x.endswith("/pg_restore") for x in a) and "--version" not in a]
        self.assertEqual(len(restores), 1)
        self.assertEqual(psql("postgres", "select count(*) from pg_database where datname='%s'" % r["temp"]), "1")
        self.assertEqual(self.live_writes(), r["live_before"])
        if populated is not None:
            self.assertEqual(self.tables_in_temp(r["temp"]) > 0, populated)

    def test_owner_role_missing_fails_closed_and_rolls_back(self):
        r = self.restore("ghost")
        self._assert_hold(r, "TEMP_RESTORE_COMMAND", populated=False)
        # Only a fixed label may be shown; raw tool text (role names, data) must never appear.
        self.assertIn("restore_error_category=ROLE_MISSING", r["out"])
        self.assertNotIn("vrf_ghost", r["out"])
        self.assertNotIn("pg_restore:", r["out"])

    def test_truncated_archive_fails_closed_and_rolls_back(self):
        full = self.archive("pristine").read_bytes()
        r = self.restore(self.derived("trunc", full[: len(full) // 2]), mode="file")
        self._assert_hold(r, populated=False)

    def test_corrupted_middle_fails_closed_or_is_detected(self):
        full = bytearray(self.archive("pristine").read_bytes())
        for i in range(len(full) // 2, len(full) // 2 + 4000):
            full[i] ^= 0xFF
        r = self.restore(self.derived("corrupt", bytes(full)), mode="file")
        self._assert_hold(r, populated=False)

    def test_archive_with_033_footprint_is_held_and_not_dropped(self):
        r = self.restore("post033")
        self._assert_hold(r, "TEMP_MUST_PRE_033".replace("PRE_033", "BE_PRE_033"), populated=True)

    def test_main_table_empty_is_held(self):
        r = self.restore("v_empty_products")
        self._assert_hold(r, "TEMP_MAIN_TABLES_PRESENT_NONEMPTY", populated=True)

    def test_main_table_missing_is_held(self):
        r = self.restore("v_no_statuses")
        self._assert_hold(r, "TEMP_RESTORED_READ", populated=True)

    def test_missing_030_footprint_is_held(self):
        r = self.restore("v_no_030")
        self._assert_hold(r, "TEMP_MISSING_030_032", populated=True)

    def test_missing_032_footprint_is_held(self):
        r = self.restore("v_no_032")
        self._assert_hold(r, "TEMP_MISSING_030_032", populated=True)

    # ---- V5 detector: partial 033 residue with the exact 033 NAMES but a different SHAPE
    def _shape_case(self, name):
        r = self.restore(name)
        return r

    def test_v5_partial_033_residue_detected_when_shape_exact(self):
        r = self.restore("v_func_only")
        self._assert_hold(r, "TEMP_MUST_BE_PRE_033", populated=True)

    def test_v5_same_name_different_shape_033_residue_must_be_held(self):
        """V5 oracle: every 033-specific footprint must be ABSENT. These DBs contain a 033-named object
        (revision column / manual_keys relation / zz_regulatory_rule_revision trigger) with a shape the
        script's strict flags do not match. Expected: HOLD. Observed (see FAIL output): marker + exit 0."""
        seen = {}
        for name in ("v_nullable_rev", "v_keys_view", "v_wrong_trigger"):
            r = self.restore(name)
            seen[name] = (r["code"], "STAGING_TEMP_RESTORE_COMPLETED" in r["out"])
        print("V5 shape-mismatch residue observed (code, completed_marker):", seen)
        self.assertEqual(seen, {k: (1, False) for k in seen}, "033-named residue was reported as pre-033 PASS")

    # ---- gates before the write
    def test_second_run_after_success_refuses_before_any_write(self):
        r = self.restore("pristine")
        self.assertEqual(r["code"], 0)
        run = load("run_mod2", "scripts/staging_restore_run_temp.py")
        h = r["h"]
        h.calls.clear()
        h.restore_results.clear()
        oid = int(psql("postgres", "select oid from pg_database where datname='%s'" % r["temp"]))
        code, out, exc = h.run_module(run, "--restore", archive=self.archive("pristine"), oid=oid)
        self.assertEqual(code, 1)
        self.assertIn("DUNG: TEMP_DATABASE_MUST_BE_EMPTY", out)
        self.assertNotIn("HOLD", out)
        self.assertEqual(h.restore_results, [])

    def test_temp_with_open_session_refuses(self):
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        h = Harness(self.tmp)
        temp, oid = self.fresh_temp(h, path)
        bg = subprocess.Popen([DOCKER, "exec", "-u", "postgres", CONTAINER, "psql", "-X", "-d", temp, "-c",
                               "select pg_sleep(20)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.5)
            run = load("run_mod3", "scripts/staging_restore_run_temp.py")
            code, out, exc = h.run_module(run, "--restore", archive=path, oid=oid)
        finally:
            bg.kill()
        self.assertEqual(code, 1)
        self.assertIn("DUNG: TEMP_DATABASE_HAS_SESSIONS", out)
        self.assertEqual([x for x in h.restore_results], [])

    def test_wrong_pinned_oid_refuses(self):
        path = self.archive("pristine")
        self.put_archive_in_container(path)
        h = Harness(self.tmp)
        temp, oid = self.fresh_temp(h, path)
        run = load("run_mod4", "scripts/staging_restore_run_temp.py")
        code, out, exc = h.run_module(run, "--restore", archive=path, oid=oid + 1)
        self.assertEqual(code, 1)
        self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", out)
        self.assertEqual(h.restore_results, [])
        self.assertEqual(self.tables_in_temp(temp), 0)

    def test_missing_temp_refuses_and_never_creates(self):
        path = self.archive("pristine")
        h = Harness(self.tmp)
        run = load("run_mod5", "scripts/staging_restore_run_temp.py")
        psql("postgres", 'drop database if exists "%s" with (force)' % run.TEMP)
        code, out, exc = h.run_module(run, "--restore", archive=path, oid=1)
        self.assertEqual(code, 1)
        self.assertIn("DUNG: TEMP_DATABASE_MUST_EXIST", out)
        self.assertEqual(psql("postgres", "select count(*) from pg_database where datname='%s'" % run.TEMP), "0")

    def test_no_flag_or_wrong_flag_refuses(self):
        for flag in ("--create", ""):
            run = load("run_mod6", "scripts/staging_restore_run_temp.py")
            h = Harness(self.tmp)
            code, out, exc = h.run_module(run, flag, archive=self.archive("pristine"), oid=1)
            self.assertEqual(code, 1)
            self.assertIn("ROOT_EXPLICIT_RESTORE_ONLY", out)
            self.assertEqual(h.calls, [])

    # ---- timeout / lost connection
    def test_timeout_then_orphan_pgrestore_keeps_running_and_commits_after_hold(self):
        """subprocess.run(timeout) kills only its direct child (docker-exec client here, `sudo` on the
        server). Real pg_restore in the container keeps running and later COMMITS into the temp DB
        although the script already printed DUNG/HOLD."""
        r = self.restore("v_heavy", timeout=1.0)
        self.assertEqual(r["code"], 1)
        self.assertIn("DUNG: TEMP_RESTORE_COMMAND_TIMEOUT", r["out"])
        self.assertIn("HOLD:", r["out"])
        at_hold = self.tables_in_temp(r["temp"])
        deadline = time.time() + 120
        later = at_hold
        while time.time() < deadline and later == at_hold:
            time.sleep(2)
            later = self.tables_in_temp(r["temp"])
        print("PROBE timeout: tables at HOLD=%d, tables later=%d" % (at_hold, later))
        self.assertEqual(at_hold, 0)
        self.assertGreater(later, 0, "restore did not finish after the script reported HOLD (not observed)")

    def test_stdout_broken_after_restore_started(self):
        class Breaking(io.StringIO):
            def __init__(self):
                super().__init__()
                self.n = 0

            def write(self, s):
                if "restore_finished_at_utc" in s:
                    raise BrokenPipeError(32, "Broken pipe")
                return super().write(s)

        r = self.restore("pristine", stdout_obj=Breaking())
        print("PROBE broken stdout: code=%r exc=%r tables=%d" % (r["code"], r["exc"], self.tables_in_temp(r["temp"])))
        self.assertGreater(self.tables_in_temp(r["temp"]), 0)

    # ---- V4/V5 query parity with the locked SOURCE_CHECK.sql, executed on real PG
    def test_footprint_queries_equal_source_check_sql_on_real_databases(self):
        run = load("run_parity", "scripts/staging_restore_run_temp.py")
        text = (ROOT / "specs/staging-restore-rehearsal/SOURCE_CHECK.sql").read_text()
        stmts = [x.strip() for x in text.split(";") if "footprint_030" in x or "manual_keys_table" in x]
        self.assertEqual(len(stmts), 2)
        names = list(run.FLAGS_030_032) + list(run.FLAGS_033)
        exprs = dict(run.footprints_030_032_sql())
        exprs.update(run.flag_sql())
        mine = "SELECT " + ", ".join("(%s) AS %s" % (exprs[n], n) for n in names)

        def row(db, sql):
            out = psql(db, sql, tuples=False).splitlines()  # aligned table; use csv instead
            return out

        def csv(db, sql):
            r = sh(DOCKER, "exec", "-u", "postgres", CONTAINER, "psql", "-X", "-d", db, "--csv", "-c", sql)
            lines = r.stdout.strip().splitlines()
            return dict(zip(lines[0].split(","), lines[1].split(",")))

        for db in ("prechange_src", "search_tools_staging", "v_nullable_rev", "v_func_only", "v_keys_view",
                   "v_wrong_trigger", "v_no_030", "v_no_032"):
            ref = {}
            for st in stmts:
                ref.update(csv(db, st))
            self.assertEqual(csv(db, mine), ref, db)

    def test_static_no_drop_or_cleanup_or_other_writes_in_restore_script(self):
        src = (ROOT / "scripts/staging_restore_run_temp.py").read_text()
        for bad in ("dropdb", "DROP ", "drop ", "--clean", "--create", "terminate", "pg_dump", "createdb",
                    "systemctl\", \"start", "restart", "--force", "FORCE"):
            self.assertNotIn(bad, src, bad)

    def test_sigkill_of_direct_child_leaves_grandchild_running_like_sudo(self):
        """Generic POSIX fork-wait wrapper (stand-in for sudo): killing only the wrapper, as
        subprocess.run(timeout) does, leaves the grandchild running. Shows why a timed-out
        `sudo ... pg_restore` can finish later. Waits until the grandchild has really started
        before the kill, so machine load cannot make the demonstration flaky."""
        d = Path(self.tmp)
        started = d / "grandchild_started"
        marker = d / "grandchild_done"
        for path in (started, marker):
            if path.exists():
                path.unlink()
        wrapper = d / "fakesudo.sh"
        wrapper.write_text('#!/bin/sh\n"$@" &\nwait\n')
        wrapper.chmod(0o755)
        proc = subprocess.Popen([str(wrapper), "/bin/sh", "-c", "touch %s; sleep 3; touch %s" % (started, marker)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.1)
        self.assertTrue(started.exists(), "grandchild never started")
        proc.kill()  # what subprocess.run does on timeout: only the direct child is killed
        proc.wait(timeout=10)
        self.assertFalse(marker.exists())
        for _ in range(60):
            if marker.exists():
                break
            time.sleep(0.5)
        self.assertTrue(marker.exists())

if __name__ == "__main__":
    unittest.main()
