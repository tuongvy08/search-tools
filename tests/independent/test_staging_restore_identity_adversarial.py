"""Independent S0.4 probes. No actual proc, systemd, SSH or DB access.

Exercise the real identity/proc/DSN helpers with a restricted filesystem and
subprocess double; unexpected reads/commands fail closed in the fixture.
"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import importlib.util
import io
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/staging_restore_identity_readonly.py"
SPEC = importlib.util.spec_from_file_location("independent_s04_identity", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CANARY = "INDEPENDENT_S04_NOT_A_REAL_PASSWORD"
DSN = ("postgresql://deploy:" + CANARY
       + "@127.0.0.1:5433/search_tools_staging").encode()


def probe(fault=None, web=DSN, worker=DSN):
    calls, reads = [], []

    class FakePath:
        def __init__(self, *parts):
            self.path = str(PurePosixPath(*parts))

        def __str__(self):
            return self.path

        def __eq__(self, other):
            return isinstance(other, FakePath) and self.path == other.path

        def is_dir(self):
            reads.append(("is_dir", self.path))
            return self.path == "/srv/search-tools" and fault != "live_missing"

        def is_symlink(self):
            reads.append(("is_symlink", self.path))
            return fault == "live_symlink"

        def resolve(self, strict=False):
            reads.append(("resolve", self.path, strict))
            if self.path == "/srv/search-tools":
                return FakePath("/opt/other") if fault == "ancestor_symlink" else self
            if self.path in ("/proc/101/cwd", "/proc/102/cwd"):
                if fault == "proc_disappeared":
                    raise FileNotFoundError(CANARY)
                if fault == "worker_wrong_cwd" and self.path == "/proc/102/cwd":
                    return FakePath("/srv/production")
                return FakePath("/srv/search-tools")
            raise AssertionError("Unapproved resolve: " + self.path)

        def read_bytes(self):
            reads.append(("read_bytes", self.path))
            if self.path not in ("/proc/101/environ", "/proc/102/environ"):
                raise AssertionError("Unapproved file read: " + self.path)
            if fault == "proc_permission":
                raise PermissionError(CANARY)
            if fault == "env_missing":
                return b"OTHER_SECRET=" + CANARY.encode() + b"\0"
            value = web if self.path == "/proc/101/environ" else worker
            result = b"DATABASE_URL=" + value + b"\0OTHER_SECRET=" + CANARY.encode() + b"\0"
            return result * 2 if fault == "duplicate_env" else result

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if fault == "command_timeout":
            raise subprocess.TimeoutExpired(args, 20, stderr=CANARY)
        if fault == "command_nonzero":
            return subprocess.CompletedProcess(args, 1, CANARY, CANARY)
        if args[0] == "systemctl":
            role = "web" if args[2] == MODULE.WEB else "worker"
            assert args == ["systemctl", "show", MODULE.WEB if role == "web" else MODULE.WORKER,
                            "--no-pager", "-p", "ActiveState", "-p", "SubState",
                            "-p", "MainPID", "-p", "User"]
            pid = "101" if role == "web" else "102"
            if fault == "missing_worker_pid" and role == "worker":
                pid = "0"
            if fault == "nondigit_pid":
                pid = CANARY
            state = "failed" if fault == "failed_worker" and role == "worker" else "active"
            substate = "exited" if fault == "not_running" else "running"
            text = f"ActiveState={state}\nSubState={substate}\nMainPID={pid}\nUser=deploy\n"
            return subprocess.CompletedProcess(args, 0, text, "")
        assert args == ["git", "-c", "safe.directory=/srv/search-tools",
                        "-C", "/srv/search-tools", "rev-parse", "HEAD"]
        return subprocess.CompletedProcess(args, 0,
                                           "0" * 40 if fault == "wrong_commit" else MODULE.TARGET,
                                           "")

    out, err = io.StringIO(), io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(MODULE, "Path", FakePath))
        stack.enter_context(patch.object(MODULE, "LIVE", FakePath("/srv/search-tools")))
        stack.enter_context(patch.object(MODULE.subprocess, "run", side_effect=run))
        stack.enter_context(patch.object(MODULE.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(MODULE.sys, "argv", ["-"]))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(redirect_stderr(err))
        code = MODULE.main()
    return code, out.getvalue(), err.getvalue(), calls, reads


class IdentityAdversarialTests(unittest.TestCase):
    def assert_safe(self, result):
        code, out, err, calls, reads = result
        self.assertNotIn(CANARY, out + err + repr(calls))
        self.assertEqual(err, "")
        for args, kwargs in calls:
            self.assertIn(args[0], ("systemctl", "git"))
            self.assertEqual(kwargs["env"], MODULE.READ_ENV)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(kwargs["timeout"], 20)
        self.assertFalse(any(".env" in item[1] for item in reads))

    def assert_stop(self, result, gate, call_count):
        code, out, _, calls, _ = result
        self.assert_safe(result)
        self.assertEqual(code, 1, out)
        self.assertEqual(out, f"DUNG: {gate} (no secret output, no retry)\n")
        self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", out)
        self.assertEqual(len(calls), call_count)

    def test_valid_canary_environment_never_leaves_process(self):
        result = probe()
        self.assert_safe(result)
        self.assertEqual(result[0], 0)
        self.assertIn("STAGING_RUNTIME_IDENTITY_OK", result[1])
        self.assertEqual([args[0] for args, _ in result[3]],
                         ["systemctl", "git", "systemctl", "git"])

    def test_web_worker_different_port_stops_without_db_or_retry(self):
        self.assert_stop(probe(worker=DSN.replace(b":5433/", b":5434/")),
                         "STAGING_WEB_WORKER_SAME_DSN", 4)

    def test_wrong_worker_database_stops_without_success(self):
        self.assert_stop(probe(worker=DSN.replace(b"/search_tools_staging", b"/production")),
                         "STAGING_DATABASE_ONLY", 4)

    def test_missing_or_malformed_pid_does_not_read_that_process(self):
        for fault, gate, count in [("missing_worker_pid", "WORKER_PID_PRESENT", 3),
                                   ("nondigit_pid", "WEB_PID_PRESENT", 1)]:
            with self.subTest(fault=fault):
                result = probe(fault)
                self.assert_stop(result, gate, count)
                rejected = "102" if fault == "missing_worker_pid" else CANARY
                self.assertFalse(any("/proc/" + rejected + "/" in item[1]
                                     for item in result[4]))

    def test_errors_and_secret_exception_text_are_redacted(self):
        cases = [("command_timeout", "WEB_SERVICE_READ", 1),
                 ("command_nonzero", "WEB_SERVICE_READ", 1),
                 ("proc_disappeared", "RUNTIME_READ_FAILED", 1),
                 ("proc_permission", "RUNTIME_READ_FAILED", 2),
                 ("env_missing", "RUNTIME_DATABASE_URL_PRESENT", 2),
                 ("duplicate_env", "RUNTIME_DATABASE_URL_PRESENT", 2)]
        for fault, gate, count in cases:
            with self.subTest(fault=fault):
                self.assert_stop(probe(fault), gate, count)

    def test_filesystem_service_and_checkout_mismatches_stop(self):
        cases = [("live_missing", "STAGING_LIVE_DIRECTORY", 0),
                 ("live_symlink", "STAGING_LIVE_DIRECTORY", 0),
                 ("ancestor_symlink", "STAGING_LIVE_DIRECTORY", 0),
                 ("worker_wrong_cwd", "WORKER_STAGING_CWD", 3),
                 ("failed_worker", "WORKER_STAGING_SERVICE", 3),
                 ("not_running", "WEB_STAGING_SERVICE", 1),
                 ("wrong_commit", "WEB_STAGING_TARGET", 2)]
        for fault, gate, count in cases:
            with self.subTest(fault=fault):
                self.assert_stop(probe(fault), gate, count)

    def test_encoded_host_service_and_options_overrides_do_not_pass(self):
        for suffix in (b"?%68ost=production", b"?servicefile=/tmp/fake",
                       b"?options=-c%20search_path=evil", b"?dbname=production",
                       b"?port=5432", b"?sslmode=prefer&sslmode=require"):
            with self.subTest(suffix=suffix):
                self.assert_stop(probe(web=DSN + suffix), "STAGING_DSN_OPTIONS", 2)

    def test_only_prepared_s04_command_uses_staging_and_pinned_input(self):
        doc = (ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md").read_text()
        block = doc.split("## S0.4 —", 1)[1].split("```bash\n", 1)[1].split("```", 1)[0]
        self.assertEqual(shlex.split(block), [
            "ssh", "staging", "sudo -n env LC_ALL=C LANG=C python3 -I -B -",
            "<", str(SOURCE),
        ])


if __name__ == "__main__":
    unittest.main()
