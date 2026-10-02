"""Independent S0.7 shell probes, never SSH/sudo/psql or connect to a DB.

Use real file predicates, real env -i, and explicit local recorders for ss and
psql. All generated fixtures are confined to tests/independent. SQL semantic
execution on PostgreSQL remains NOT RUN; static SQL checks are not DB evidence.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
SQL = ROOT / "specs/staging-restore-rehearsal/SOURCE_CHECK.sql"
PID = "/var/lib/postgresql/16/main/postmaster.pid"
CLIENT = "/usr/lib/postgresql/16/bin/psql"
CANARY = "INDEPENDENT_S07_FAKE_SECRET_NOT_REAL"
LISTENER = 'LISTEN 0 244 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=101,fd=5))\n'
FLAGS = ("manual_keys_table", "manual_events_table", "manual_protected_column",
         "revision_column", "revision_function", "revision_trigger",
         "key_identity_index", "key_owner_index", "event_history_index")
NORMAL = ("database_name | search_tools_staging\nserver_version | 16.15\n"
          "server_port | 5432\npostmaster_pid | 101\ntransaction_read_only | on\n"
          + "".join(f"{flag} | t\n" for flag in FLAGS)
          + "products | 100\nstock_items | 100\nregulatory_rules | 100\nregulatory_statuses | 100\n")


def command():
    section = DOC.read_text().split("## S0.7 —", 1)[1].split("## Các bước còn lại", 1)[0]
    args = shlex.split(section.split("```bash\n", 1)[1].split("```", 1)[0])
    if args[:2] != ["ssh", "staging"] or args[3:] != ["<", str(SQL)]:
        raise AssertionError("Unexpected alias or stdin source")
    return args[2], section


def probe(kind="regular", deny_at=0, ss_status=0, psql_status=0,
          listener=LISTENER, output=NORMAL, warning=""):
    with tempfile.TemporaryDirectory(prefix="s07-independent-", dir=HERE) as folder:
        base = Path(folder)
        pid = base / "pid with spaces"
        target = base / "symlink-target"
        target.write_bytes(b"101\nmetadata fixture only\n")
        if kind == "regular":
            pid.write_bytes(target.read_bytes())
        elif kind == "symlink":
            pid.symlink_to(target)
        elif kind == "dangling":
            pid.symlink_to(base / "absent")
        elif kind == "empty":
            pid.touch()
        elif kind == "directory":
            pid.mkdir()
        elif kind != "missing":
            raise AssertionError(kind)
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        trace = base / "sudo.jsonl"
        records = {}
        for role, status, stdout in (("ss", ss_status, listener),
                                    ("psql", psql_status, output)):
            record = base / (role + ".json")
            records[role] = record
            executable = base / ("fake-" + role)
            executable.write_text(
                f"#!{sys.executable} -B\n"
                "import json, os, pathlib, sys\n"
                f"pathlib.Path({str(record)!r}).write_text(json.dumps({{"
                "'argv': sys.argv[1:], 'env': dict(os.environ), "
                + ("'stdin': sys.stdin.read()" if role == "psql" else "'stdin': ''")
                + "}))\n"
                f"sys.stdout.write({stdout!r})\n"
                + (f"sys.stderr.write({warning!r})\n" if role == "psql" else "")
                + f"sys.exit({status})\n"
            )
            executable.chmod(0o700)
        sudo = base / "sudo"
        sudo.write_text(
            f"#!{sys.executable} -B\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"trace = pathlib.Path({str(trace)!r})\n"
            "with trace.open('a') as handle: handle.write(json.dumps(args) + '\\n')\n"
            f"if len(trace.read_text().splitlines()) == {deny_at}: sys.exit(77)\n"
            "if args[:2] == ['-n', 'test']:\n"
            f"    assert args[-1] == {str(pid)!r}\n"
            "    os.execv('/bin/test', ['test'] + args[2:])\n"
            "elif args[:5] == ['-n', 'env', '-i', 'LC_ALL=C', 'LANG=C']:\n"
            "    assert args[5:] == ['/usr/bin/ss', '-H', '-ltnp', 'sport = :5432']\n"
            f"    args[5] = {str(base / 'fake-ss')!r}\n"
            "    os.execv('/usr/bin/env', ['env'] + args[2:])\n"
            "else:\n"
            "    assert args[:7] == ['-n', '-u', 'postgres', 'env', '-i', 'LC_ALL=C', 'LANG=C']\n"
            f"    assert args[8] == {CLIENT!r}\n"
            f"    args[8] = {str(base / 'fake-psql')!r}\n"
            "    os.execv('/usr/bin/env', ['env'] + args[4:])\n"
        )
        sudo.chmod(0o700)
        text = command()[0]
        if text.count(PID) != 1:
            raise AssertionError("Unexpected PID path occurrence")
        text = text.replace(PID, shlex.quote(str(pid)))
        poisoned = {key: CANARY for key in (
            "PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE",
            "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGOPTIONS",
            "DATABASE_URL", "HOME", "PSQLRC", "ENV", "BASH_ENV")}
        result = subprocess.run(
            ["/bin/sh", "-c", text], env={"PATH": str(base), **poisoned},
            input=SQL.read_text(), capture_output=True, text=True, timeout=5,
        )
        events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        results = {role: json.loads(path.read_text()) if path.exists() else None
                   for role, path in records.items()}
        if before != (target.read_bytes(), target.stat().st_mtime_ns):
            raise AssertionError("PID fixture target changed")
        return result, events, results


class SourceIndependentTests(unittest.TestCase):
    def test_real_env_cleanup_and_exact_readonly_staging_input(self):
        result, events, records = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(events), 5)
        self.assertNotIn(CANARY, result.stdout + result.stderr + repr(records) + repr(events))
        self.assertEqual(records["psql"]["stdin"], SQL.read_text())
        self.assertEqual(records["psql"]["argv"], [
            "-X", "-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
            "-d", "search_tools_staging", "-v", "ON_ERROR_STOP=1", "-P", "pager=off", "-x", "-f", "-",
        ])
        for role in ("ss", "psql"):
            env = records[role]["env"].copy()
            if sys.platform == "darwin":
                env.pop("__CF_USER_TEXT_ENCODING", None)
            expected = {"LC_ALL": "C", "LANG": "C"}
            if role == "psql":
                expected["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=10000"
            self.assertEqual(env, expected)

    def test_real_bad_metadata_paths_block_both_tools(self):
        for kind, calls in (("missing", 1), ("symlink", 2), ("dangling", 1),
                            ("empty", 3), ("directory", 1)):
            with self.subTest(kind=kind):
                result, events, records = probe(kind=kind)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(events), calls)
                self.assertEqual(records, {"ss": None, "psql": None})
                self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_denied_privilege_at_every_step_never_retries(self):
        for step in range(1, 6):
            with self.subTest(step=step):
                result, events, records = probe(deny_at=step)
                self.assertEqual(result.returncode, 77)
                self.assertEqual(len(events), step)
                self.assertIsNone(records["psql"])
                self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_listener_failure_and_interrupt_stop_before_query(self):
        for status in (1, 2, 124, 127, 130, 141, 143):
            with self.subTest(status=status):
                result, events, records = probe(ss_status=status)
                self.assertEqual(result.returncode, status)
                self.assertEqual(len(events), 4)
                self.assertIsNone(records["psql"])
                self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_partial_query_failure_missing_table_timeout_never_emit_marker(self):
        for status in (1, 2, 3, 124, 127, 130, 141, 143):
            with self.subTest(status=status):
                result, events, records = probe(psql_status=status,
                    output="database_name | search_tools_staging\n", warning="mock missing table or query failure\n")
                self.assertEqual(result.returncode, status)
                self.assertEqual(len(events), 5)
                self.assertIsNotNone(records["psql"])
                self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_zero_exit_partial_033_zero_counts_wrong_instance_stay_visible(self):
        cases = [(LISTENER, NORMAL.replace(f"{flag} | t", f"{flag} | f")) for flag in FLAGS]
        cases += [("", NORMAL), (LISTENER.replace("pid=101", "pid=202"), NORMAL),
                  (LISTENER, NORMAL.replace("products | 100", "products | 0")),
                  (LISTENER, NORMAL.replace("search_tools_staging", "wrong_database")),
                  (LISTENER, NORMAL.replace("read_only | on", "read_only | off"))]
        for listener, output in cases:
            with self.subTest(listener=listener, output=output):
                result, events, _ = probe(listener=listener, output=output)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, listener + output + "STAGING_SOURCE_CHECK_OK\n")
                self.assertEqual(len(events), 5)  # no create/restore/drop follows marker
        section = command()[1]
        for gate in ("không có PID/listener", "không khớp", "chín cờ033 phải true",
                     "bảng chính thiếu/rỗng", "Marker chỉ exit 0", "Chưa phát create/restore/drop"):
            self.assertIn(gate, section)

    def test_warnings_survive_for_manual_stop(self):
        result, events, _ = probe(warning="mock source warning\n")
        self.assertEqual(result.stderr, "mock source warning\n")
        self.assertEqual(len(events), 5)
        self.assertIn("warning/lỗi/marker thiếu thì dừng", command()[1])

    def test_pinned_sql_and_catalog_checks_cover_each_named_033_object(self):
        source = SQL.read_text()
        self.assertEqual(hashlib.sha256(SQL.read_bytes()).hexdigest(),
                         "05a868759de8611451bd3aec060fc2b6d70bce31f5ed54943a41fe9b394f2951")
        code = "\n".join(line for line in source.splitlines() if not line.startswith("--"))
        self.assertEqual(len([item for item in code.split(";") if item.strip()]), 4)
        for statement in code.split(";"):
            if statement.strip():
                self.assertTrue(statement.lstrip().startswith("SELECT "))
        self.assertEqual(re.findall(r"\bpg_read_file\([^)]*\)", code),
                         ["pg_read_file('postmaster.pid', 0, 64)"])
        migration = (ROOT / "sql/migration_033_regulatory_manual_edit.sql").read_text()
        names = re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)", migration)
        self.assertEqual(len(names), 3)
        for name in names:
            self.assertIn("to_regclass('public." + name + "')", code)
        self.assertEqual(code.count("indisvalid AND indisready"), 3)
        self.assertIn("indisunique", code)
        self.assertIn("tgenabled IN ('O','A') AND tgtype=19", code)
        self.assertIn("tgfoid=to_regprocedure('public.update_regulatory_rule_revision()')", code)
        for flag in FLAGS:
            self.assertEqual(code.count(" AS " + flag), 1)


if __name__ == "__main__":
    unittest.main()
