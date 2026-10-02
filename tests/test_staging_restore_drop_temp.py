"""Local tests for scripts/staging_restore_drop_temp.py (S3). No SSH/PostgreSQL/systemd."""
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
spec = importlib.util.spec_from_file_location("restore_drop", ROOT / "scripts" / "staging_restore_drop_temp.py")
DROP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DROP)
CANARY = "FAKE_SECRET_CANARY"
WARNING = "WARNING: unexpected " + CANARY
# PostgreSQL 16 dropdb options (docs: app-dropdb): the script may use only these.
DROPDB16_LONG = {"--echo", "--force", "--interactive", "--version", "--if-exists", "--help", "--host", "--port",
                 "--username", "--no-password", "--password", "--maintenance-db"}
DROPDB16_SHORT = {"-e", "-f", "-i", "-V", "-h", "-p", "-U", "-w", "-W"}
DBS = ["postgres", "search_tools_production_copy", "search_tools_restore_test_20261001_064530",
       "search_tools_staging", "template0", "template1"]


def probe(fault=None, inline=None):
    trace, argvs = [], []
    with tempfile.TemporaryDirectory(prefix="drop-run-") as folder:
        data_dir = Path(folder).resolve()
        (data_dir / "postmaster.pid").write_bytes(b"3993835\nfixture\n")
        state = {"dropped": False}

        def source():
            names = [n for n in DBS if not (state["dropped"] and n == DROP.TEMP)]
            if fault == "temp_missing":
                names = [n for n in names if n != DROP.TEMP]
            if fault == "other_db_also_gone" and state["dropped"]:
                names = [n for n in names if n != "postgres"]
            return dict(database=DROP.SOURCE, oid=17001 if fault != "oid_changed_after" or not state["dropped"] else 17999,
                        version_num=160015, port=5432, data_directory=str(data_dir), read_only="on",
                        bytes=1228209175, encoding="UTF8", provider="c", collate="en_US.UTF-8",
                        ctype="en_US.UTF-8", tablespace="pg_default", schema033=True,
                        temp_exists=DROP.TEMP in names, databases=names)

        def temp():
            return dict(database=DROP.TEMP, oid=18535 if fault != "wrong_oid" else 18536, owner="postgres",
                        label=DROP.LABEL if fault != "wrong_label" else "other", encoding="UTF8",
                        provider="c", collate="en_US.UTF-8", ctype="en_US.UTF-8", tablespace="pg_default",
                        public_connect=False, user_tables=48, sessions=2 if fault == "sessions" else 0)

        def run(args, **kwargs):
            argvs.append(list(args))
            stdout, code, stderr = "", 0, ""
            if args[0] == "/usr/bin/ss":
                gate = "listener"
                stdout = 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7))'
            elif args[0] == "/usr/lib/postgresql/16/bin/dropdb":
                gate, stdout = "version", "dropdb (PostgreSQL) 16.15 (Ubuntu 16.15-1.pgdg22.04+1)"
            elif "/usr/lib/postgresql/16/bin/dropdb" in args:
                gate = "drop"
                if fault == "drop_nonzero":
                    code, stderr = 1, "dropdb: error: database removal failed: " + CANARY
                elif fault == "drop_timeout":
                    raise subprocess.TimeoutExpired(args, 120)
                elif fault == "drop_stdout":
                    stdout = "dropped"
                else:
                    state["dropped"] = True
            elif "/usr/lib/postgresql/16/bin/psql" in args:
                db = args[args.index("-d") + 1]
                if db == DROP.SOURCE:
                    gate, stdout = "source", json.dumps(source())
                else:
                    gate, stdout = "temp", json.dumps(temp())
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
        free = iter([60664930304, 61369000000] * 3)
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(DROP.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(DROP.sys, "argv", ["-", "--drop"]))
            stack.enter_context(patch.object(DROP, "runtime_guard"))
            stack.enter_context(patch.object(DROP, "DATA", data_dir))
            stack.enter_context(patch.object(DROP, "archive_guard", return_value=("same",)))
            stack.enter_context(patch.object(DROP, "BASE", base))
            stack.enter_context(patch.object(DROP.shutil, "disk_usage", side_effect=lambda p: SimpleNamespace(free=next(free))))
            stack.enter_context(patch.object(DROP.subprocess, "run", side_effect=run))
            stack.enter_context(redirect_stdout(output))
            code = DROP.main()
    return code, output.getvalue(), trace, argvs


