"""Restore the existing staging archive into the ONE empty restore-test DB; nothing else.

Self-contained stdin program (no application/old-ops imports). PO executes only after
independent review. The restore runs as ONE transaction (all-or-nothing) and only
against the temp DB created earlier; the live staging DB is never written. Any error
after the restore started means HOLD: no automatic drop/cleanup/rerun.
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
TEMP_OID = 18535
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
MAIN_TABLES = ("products", "stock_items", "regulatory_rules", "regulatory_statuses")
FLAGS_033 = ("manual_keys_table", "manual_events_table", "manual_protected_column", "revision_column",
             "revision_function", "revision_trigger", "key_identity_index", "key_owner_index",
             "event_history_index")
FLAGS_030_032 = ("footprint_030", "footprint_031", "footprint_032")
NAME_FLAGS_033 = tuple("name_" + name for name in FLAGS_033)
RESTORE_TIMEOUT = 3600


class GateFailure(RuntimeError):
    pass


def checked(args, gate, timeout=20, stdin=None):
    try:
        result = subprocess.run(args, env=ENV, stdin=stdin, stdout=subprocess.PIPE,
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


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def validate_output(args, gate, output):
    def reject():
        raise GateFailure(gate + "_STDOUT_UNEXPECTED")

    # Tool outputs are data, not logs to ignore: accept only the exact known shape.
    if re.search(r"(?im)^\s*(WARNING|NOTICE|ERROR|FATAL|PANIC|DETAIL|HINT|INFO|DEBUG)(?:\s*:|\s*$)", output):
        reject()
    if gate in ("WEB_SERVICE_READ", "WORKER_SERVICE_READ"):
        pairs = [line.split("=", 1) for line in output.splitlines()]
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
    elif gate == "PG_RESTORE_VERSION":
        if not re.fullmatch(r"pg_restore \(PostgreSQL\) 16\.[0-9]+(?: \(Ubuntu [A-Za-z0-9.+~_-]+\))?", output):
            reject()
    elif gate == "TEMP_RESTORE_COMMAND":
        if output:
            reject()
    elif gate in ("STAGING_METADATA_READ", "TEMP_RESTORED_READ"):
        try:
            obj = json.loads(output, object_pairs_hook=unique_object)
            if type(obj) is not dict:
                reject()
            if gate == "TEMP_RESTORED_READ":
                required = ({"database", "temp_bytes"} | set(MAIN_TABLES) | {"rules_inactive"}
                            | set(FLAGS_030_032) | set(FLAGS_033) | set(NAME_FLAGS_033))
            else:
                database = args[args.index("-d") + 1]
                common = {"database", "oid", "encoding", "collate", "ctype", "provider", "tablespace"}
                required = (common | {"version_num", "port", "data_directory", "read_only", "bytes",
                                      "temp_exists", "schema033"} if database == SOURCE
                            else common | {"owner", "label", "public_connect", "user_tables", "user_objects", "sessions"})
            if set(obj) != required:
                reject()
        except GateFailure:
            raise
        except Exception:
            reject()
    else:
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


def pg_args(tool, args, write=False, statement_timeout_ms=None):
    if tool not in ("psql", "pg_restore"):
        raise GateFailure("PG_TOOL_ONLY")
    timeout = statement_timeout_ms if statement_timeout_ms is not None else (60000 if write else 10000)
    options = ("-c default_transaction_read_only=" + ("off" if write else "on")
               + " -c statement_timeout=" + str(timeout) + " -c lock_timeout=5000")
    return ["sudo", "-n", "-u", "postgres", "env", "-i", "LC_ALL=C", "LANG=C",
            "PGOPTIONS=" + options, "PGCONNECT_TIMEOUT=10",
            "/usr/lib/postgresql/16/bin/" + tool, *args]


def query(database, sql, gate="STAGING_METADATA_READ", statement_timeout_ms=None):
    """Read-only helper. The only write in this program is pg_restore into TEMP."""
    validate_name()
    if database not in (SOURCE, TEMP):
        raise GateFailure("QUERY_DATABASE_SCOPE")
    return checked(pg_args("psql", ["-X", "-w", "-h", "/var/run/postgresql", "-p", "5432",
                    "-U", "postgres", "-d", database, "-v", "ON_ERROR_STOP=1",
                    "-P", "pager=off", "-A", "-t", "-c", sql], False, statement_timeout_ms), gate)


def source_metadata():
    sql = """SELECT json_build_object(
      'database',current_database(), 'oid',d.oid::bigint,
      'version_num',current_setting('server_version_num')::int,
      'port',current_setting('port')::int, 'data_directory',current_setting('data_directory'),
      'read_only',current_setting('transaction_read_only'),
      'bytes',pg_database_size(current_database()), 'encoding',pg_encoding_to_char(d.encoding),
      'collate',d.datcollate, 'ctype',d.datctype, 'provider',d.datlocprovider, 'tablespace',t.spcname,
      'temp_exists',EXISTS(SELECT 1 FROM pg_database WHERE datname='""" + TEMP + """'),
      'schema033',(""" + schema033_sql() + """)
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
    if meta.get("temp_exists") is not True:
        raise GateFailure("TEMP_DATABASE_MUST_EXIST")
    return meta


def flag_sql():
    return {
        "manual_keys_table": "EXISTS(SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_keys') AND relkind='r')",
        "manual_events_table": "EXISTS(SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_events') AND relkind='r')",
        "manual_protected_column": "EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='regulatory_rules' AND column_name='manual_protected' AND data_type='boolean' AND is_nullable='NO')",
        "revision_column": "EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='regulatory_rules' AND column_name='revision' AND data_type='bigint' AND is_nullable='NO')",
        "revision_function": "EXISTS(SELECT 1 FROM pg_proc WHERE oid=to_regprocedure('public.update_regulatory_rule_revision()') AND prorettype='trigger'::regtype AND prokind='f')",
        "revision_trigger": "EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.regulatory_rules') AND tgname='zz_regulatory_rule_revision' AND NOT tgisinternal AND tgenabled IN ('O','A') AND tgtype=19 AND tgfoid=to_regprocedure('public.update_regulatory_rule_revision()'))",
        "key_identity_index": "EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_identity') AND indrelid=to_regclass('public.regulatory_rule_manual_keys') AND indisvalid AND indisready AND indisunique)",
        "key_owner_index": "EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_owner') AND indrelid=to_regclass('public.regulatory_rule_manual_keys') AND indisvalid AND indisready)",
        "event_history_index": "EXISTS(SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_event_history') AND indrelid=to_regclass('public.regulatory_rule_manual_events') AND indisvalid AND indisready)",
    }


def name_flag_sql():
    """Presence by NAME only, any schema and any shape: pre-033 requires all of these to be absent."""
    relation = ("EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname='%s' AND n.nspname NOT IN ('pg_catalog','information_schema'))")
    column = ("EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
              "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname='regulatory_rules' "
              "AND a.attname='%s' AND NOT a.attisdropped AND a.attnum>0 "
              "AND n.nspname NOT IN ('pg_catalog','information_schema'))")
    return {
        "name_manual_keys_table": relation % "regulatory_rule_manual_keys",
        "name_manual_events_table": relation % "regulatory_rule_manual_events",
        "name_manual_protected_column": column % "manual_protected",
        "name_revision_column": column % "revision",
        "name_revision_function": ("EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                   "WHERE p.proname='update_regulatory_rule_revision' "
                                   "AND n.nspname NOT IN ('pg_catalog','information_schema'))"),
        "name_revision_trigger": ("EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='zz_regulatory_rule_revision' "
                                  "AND NOT tgisinternal)"),
        "name_key_identity_index": relation % "regulatory_manual_key_identity",
        "name_key_owner_index": relation % "regulatory_manual_key_owner",
        "name_event_history_index": relation % "regulatory_manual_event_history",
    }


def schema033_sql():
    return " AND ".join(flag_sql()[name] for name in FLAGS_033)


def footprints_030_032_sql():
    return {
        "footprint_030": """(to_regclass('public.admin_menu_grants') IS NOT NULL
            AND to_regclass('public.admin_rbac_events') IS NOT NULL
            AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
              AND table_name='app_users' AND column_name='is_super_admin'))""",
        "footprint_031": """EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
            AND table_name='stock_items' AND column_name='stock_note')""",
        "footprint_032": """(to_regclass('public.stock_manual_requests') IS NOT NULL
            AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.stock_snapshots')
              AND conname='stock_snapshots_source_kind_check'
              AND pg_get_constraintdef(oid) LIKE '%MANUAL%'))""",
    }


def disk_locale_guard(meta):
    if not BASE.is_dir() or BASE.is_symlink() or BASE.resolve(strict=True) != BASE:
        raise GateFailure("DEFAULT_TABLESPACE_DIRECTORY")
    required = max(5 * 1024**3, 4 * meta["bytes"])
    free = shutil.disk_usage(BASE).free
    if free < required:
        raise GateFailure("DEFAULT_TABLESPACE_FREE_SPACE")
    if not checked(["/usr/lib/postgresql/16/bin/pg_restore", "--version"],
                   "PG_RESTORE_VERSION").startswith("pg_restore (PostgreSQL) 16."):
        raise GateFailure("PG_RESTORE_VERSION")
    return free, required


def temp_metadata(source_oid, expect_empty):
    sql = """SELECT json_build_object('database',current_database(), 'oid',d.oid::bigint,
      'owner',pg_get_userbyid(d.datdba), 'label',shobj_description(d.oid,'pg_database'),
      'encoding',pg_encoding_to_char(d.encoding), 'collate',d.datcollate, 'ctype',d.datctype,
      'provider',d.datlocprovider, 'tablespace',t.spcname,
      'public_connect',EXISTS(SELECT 1 FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a
        WHERE a.grantee=0 AND a.privilege_type='CONNECT'),
      'user_tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')),
      'user_objects',((SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%')
        + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp%')
        + (SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public','pg_toast')
            AND nspname NOT LIKE 'pg\\_temp%' AND nspname NOT LIKE 'pg\\_toast%')
        + (SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql')
        + (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal)
        + (SELECT count(*) FROM pg_event_trigger)
        + (SELECT count(*) FROM pg_policy)
        + (SELECT count(*) FROM pg_largeobject_metadata)
        + (SELECT count(*) FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_collation o JOIN pg_namespace n ON n.oid=o.collnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_conversion o JOIN pg_namespace n ON n.oid=o.connamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_ts_config o JOIN pg_namespace n ON n.oid=o.cfgnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_ts_dict o JOIN pg_namespace n ON n.oid=o.dictnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_ts_parser o JOIN pg_namespace n ON n.oid=o.prsnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_ts_template o JOIN pg_namespace n ON n.oid=o.tmplnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema'))
        + (SELECT count(*) FROM pg_statistic_ext)
        + (SELECT count(*) FROM pg_foreign_data_wrapper)
        + (SELECT count(*) FROM pg_foreign_server)
        + (SELECT count(*) FROM pg_user_mapping)
        + (SELECT count(*) FROM pg_publication)
        + (SELECT count(*) FROM pg_default_acl)
        + (SELECT count(*) FROM pg_transform)
        + (SELECT count(*) FROM pg_language WHERE lanname NOT IN ('internal','c','sql','plpgsql'))
        + (SELECT count(*) FROM pg_db_role_setting WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()))
        + (SELECT CASE WHEN count(*)=1 THEN 0 ELSE 1 END FROM pg_namespace WHERE nspname='public')
        + (SELECT count(*) FROM pg_namespace n WHERE n.nspname='public'
            AND (n.nspowner<>'pg_database_owner'::regrole
                 OR COALESCE(obj_description(n.oid,'pg_namespace'),'')<>'standard public schema'
                 OR n.nspacl::text<>'{pg_database_owner=UC/pg_database_owner,=U/pg_database_owner}'))),
      'sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid())
      )::text FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace
      WHERE d.datname=current_database();"""
    meta = json.loads(query(TEMP, sql))
    if (meta.get("database") != TEMP or type(meta.get("oid")) is not int or meta["oid"] != TEMP_OID
            or meta["oid"] == source_oid or meta.get("owner") != "postgres" or meta.get("label") != LABEL
            or meta.get("public_connect") is not False or type(meta.get("user_tables")) is not int
            or meta.get("encoding") != "UTF8" or meta.get("collate") != "en_US.UTF-8"
            or meta.get("ctype") != "en_US.UTF-8" or meta.get("provider") != "c"
            or meta.get("tablespace") != "pg_default"):
        raise GateFailure("TEMP_DATABASE_IDENTITY")
    if type(meta.get("sessions")) is not int or meta["sessions"] != 0:
        raise GateFailure("TEMP_DATABASE_HAS_SESSIONS")
    if type(meta.get("user_objects")) is not int:
        raise GateFailure("TEMP_DATABASE_IDENTITY")
    if expect_empty and (meta["user_tables"] != 0 or meta["user_objects"] != 0):
        raise GateFailure("TEMP_DATABASE_MUST_BE_EMPTY")
    if not expect_empty and meta["user_tables"] <= 0:
        raise GateFailure("TEMP_DATABASE_NOT_RESTORED")
    return meta


def restored_read():
    counts = ", ".join("'%s',(SELECT count(*) FROM public.%s)" % (name, name) for name in MAIN_TABLES)
    flags = ", ".join("'%s',%s" % (name, sql) for name, sql in
                      list(footprints_030_032_sql().items()) + list(flag_sql().items())
                      + list(name_flag_sql().items()))
    sql = ("SELECT json_build_object('database',current_database(), 'temp_bytes',pg_database_size(current_database()), "
           + counts + ", 'rules_inactive',(SELECT count(*) FROM public.regulatory_rules WHERE NOT is_active), "
           + flags + ")::text;")
    data = json.loads(query(TEMP, sql, "TEMP_RESTORED_READ", statement_timeout_ms=120000))
    if data.get("database") != TEMP:
        raise GateFailure("TEMP_RESTORED_DATABASE")
    return data


ERROR_CATEGORIES = (
    ("ROLE_MISSING", r"role .* does not exist"),
    ("EXTENSION_PROBLEM", r"extension .*(not available|does not exist|could not open extension)"),
    ("PERMISSION_DENIED", r"permission denied|must be (owner|superuser)"),
    ("OBJECT_ALREADY_EXISTS", r"already exists"),
    ("DISK_OR_MEMORY", r"no space left|out of memory|could not extend|disk full"),
    ("ARCHIVE_UNREADABLE", r"unsupported version|unexpected end of file|could not read|invalid archive|input file"),
    ("CONNECTION_PROBLEM", r"connection to server|could not connect|server closed the connection|terminating connection"),
)


def restore_error_category(error_text):
    """Fixed label only: raw tool text may contain row data, so it is never printed."""
    text = (error_text or "").lower()
    for label, pattern in ERROR_CATEGORIES:
        if re.search(pattern, text):
            return label
    return "UNCLASSIFIED"


def run_restore():
    """The single write: pg_restore into TEMP only, one transaction, archive on stdin."""
    validate_name()
    args = pg_args("pg_restore", ["--exit-on-error", "--single-transaction", "-w",
                                  "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
                                  "--dbname=" + TEMP], write=True, statement_timeout_ms=0)
    try:
        with open(ARCHIVE, "rb") as stream:
            result = subprocess.run(args, env=ENV, stdin=stream, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, timeout=RESTORE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise GateFailure("TEMP_RESTORE_COMMAND_TIMEOUT") from None
    except Exception:
        raise GateFailure("TEMP_RESTORE_COMMAND") from None
    if result.returncode:
        print("restore_error_category=" + restore_error_category(result.stderr), flush=True)
        raise GateFailure("TEMP_RESTORE_COMMAND")
    if (result.stderr or "").strip():
        raise GateFailure("TEMP_RESTORE_COMMAND_STDERR_UNEXPECTED")
    if (result.stdout or "").strip():
        raise GateFailure("TEMP_RESTORE_COMMAND_STDOUT_UNEXPECTED")


def main():
    attempted = False
    name_validated = False
    try:
        if os.geteuid() != 0 or sys.argv[1:] != ["--restore"]:
            raise GateFailure("ROOT_EXPLICIT_RESTORE_ONLY")
        validate_name()
        name_validated = True
        runtime_guard()
        pid = instance_guard()
        archive_before = archive_guard()
        source = source_metadata()
        free, required = disk_locale_guard(source)
        temp_metadata(source["oid"], expect_empty=True)
        print("PRERESTORE_READONLY_GATES_OK temp_database=" + TEMP + " temp_oid=" + str(TEMP_OID) + " temp_user_tables=0", flush=True)
        print(f"source_database={SOURCE} postmaster_pid={pid} source_database_bytes={source['bytes']}", flush=True)
        print(f"data_base_free_bytes={free} required_free_bytes={required}", flush=True)
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        print("restore_started_at_utc=" + started_at, flush=True)
        attempted = True
        run_restore()
        elapsed = time.monotonic() - started
        finished_at = datetime.now(timezone.utc).isoformat()
        after = temp_metadata(source["oid"], expect_empty=False)
        data = restored_read()
        print("restore_finished_at_utc=" + finished_at, flush=True)
        print(f"restore_elapsed_seconds={elapsed:.3f}", flush=True)
        print(f"temp_database={TEMP} temp_oid={after['oid']} temp_user_tables={after['user_tables']} temp_bytes={data['temp_bytes']}", flush=True)
        for name in MAIN_TABLES:
            print(f"temp_count_{name}={data[name]}", flush=True)
        print(f"temp_count_regulatory_rules_inactive={data['rules_inactive']}", flush=True)
        print("footprints_030_032=" + ",".join(f"{n}={data[n]}" for n in FLAGS_030_032), flush=True)
        print("footprints_033=" + ",".join(f"{n}={data[n]}" for n in FLAGS_033), flush=True)
        if (any(type(data[n]) is not int or data[n] <= 0 for n in MAIN_TABLES)
                or type(data["rules_inactive"]) is not int or data["rules_inactive"] < 0):
            raise GateFailure("TEMP_MAIN_TABLES_PRESENT_NONEMPTY")
        if any(data[n] is not True for n in FLAGS_030_032):
            raise GateFailure("TEMP_MISSING_030_032")
        print("names_033=" + ",".join(f"{n}={data[n]}" for n in NAME_FLAGS_033), flush=True)
        if any(data[n] is not False for n in FLAGS_033 + NAME_FLAGS_033):
            raise GateFailure("TEMP_MUST_BE_PRE_033")
        runtime_guard()
        if instance_guard() != pid or archive_guard() != archive_before:
            raise GateFailure("POSTRESTORE_RUNTIME_ARCHIVE_UNCHANGED")
        if source_metadata()["oid"] != source["oid"]:
            raise GateFailure("POSTRESTORE_SOURCE_UNCHANGED")
        print("archive_sha256=" + ARCHIVE_SHA + " unchanged=YES live_database_untouched=YES", flush=True)
        print("STAGING_TEMP_RESTORE_COMPLETED (temp database only; counts must still be reviewed against snapshot)", flush=True)
        return 0
    except BaseException as error:
        gate = str(error) if isinstance(error, GateFailure) and re.fullmatch(r"[A-Z0-9_]+", str(error)) else "RESTORE_CHECK_FAILED"
        print("DUNG: " + gate + " (no raw error/config/secret output, no retry)", flush=True)
        if name_validated:
            print("temp_database=" + TEMP, flush=True)
        if attempted:
            print("HOLD: RESTORE attempted; its state is UNKNOWN (a restore may still be finishing or may have committed). "
                  "Inspect the temp DB read-only before any recovery; no automatic drop/cleanup/rerun.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
