"""Strict-output regression: a diagnostic in any position must stop both scripts.

Local only (no SSH/PostgreSQL/systemd). The warning is placed inline, on its own
line, before/after valid output, and on stderr.
"""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IDENTITY = load("strict_identity", "staging_restore_identity_readonly.py")
CREATE = load("strict_create", "staging_restore_create_temp.py")
CANARY = "FAKE_SECRET_CANARY"
WARNING = "WARNING: unexpected " + CANARY
SERVICE = "ActiveState=active\nSubState=running\nMainPID=101\nUser=deploy"
LISTENER = 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7))'
COMMIT = "2ec9b6c940c89802cce9fc77150e4f2b8dcfff64"


def variants(valid):
    lines = valid.split("\n")
    out = {"own_line_after": valid + "\n" + WARNING, "own_line_before": WARNING + "\n" + valid,
           "inline_end": valid + " " + WARNING, "inline_last_line": "\n".join(lines[:-1] + [lines[-1] + " " + WARNING])}
    if len(lines) > 1:
        out["own_line_middle"] = "\n".join(lines[:1] + [WARNING] + lines[1:])
        out["inline_first_line"] = "\n".join([lines[0] + " " + WARNING] + lines[1:])
    return out


def completed(stdout="", stderr="", code=0):
    return lambda args, **kwargs: subprocess.CompletedProcess(args, code, stdout, stderr)


class StrictOutputTests(unittest.TestCase):
    def assert_stops(self, module, gate, stdout="", stderr=""):
        with patch.object(module.subprocess, "run", side_effect=completed(stdout, stderr)):
            with self.assertRaises(module.GateFailure) as caught:
                module.checked(["x"], gate)
        self.assertNotIn(CANARY, str(caught.exception))

    def test_identity_valid_outputs_pass(self):
        for gate, valid in (("WEB_SERVICE_READ", SERVICE), ("WORKER_SERVICE_READ", SERVICE),
                            ("WEB_COMMIT_READ", COMMIT)):
            with patch.object(IDENTITY.subprocess, "run", side_effect=completed(valid + "\n")):
                self.assertEqual(IDENTITY.checked(["x"], gate), valid)

    def test_identity_stdout_warning_any_position_stops(self):
        for gate, valid in (("WEB_SERVICE_READ", SERVICE), ("WORKER_COMMIT_READ", COMMIT)):
            for name, stdout in variants(valid).items():
                with self.subTest(gate=gate, position=name):
                    self.assert_stops(IDENTITY, gate, stdout)

    def test_identity_stderr_warning_stops(self):
        for gate, valid in (("WEB_SERVICE_READ", SERVICE), ("WEB_COMMIT_READ", COMMIT)):
            with self.subTest(gate=gate):
                self.assert_stops(IDENTITY, gate, valid, WARNING + "\n")

    def test_identity_real_subprocess_stderr_warning_stops(self):
        script = "import sys; sys.stderr.write(%r); print(%r)" % (WARNING, COMMIT)
        with patch.object(IDENTITY, "READ_ENV", {"PATH": "/usr/bin:/bin"}):
            with self.assertRaises(IDENTITY.GateFailure):
                IDENTITY.checked([__import__("sys").executable, "-B", "-c", script], "WEB_COMMIT_READ")

    def test_create_valid_listener_and_service_pass(self):
        with patch.object(CREATE.subprocess, "run", side_effect=completed(LISTENER + "\n")):
            self.assertEqual(CREATE.checked(["x"], "STAGING_LISTENER_READ"), LISTENER)
        with patch.object(CREATE.subprocess, "run", side_effect=completed(SERVICE)):
            self.assertEqual(CREATE.checked(["x"], "WEB_SERVICE_READ"), SERVICE)

    def test_create_stdout_warning_any_position_stops(self):
        cases = (("STAGING_LISTENER_READ", LISTENER), ("WEB_SERVICE_READ", SERVICE),
                 ("WORKER_COMMIT_READ", COMMIT), ("SOURCE_LOCALE_AVAILABLE", "C\nen_US.utf8"),
                 ("CREATEDB_VERSION", "createdb (PostgreSQL) 16.15"),
                 ("TEMP_METADATA_WRITE", "REVOKE\nCOMMENT"))
        for gate, valid in cases:
            for name, stdout in variants(valid).items():
                with self.subTest(gate=gate, position=name):
                    self.assert_stops(CREATE, gate, stdout)

    def test_create_command_and_metadata_reject_any_output(self):
        self.assert_stops(CREATE, "TEMP_CREATE_COMMAND", WARNING)
        self.assert_stops(CREATE, "TEMP_CREATE_COMMAND", " ".join([WARNING, WARNING]))

    def test_create_stderr_warning_stops(self):
        for gate, valid in (("STAGING_LISTENER_READ", LISTENER), ("WEB_COMMIT_READ", COMMIT)):
            with self.subTest(gate=gate):
                self.assert_stops(CREATE, gate, valid, WARNING)

    def test_create_listener_rejects_extra_fields_and_unknown_shape(self):
        for stdout in (LISTENER + " extra", LISTENER.replace("users:", "users: "),
                       LISTENER + "\n\n" + LISTENER, "garbage"):
            with self.subTest(stdout=stdout):
                self.assert_stops(CREATE, "STAGING_LISTENER_READ", stdout)


