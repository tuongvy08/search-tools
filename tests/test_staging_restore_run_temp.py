"""Local tests for scripts/staging_restore_run_temp.py (S1.2). No SSH/PostgreSQL/systemd.

External commands are doubles. A separate check compares pg_restore argv with the
documented PostgreSQL 16 options, because doubles cannot reveal options the real
tool rejects.
"""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("restore_run", ROOT / "scripts" / "staging_restore_run_temp.py")
RUN = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RUN)
CANARY = "FAKE_SECRET_CANARY"
WARNING = "WARNING: unexpected " + CANARY

# PostgreSQL 16 pg_restore options the script is allowed to use (documented).
PG_RESTORE16_LONG = {"--exit-on-error", "--single-transaction", "--no-password", "--host", "--port",
                     "--username", "--dbname", "--version"}
PG_RESTORE16_SHORT = {"-e", "-1", "-w", "-h", "-p", "-U", "-d"}
FORBIDDEN_PG_RESTORE = ("--clean", "--create", "--data-only", "--schema-only", "--jobs", "-j", "-c", "-C",
                        "--file", "-f", "--list", "-l", "--role", "--superuser")


def counts(**over):
    data = dict(database=RUN.TEMP, temp_bytes=1100000000, products=1262100, stock_items=2290,
                regulatory_rules=6000, regulatory_statuses=6, rules_inactive=3,
                footprint_030=True, footprint_031=True, footprint_032=True)
    data.update({name: False for name in RUN.FLAGS_033 + RUN.NAME_FLAGS_033})
    data.update(over)
    return data


def probe(fault=None, inline=None):
    """Run main() with doubles. Returns (code, stdout, trace, argvs)."""
    trace, argvs = [], []
    with tempfile.TemporaryDirectory(prefix="restore-run-") as folder:
        data_dir = Path(folder).resolve()
        (data_dir / "postmaster.pid").write_bytes(b"3993835\nfixture\n")
        source = dict(database=RUN.SOURCE, oid=17001, version_num=160015, port=5432,
                      data_directory=str(data_dir), read_only="on", bytes=1228209175, encoding="UTF8",
                      provider="c", collate="en_US.UTF-8", ctype="en_US.UTF-8", tablespace="pg_default",
                      schema033=True, temp_exists=fault != "temp_missing")
        state = {"restored": False}

        def temp_meta():
            return dict(database=RUN.TEMP, oid=RUN.TEMP_OID, owner="postgres", label=RUN.LABEL,
                        encoding="UTF8", provider="c", collate="en_US.UTF-8", ctype="en_US.UTF-8",
                        tablespace="pg_default", public_connect=False,
                        user_tables=(0 if not state["restored"] else 40) if fault != "temp_not_empty" else 40,
                        user_objects=(0 if not state["restored"] else 90) if fault != "temp_other_objects" else 3,
                        sessions=2 if fault == "temp_sessions" else 0)

        def run(args, **kwargs):
            argvs.append(list(args))
            stdout, code, stderr = "", 0, ""
            if args[0] == "/usr/bin/ss":
                gate = "listener"
                stdout = 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7))'
            elif args[0] == "/usr/lib/postgresql/16/bin/pg_restore":
                gate, stdout = "version", "pg_restore (PostgreSQL) 16.15 (Ubuntu 16.15-1.pgdg22.04+1)"
            elif "/usr/lib/postgresql/16/bin/pg_restore" in args:
                gate = "restore"
                if fault == "restore_nonzero":
                    code, stderr = 1, "pg_restore: error: could not execute query: ERROR:  role \"x\" does not exist\n" + CANARY
                elif fault == "restore_stderr":
                    stderr = WARNING
                elif fault == "restore_stdout":
                    stdout = "restored"
                elif fault == "restore_timeout":
                    raise subprocess.TimeoutExpired(args, 3600)
                else:
                    state["restored"] = True
            elif "/usr/lib/postgresql/16/bin/psql" in args:
                db = args[args.index("-d") + 1]
                sql = args[-1]
                if db == RUN.SOURCE:
                    gate, stdout = "source", json.dumps(source)
                elif "temp_bytes" in sql:
                    gate = "counts"
                    over = {}
                    if fault == "empty_table":
                        over["products"] = 0
                    elif fault == "has_033":
                        over["revision_trigger"] = True
                    elif fault == "same_name_other_shape":
                        over["name_revision_column"] = True  # e.g. nullable column: exact-shape flag stays False
                    elif fault == "missing_030":
                        over["footprint_030"] = False
                    stdout = json.dumps(counts(**over))
                else:
                    gate, stdout = "temp", json.dumps(temp_meta())
            else:
                raise AssertionError("Unexpected executable " + str(args))
            if inline == gate:
                stdout += " " + WARNING
            trace.append(gate)
            return subprocess.CompletedProcess(args, code, stdout, stderr)

        base = MagicMock()
        base.is_dir.return_value = True
        base.is_symlink.return_value = False
        base.resolve.return_value = base
        archive_fd = MagicMock()
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(RUN.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(RUN.sys, "argv", ["-", "--restore"]))
            stack.enter_context(patch.object(RUN, "runtime_guard"))
            stack.enter_context(patch.object(RUN, "DATA", data_dir))
            stack.enter_context(patch.object(RUN, "archive_guard", return_value=("same",)))
            stack.enter_context(patch.object(RUN, "BASE", base))
            stack.enter_context(patch.object(RUN.shutil, "disk_usage", return_value=SimpleNamespace(
                free=1 if fault == "disk_low" else 60000000000)))
            stack.enter_context(patch.object(RUN.subprocess, "run", side_effect=run))
            stack.enter_context(patch("builtins.open", return_value=archive_fd))
            stack.enter_context(redirect_stdout(output))
            code = RUN.main()
    return code, output.getvalue(), trace, argvs


