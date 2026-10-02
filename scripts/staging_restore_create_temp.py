"""Create exactly one isolated EMPTY restore-test DB on staging, never restore/drop.

Self-contained stdin program: no install/import of application or old ops scripts.
PO executes only after independent review. Error after CREATE attempted means HOLD,
not permission to rerun or delete. Archive and application DB are never modified.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import parse_qs, unquote, urlsplit


SOURCE = "search_tools_staging"
TEMP = "search_tools_restore_test_20261001_064530"
LIVE = Path("/srv/search-tools")
DATA = Path("/var/lib/postgresql/16/main")
BASE = DATA / "base"
ARCHIVE = Path("/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump")
ARCHIVE_BYTES = 37333943
ARCHIVE_SHA = "cc021ca2848d75b98ec103726d9d42e1cabb3e39df36aa6d03e04b9789b7473f"
ARCHIVE_MTIME_NS = int(datetime(2026, 9, 29, 8, 3, 10, tzinfo=timezone.utc).timestamp()) * 10**9 + 692114220
COMMIT = "2ec9b6c940c89802cce9fc77150e4f2b8dcfff64"
WEB = "search-tools-staging.service"
WORKER = "search-tools-import-worker.service"
LABEL = "staging-restore-rehearsal|snapshot=20261001T064530Z|archive=" + ARCHIVE_SHA
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}


class GateFailure(RuntimeError):
    pass


def checked(args, gate, timeout=20):
    try:
        result = subprocess.run(args, env=ENV, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    except Exception:
        raise GateFailure(gate) from None
    if result.returncode:
        raise GateFailure(gate)
    if (result.stderr or "").strip():
        raise GateFailure(gate + "_STDERR_UNEXPECTED")
    output = (result.stdout or "").strip()
    validate_output(args, gate, output)
    return output


def validate_output(args, gate, output):
    def reject():
        raise GateFailure(gate + "_STDOUT_UNEXPECTED")

    # Tool outputs are data, not logs to silently ignore. No raw output is echoed
    # on rejection, even if a diagnostic includes credentials/configuration.
    if re.search(r"(?im)^\s*(WARNING|NOTICE|ERROR|FATAL|PANIC|DETAIL|HINT|INFO|DEBUG)(?:\s*:|\s*$)", output):
        reject()
    if gate in ("WEB_SERVICE_READ", "WORKER_SERVICE_READ"):
        lines = output.splitlines()
        pairs = [line.split("=", 1) for line in lines]
        if (len(pairs) != 4
                or any(len(pair) != 2 or not re.fullmatch(r"[A-Za-z0-9_.-]*", pair[1]) for pair in pairs)
                or {pair[0] for pair in pairs} != {"ActiveState", "SubState", "MainPID", "User"}):
            reject()
    elif gate in ("WEB_COMMIT_READ", "WORKER_COMMIT_READ"):
        if not re.fullmatch(r"[0-9a-f]{40}", output):
            reject()
    elif gate == "STAGING_LISTENER_READ":
        process = r'\("[A-Za-z0-9_.-]+",pid=[0-9]+,fd=[0-9]+\)'
        shape = (r"LISTEN\s+[0-9]+\s+[0-9]+\s+[0-9.:\[\]*]+:5432\s+[0-9.:\[\]*]+\s+users:\("
                 + process + "(?:," + process + r")*\)")
        if not output or any(not re.fullmatch(shape, line) for line in output.splitlines()):
            reject()
    elif gate == "SOURCE_LOCALE_AVAILABLE":
        if not output or any(not re.fullmatch(r"[A-Za-z0-9_.@-]+", line) for line in output.splitlines()):
            reject()
    elif gate == "CREATEDB_VERSION":
        if not re.fullmatch(r"createdb \(PostgreSQL\) 16\.[0-9]+(?: \(Ubuntu [A-Za-z0-9.+~_-]+\))?", output):
            reject()
    elif gate == "TEMP_CREATE_COMMAND":
        if output:
            reject()
    elif gate == "TEMP_METADATA_WRITE":
        if output not in ("", "COMMENT", "REVOKE\nCOMMENT"):
            reject()
    elif gate == "STAGING_METADATA_READ":
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result
        try:
            obj = json.loads(output, object_pairs_hook=unique_object)
            database = args[args.index("-d") + 1]
            common = {"database", "oid", "encoding", "collate", "ctype", "provider", "tablespace"}
            required = (common | {"version_num", "port", "data_directory", "read_only", "bytes", "temp_exists", "schema033"}
                        if database == SOURCE else common | {"owner", "label", "public_connect", "user_tables"})
            if type(obj) is not dict or set(obj) != required:
                reject()
        except GateFailure:
            raise
        except Exception:
            reject()
    elif gate != "LOCAL_CHECK":
        reject()


def validate_name():
    if (TEMP == SOURCE or len(TEMP) > 63
            or not re.fullmatch(r"search_tools_restore_test_[0-9]{8}_[0-9]{6}", TEMP)):
        raise GateFailure("TEMP_NAME_ONLY")


def runtime_guard():
    if not LIVE.is_dir() or LIVE.is_symlink() or LIVE.resolve(strict=True) != LIVE:
        raise GateFailure("STAGING_LIVE_PATH")
    urls = []
    for role, unit in (("WEB", WEB), ("WORKER", WORKER)):
        info = dict(line.split("=", 1) for line in checked([
            "systemctl", "show", unit, "--no-pager", "-p", "ActiveState", "-p", "SubState",
            "-p", "MainPID", "-p", "User"], role + "_SERVICE_READ").splitlines() if "=" in line)
        pid = info.get("MainPID", "")
        if (info.get("ActiveState") != "active" or info.get("SubState") != "running"
                or info.get("User") != "deploy" or not pid.isascii()
                or not pid.isdigit() or int(pid) <= 0):
            raise GateFailure(role + "_STAGING_RUNTIME")
        if Path("/proc", pid, "cwd").resolve(strict=True) != LIVE:
            raise GateFailure(role + "_STAGING_CWD")
        if checked(["git", "-c", "safe.directory=" + str(LIVE), "-C", str(LIVE),
                    "rev-parse", "HEAD"], role + "_COMMIT_READ") != COMMIT:
            raise GateFailure(role + "_STAGING_COMMIT")
        values = [item.split(b"=", 1)[1] for item in
                  Path("/proc", pid, "environ").read_bytes().split(b"\0")
                  if item.startswith(b"DATABASE_URL=")]
        if len(values) != 1 or not values[0]:
            raise GateFailure(role + "_RUNTIME_DSN")
        parsed = urlsplit(values[0].decode("utf-8"))
        if (parsed.scheme not in ("postgres", "postgresql") or not parsed.username
                or unquote(parsed.path) != "/" + SOURCE or parsed.fragment
                or parsed.hostname != "127.0.0.1"
                or (parsed.port if parsed.port is not None else 5432) != 5432):
            raise GateFailure(role + "_STAGING_DB_ONLY")
        options = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True) if parsed.query else {}
        if set(options) - {"sslmode", "sslrootcert", "connect_timeout", "target_session_attrs"} or any(
                len(value) != 1 or not value[0] for value in options.values()):
            raise GateFailure(role + "_DSN_OPTIONS")
        urls.append(values[0])
    if urls[0] != urls[1]:
        raise GateFailure("STAGING_WEB_WORKER_SAME_DSN")


def archive_guard():
    if ARCHIVE.resolve(strict=True) != ARCHIVE:
        raise GateFailure("ARCHIVE_CANONICAL_PATH")
    fd = os.open(ARCHIVE, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size != ARCHIVE_BYTES
                or before.st_mtime_ns != ARCHIVE_MTIME_NS):
            raise GateFailure("ARCHIVE_METADATA_MATCH")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        fields = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid,
                               item.st_gid, item.st_size, item.st_mtime_ns)
        if fields(before) != fields(after) or digest.hexdigest() != ARCHIVE_SHA:
            raise GateFailure("ARCHIVE_HASH_UNCHANGED")
        return fields(after)


def instance_guard():
    if DATA.resolve(strict=True) != DATA or not DATA.is_dir():
        raise GateFailure("STAGING_DATA_DIRECTORY")
    path = DATA / "postmaster.pid"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise GateFailure("POSTMASTER_FILE_REGULAR")
        first = stream.read(64).split(b"\n", 1)[0]
    if not re.fullmatch(rb"[1-9][0-9]{0,9}", first):
        raise GateFailure("POSTMASTER_PID_VALID")
    pid = first.decode("ascii")
    listener = checked(["/usr/bin/ss", "-H", "-ltnp", "sport = :5432"], "STAGING_LISTENER_READ")
    for line in listener.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0] == "LISTEN"
                and parts[3] in ("127.0.0.1:5432", "0.0.0.0:5432")
                and pid in re.findall(r"\bpid=([0-9]+)\b", line)):
            return pid
    raise GateFailure("STAGING_TCP_SOCKET_INSTANCE")


def pg_args(tool, args, write=False):
    if tool not in ("psql", "createdb"):
        raise GateFailure("PG_TOOL_ONLY")
    options = "-c default_transaction_read_only=" + ("off" if write else "on")
    options += " -c statement_timeout=" + ("60000" if write else "10000") + " -c lock_timeout=5000"
    return ["sudo", "-n", "-u", "postgres", "env", "-i", "LC_ALL=C", "LANG=C",
            "PGOPTIONS=" + options, "PGCONNECT_TIMEOUT=10",
            "/usr/lib/postgresql/16/bin/" + tool, *args]


def query(database, sql, write=False):
    validate_name()
    if database not in (SOURCE, TEMP) or (write and database != TEMP):
        raise GateFailure("QUERY_DATABASE_SCOPE")
    return checked(pg_args("psql", ["-X", "-w", "-h", "/var/run/postgresql", "-p", "5432",
                    "-U", "postgres", "-d", database, "-v", "ON_ERROR_STOP=1",
                    "-P", "pager=off", "-A", "-t", "-c", sql], write),
                   "TEMP_METADATA_WRITE" if write else "STAGING_METADATA_READ")


def source_metadata():
    sql = """SELECT json_build_object(
      'database',current_database(), 'oid',d.oid::bigint,
      'version_num',current_setting('server_version_num')::int,
      'port',current_setting('port')::int, 'data_directory',current_setting('data_directory'),
      'read_only',current_setting('transaction_read_only'),
      'bytes',pg_database_size(current_database()), 'encoding',pg_encoding_to_char(d.encoding),
      'collate',d.datcollate, 'ctype',d.datctype, 'provider',d.datlocprovider, 'tablespace',t.spcname,
      'temp_exists',EXISTS(SELECT 1 FROM pg_database WHERE datname='""" + TEMP + """'),
      'schema033',(
        EXISTS(SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_keys') AND relkind='r')
        AND EXISTS(SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_events') AND relkind='r')
        AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='regulatory_rules'
          AND column_name='manual_protected' AND data_type='boolean' AND is_nullable='NO')
        AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='regulatory_rules'
          AND column_name='revision' AND data_type='bigint' AND is_nullable='NO')
        AND EXISTS(SELECT 1 FROM pg_proc WHERE oid=to_regprocedure('public.update_regulatory_rule_revision()')
          AND prorettype='trigger'::regtype AND prokind='f')
        AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.regulatory_rules')
          AND tgname='zz_regulatory_rule_revision' AND NOT tgisinternal AND tgenabled IN ('O','A') AND tgtype=19
          AND tgfoid=to_regprocedure('public.update_regulatory_rule_revision()'))
        AND EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_identity')
          AND indrelid=to_regclass('public.regulatory_rule_manual_keys') AND indisvalid AND indisready AND indisunique)
        AND EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_owner')
          AND indrelid=to_regclass('public.regulatory_rule_manual_keys') AND indisvalid AND indisready)
        AND EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_event_history')
          AND indrelid=to_regclass('public.regulatory_rule_manual_events') AND indisvalid AND indisready))
      )::text FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace
      WHERE d.datname=current_database();"""
    meta = json.loads(query(SOURCE, sql))
    if (meta.get("database") != SOURCE or meta.get("version_num") != 160015
            or meta.get("port") != 5432 or meta.get("data_directory") != str(DATA)
            or meta.get("read_only") != "on" or meta.get("encoding") != "UTF8"
            or meta.get("provider") != "c" or meta.get("collate") != "en_US.UTF-8"
            or meta.get("ctype") != "en_US.UTF-8" or meta.get("tablespace") != "pg_default"
            or meta.get("schema033") is not True or type(meta.get("oid")) is not int or meta["oid"] <= 0
            or type(meta.get("bytes")) is not int or meta["bytes"] <= 0):
        raise GateFailure("STAGING_SOURCE_METADATA_MATCH")
    if meta.get("temp_exists") is not False:
        raise GateFailure("TEMP_DATABASE_MUST_NOT_EXIST")
    return meta


def disk_locale_guard(meta):
    if not BASE.is_dir() or BASE.is_symlink() or BASE.resolve(strict=True) != BASE:
        raise GateFailure("DEFAULT_TABLESPACE_DIRECTORY")
    required = max(5 * 1024**3, 4 * meta["bytes"])
    free = shutil.disk_usage(BASE).free
    if free < required:
        raise GateFailure("DEFAULT_TABLESPACE_FREE_SPACE")
    canonical = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    locales = checked(["/usr/bin/locale", "-a"], "SOURCE_LOCALE_AVAILABLE").splitlines()
    if canonical(meta["collate"]) not in {canonical(value) for value in locales}:
        raise GateFailure("SOURCE_LOCALE_AVAILABLE")
    if not checked(["/usr/lib/postgresql/16/bin/createdb", "--version"],
                   "CREATEDB_VERSION").startswith("createdb (PostgreSQL) 16."):
        raise GateFailure("CREATEDB_VERSION")
    return free, required


def temp_metadata(source_oid):
    sql = """SELECT json_build_object('database',current_database(), 'oid',d.oid::bigint,
      'owner',pg_get_userbyid(d.datdba), 'label',shobj_description(d.oid,'pg_database'),
      'encoding',pg_encoding_to_char(d.encoding), 'collate',d.datcollate, 'ctype',d.datctype,
      'provider',d.datlocprovider, 'tablespace',t.spcname,
      'public_connect',EXISTS(SELECT 1 FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a
        WHERE a.grantee=0 AND a.privilege_type='CONNECT'),
      'user_tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f'))
      )::text FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace
      WHERE d.datname=current_database();"""
    meta = json.loads(query(TEMP, sql))
    if (meta.get("database") != TEMP or type(meta.get("oid")) is not int or meta["oid"] <= 0
            or meta["oid"] == source_oid or meta.get("owner") != "postgres" or meta.get("label") != LABEL
            or meta.get("public_connect") is not False or type(meta.get("user_tables")) is not int
            or meta["user_tables"] != 0 or meta.get("encoding") != "UTF8"
            or meta.get("collate") != "en_US.UTF-8" or meta.get("ctype") != "en_US.UTF-8"
            or meta.get("provider") != "c" or meta.get("tablespace") != "pg_default"):
        raise GateFailure("TEMP_DATABASE_EMPTY_IDENTITY")
    return meta


def main():
    attempted = False
    name_validated = False
    try:
        if os.geteuid() != 0 or sys.argv[1:] != ["--create"]:
            raise GateFailure("ROOT_EXPLICIT_CREATE_ONLY")
        validate_name()
        name_validated = True
        runtime_guard()
        pid = instance_guard()
        archive_before = archive_guard()
        source = source_metadata()
        free, required = disk_locale_guard(source)
        print("PRECREATE_READONLY_GATES_OK temp_database=" + TEMP, flush=True)
        print(f"source_database={SOURCE} postmaster_pid={pid} source_database_bytes={source['bytes']}", flush=True)
        print(f"data_base_free_bytes={free} required_free_bytes={required}", flush=True)
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        print("create_started_at_utc=" + started_at, flush=True)
        attempted = True
        checked(pg_args("createdb", ["-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
                "--maintenance-db=" + SOURCE, "--template=template0", "--owner=postgres", "--encoding=UTF8",
                "--locale-provider=libc", "--lc-collate=en_US.UTF-8", "--lc-ctype=en_US.UTF-8",
                "--tablespace=pg_default", TEMP], write=True), "TEMP_CREATE_COMMAND", timeout=120)
        # These catalog changes refer only to the NEW temporary DB, never source.
        query(TEMP, 'REVOKE CONNECT ON DATABASE "' + TEMP + '" FROM PUBLIC; COMMENT ON DATABASE "'
              + TEMP + '" IS \'' + LABEL + "';", write=True)
        created = temp_metadata(source["oid"])
        runtime_guard()
        if instance_guard() != pid or archive_guard() != archive_before:
            raise GateFailure("POSTCREATE_RUNTIME_ARCHIVE_UNCHANGED")
        print("create_finished_at_utc=" + datetime.now(timezone.utc).isoformat(), flush=True)
        print(f"create_elapsed_seconds={time.monotonic()-started:.3f}", flush=True)
        print(f"temp_database={TEMP} temp_oid={created['oid']} temp_owner=postgres", flush=True)
        print("temp_public_connect=NO temp_user_tables=0 label_verified=YES", flush=True)
        print("archive_sha256=" + ARCHIVE_SHA + " unchanged=YES", flush=True)
        print("STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)", flush=True)
        return 0
    except BaseException as error:
        gate = str(error) if isinstance(error, GateFailure) and re.fullmatch(r"[A-Z0-9_]+", str(error)) else "CREATE_CHECK_FAILED"
        print("DUNG: " + gate + " (no raw error/config/secret output, no retry)", flush=True)
        if name_validated:
            print("temp_database=" + TEMP, flush=True)
        if attempted:
            print("HOLD: CREATE attempted; inspect exact temp DB before recovery; no automatic drop/cleanup/rerun.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