class PinnedConstantShapeTests(unittest.TestCase):
    def test_archive_sha_is_a_real_sha256_hexdigest(self):
        # Unpatched constant: a mistyped/truncated pin makes the guard unsatisfiable.
        self.assertRegex(CREATE.ARCHIVE_SHA, r"[0-9a-f]{64}")
        self.assertRegex(COMMIT, r"[0-9a-f]{40}")
        self.assertRegex(CREATE.COMMIT, r"[0-9a-f]{40}")
        self.assertRegex(IDENTITY.TARGET, r"[0-9a-f]{40}")

    def test_archive_guard_accepts_matching_file_and_rejects_one_char_change(self):
        import hashlib, os, tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / "a.dump"
            path.write_bytes(b"fixture")
            stat = path.stat()
            digest = hashlib.sha256(b"fixture").hexdigest()
            for sha, ok in ((digest, True), (digest[:-1] + ("0" if digest[-1] != "0" else "1"), False)):
                with patch.object(CREATE, "ARCHIVE", path), patch.object(CREATE, "ARCHIVE_BYTES", 7), \
                        patch.object(CREATE, "ARCHIVE_MTIME_NS", stat.st_mtime_ns), patch.object(CREATE, "ARCHIVE_SHA", sha):
                    if ok:
                        CREATE.archive_guard()
                    else:
                        with self.assertRaises(CREATE.GateFailure):
                            CREATE.archive_guard()


# Options documented for PostgreSQL 16 createdb (https://www.postgresql.org/docs/16/app-createdb.html).
# Mocks cannot reveal an option the real tool rejects, so check argv against the real list.
CREATEDB16_OPTIONS = {"--tablespace", "--echo", "--encoding", "--locale", "--lc-collate", "--lc-ctype",
                      "--icu-locale", "--icu-rules", "--locale-provider", "--owner", "--strategy",
                      "--template", "--version", "--help", "--host", "--port", "--username",
                      "--no-password", "--password", "--maintenance-db"}
CREATEDB16_SHORT = {"-D", "-e", "-E", "-l", "-O", "-S", "-T", "-V", "-h", "-p", "-U", "-w", "-W"}


class RealToolOptionTests(unittest.TestCase):
    def test_createdb_argv_uses_only_documented_pg16_options(self):
        seen = []

        def record(tool, args, write=False):
            seen.append((tool, list(args)))
            raise RuntimeError("stop after building createdb argv")

        with patch.object(CREATE.os, "geteuid", return_value=0), \
                patch.object(CREATE.sys, "argv", ["-", "--create"]), \
                patch.object(CREATE, "runtime_guard"), patch.object(CREATE, "instance_guard", return_value="1"), \
                patch.object(CREATE, "archive_guard", return_value=("x",)), \
                patch.object(CREATE, "source_metadata", return_value={"bytes": 1, "oid": 1}), \
                patch.object(CREATE, "disk_locale_guard", return_value=(10, 5)), \
                patch.object(CREATE, "pg_args", side_effect=record), \
                patch("builtins.print"):
            CREATE.main()
        createdb = [args for tool, args in seen if tool == "createdb"]
        self.assertEqual(len(createdb), 1)
        for token in createdb[0]:
            if token.startswith("--"):
                self.assertIn(token.split("=", 1)[0], CREATEDB16_OPTIONS, token)
            elif token.startswith("-"):
                self.assertIn(token, CREATEDB16_SHORT, token)
        self.assertNotIn("--connection-limit", " ".join(createdb[0]))


if __name__ == "__main__":
    unittest.main()
