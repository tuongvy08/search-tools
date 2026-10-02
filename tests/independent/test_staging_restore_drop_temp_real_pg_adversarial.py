"""Independent adversarial verification of scripts/staging_restore_drop_temp.py (S3, claim V6) against a
REAL PostgreSQL 16.15 server + real dropdb/psql 16.15 binaries in a disposable Docker container
(--network none, fake data built from the repo's own sql/ migrations, log_statement=all).

Real: server, dropdb/psql argv parsing + behaviour, PGOPTIONS, the script's real main(), real SQL gates, real
runtime_guard (fixture /proc, fake systemctl/git), real archive_guard (fixture file), real instance_guard
(fixture postmaster.pid). Shimmed: `sudo -n -u postgres` -> `docker exec -u postgres`, `ss` fixture, systemctl/git
text, data dir path, pinned archive size/hash/mtime + OID, `Debian` -> `Ubuntu` in version text.
Skipped automatically when Docker or the postgres:16 image is unavailable.
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
import pathlib
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_RUN = subprocess.run
ROOT = Path(__file__).resolve().parents[2]
DOCKER = shutil.which("docker") or "/usr/local/bin/docker"
IMAGE = "postgres:16"
CONTAINER = os.environ.get("VRF_DROP_CONTAINER", "vrf-pg16-s3drop")
KEEP = bool(os.environ.get("VRF_PG_KEEP"))
CANARY = "FAKE_SECRET_CANARY_s3"
KEEP_DBS = {"postgres", "template0", "template1", "search_tools_staging", "vrf_fixtures_done"}
PG = "/usr/lib/postgresql/16/bin/"


def load(name="drop_mod"):
    spec = importlib.util.spec_from_file_location(
        name, os.environ.get("VRF_DROP_SCRIPT") or ROOT / "scripts" / "staging_restore_drop_temp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sh(*args, check=True, timeout=300, stdin=None):
    r = _RUN(list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
                       stdin=stdin)
    if check and r.returncode:
        raise RuntimeError("cmd failed: %s\n%s" % (args[:6], r.stderr[-800:]))
    return r


def psql(db, *sqls, user="postgres", check=True):
    cmd = [DOCKER, "exec", "-i", "-u", user, "-e", "PGAPPNAME=harness", CONTAINER, "psql", "-X",
           "-v", "ON_ERROR_STOP=1", "-d", db, "-A", "-t"]
    for s in sqls:
        cmd += ["-c", s]
    r = sh(*cmd, check=check)
    return r.stdout.strip()


def docker_ok():
    try:
        return _RUN([DOCKER, "image", "inspect", IMAGE], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30).returncode == 0
    except Exception:
        return False


def apply_sql(db, path):
    with open(path, "rb") as fh:
        r = _RUN([DOCKER, "exec", "-i", "-u", "postgres", CONTAINER, "psql", "-X", "-d", db,
                            "-v", "ON_ERROR_STOP=1", "-q", "-c", "set role vrf_app", "-f", "-"],
                           stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        raise RuntimeError("apply %s: %s" % (path, r.stderr[-500:]))


def build_container():
    exists = _RUN([DOCKER, "ps", "-a", "--filter", "name=^%s$" % CONTAINER, "--format", "{{.Names}}"],
                            stdout=subprocess.PIPE, text=True).stdout.strip()
    if not exists:
        sh(DOCKER, "run", "-d", "--name", CONTAINER, "--network", "none", "-e", "POSTGRES_PASSWORD=x",
           "-e", "POSTGRES_HOST_AUTH_METHOD=trust", IMAGE, "-c", "log_statement=all",
           "-c", "log_line_prefix=%d|%a|%u ")
    else:
        sh(DOCKER, "start", CONTAINER, check=False)
    for _ in range(90):
        r = _RUN([DOCKER, "exec", CONTAINER, "psql", "-U", "postgres", "-Atc", "select 1"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            time.sleep(1)
            if _RUN([DOCKER, "exec", CONTAINER, "psql", "-U", "postgres", "-Atc", "select 1"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0:
                break
        time.sleep(1)
    if psql("postgres", "select 1 from pg_database where datname='vrf_fixtures_done'"):
        return
    psql("postgres", "create role vrf_app login")
    psql("postgres", "create database search_tools_staging owner vrf_app encoding 'UTF8' "
         "lc_collate 'en_US.UTF-8' lc_ctype 'en_US.UTF-8' template template0")
    sqls = [ROOT / "sql" / "schema.sql"] + sorted((ROOT / "sql").glob("migration_0[0-2][0-9]_*.sql")) \
        + sorted((ROOT / "sql").glob("migration_03[0-2]_*.sql"))
    sqls = [p for p in sqls if not p.name.startswith("migration_001")]
    for p in sqls:
        apply_sql("search_tools_staging", p)
    apply_sql("search_tools_staging", ROOT / "sql" / "migration_033_regulatory_manual_edit.sql")
    psql("search_tools_staging", "set role vrf_app; insert into products(name,code,cas,brand,size,ship,price,note,source_brand)"
         " select 'n'||g,'c'||g,'cas'||g,'A2S','1g','s','1','x','A2S' from generate_series(1,500) g")
    psql("postgres", "create database vrf_fixtures_done")


def log_lines():
    r = _RUN([DOCKER, "logs", CONTAINER], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return r.stdout.splitlines()


STMT = re.compile(r"^(?P<db>[^|]*)\|(?P<app>[^|]*)\|(?P<user>\S*) LOG:  statement: (?P<sql>.*)$")


class Fx:
    """Per-test fixture: fresh world, pinned module, translating subprocess.run."""

    def __init__(self, tc):
        self.tc = tc
        self.tmp = Path(tempfile.mkdtemp(prefix="s3drop-")).resolve()
        self.data = self.tmp / "data"
        (self.data / "base").mkdir(parents=True)
        (self.data / "postmaster.pid").write_bytes(b"4242\nfixture\n")
        self.archive = self.tmp / "archive.dump"
        self.archive.write_bytes(b"PGDMP-fake-archive-content" * 100)
        os.utime(self.archive, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))
        self.live = self.tmp / "live"
        self.live.mkdir()
        self.proc = self.tmp / "proc"
        self.mod = load()
        self.calls = []
        self.hook_before_drop = None
        self.hook_after_drop = None
        self.hook_gate = {}
        self.inject = {}
        self.ss_text = 'LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=4242,fd=7))\n'
        self.dropdb_version = None
        self.systemd = {"search-tools-staging.service": ("active", "running", "11", "deploy"),
                        "search-tools-import-worker.service": ("active", "running", "12", "deploy")}
        self.dsn = {"11": "postgresql://app:%s@127.0.0.1:5432/search_tools_staging" % CANARY,
                    "12": "postgresql://app:%s@127.0.0.1:5432/search_tools_staging" % CANARY}
        self.git_head = {}
        self.drop_results = []
        self.real_runtime = False

    def write_proc(self):
        for pid, dsn in self.dsn.items():
            d = self.proc / pid
            d.mkdir(parents=True, exist_ok=True)
            if not (d / "cwd").exists() and not (d / "cwd").is_symlink():
                (d / "cwd").symlink_to(self.live)
            (d / "environ").write_bytes(b"A=1\0DATABASE_URL=" + dsn.encode() + b"\0Z=2\0")

    def pin_archive(self):
        st = self.archive.stat()
        m = self.mod
        m.ARCHIVE, m.ARCHIVE_BYTES, m.ARCHIVE_MTIME_NS = self.archive, st.st_size, st.st_mtime_ns
        m.ARCHIVE_SHA = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def reset_world(self, owner="postgres", label=None, public_connect=False, extra=()):
        t = self.mod.TEMP
        for d in psql("postgres", "select datname from pg_database").splitlines():
            if d not in KEEP_DBS:
                psql("postgres", 'drop database "%s" with (force)' % d)
        psql("postgres", 'create database "%s" template template0 encoding \'UTF8\' lc_collate \'en_US.UTF-8\' '
             "lc_ctype 'en_US.UTF-8'" % t)
        psql("postgres", 'revoke connect on database "%s" from public' % t)
        if public_connect:
            psql("postgres", 'grant connect on database "%s" to public' % t)
        psql("postgres", 'comment on database "%s" is \'%s\'' % (t, label or self.mod.LABEL))
        psql(t, "create table products as select g id from generate_series(1,1000) g",
             "create table stock_items as select g id from generate_series(1,10) g")
        if owner != "postgres":
            psql("postgres", 'alter database "%s" owner to %s' % (t, owner))
        for name in extra:
            psql("postgres", 'create database "%s" template template0' % name)
        self.pin_oid()

    def pin_oid(self):
        self.mod.TEMP_OID = int(psql("postgres", "select oid from pg_database where datname='%s'" % self.mod.TEMP))

    def dbs(self):
        return psql("postgres", "select datname||':'||oid from pg_database order by 1").splitlines()

    def live_writes(self):
        return psql("postgres", "select datname, tup_inserted, tup_updated, tup_deleted from pg_stat_database "
                    "where datname not in ('postgres','template0','template1','%s') and datname is not null order by 1" % self.mod.TEMP)

    def exists(self, name=None):
        return psql("postgres", "select count(*) from pg_database where datname='%s'" % (name or self.mod.TEMP)) == "1"

    # ---- translating subprocess.run
    def gate_of(self, args):
        m = self.mod
        if args[0] == "/usr/bin/ss":
            return "listener"
        if args[0] == PG + "dropdb":
            return "version"
        if "env" in args[:5] and (PG + "dropdb") in args:
            return "drop"
        if (PG + "psql") in args:
            return "source" if args[args.index("-d") + 1] == m.SOURCE else "temp"
        return args[0]

    def translate(self, args, kw):
        args = list(args)
        self.calls.append(args)
        gate = self.gate_of(args)
        if args[0] == "systemctl":
            st = self.systemd[args[2]]
            text = "ActiveState=%s\nSubState=%s\nMainPID=%s\nUser=%s\n" % st
            return subprocess.CompletedProcess(args, 0, text, "")
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, self.git_head.get(args[args.index("-C") + 1],
                                                                          self.mod.COMMIT) + "\n", "")
        if args[0] == "/usr/bin/ss":
            return subprocess.CompletedProcess(args, 0, self.ss_text + self.inject.get("listener", ""), "")
        if args[0] == PG + "dropdb":  # direct --version
            text = self.dropdb_version or sh(DOCKER, "exec", CONTAINER, PG + "dropdb", "--version").stdout
            return subprocess.CompletedProcess(args, 0, text.replace("(Debian", "(Ubuntu") + self.inject.get("version", ""), "")
        if args[:5] != ["sudo", "-n", "-u", "postgres", "env"]:
            raise AssertionError("unexpected executable %r" % (args[:3],))
        if gate == "drop" and self.hook_before_drop:
            self.hook_before_drop(self)
        cmd = [DOCKER, "exec", "-u", "postgres", CONTAINER] + args[4:]
        timeout = kw.get("timeout")
        res = _RUN(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        out, err, rc = res.stdout.replace("(Debian", "(Ubuntu"), res.stderr, res.returncode
        if gate == "drop":
            self.drop_results.append((rc, out, err))
            if self.hook_after_drop:
                self.hook_after_drop(self)
        if gate in self.inject:
            out += "\n" + self.inject[gate]
        if gate in self.hook_gate:
            self.hook_gate[gate](self)
        return subprocess.CompletedProcess(args, rc, out, err)

    def run(self, argv=("--drop",), euid=0, patches=(), timeout=None, post_pin=None, stream=None):
        m = self.mod
        self.pin_archive()
        if post_pin:
            post_pin(self)
        out = stream or io.StringIO()
        fx = self
        real_path = pathlib.Path

        def fake_path(*parts):
            if parts and parts[0] == "/proc":
                return real_path(fx.proc, *parts[1:])
            return real_path(*parts)

        orig_instance = m.instance_guard

        def instance_guard():
            keep = m.DATA
            m.DATA = fx.data
            try:
                return orig_instance()
            finally:
                m.DATA = keep

        self.write_proc()
        before_log = len(log_lines())
        with ExitStack() as st:
            st.enter_context(patch.object(m.os, "geteuid", return_value=euid))
            st.enter_context(patch.object(m.sys, "argv", ["-", *argv]))
            st.enter_context(patch.object(m, "DATA", Path("/var/lib/postgresql/data")))
            st.enter_context(patch.object(m, "BASE", self.data / "base"))
            st.enter_context(patch.object(m, "LIVE", self.live))
            st.enter_context(patch.object(m, "Path", fake_path))
            st.enter_context(patch.object(m, "instance_guard", instance_guard))
            st.enter_context(patch.object(m.subprocess, "run", side_effect=lambda a, **k: self.translate(a, k)))
            if timeout is not None:
                st.enter_context(patch.object(m, "DROP_TIMEOUT", timeout))
            for obj, attr, val in patches:
                st.enter_context(patch.object(obj, attr, val))
            st.enter_context(redirect_stdout(out))
            code, exc = None, None
            try:
                code = m.main()
            except BaseException as e:
                exc = e
        statements = []
        for line in log_lines()[before_log:]:
            mm = STMT.match(line)
            if mm:
                statements.append((mm["app"], mm["db"], mm["sql"]))
        self.last = dict(code=code, out=out.getvalue() if hasattr(out, "getvalue") else "", exc=exc, statements=statements)
        return self.last

    def writes(self):
        return [a for a in self.calls if (PG + "dropdb") in a and "env" in a[:5]]

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RealDrop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not docker_ok():
            raise unittest.SkipTest("docker/postgres:16 image unavailable")
        build_container()

    @classmethod
    def tearDownClass(cls):
        if not KEEP and not os.environ.get("VRF_DROP_CONTAINER"):
            _RUN([DOCKER, "rm", "-f", CONTAINER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def setUp(self):
        self.fx = Fx(self)
        self.addCleanup(self.fx.cleanup)
        self.sessions = []
        self.addCleanup(self.stop_sessions)

    def stop_sessions(self):
        for p in self.sessions:
            try:
                p.kill()
            except Exception:
                pass
        self.sessions = []

    def hold_session(self, db):
        p = subprocess.Popen([DOCKER, "exec", "-u", "postgres", CONTAINER, "psql", "-X", "-d", db, "-c",
                              "select pg_sleep(600)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sessions.append(p)
        for _ in range(50):
            if psql("postgres", "select count(*) from pg_stat_activity where datname='%s' and query like "
                    "'select pg_sleep%%'" % db) == "1":
                return
            time.sleep(0.2)
        self.fail("could not open a session on " + db)

    def session_alive(self, db):
        return psql("postgres", "select count(*) from pg_stat_activity where datname='%s' and query like "
                    "'select pg_sleep%%'" % db) == "1"

    def assertNoCanary(self, r):
        self.assertNotIn(CANARY, r["out"])
        for a in self.fx.calls:
            self.assertNotIn(CANARY, " ".join(a))

    def assertNoDrop(self, r, code=1):
        self.assertEqual(r["code"], code, r["out"])
        self.assertIsNone(r["exc"])
        self.assertEqual(self.fx.writes(), [], "dropdb must not be invoked")
        self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", r["out"])
        self.assertNotIn("HOLD", r["out"])
        self.assertNoCanary(r)

    def assertHold(self, r):
        self.assertEqual(r["code"], 1, r["out"])
        self.assertIsNone(r["exc"])
        self.assertIn("HOLD:", r["out"])
        self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", r["out"])
        self.assertLessEqual(len(self.fx.writes()), 1, "no retry")
        self.assertNoCanary(r)
        self.assertNoForce(r)

    def assertNoForce(self, r):
        joined = " ".join(" ".join(a) for a in self.fx.calls)
        for bad in ("--force", " -f ", "pg_terminate", "pg_cancel", "--if-exists", "WITH (FORCE", "--interactive"):
            self.assertNotIn(bad, joined)
        for app, db, sql in r["statements"]:
            self.assertNotRegex(sql.lower(), r"terminate|pg_cancel|force")
            self.assertNotRegex(sql, r"(?i)drop\s+database.*force")

    # ============ positive control + argv/SQL evidence
    def test_positive_drops_only_temp_and_nothing_else_changes(self):
        fx = self.fx
        fx.reset_world(extra=["search_tools_restore_test_20260930_010101", "search_tools_restore_test_20261001_0645"])
        before = fx.dbs()
        writes_before = fx.live_writes()
        src_rows = psql("search_tools_staging", "select count(*) from products")
        r = fx.run()
        self.assertEqual(r["code"], 0, r["out"])
        self.assertIn("STAGING_TEMP_DATABASE_DROPPED", r["out"])
        self.assertNotIn("HOLD", r["out"])
        self.assertFalse(fx.exists())
        expect = [x for x in before if not x.startswith(fx.mod.TEMP + ":")]
        self.assertEqual(fx.dbs(), expect)  # others (incl. same-prefix siblings) identical names AND oids
        self.assertEqual(psql("search_tools_staging", "select count(*) from products"), src_rows)
        self.assertEqual(fx.live_writes(), writes_before)
        self.assertEqual(len(fx.writes()), 1)
        self.assertEqual(fx.drop_results, [(0, "", "")], fx.drop_results)  # real dropdb: empty stdout/stderr
        self.assertIn("temp_database_exists_after=NO", r["out"])
        self.assertIn("search_tools_restore_test_20260930_010101", r["out"].split("databases_after=")[1])
        self.assertNoCanary(r)
        # server-side evidence: exactly one DROP DATABASE statement, from dropdb, naming TEMP; everything else is SELECT
        drops = [s for s in r["statements"] if re.match(r"(?i)drop", s[2])]
        self.assertEqual(len(drops), 1, drops)
        self.assertEqual(drops[0][:2], ("dropdb", "search_tools_staging"))
        self.assertIn(drops[0][2], ('DROP DATABASE "%s";' % fx.mod.TEMP, "DROP DATABASE %s;" % fx.mod.TEMP), drops)
        others = [s for s in r["statements"] if s not in drops]
        self.assertTrue(others)
        for app, db, sql in others:
            self.assertIn(app, ("psql", "dropdb"))
            if app == "dropdb":  # libpq/client housekeeping sent by dropdb itself
                self.assertEqual(sql, "SELECT pg_catalog.set_config('search_path', '', false);")
                continue
            self.assertRegex(sql, r"^SELECT json_build_object")
            self.assertIn(db, (fx.mod.SOURCE, fx.mod.TEMP))
        self.assertNoForce(r)

    def test_argv_of_the_single_write_is_exactly_as_documented(self):
        fx = self.fx
        fx.reset_world()
        r = fx.run()
        self.assertEqual(r["code"], 0, r["out"])
        w = fx.writes()[0]
        self.assertEqual(w[:5], ["sudo", "-n", "-u", "postgres", "env"])
        self.assertEqual(w[5:7], ["-i", "LC_ALL=C"])
        tail = w[w.index(PG + "dropdb"):]
        self.assertEqual(tail, [PG + "dropdb", "-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
                                "--maintenance-db=search_tools_staging", fx.mod.TEMP])
        pgopt = [a for a in w if a.startswith("PGOPTIONS=")]
        self.assertEqual(len(pgopt), 1)
        self.assertIn("default_transaction_read_only=off", pgopt[0])
        for a in w:
            for bad in ("postgres://", "postgresql://", "password", "PGPASSWORD", "DATABASE_URL"):
                self.assertNotIn(bad.lower(), a.lower())
        # every psql (read) call is read-only at session level
        for a in fx.calls:
            if (PG + "psql") in a:
                self.assertIn("default_transaction_read_only=on", [x for x in a if x.startswith("PGOPTIONS=")][0])
                self.assertIn("-w", a)

    # ============ preconditions: refuse, never drop, never force
    def test_session_in_temp_refuses_without_dropping_or_terminating(self):
        fx = self.fx
        fx.reset_world()
        self.hold_session(fx.mod.TEMP)
        r = fx.run()
        self.assertNoDrop(r)
        self.assertIn("DUNG: TEMP_DATABASE_HAS_SESSIONS", r["out"])
        self.assertTrue(fx.exists())
        self.assertTrue(self.session_alive(fx.mod.TEMP), "the foreign session must not be terminated")
        self.assertNoForce(r)

    def test_session_appears_between_gate_and_dropdb_real_dropdb_refuses_state_hold(self):
        fx = self.fx
        fx.reset_world()
        fx.hook_before_drop = lambda f: self.hold_session(f.mod.TEMP)
        r = fx.run()
        self.assertHold(r)
        self.assertEqual(len(fx.writes()), 1)
        rc, out, err = fx.drop_results[0]
        self.assertNotEqual(rc, 0)
        self.assertIn("being accessed by other users", err)
        self.assertTrue(fx.exists())
        self.assertTrue(self.session_alive(fx.mod.TEMP), "session survived; nothing forced")
        self.assertNotIn("being accessed", r["out"])  # raw tool text is not echoed

    def test_identity_mismatches_refuse(self):
        fx = self.fx
        cases = {
            "wrong_label": dict(label="other-label"),
            "wrong_owner": dict(owner="vrf_app"),
            "public_connect": dict(public_connect=True),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                fx.reset_world(**kw)
                fx.calls.clear()
                r = fx.run()
                self.assertNoDrop(r)
                self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", r["out"])
                self.assertTrue(fx.exists())

    def test_recreated_database_with_same_name_but_new_oid_is_refused(self):
        fx = self.fx
        fx.reset_world()
        old = fx.mod.TEMP_OID
        psql("postgres", 'drop database "%s"' % fx.mod.TEMP)
        fx.reset_world()
        fx.mod.TEMP_OID = old  # pinned OID is the old one; name identical
        self.assertNotEqual(int(psql("postgres", "select oid from pg_database where datname='%s'" % fx.mod.TEMP)), old)
        r = fx.run()
        self.assertNoDrop(r)
        self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", r["out"])
        self.assertTrue(fx.exists())

    def test_temp_missing_refuses_and_rerun_after_success_is_a_noop(self):
        fx = self.fx
        fx.reset_world()
        r1 = fx.run()
        self.assertEqual(r1["code"], 0, r1["out"])
        fx.calls.clear()
        dbs = fx.dbs()
        r2 = fx.run()
        self.assertNoDrop(r2)
        self.assertIn("DUNG: TEMP_DATABASE_PRESENCE", r2["out"])
        self.assertEqual(fx.dbs(), dbs)

    def test_name_not_matching_pattern_refused_before_any_command(self):
        fx = self.fx
        fx.reset_world()
        for bad in ("search_tools_staging", "postgres", "search_tools_restore_test_2026", "x" * 64,
                    "search_tools_restore_test_20261001_064530_x", "SEARCH_TOOLS_RESTORE_TEST_20261001_064530",
                    'search_tools_restore_test_20261001_064530"; drop database postgres; --',
                    "search_tools_restore_test_20261001_06453\n"):
            with self.subTest(name=bad[:40]):
                fx.calls.clear()
                r = fx.run(patches=[(fx.mod, "TEMP", bad)])
                self.assertEqual(r["code"], 1)
                self.assertEqual(fx.calls, [], "no command at all may run for a bad name")
                self.assertIn("DUNG: TEMP_NAME_ONLY", r["out"])
        self.assertTrue(fx.exists(fx.mod.TEMP))

    def test_argument_and_privilege_gate(self):
        fx = self.fx
        fx.reset_world()
        for argv, euid in (((), 0), (("--drop", "--force"), 0), (("drop",), 0), (("--drop",), 1000), (("--force",), 0)):
            with self.subTest(argv=argv, euid=euid):
                fx.calls.clear()
                r = fx.run(argv=argv, euid=euid)
                self.assertEqual(fx.calls, [])
                self.assertIn("DUNG: ROOT_EXPLICIT_DROP_ONLY", r["out"])
                self.assertEqual(r["code"], 1)

    def test_only_pinned_name_dropped_when_two_restore_test_dbs_exist(self):
        fx = self.fx
        other = "search_tools_restore_test_20261001_064531"
        fx.reset_world(extra=[other])
        r = fx.run()
        self.assertEqual(r["code"], 0, r["out"])
        self.assertTrue(fx.exists(other))
        self.assertFalse(fx.exists())
        # and the converse: pinned name absent, only the sibling exists -> nothing dropped
        fx.calls.clear()
        r = fx.run()
        self.assertNoDrop(r)
        self.assertTrue(fx.exists(other))

    # ============ runtime guard (real code, fixture /proc)
    def test_service_pointing_at_temp_db_or_not_active_refuses(self):
        fx = self.fx
        fx.reset_world()
        oid = fx.mod.TEMP_OID
        T = fx.mod.TEMP
        good = "postgresql://app:%s@127.0.0.1:5432/search_tools_staging" % CANARY
        cases = {
            "web_dsn_to_temp": (lambda f: f.dsn.update({"11": "postgresql://app:%s@127.0.0.1:5432/%s" % (CANARY, T)}),
                                "WEB_STAGING_DB_ONLY"),
            "worker_dsn_to_temp": (lambda f: f.dsn.update({"12": "postgresql://app:%s@127.0.0.1:5432/%s" % (CANARY, T)}),
                                   "WORKER_STAGING_DB_ONLY"),
            "dbname_override": (lambda f: f.dsn.update({"11": good + "?dbname=" + T, "12": good + "?dbname=" + T}),
                                "WEB_DSN_OPTIONS"),
            "host_override": (lambda f: f.dsn.update({"11": good + "?host=/tmp", "12": good + "?host=/tmp"}),
                              "WEB_DSN_OPTIONS"),
            "keyvalue_dsn": (lambda f: f.dsn.update({"11": "host=127.0.0.1 dbname=%s password=%s" % (T, CANARY)}),
                             "WEB_STAGING_DB_ONLY"),
            "web_inactive": (lambda f: f.systemd.update({"search-tools-staging.service": ("inactive", "dead", "0", "deploy")}),
                             "WEB_STAGING_RUNTIME"),
            "worker_failed": (lambda f: f.systemd.update({"search-tools-import-worker.service": ("failed", "failed", "0", "deploy")}),
                              "WORKER_STAGING_RUNTIME"),
            "web_other_user": (lambda f: f.systemd.update({"search-tools-staging.service": ("active", "running", "11", "root")}),
                               "WEB_STAGING_RUNTIME"),
            "web_worker_dsn_differ": (lambda f: f.dsn.update({"12": good + "?sslmode=disable"}), "STAGING_WEB_WORKER_SAME_DSN"),
            "wrong_commit": (lambda f: f.git_head.update({str(f.live): "0" * 40}), "WEB_STAGING_COMMIT"),
        }
        for name, (mutate, gate) in cases.items():
            with self.subTest(case=name):
                f = Fx(self)
                self.addCleanup(f.cleanup)
                f.mod.TEMP_OID = oid
                mutate(f)
                r = f.run()
                self.assertNoDrop(r)
                self.assertIn("DUNG: " + gate, r["out"], name)
                self.assertTrue(fx.exists())

    def test_real_runtime_guard_passes_good_fixture_and_positive_works_with_it(self):
        fx = self.fx
        fx.reset_world()
        r = fx.run()
        self.assertEqual(r["code"], 0, r["out"])
        self.assertTrue(any(a[0] == "systemctl" for a in fx.calls))
        self.assertGreaterEqual(len([a for a in fx.calls if a[0] == "systemctl"]), 4)  # before and after the drop

    # ============ archive / instance guards
    def test_archive_tampered_after_drop_is_hold_not_success(self):
        fx = self.fx
        fx.reset_world()

        def tamper(f):
            b = bytearray(f.archive.read_bytes())
            b[5] ^= 1
            f.archive.write_bytes(bytes(b))
            os.utime(f.archive, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))

        fx.hook_after_drop = tamper
        r = fx.run()
        self.assertHold(r)
        self.assertIn("ARCHIVE_HASH_UNCHANGED", r["out"])
        self.assertFalse(fx.exists(), "DB was dropped; output must be HOLD, never the success marker")
        self.assertNotIn("temp_database_exists_after=NO", r["out"])

    def test_archive_mtime_only_touch_after_drop_is_hold(self):
        fx = self.fx
        fx.reset_world()
        fx.hook_after_drop = lambda f: os.utime(f.archive, ns=(1_700_000_000_000_000_000, 1_700_000_000_999_999_999))
        r = fx.run()
        self.assertHold(r)

    def test_archive_pre_drop_mismatch_symlink_missing(self):
        fx = self.fx
        fx.reset_world()
        link = fx.tmp / "link.dump"
        link.symlink_to(fx.archive)

        def sha(f):
            f.mod.ARCHIVE_SHA = "0" * 64

        def size(f):
            f.mod.ARCHIVE_BYTES += 1

        def mtime(f):
            f.mod.ARCHIVE_MTIME_NS += 1

        def symlink(f):
            f.mod.ARCHIVE = link

        def missing(f):
            f.archive.unlink()
        cases = (("sha", sha, "ARCHIVE_HASH_UNCHANGED"), ("size", size, "ARCHIVE_METADATA_MATCH"),
                 ("mtime", mtime, "ARCHIVE_METADATA_MATCH"), ("symlink", symlink, "ARCHIVE_CANONICAL_PATH"),
                 ("missing", missing, "DROP_CHECK_FAILED"))
        for name, hook, gate in cases:
            with self.subTest(case=name):
                fx.calls.clear()
                r = fx.run(post_pin=hook)
                self.assertNoDrop(r)
                self.assertIn("DUNG: " + gate, r["out"])
                self.assertTrue(fx.exists())
                if name == "missing":
                    fx.archive.write_bytes(b"PGDMP-fake-archive-content" * 100)
                    os.utime(fx.archive, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))

    def test_other_postgres_instance_refuses(self):
        fx = self.fx
        fx.reset_world()
        for text in ('LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=9999,fd=7))\n',
                     'LISTEN 0 244 10.0.0.5:5432 0.0.0.0:* users:(("postgres",pid=4242,fd=7))\n',
                     "", 'LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=4242,fd=7)) CANARY\n'):
            with self.subTest(ss=text[:40]):
                fx.calls.clear()
                fx.ss_text = text
                r = fx.run()
                self.assertNoDrop(r)
                self.assertRegex(r["out"], r"DUNG: STAGING_(TCP_SOCKET_INSTANCE|LISTENER_READ_STDOUT_UNEXPECTED)")
        (fx.data / "postmaster.pid").unlink()
        fx.ss_text = 'LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=4242,fd=7))\n'
        fx.calls.clear()
        r = fx.run()
        self.assertNoDrop(r)

    def test_dropdb_client_version_and_server_identity(self):
        fx = self.fx
        fx.reset_world()
        for text in ("dropdb (PostgreSQL) 15.4 (Ubuntu 15.4-1)\n", "dropdb (PostgreSQL) 17.0\n",
                     "dropdb (PostgreSQL) 16.15 WARNING " + CANARY + "\n"):
            with self.subTest(v=text):
                fx.calls.clear()
                fx.dropdb_version = text
                r = fx.run()
                self.assertNoDrop(r)
                self.assertRegex(r["out"], r"DUNG: DROPDB_VERSION")
        fx.dropdb_version = None
        fx.calls.clear()
        r = fx.run(patches=[(fx.mod.os, "geteuid", lambda: 0)])
        self.assertEqual(r["code"], 0, r["out"])

    def test_temp_in_server_with_wrong_encoding_or_collation_refuses(self):
        fx = self.fx
        fx.reset_world()
        psql("postgres", 'drop database "%s"' % fx.mod.TEMP)
        psql("postgres", 'create database "%s" template template0 encoding \'SQL_ASCII\' lc_collate \'C\' lc_ctype \'C\''
             % fx.mod.TEMP)
        psql("postgres", 'revoke connect on database "%s" from public' % fx.mod.TEMP)
        psql("postgres", 'comment on database "%s" is \'%s\'' % (fx.mod.TEMP, fx.mod.LABEL))
        fx.pin_oid()
        r = fx.run()
        self.assertNoDrop(r)
        self.assertIn("DUNG: TEMP_DATABASE_IDENTITY", r["out"])

    def test_unrelated_database_with_hyphen_in_name_makes_script_fail_closed_before_drop(self):
        """Observed behaviour (availability, not safety): a pre-existing database whose name has chars outside
        [A-Za-z0-9_] makes the source gate reject the list; nothing is dropped."""
        fx = self.fx
        fx.reset_world(extra=["legacy-app-db"])
        r = fx.run()
        self.assertNoDrop(r)
        self.assertIn("DUNG: STAGING_METADATA_READ_STDOUT_UNEXPECTED", r["out"])
        self.assertTrue(fx.exists())

    # ============ injected tool output at every read gate (secret canary)
    def test_unexpected_tool_output_at_each_gate_stops_before_drop(self):
        fx = self.fx
        fx.reset_world()
        for gate in ("listener", "version", "source", "temp"):
            for text in ("WARNING: " + CANARY, "NOTICE: x " + CANARY, CANARY):
                with self.subTest(gate=gate, text=text[:12]):
                    fx.calls.clear()
                    fx.inject = {gate: text}
                    r = fx.run()
                    self.assertNoDrop(r)
                    self.assertTrue(fx.exists())
        fx.inject = {}

    def test_dropdb_nonzero_stdout_or_stderr_canaries_are_not_leaked_and_hold(self):
        fx = self.fx
        fx.reset_world()
        # dropdb tool output (success but noisy): fake by injecting into the real result
        fx.inject = {"drop": "NOTICE: " + CANARY}
        r = fx.run()
        self.assertHold(r)
        self.assertFalse(fx.exists(), "real dropdb succeeded; script must say state is unknown/HOLD, never claim OK")
        self.assertIn("DUNG: TEMP_DROP_COMMAND_STDOUT_UNEXPECTED", r["out"])

    # ============ post-drop checks
    def test_post_check_catches_other_database_vanishing(self):
        fx = self.fx
        fx.reset_world(extra=["other_app_db"])
        fx.hook_after_drop = lambda f: psql("postgres", 'drop database "other_app_db"')
        r = fx.run()
        self.assertHold(r)
        self.assertIn("DUNG: POSTDROP_ONLY_TEMP_REMOVED", r["out"])
        self.assertFalse(fx.exists())

    def test_post_check_catches_extra_database_appearing(self):
        fx = self.fx
        fx.reset_world()
        fx.hook_after_drop = lambda f: psql("postgres", 'create database "late_db"')
        r = fx.run()
        self.assertHold(r)
        self.assertIn("DUNG: POSTDROP_ONLY_TEMP_REMOVED", r["out"])

    def test_post_check_catches_source_database_replaced_with_new_oid(self):
        fx = self.fx
        fx.reset_world()

        def swap(f):
            psql("postgres", 'create database src2 template search_tools_staging')
            psql("postgres", 'drop database search_tools_staging')
            psql("postgres", 'alter database src2 rename to search_tools_staging')
        fx.hook_after_drop = swap
        r = fx.run()
        self.assertHold(r)
        self.assertIn("DUNG: POSTDROP_ONLY_TEMP_REMOVED", r["out"])

    def test_post_check_catches_source_losing_033_schema(self):
        fx = self.fx
        fx.reset_world()
        fx.hook_after_drop = lambda f: psql("search_tools_staging", "alter table regulatory_rules disable trigger "
                                            "zz_regulatory_rule_revision")
        r = fx.run()
        try:
            self.assertHold(r)
            self.assertIn("STAGING_SOURCE_METADATA_MATCH", r["out"])
        finally:
            psql("search_tools_staging", "alter table regulatory_rules enable trigger zz_regulatory_rule_revision")

    def test_post_check_known_gap_other_database_replaced_by_same_name_is_not_detected(self):
        """Documents (does not assert as failure of the claim): list-of-names equality cannot see a same-named
        database being dropped+recreated (new OID). Claim V6 only requires 'list after = list before minus temp'."""
        fx = self.fx
        fx.reset_world(extra=["other_app_db"])

        def swap(f):
            psql("postgres", 'drop database "other_app_db"')
            psql("postgres", 'create database "other_app_db"')
        fx.hook_after_drop = swap
        r = fx.run()
        self.assertEqual(r["code"], 0, r["out"])  # observed: success marker printed

    def test_services_or_instance_failing_after_drop_is_hold(self):
        fx = self.fx
        fx.reset_world()

        def kill_worker(f):
            f.systemd["search-tools-import-worker.service"] = ("inactive", "dead", "0", "deploy")
        fx.hook_after_drop = kill_worker
        r = fx.run()
        self.assertHold(r)
        self.assertIn("DUNG: WORKER_STAGING_RUNTIME", r["out"])
        self.assertFalse(fx.exists())

    def test_instance_pid_changed_after_drop_is_hold(self):
        fx = self.fx
        fx.reset_world()
        fx.hook_after_drop = lambda f: (f.data / "postmaster.pid").write_bytes(b"4343\nx\n")
        r = fx.run()
        self.assertHold(r)

    # ============ static / cross-script / invocation
    def test_static_no_raw_drop_force_terminate_in_script(self):
        src = (ROOT / "scripts" / "staging_restore_drop_temp.py").read_text()
        self.assertNotRegex(src, r"(?i)drop\s+database")
        for bad in ("pg_terminate", "pg_cancel", '"--force"', '"-f"', '"--if-exists"', "createdb", "pg_restore", "pg_dump",
                    "systemctl\", \"restart", "os.kill", "signal.", "restart"):
            self.assertNotIn(bad, src.replace("no retry/force/terminate", ""), bad)
        import ast
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        subproc = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "Popen", "call", "check_output")
                   and getattr(n.func.value, "id", "") == "subprocess"]
        self.assertEqual(len(subproc), 1, "single subprocess entry point (checked)")
        # no os.system / eval / exec / shell=True
        self.assertFalse([n for n in calls if getattr(n.func, "attr", "") in ("system", "popen")])
        self.assertNotIn("shell=True", src)

    def test_pinned_constants_are_consistent_across_scripts(self):
        d = load()

        def mod(name):
            spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / name)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
        run, create, ident = mod("staging_restore_run_temp.py"), mod("staging_restore_create_temp.py"), mod("staging_restore_identity_readonly.py")
        names = ("SOURCE", "TEMP", "LIVE", "DATA", "BASE", "ARCHIVE", "ARCHIVE_BYTES", "ARCHIVE_SHA", "ARCHIVE_MTIME_NS", "COMMIT",
                 "WEB", "WORKER", "LABEL", "ENV")
        for n in names:
            self.assertEqual(getattr(d, n), getattr(run, n), n)
            self.assertEqual(getattr(d, n), getattr(create, n), n)
        self.assertEqual(d.TEMP_OID, run.TEMP_OID)
        self.assertEqual(d.TEMP_OID, 18535)
        self.assertEqual((d.SOURCE, d.LIVE, d.WEB, d.WORKER, d.COMMIT), (ident.DATABASE, ident.LIVE, ident.WEB, ident.WORKER, ident.TARGET))
        self.assertEqual(d.LABEL, "staging-restore-rehearsal|snapshot=20261001T064530Z|archive=" + d.ARCHIVE_SHA)
        self.assertNotIn("production", (d.SOURCE + d.TEMP).lower())

    def test_stdin_invocation_form_as_in_operations_runs_the_same_gate_without_root(self):
        """`python3 -I -B - --drop < script` (as in the S3 command) as a NON-root user must stop at the first gate."""
        script = (ROOT / "scripts" / "staging_restore_drop_temp.py").read_bytes()
        r = _RUN([sys.executable, "-I", "-B", "-", "--drop"], input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.geteuid() == 0:
            self.skipTest("running as root")
        self.assertEqual(r.returncode, 1)
        out = r.stdout.decode()
        self.assertIn("DUNG: ROOT_EXPLICIT_DROP_ONLY", out)
        self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", out)
        self.assertEqual(r.stderr, b"")
        r = _RUN([sys.executable, "-I", "-B", "-"], input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertIn(b"DUNG: ROOT_EXPLICIT_DROP_ONLY", r.stdout)

    # ============ dropdb timeout / interruption
    def test_dropdb_timeout_state_unknown_hold_no_retry(self):
        fx = self.fx
        fx.reset_world()
        # a session is opened right before dropdb so that the real dropdb blocks inside the server (~5s)
        fx.hook_before_drop = lambda f: self.hold_session(f.mod.TEMP)
        r = fx.run(timeout=1)
        self.assertHold(r)
        self.assertEqual(len(fx.writes()), 1)
        self.assertIn("DUNG: TEMP_DROP_COMMAND", r["out"])
        self.assertTrue(fx.exists())  # nobody forced anything
        self.assertTrue(self.session_alive(fx.mod.TEMP))

    def test_keyboard_interrupt_during_dropdb_prints_hold(self):
        fx = self.fx
        fx.reset_world()

        def boom(f):
            raise KeyboardInterrupt()
        fx.hook_before_drop = boom
        r = fx.run()
        self.assertHold(r)

    def test_sigkill_and_sighup_of_the_script_process_midway(self):
        """Process-level interruption (ssh drop / kill -9 while dropdb is waiting): observe what the PO sees."""
        results = {}
        for sig in (signal.SIGKILL, signal.SIGHUP, signal.SIGTERM):
            fx = Fx(self)
            self.addCleanup(fx.cleanup)
            fx.reset_world()
            psql("postgres", 'drop database "%s" with (force)' % fx.mod.TEMP)  # child rebuilds its own world
            env = dict(os.environ, VRF_DROP_CHILD="1", VRF_DROP_CONTAINER=CONTAINER)
            p = subprocess.Popen([sys.executable, "-B", __file__], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            seen = []
            deadline = time.time() + 60
            while time.time() < deadline:
                line = p.stdout.readline()
                if not line:
                    break
                seen.append(line.rstrip())
                if line.startswith("drop_started_at_utc"):
                    time.sleep(1.0)  # dropdb is now blocked behind the injected session
                    break
            p.send_signal(sig)
            try:
                rest, err = p.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
                rest, err = p.communicate()
            seen += rest.splitlines()
            time.sleep(7)  # let the orphaned dropdb (if any) finish/fail inside the server
            results[sig.name] = dict(seen=seen, rc=p.returncode, exists=fx.exists(),
                                     session_alive=self.session_alive(fx.mod.TEMP) if fx.exists() else None)
            for pid in psql("postgres", "select pid from pg_stat_activity where datname='%s'" % fx.mod.TEMP).splitlines():
                psql("postgres", "select pg_terminate_backend(%s)" % pid)
            self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", "\n".join(seen))
            self.assertNotIn(CANARY, "\n".join(seen) + err)
        self.last_signal_results = results
        # record for the report
        print("\nSIGNAL_OBSERVATIONS " + json.dumps(results))


def child_main():
    """Runs the real main() with the shim in a separate process so it can be signalled."""
    class Dummy(unittest.TestCase):
        def runTest(self):
            pass
    fx = Fx(Dummy())
    fx.reset_world()

    def before(f):
        # a session appears right before dropdb so the real dropdb blocks ~5s inside the server
        sh(DOCKER, "exec", "-d", "-u", "postgres", CONTAINER, "psql", "-X", "-d", f.mod.TEMP, "-c",
           "select pg_sleep(600)")
        time.sleep(1)
    fx.hook_before_drop = before
    fx.run(stream=sys.stdout)


if __name__ == "__main__" and os.environ.get("VRF_DROP_CHILD"):
    child_main()
    sys.exit(0)
if __name__ == "__main__":
    unittest.main()
