"""Independent output-channel probes. Entire flow uses mocks; no PG/server I/O."""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "independent_create_output", ROOT / "scripts/staging_restore_create_temp.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CANARY = "INDEPENDENT_OUTPUT_FAKE_SECRET"
WARNING = "WARNING: unexpected database diagnostic " + CANARY + "\n"


def output_probe(stage, channel):
    source = dict(database=M.SOURCE, oid=17001, version_num=160015, port=5432,
                  data_directory=str(M.DATA), read_only="on", bytes=1228176407,
                  encoding="UTF8", provider="c", collate="en_US.UTF-8",
                  ctype="en_US.UTF-8", tablespace="pg_default", schema033=True,
                  temp_exists=False)
    temp = dict(database=M.TEMP, oid=17002, owner="postgres", label=M.LABEL,
                encoding="UTF8", provider="c", collate="en_US.UTF-8",
                ctype="en_US.UTF-8", tablespace="pg_default", public_connect=False,
                user_tables=0)
    trace = []

    def run(args, **kwargs):
        if args[0] == "/usr/bin/locale":
            gate, stdout = "locale", "C\nen_US.utf8\n"
        elif args[0] == "/usr/lib/postgresql/16/bin/createdb":
            gate, stdout = "version", "createdb (PostgreSQL) 16.15\n"
        elif "/usr/lib/postgresql/16/bin/createdb" in args:
            gate, stdout = "create", ""
        elif "/usr/lib/postgresql/16/bin/psql" in args:
            if args[args.index("-d") + 1] == M.SOURCE:
                gate, stdout = "source", json.dumps(source)
            elif any("read_only=off" in value for value in args):
                gate, stdout = "acl", "REVOKE\nCOMMENT\n"
            else:
                gate, stdout = "temp", json.dumps(temp)
        else:
            raise AssertionError("Unexpected executable")
        trace.append((gate, args))
        stderr = ""
        if gate == stage:
            if channel == "stdout":
                stdout += WARNING
            else:
                stderr = WARNING
        return subprocess.CompletedProcess(args, 0, stdout, stderr)

    base = MagicMock()
    base.is_dir.return_value = True
    base.is_symlink.return_value = False
    base.resolve.return_value = base
    output = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(M.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(M.sys, "argv", ["-", "--create"]))
        stack.enter_context(patch.object(M, "runtime_guard"))
        stack.enter_context(patch.object(M, "instance_guard", return_value="3993835"))
        stack.enter_context(patch.object(M, "archive_guard", return_value=("unchanged",)))
        stack.enter_context(patch.object(M, "BASE", base))
        stack.enter_context(patch.object(M.shutil, "disk_usage",
                                        return_value=SimpleNamespace(free=60_000_000_000)))
        stack.enter_context(patch.object(M.subprocess, "run", side_effect=run))
        stack.enter_context(redirect_stdout(output))
        code = M.main()
    return code, output.getvalue(), [gate for gate, _ in trace]


class OutputAdversarialTests(unittest.TestCase):
    def test_stderr_warning_each_stage_stops_without_secret_or_retry(self):
        for stage in ("locale", "version", "source", "create", "acl", "temp"):
            with self.subTest(stage=stage):
                code, output, trace = output_probe(stage, "stderr")
                self.assertEqual(code, 1)
                self.assertIn("DUNG:", output)
                self.assertNotIn(CANARY, output)
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
                self.assertLessEqual(trace.count("create"), 1)
                self.assertEqual(trace[-1], stage)
                if stage in ("create", "acl", "temp"):
                    self.assertIn("HOLD:", output)
                else:
                    self.assertNotIn("create", trace)

    def test_stdout_warning_at_create_must_hold_before_acl(self):
        code, output, trace = output_probe("create", "stdout")
        self.assertEqual(code, 1, f"trace={trace}; output={output}")
        self.assertIn("HOLD:", output)
        self.assertNotIn("acl", trace)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
        self.assertNotIn(CANARY, output)

    def test_stdout_warning_at_acl_must_hold_before_postcheck(self):
        code, output, trace = output_probe("acl", "stdout")
        self.assertEqual(code, 1, f"trace={trace}; output={output}")
        self.assertIn("HOLD:", output)
        self.assertNotIn("temp", trace)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
        self.assertNotIn(CANARY, output)

    def test_version_stdout_warning_must_stop_before_create(self):
        code, output, trace = output_probe("version", "stdout")
        self.assertEqual(code, 1, f"trace={trace}; output={output}")
        self.assertNotIn("create", trace)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)


if __name__ == "__main__":
    unittest.main()
