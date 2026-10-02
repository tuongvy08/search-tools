"""Independent verifier test: every child-process argv the two staging scripts build is
checked against the OFFICIAL option tables of the real tools, not against mocks that
accept anything.

Only subprocess.run is faked (never `checked`, `pg_args`, `query`, validators), so every
argv comes from the genuine code path, and the fake tools reject what the real ones reject:
unknown/mis-arity options (getopt_long emulation), stray positionals, bad PGOPTIONS, writes
under default_transaction_read_only=on, a CREATE DATABASE collision, wrong -d database.

Optional layers (skipped when unavailable, never a failure by themselves):
  * docker image postgres:16-alpine, run with --network none against a NON-EXISTENT socket
    directory: the real createdb/psql 16 binaries parse our argv and then fail to connect.
    Option rejection ("invalid option", "unrecognized option", ...) is distinguishable from
    the connection error. No PostgreSQL server is contacted.
  * pglast (PostgreSQL's own parser, no server) to syntax-check SQL text.
"""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    import pglast  # type: ignore
except Exception:  # pragma: no cover
    pglast = None

DOCKER = shutil.which("docker")
IMAGE = "postgres:16-alpine"


def docker_image_available():
    if not DOCKER:
        return False
    try:
        r = subprocess.run([DOCKER, "image", "inspect", IMAGE], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- option tables (official docs)
# https://www.postgresql.org/docs/16/app-createdb.html
CREATEDB_LONG = {"tablespace": 1, "echo": 0, "encoding": 1, "locale": 1, "lc-collate": 1, "lc-ctype": 1,
                 "icu-locale": 1, "icu-rules": 1, "locale-provider": 1, "owner": 1, "strategy": 1,
                 "template": 1, "version": 0, "help": 0, "host": 1, "port": 1, "username": 1,
                 "no-password": 0, "password": 0, "maintenance-db": 1}
CREATEDB_SHORT = {"D": 1, "e": 0, "E": 1, "l": 1, "O": 1, "S": 1, "T": 1, "V": 0, "h": 1, "p": 1,
                  "U": 1, "w": 0, "W": 0, "?": 0}
# https://www.postgresql.org/docs/16/app-psql.html (subset that could legitimately appear)
PSQL_SHORT = {"X": 0, "w": 0, "W": 0, "h": 1, "p": 1, "U": 1, "d": 1, "v": 1, "P": 1, "A": 0, "t": 0,
              "c": 1, "f": 1, "x": 0, "q": 0, "1": 0, "a": 0, "b": 0, "e": 0, "E": 0, "H": 0, "n": 0,
              "L": 1, "l": 0, "o": 1, "F": 1, "R": 1, "s": 0, "S": 0, "T": 1, "V": 0, "z": 0, "0": 0,
              "?": 0}
PSQL_LONG = {"no-psqlrc": 0, "no-password": 0, "host": 1, "port": 1, "username": 1, "dbname": 1,
             "set": 1, "variable": 1, "pset": 1, "command": 1, "file": 1, "no-align": 0,
             "tuples-only": 0, "quiet": 0, "single-transaction": 0, "expanded": 0}
GUC_ALLOWED = {"default_transaction_read_only": {"on", "off"}, "statement_timeout": None, "lock_timeout": None}
ENV_ALLOWED = {"LC_ALL", "LANG", "PGOPTIONS", "PGCONNECT_TIMEOUT"}  # libpq env vars + locale


class ToolReject(Exception):
    """The real tool would exit non-zero at argument parsing."""


def getopt(args, short, long):
    opts, pos, i = [], [], 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            pos.extend(args[i:])
            break
        if tok.startswith("--"):
            name, eq, val = tok[2:].partition("=")
            if name not in long:
                raise ToolReject("unrecognized option '--%s'" % name)
            arity = long[name]
            if arity and not eq:
                if i >= len(args):
                    raise ToolReject("option '--%s' requires an argument" % name)
                val, i = args[i], i + 1
            if not arity and eq:
                raise ToolReject("option '--%s' doesn't allow an argument" % name)
            opts.append((name, val if arity else None))
        elif tok.startswith("-") and tok != "-":
            j = 1
            while j < len(tok):
                ch = tok[j]
                j += 1
                if ch not in short:
                    raise ToolReject("invalid option -- '%s'" % ch)
                if short[ch]:
                    val = tok[j:]
                    if not val:
                        if i >= len(args):
                            raise ToolReject("option requires an argument -- '%s'" % ch)
                        val, i = args[i], i + 1
                    opts.append((ch, val))
                    break
                opts.append((ch, None))
        else:
            pos.append(tok)
    return opts, pos


def parse_pgoptions(text):
    parts = text.split(" ")
    out = {}
    if len(parts) % 2:
        raise ToolReject("PGOPTIONS malformed: " + text)
    for flag, setting in zip(parts[0::2], parts[1::2]):
        if flag != "-c" or "=" not in setting:
            raise ToolReject("PGOPTIONS malformed: " + text)
        key, value = setting.split("=", 1)
        if key not in GUC_ALLOWED:
            raise ToolReject("unknown GUC " + key)
        allowed = GUC_ALLOWED[key]
        if (allowed and value not in allowed) or (allowed is None and not value.isdigit()):
            raise ToolReject("bad GUC value " + setting)
        out[key] = value
    return out


def parse_sudo_env(args):
    """sudo -n -u postgres env -i K=V... BIN ARGS  ->  (env dict, bin, binargs)"""
    if args[:4] != ["sudo", "-n", "-u", "postgres"] or args[4:6] != ["env", "-i"]:
        raise ToolReject("unexpected sudo/env prefix: %r" % (args[:6],))
    i, env = 6, {}
    while i < len(args) and re.fullmatch(r"[A-Z_]+=.*", args[i]) and not args[i].startswith("/"):
        k, v = args[i].split("=", 1)
        if k not in ENV_ALLOWED:
            raise ToolReject("unexpected env var " + k)
        env[k] = v
        i += 1
    if i >= len(args) or not args[i].startswith("/usr/lib/postgresql/16/bin/"):
        raise ToolReject("binary must be absolute pg16 path")
    return env, args[i], args[i + 1:]


class FakeHost:
    """Fake of subprocess.run for every command either script runs."""

    def __init__(self, module, psql_multi="all", source_bytes=1228209175, existing=()):
        self.m = module
        self.calls = []
        self.psql_multi = psql_multi  # "all" (psql>=15) or "last" (psql<=14)
        self.dbs = {module.SOURCE} | set(existing)
        self.bytes = source_bytes
        self.acl_revoked = False
        self.label = None

    # ---- dispatch
    def run(self, args, env=None, stdout=None, stderr=None, text=None, timeout=None, check=None, **kw):
        assert env is not None and set(env) <= {"PATH", "LC_ALL", "LANG"}, env
        assert all(isinstance(a, str) and "\0" not in a for a in args)
        self.calls.append((list(args), timeout))
        try:
            out = self.dispatch(list(args))
        except ToolReject as e:
            return subprocess.CompletedProcess(args, 1, "", "tool: %s\n" % e)
        return subprocess.CompletedProcess(args, 0, out, "")

    def dispatch(self, args):
        head = args[0]
        if head == "systemctl":
            return self.systemctl(args)
        if head == "git":
            return self.git(args)
        if head == "/usr/bin/ss":
            return self.ss(args)
        if head == "/usr/bin/locale":
            if args[1:] != ["-a"]:
                raise ToolReject("locale args")
            return "C\nC.utf8\nen_US.utf8\nPOSIX\n"
        if head == "sudo":
            env, binary, rest = parse_sudo_env(args)
            if "LC_ALL" not in env or env.get("LC_ALL") != "C":
                raise ToolReject("locale env")
            guc = parse_pgoptions(env["PGOPTIONS"]) if "PGOPTIONS" in env else {}
            if binary.endswith("/createdb"):
                return self.createdb(rest, env, guc)
            if binary.endswith("/psql"):
                return self.psql(rest, env, guc)
            raise ToolReject("unexpected pg binary " + binary)
        if head == "/usr/lib/postgresql/16/bin/createdb":
            if args[1:] != ["--version"]:
                raise ToolReject("createdb direct only --version")
            return "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)\n"
        raise ToolReject("unexpected command " + head)

    # ---- systemctl (man systemctl: show [PATTERN...], -p/--property=, --no-pager)
    def systemctl(self, args):
        if args[1] != "show" or args[2] not in (self.m.WEB, self.m.WORKER):
            raise ToolReject("systemctl show unit")
        props, i = [], 3
        while i < len(args):
            if args[i] == "--no-pager":
                i += 1
            elif args[i] in ("-p", "--property") and i + 1 < len(args):
                props.append(args[i + 1])
                i += 2
            else:
                raise ToolReject("systemctl option %r" % args[i])
        valid = {"ActiveState": "active", "SubState": "running", "MainPID": "4242", "User": "deploy"}
        if not props or set(props) - set(valid):
            raise ToolReject("unknown property")
        # systemctl prints in its own order, not request order
        return "".join("%s=%s\n" % (k, v) for k, v in valid.items() if k in props)

    def git(self, args):
        i, cfg = 1, []
        while args[i] in ("-c", "-C"):
            cfg.append((args[i], args[i + 1]))
            i += 2
        if args[i:] != ["rev-parse", "HEAD"] or not cfg:
            raise ToolReject("git subcommand")
        return self.m.COMMIT + "\n"

    def ss(self, args):
        flags = "".join(a[1:] for a in args[1:] if re.fullmatch(r"-[HltnpOo]+", a))
        rest = [a for a in args[1:] if not re.fullmatch(r"-[HltnpOo]+", a)]
        if not set(flags) <= set("HltnpOo") or rest != ["sport = :5432"]:
            raise ToolReject("ss arguments %r" % (args,))
        return 'LISTEN 0      244        127.0.0.1:5432      0.0.0.0:*    users:(("postgres",pid=3993835,fd=6))\n' \
               'LISTEN 0      244            [::1]:5432         [::]:*    users:(("postgres",pid=3993835,fd=5))\n'

    # ---- createdb 16
    def createdb(self, rest, env, guc):
        opts, pos = getopt(rest, CREATEDB_SHORT, CREATEDB_LONG)
        o = {}
        for k, v in opts:
            o[{"h": "host", "p": "port", "U": "username", "w": "no-password", "O": "owner", "T": "template",
               "E": "encoding", "D": "tablespace"}.get(k, k)] = v
        if len(pos) not in (1, 2):
            raise ToolReject("wrong number of positionals %r" % pos)
        if o.get("locale-provider") not in (None, "libc", "icu"):
            raise ToolReject("invalid locale provider")
        if guc.get("default_transaction_read_only") == "on":
            return self.pg_error("cannot execute CREATE DATABASE in a read-only transaction")
        name = pos[0]
        if name in self.dbs:
            raise ToolReject("database already exists")  # non-zero exit like the real one
        if o.get("maintenance-db") == name:
            raise ToolReject("maintenance db equals new db")
        sql = self.createdb_sql(name, o)
        self.check_sql(sql)
        self.dbs.add(name)
        self.created = name
        return ""  # no -e: silent on success

    @staticmethod
    def createdb_sql(name, o):
        # Mirrors src/bin/scripts/createdb.c (PG16)
        sql = 'CREATE DATABASE "%s"' % name
        if "owner" in o:
            sql += ' OWNER "%s"' % o["owner"]
        if "template" in o:
            sql += ' TEMPLATE "%s"' % o["template"]
        if "encoding" in o:
            sql += " ENCODING '%s'" % o["encoding"]
        if "strategy" in o:
            sql += " STRATEGY '%s'" % o["strategy"]
        if "tablespace" in o:
            sql += ' TABLESPACE "%s"' % o["tablespace"]
        if "locale" in o:
            sql += " LOCALE '%s'" % o["locale"]
        if "lc-collate" in o:
            sql += " LC_COLLATE '%s'" % o["lc-collate"]
        if "lc-ctype" in o:
            sql += " LC_CTYPE '%s'" % o["lc-ctype"]
        if "locale-provider" in o:
            sql += " LOCALE_PROVIDER %s" % o["locale-provider"]
        return sql

    @staticmethod
    def pg_error(message):
        raise ToolReject("ERROR:  " + message)

    @staticmethod
    def check_sql(sql):
        if pglast is not None:
            pglast.parse_sql(sql)

    # ---- psql 16
    def psql(self, rest, env, guc):
        opts, pos = getopt(rest, PSQL_SHORT, PSQL_LONG)
        if pos:
            raise ToolReject("psql stray positional (would be taken as dbname/user): %r" % pos)
        names = [k for k, _ in opts]
        db = dict(opts).get("d")
        cmd = [v for k, v in opts if k == "c"]
        if db not in self.dbs or len(cmd) != 1:
            raise ToolReject("connection/-c problem")
        if db == self.m.TEMP and self.m.TEMP not in self.dbs:
            raise ToolReject("no such db")
        if "w" not in names or "X" not in names:
            raise ToolReject("missing -w/-X (would read psqlrc/prompt)")
        sql = cmd[0]
        self.check_sql(sql)
        readonly = guc.get("default_transaction_read_only") == "on"
        tuples_only = "t" in names
        statements = [s.strip() for s in re.split(r";\s*", sql) if s.strip()] \
            if not re.match(r"(?is)\s*SELECT", sql) else [sql.strip().rstrip(";")]
        tags = []
        for s in statements:
            if re.match(r"(?is)SELECT", s):
                if "temp_exists" in s:
                    return json.dumps(self.source_json(db)) + "\n"
                if "public_connect" in s:
                    return json.dumps(self.temp_json(db)) + "\n"
                raise ToolReject("unknown select")
            if readonly:
                raise ToolReject("ERROR:  cannot execute %s in a read-only transaction" % s.split()[0])
            if re.fullmatch(r'REVOKE CONNECT ON DATABASE "%s" FROM PUBLIC' % re.escape(db), s):
                self.acl_revoked = True
                tags.append("REVOKE")
            elif m := re.fullmatch(r"COMMENT ON DATABASE \"%s\" IS '([^']*)'" % re.escape(db), s):
                self.label = m.group(1)
                tags.append("COMMENT")
            else:
                raise ToolReject("unexpected statement " + s)
        # command tags are printed even with -t (only -q suppresses them)
        out = tags if self.psql_multi == "all" else tags[-1:]
        return "\n".join(out) + "\n"

    def source_json(self, db):
        if db != self.m.SOURCE:
            raise ToolReject("source query on wrong db")
        return {"database": db, "oid": 17000, "version_num": 160015, "port": 5432,
                "data_directory": str(self.m.DATA), "read_only": "on", "bytes": self.bytes,
                "encoding": "UTF8", "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8", "provider": "c",
                "tablespace": "pg_default", "temp_exists": self.m.TEMP in self.dbs, "schema033": True}

    def temp_json(self, db):
        if db != self.m.TEMP:
            raise ToolReject("temp query on wrong db")
        return {"database": db, "oid": 38000, "owner": "postgres", "label": self.label,
                "encoding": "UTF8", "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8", "provider": "c",
                "tablespace": "pg_default", "public_connect": not self.acl_revoked, "user_tables": 0}


class LiveStub:
    def is_dir(self): return True
    def is_symlink(self): return False
    def resolve(self, strict=True): return self
    def __str__(self): return "/srv/search-tools"


def run_script(module, host, argv=("-", "--create"), dsn_b=b"postgresql://app:FAKE_PW@127.0.0.1:5432/search_tools_staging"):
    live = LiveStub()
    tmp = tempfile.TemporaryDirectory(prefix="argv-verify-")
    base = Path(tmp.name).resolve()
    data = base / "data"
    (data / "base").mkdir(parents=True)
    (data / "postmaster.pid").write_bytes(b"3993835\nsynthetic\n")
    content = b"synthetic archive"
    archive = base / "a.dump"
    archive.write_bytes(content)
    mtime = getattr(module, "ARCHIVE_MTIME_NS", 1)
    os.utime(archive, ns=(mtime, mtime))

    def fake_path(*parts):
        if parts and parts[0] == "/proc":
            return SimpleNamespace(resolve=lambda strict=True: live,
                                   read_bytes=lambda: b"PATH=/x\0DATABASE_URL=" + dsn_b + b"\0")
        return Path(*parts)

    patches = [patch.object(module.subprocess, "run", side_effect=host.run),
               patch.object(module.os, "geteuid", return_value=0),
               patch.object(module.sys, "argv", list(argv)),
               patch.object(module, "LIVE", live), patch.object(module, "Path", side_effect=fake_path)]
    if hasattr(module, "DATA"):
        patches += [patch.object(module, "DATA", data), patch.object(module, "BASE", data / "base"),
                    patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=60_000_000_000)),
                    patch.object(module, "ARCHIVE", archive), patch.object(module, "ARCHIVE_BYTES", len(content)),
                    patch.object(module, "ARCHIVE_SHA", hashlib.sha256(content).hexdigest())]
        # postmaster pid 3993835 in tmp file; DATA.resolve must equal DATA
    out = io.StringIO()
    try:
        for p in patches:
            p.start()
        with redirect_stdout(out):
            code = module.main()
    finally:
        for p in reversed(patches):
            p.stop()
        tmp.cleanup()
    return code, out.getvalue()


class RealToolArgvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create = load("create_argv_verify", "staging_restore_create_temp.py")
        cls.identity = load("identity_argv_verify", "staging_restore_identity_readonly.py")

    # -- the full genuine path with strict fake tools
    def test_create_script_full_path_with_option_table_enforcing_tools(self):
        for mode in ("all", "last"):
            with self.subTest(psql_multi_statement_output=mode):
                host = FakeHost(self.create, psql_multi=mode)
                code, out = run_script(self.create, host)
                self.assertEqual(code, 0, out)
                self.assertIn("STAGING_TEMP_DATABASE_CREATED", out)
                creates = [c for c, _ in host.calls if "createdb" in " ".join(c) and "--version" not in c]
                self.assertEqual(len(creates), 1)
                self.assertTrue(host.acl_revoked)
                self.assertEqual(host.label, self.create.LABEL)
                self.assertNotIn("FAKE_PW", out)
                for argv, _ in host.calls:  # no secret / DSN / password ever in argv
                    self.assertNotIn("FAKE_PW", " ".join(argv))
                    self.assertNotRegex(" ".join(argv), r"postgres(ql)?://")
                    self.assertNotIn("--password", argv)

    def test_identity_script_full_path_with_strict_tools(self):
        host = FakeHost(self.create)
        code, out = run_script(self.identity, host, argv=("-",))
        self.assertEqual(code, 0, out)
        self.assertIn("STAGING_RUNTIME_IDENTITY_OK", out)
        heads = {c[0] for c, _ in host.calls}
        self.assertEqual(heads, {"systemctl", "git"})

    def test_no_forbidden_command_ever_spawned(self):
        host = FakeHost(self.create)
        run_script(self.create, host)
        flat = [" ".join(c) for c, _ in host.calls]
        for needle in ("pg_restore", "pg_dump", "dropdb", "DROP ", "systemctl restart", "systemctl stop",
                       "systemctl start", "--force", "pg_terminate", "ALTER ", "CREATE ROLE", "CREATE EXTENSION"):
            for line in flat:
                self.assertNotIn(needle, line)
        # exactly one writing psql call (REVOKE+COMMENT) and exactly one createdb
        writes = [c for c, _ in host.calls if any("default_transaction_read_only=off" in a for a in c)]
        self.assertEqual(len(writes), 2, writes)  # createdb + one psql
        for c in writes:
            if c[-1] != self.create.TEMP:  # psql
                self.assertEqual(c[c.index("-d") + 1], self.create.TEMP)

    # -- negative controls: the harness must catch the original field failure class
    def test_harness_rejects_the_original_bad_option_and_other_bad_argv(self):
        good = ["-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres", "--maintenance-db=x",
                "--template=template0", "--owner=postgres", "--encoding=UTF8", "--locale-provider=libc",
                "--lc-collate=en_US.UTF-8", "--lc-ctype=en_US.UTF-8", "--tablespace=pg_default", "t"]
        getopt(good, CREATEDB_SHORT, CREATEDB_LONG)
        for bad in (["--connection-limit=3", "t"], ["--lc-collate", "x", "--bogus", "t"], ["-w=1", "t"],
                    ["--no-password=1", "t"], ["--owner"], ["-Z", "t"], ["--encoding"]):
            with self.subTest(bad=bad), self.assertRaises(ToolReject):
                getopt(bad, CREATEDB_SHORT, CREATEDB_LONG)
        with self.assertRaises(ToolReject):
            parse_pgoptions("-c default_transaction_read_only=on -c bogus_guc=1")

    def test_mutated_create_argv_is_stopped_with_hold_not_success(self):
        original = self.create.pg_args

        def mutate(tool, args, write=False):
            if tool == "createdb":
                args = list(args)[:-1] + ["--connection-limit=3", args[-1]]
            return original(tool, args, write)

        host = FakeHost(self.create)
        with patch.object(self.create, "pg_args", side_effect=mutate):
            code, out = run_script(self.create, host)
        self.assertEqual(code, 1)
        self.assertIn("HOLD", out)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", out)
        self.assertNotIn(self.create.TEMP, host.dbs)

    # -- collision and read-only behaviour as the real tools would show
    def test_existing_temp_db_blocks_before_createdb(self):
        host = FakeHost(self.create, existing=[self.create.TEMP])
        code, out = run_script(self.create, host)
        self.assertEqual(code, 1)
        self.assertEqual(sum("createdb" in " ".join(c) and "--version" not in c for c, _ in host.calls), 0)
        self.assertNotIn("HOLD", out)

    def test_read_only_sessions_are_really_read_only(self):
        # Every psql call without the explicit write flag carries default_transaction_read_only=on
        host = FakeHost(self.create)
        run_script(self.create, host)
        for c, _ in host.calls:
            if c[0] == "sudo":
                env, binary, rest = parse_sudo_env(c)
                guc = parse_pgoptions(env["PGOPTIONS"])
                if binary.endswith("psql") and "-d" in rest and rest[rest.index("-d") + 1] == self.create.SOURCE:
                    self.assertEqual(guc["default_transaction_read_only"], "on")

    def test_secret_in_dsn_wrong_database_or_host_stops_before_any_pg_tool(self):
        for dsn in (b"postgresql://app:FAKE_PW@127.0.0.1:5432/production",
                    b"postgresql://app:FAKE_PW@db.example.com:5432/search_tools_staging"):
            host = FakeHost(self.create)
            code, out = run_script(self.create, host, dsn_b=dsn)
            self.assertEqual(code, 1)
            self.assertNotIn("FAKE_PW", out)
            self.assertFalse(any(c[0] == "sudo" for c, _ in host.calls))

    # -- SQL text parse with PostgreSQL's own parser (no server)
    @unittest.skipIf(pglast is None, "pglast not installed")
    def test_sql_text_is_valid_postgresql_syntax(self):
        host = FakeHost(self.create)
        run_script(self.create, host)  # FakeHost.check_sql already parsed every statement
        statements = [c[-1] for c, _ in host.calls if c[0] == "sudo" and "-c" in c]
        self.assertTrue(statements)
        for sql in statements:
            pglast.parse_sql(sql)
        write = [s for s in statements if s.startswith("REVOKE")]
        self.assertEqual(len(write), 1)
        tree = pglast.parse_sql(write[0])
        self.assertEqual(len(tree), 2)

    # -- real createdb/psql 16 binaries, argument parsing only (no server contacted)
    @unittest.skipUnless(docker_image_available(), "docker image postgres:16-alpine not available")
    def test_real_pg16_clients_accept_every_argv_we_build(self):
        host = FakeHost(self.create)
        code, _ = run_script(self.create, host)
        self.assertEqual(code, 0)
        reject = re.compile(r"invalid option|unrecognized option|too many command-line|requires an argument|"
                            r"ambiguous option|doesn't allow an argument|invalid .* provider|"
                            r"invalid locale provider", re.I)
        checked = 0
        tools = []
        for argv, _ in host.calls:
            if argv[0] != "sudo":
                continue
            env, binary, rest = parse_sudo_env(argv)
            rest = list(rest)
            rest[rest.index("-h") + 1] = "/nonexistent-socket-dir-verifier"
            tool = os.path.basename(binary)
            tools.append(tool)
            cmd = [DOCKER, "run", "--rm", "--network", "none", "--entrypoint", "env", IMAGE, "-i"]
            cmd += ["%s=%s" % kv for kv in env.items()] + [tool] + rest
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            self.assertNotRegex(result.stderr, reject, "%s rejected our argv: %s" % (tool, result.stderr))
            self.assertIn("nonexistent-socket-dir-verifier", result.stderr)  # got as far as connecting
            self.assertNotEqual(result.returncode, 0)
            checked += 1
        # S1.1 builds exactly four PostgreSQL argv: read source, createdb, write label/ACL, read temp.
        self.assertEqual(checked, 4)
        self.assertEqual(tools, ["psql", "createdb", "psql", "psql"])

    @unittest.skipUnless(docker_image_available(), "docker image postgres:16-alpine not available")
    def test_real_createdb_rejects_the_original_failing_option(self):
        cmd = [DOCKER, "run", "--rm", "--network", "none", "--entrypoint", "createdb", IMAGE, "-w", "-h",
               "/nonexistent-socket-dir-verifier", "--connection-limit=3", "x"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"unrecognized option")
        self.assertNotIn("nonexistent-socket-dir-verifier", result.stderr)  # failed before connecting


if __name__ == "__main__":
    unittest.main()
