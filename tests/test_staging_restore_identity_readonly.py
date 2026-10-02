"""Staging runtime guard tests; all /proc/systemd/Git/server I/O is mocked."""

import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts/staging_restore_identity_readonly.py"
SPEC = importlib.util.spec_from_file_location("restore_identity_under_test", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DSN = b"postgresql://search_tools_staging:FAKE_PASSWORD_ONLY@localhost:5433/search_tools_staging"


class RuntimeIdentityTests(unittest.TestCase):
    def probe(self, web_dsn=DSN, worker_dsn=DSN, failure=None):
        trace = []

        def run(args, **kwargs):
            trace.append((args, kwargs))
            if failure == "command_secret_exception":
                raise RuntimeError("FAKE_PASSWORD_ONLY")
            if failure == "command_timeout":
                raise subprocess.TimeoutExpired(args, 20)
            if args[0] == "systemctl":
                web = args[2] == MODULE.WEB
                pid = "101" if web else "102"
                if failure == "missing_pid" and web:
                    pid = "0"
                state = "inactive" if failure == "inactive_worker" and not web else "active"
                owner = "root" if failure == "wrong_service_user" else "deploy"
                output = f"ActiveState={state}\nSubState=running\nMainPID={pid}\nUser={owner}"
                return subprocess.CompletedProcess(args, 0, output)
            if args[0] == "git":
                output = "other_sha" if failure == "wrong_commit" else MODULE.TARGET
                code = 1 if failure == "git_failure" else 0
                return subprocess.CompletedProcess(args, code, output)
            raise AssertionError("Unexpected command: " + args[0])

        cwd = "/opt/production-release" if failure == "wrong_cwd" else "/srv/search-tools"
        output = io.StringIO()
        with patch.object(MODULE, "expected_cwd", return_value="/srv/search-tools"), \
                patch.object(MODULE, "process_cwd", return_value=cwd), \
                patch.object(MODULE, "process_dsn", side_effect=[web_dsn, worker_dsn]), \
                patch.object(MODULE.subprocess, "run", side_effect=run), \
                patch.object(MODULE.os, "geteuid", return_value=0), \
                patch.object(MODULE.sys, "argv", ["-"]), redirect_stdout(output):
            code = MODULE.main()
        return code, output.getvalue(), trace

    def test_correct_runtime_reports_only_whitelisted_identity(self):
        code, output, trace = self.probe()
        self.assertEqual(code, 0)
        self.assertIn("live_database=search_tools_staging", output)
        self.assertIn("live_host=localhost", output)
        self.assertIn("live_port=5433", output)
        self.assertIn("STAGING_RUNTIME_IDENTITY_OK", output)
        self.assertNotIn("FAKE_PASSWORD_ONLY", output)
        self.assertNotIn(DSN.decode(), output)
        self.assertEqual([args[0] for args, _ in trace], ["systemctl", "git", "systemctl", "git"])
        for args, kwargs in trace:
            self.assertNotIn("FAKE_PASSWORD_ONLY", repr(args))
            self.assertEqual(kwargs["env"], MODULE.READ_ENV)

    def test_wrong_database_or_remote_host_never_connects_or_passes(self):
        for raw in (DSN.replace(b"/search_tools_staging", b"/production"),
                    DSN.replace(b"localhost", b"production.invalid")):
            with self.subTest(raw=raw):
                code, output, trace = self.probe(web_dsn=raw, worker_dsn=raw)
                self.assertEqual(code, 1)
                self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", output)
                self.assertNotIn("FAKE_PASSWORD_ONLY", output)
                self.assertEqual(len(trace), 2)

    def test_different_web_worker_dsn_is_rejected(self):
        code, output, _ = self.probe(worker_dsn=DSN.replace(b":5433/", b":5434/"))
        self.assertEqual(code, 1)
        self.assertIn("STAGING_WEB_WORKER_SAME_DSN", output)

    def test_runtime_and_command_faults_stop_without_secret_or_retry(self):
        for fault in ("command_secret_exception", "command_timeout", "missing_pid",
                      "inactive_worker", "wrong_service_user", "wrong_cwd", "wrong_commit", "git_failure"):
            with self.subTest(fault=fault):
                code, output, trace = self.probe(failure=fault)
                self.assertEqual(code, 1)
                self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", output)
                self.assertNotIn("FAKE_PASSWORD_ONLY", output)
                self.assertLessEqual(len(trace), 3)

    def test_dsn_override_and_malformed_inputs_are_rejected(self):
        for raw in (DSN + b"?host=production.invalid", DSN + b"?service=production",
                    DSN + b"?sslmode=prefer&sslmode=require", DSN + b"#fragment",
                    DSN.replace(b":5433/", b":99999/"), b"\xff", b"not-a-dsn"):
            with self.subTest(raw=raw):
                with self.assertRaises(MODULE.GateFailure):
                    MODULE.staging_endpoint(raw)

    def test_local_host_variants(self):
        for raw, expected in ((DSN, ("localhost", 5433)),
                              (DSN.replace(b"localhost", b"127.0.0.1"), ("127.0.0.1", 5433)),
                              (DSN.replace(b"localhost", b"[::1]"), ("::1", 5433))):
            self.assertEqual(MODULE.staging_endpoint(raw), expected)

    def test_runtime_environment_requires_exactly_one_nonempty_dsn(self):
        for content in (b"OTHER=value\0", b"DATABASE_URL=\0",
                        b"DATABASE_URL=" + DSN + b"\0DATABASE_URL=" + DSN + b"\0"):
            with self.subTest(content=content), patch.object(MODULE, "Path") as path:
                path.return_value.read_bytes.return_value = content
                with self.assertRaises(MODULE.GateFailure):
                    MODULE.process_dsn("101")
        with patch.object(MODULE, "Path") as path:
            path.return_value.read_bytes.return_value = b"DATABASE_URL=" + DSN + b"\0"
            self.assertEqual(MODULE.process_dsn("101"), DSN)

    def test_root_and_no_extra_arguments_are_required(self):
        for uid, argv in ((1000, ["-"]), (0, ["-", "--production"])):
            with self.subTest(uid=uid, argv=argv), \
                    patch.object(MODULE.os, "geteuid", return_value=uid), \
                    patch.object(MODULE.sys, "argv", argv), \
                    patch.object(MODULE, "inspect_identity") as inspect, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(MODULE.main(), 1)
                inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