class DropTests(unittest.TestCase):
    def test_happy_path_drops_once_and_confirms_only_temp_removed(self):
        code, out, trace, _ = probe()
        self.assertEqual(code, 0, out)
        self.assertEqual(trace.count("drop"), 1)
        self.assertIn("STAGING_TEMP_DATABASE_DROPPED", out)
        self.assertIn("temp_database_exists_after=NO", out)
        self.assertIn("freed_bytes=", out)
        self.assertNotIn("HOLD", out)

    def test_drop_argv_targets_only_temp_without_force_and_with_documented_options(self):
        _, _, _, argvs = probe()
        drops = [a for a in argvs if "/usr/lib/postgresql/16/bin/dropdb" in a and "--version" not in a]
        self.assertEqual(len(drops), 1)
        tool_args = drops[0][drops[0].index("/usr/lib/postgresql/16/bin/dropdb") + 1:]
        self.assertEqual(tool_args[-1], DROP.TEMP)
        self.assertEqual([a for a in tool_args if DROP.SOURCE in a], ["--maintenance-db=" + DROP.SOURCE])
        for token in tool_args:
            if token.startswith("--"):
                self.assertIn(token.split("=", 1)[0], DROPDB16_LONG, token)
            elif token.startswith("-"):
                self.assertIn(token, DROPDB16_SHORT, token)
        for forbidden in ("--force", "-f", "--if-exists", "-i", "--interactive"):
            self.assertNotIn(forbidden, tool_args)
        joined = " ".join(drops[0]).lower()
        for word in ("pg_terminate", "drop database", "postgresql://", "pgpassword", "database_url"):
            self.assertNotIn(word, joined)

    def test_every_psql_call_is_read_only(self):
        _, _, _, argvs = probe()
        for args in argvs:
            if "/usr/lib/postgresql/16/bin/psql" in args:
                self.assertTrue(any("default_transaction_read_only=on" in a for a in args), args)

    def test_stops_before_drop_on_any_precondition_failure(self):
        for fault in ("temp_missing", "wrong_oid", "wrong_label", "sessions"):
            with self.subTest(fault=fault):
                code, out, trace, _ = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertNotIn("drop", trace)
                self.assertNotIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", out)

    def test_inline_warning_in_a_read_gate_stops_before_drop(self):
        for gate in ("listener", "version", "source", "temp"):
            with self.subTest(gate=gate):
                code, out, trace, _ = probe(inline=gate)
                self.assertEqual(code, 1, out)
                self.assertNotIn("drop", trace)
                self.assertNotIn(CANARY, out)

    def test_drop_failures_hold_without_retry_or_force(self):
        for fault in ("drop_nonzero", "drop_stdout", "drop_timeout"):
            with self.subTest(fault=fault):
                code, out, trace, argvs = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", out)
                self.assertNotIn(CANARY, out)
                self.assertLessEqual(trace.count("drop"), 1)
                self.assertNotIn("--force", " ".join(" ".join(a) for a in argvs))

    def test_postcheck_failures_hold_and_never_claim_success(self):
        for fault in ("other_db_also_gone", "oid_changed_after"):
            with self.subTest(fault=fault):
                code, out, _, _ = probe(fault)
                self.assertEqual(code, 1, out)
                self.assertIn("DUNG: POSTDROP_ONLY_TEMP_REMOVED", out)
                self.assertIn("HOLD", out)
                self.assertNotIn("STAGING_TEMP_DATABASE_DROPPED", out)

    def test_requires_root_and_explicit_flag(self):
        with patch.object(DROP.os, "geteuid", return_value=1000), patch.object(DROP.sys, "argv", ["-", "--drop"]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(DROP.main(), 1)
        self.assertIn("ROOT_EXPLICIT_DROP_ONLY", out.getvalue())
        with patch.object(DROP.os, "geteuid", return_value=0), patch.object(DROP.sys, "argv", ["-"]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(DROP.main(), 1)

    def test_pinned_constants_match_the_other_scripts(self):
        other = importlib.util.spec_from_file_location("o", ROOT / "scripts" / "staging_restore_run_temp.py")
        mod = importlib.util.module_from_spec(other)
        other.loader.exec_module(mod)
        for name in ("SOURCE", "TEMP", "TEMP_OID", "ARCHIVE_SHA", "ARCHIVE_BYTES", "ARCHIVE_MTIME_NS", "COMMIT", "LABEL", "DATA", "LIVE"):
            self.assertEqual(getattr(DROP, name), getattr(mod, name), name)
        self.assertRegex(DROP.ARCHIVE_SHA, r"[0-9a-f]{64}")


if __name__ == "__main__":
    unittest.main()