class RestoreRunTests(unittest.TestCase):
    def test_happy_path_restores_once_into_temp_and_reports_counts(self):
        code, out, trace, argvs = probe()
        self.assertEqual(code, 0, out)
        self.assertEqual(trace.count("restore"), 1)
        self.assertLess(trace.index("temp"), trace.index("restore"))
        self.assertIn("STAGING_TEMP_RESTORE_COMPLETED", out)
        self.assertIn("temp_count_products=1262100", out)
        self.assertIn("restore_elapsed_seconds=", out)
        self.assertNotIn("HOLD", out)

    def test_restore_argv_targets_only_temp_with_documented_options(self):
        _, _, _, argvs = probe()
        restores = [a for a in argvs if "/usr/lib/postgresql/16/bin/pg_restore" in a and "--version" not in a]
        self.assertEqual(len(restores), 1)
        args = restores[0]
        tool_args = args[args.index("/usr/lib/postgresql/16/bin/pg_restore") + 1:]
        self.assertIn("--dbname=" + RUN.TEMP, tool_args)
        self.assertIn("--single-transaction", tool_args)
        self.assertIn("--exit-on-error", tool_args)
        self.assertNotIn(RUN.SOURCE, " ".join(tool_args))
        self.assertNotIn(str(RUN.ARCHIVE), tool_args)  # archive arrives on stdin, not as an argument
        for token in tool_args:
            name = token.split("=", 1)[0]
            if token.startswith("--"):
                self.assertIn(name, PG_RESTORE16_LONG, token)
            elif token.startswith("-") and len(token) == 2:
                self.assertIn(token, PG_RESTORE16_SHORT, token)
            self.assertNotIn(name, FORBIDDEN_PG_RESTORE)
        joined = " ".join(args)
        for secret in ("postgres://", "postgresql://", "PGPASSWORD", "DATABASE_URL"):
            self.assertNotIn(secret, joined)

    def test_every_psql_call_is_read_only_and_never_writes_to_source(self):
        _, _, _, argvs = probe()
        for args in argvs:
            if "/usr/lib/postgresql/16/bin/psql" in args:
                self.assertTrue(any("default_transaction_read_only=on" in a for a in args), args)

    def test_gate_failures_before_restore_never_run_pg_restore(self):
        for fault in ("temp_missing", "temp_not_empty", "temp_other_objects", "temp_sessions", "disk_low"):
            with self.subTest(fault=fault):
                code, out, trace, _ = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertNotIn("restore", trace)
                self.assertNotIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", out)

    def test_restore_failures_hold_without_cleanup_or_retry(self):
        for fault in ("restore_nonzero", "restore_stderr", "restore_stdout", "restore_timeout"):
            with self.subTest(fault=fault):
                code, out, trace, argvs = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertEqual(trace.count("restore"), 1 if fault != "restore_timeout" else 0)
                self.assertIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", out)
                self.assertNotIn(CANARY, out)
                joined = " ".join(" ".join(a) for a in argvs).lower()
                for word in ("dropdb", "drop database", "pg_terminate", "--clean", "createdb"):
                    self.assertNotIn(word, joined)

    def test_error_is_reported_as_fixed_category_never_raw_text(self):
        _, out, _, _ = probe("restore_nonzero")
        self.assertIn("restore_error_category=ROLE_MISSING", out)
        self.assertNotIn("could not execute", out)
        self.assertNotIn(CANARY, out)

    def test_categories_are_fixed_labels_and_data_values_never_surface(self):
        secret = 'pg_restore: error: could not execute query: ERROR:  invalid input syntax for type integer: "SECRETMARK"'
        self.assertEqual(RUN.restore_error_category(secret), "UNCLASSIFIED")
        self.assertNotIn("SECRETMARK", RUN.restore_error_category(secret))
        for text, label in (('ERROR:  role "x" does not exist', "ROLE_MISSING"),
                            ("ERROR:  permission denied for schema public", "PERMISSION_DENIED"),
                            ('ERROR:  relation "a" already exists', "OBJECT_ALREADY_EXISTS"),
                            ("could not extend file: No space left on device", "DISK_OR_MEMORY")):
            self.assertEqual(RUN.restore_error_category(text), label)
        self.assertEqual(RUN.restore_error_category(None), "UNCLASSIFIED")

    def test_hold_message_says_state_is_unknown(self):
        _, out, _, _ = probe("restore_timeout")
        self.assertIn("UNKNOWN", out)

    def test_name_presence_flags_cover_every_033_footprint(self):
        self.assertEqual(len(RUN.name_flag_sql()), len(RUN.FLAGS_033))
        self.assertEqual(set(RUN.name_flag_sql()), set(RUN.NAME_FLAGS_033))

    def test_post_restore_data_problems_hold(self):
        for fault, gate in (("empty_table", "TEMP_MAIN_TABLES_PRESENT_NONEMPTY"),
                            ("has_033", "TEMP_MUST_BE_PRE_033"), ("missing_030", "TEMP_MISSING_030_032"),
                            ("same_name_other_shape", "TEMP_MUST_BE_PRE_033")):
            with self.subTest(fault=fault):
                code, out, _, _ = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertIn("DUNG: " + gate, out)
                self.assertIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", out)

    def test_warning_inline_in_any_read_gate_stops(self):
        for gate in ("listener", "version", "source", "temp"):
            with self.subTest(gate=gate):
                code, out, trace, _ = probe(inline=gate)
                self.assertEqual(code, 1, out)
                self.assertNotIn("restore", trace)
                self.assertNotIn(CANARY, out)
        code, out, _, _ = probe(inline="counts")
        self.assertEqual(code, 1, out)
        self.assertIn("HOLD", out)
        self.assertNotIn("STAGING_TEMP_RESTORE_COMPLETED", out)

    def test_requires_root_and_explicit_flag(self):
        with patch.object(RUN.os, "geteuid", return_value=1000), patch.object(RUN.sys, "argv", ["-", "--restore"]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(RUN.main(), 1)
        self.assertIn("ROOT_EXPLICIT_RESTORE_ONLY", out.getvalue())
        with patch.object(RUN.os, "geteuid", return_value=0), patch.object(RUN.sys, "argv", ["-"]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(RUN.main(), 1)

    def test_pinned_constants_have_real_shapes(self):
        self.assertRegex(RUN.ARCHIVE_SHA, r"[0-9a-f]{64}")
        self.assertRegex(RUN.COMMIT, r"[0-9a-f]{40}")
        self.assertEqual(RUN.TEMP_OID, 18535)
        self.assertEqual(RUN.TEMP, "search_tools_restore_test_20261001_064530")


if __name__ == "__main__":
    unittest.main()
