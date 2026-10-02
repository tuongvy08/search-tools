"""Creation guards/trace using local mocks only; no sudo, service, PG, or server I/O."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


PATH = Path(__file__).resolve().parents[1] / "scripts/staging_restore_create_temp.py"
SPEC = importlib.util.spec_from_file_location("create_temp_under_test", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def source_meta():
    return {"database": M.SOURCE, "oid": 17000, "version_num": 160015,
            "port": 5432, "data_directory": str(M.DATA), "read_only": "on",
            "bytes": 1228176407, "encoding": "UTF8", "provider": "c",
            "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8",
            "tablespace": "pg_default", "schema033": True, "temp_exists": False}


def temp_meta():
    return {"database": M.TEMP, "oid": 38000, "owner": "postgres", "label": M.LABEL,
            "public_connect": False, "user_tables": 0, "encoding": "UTF8",
            "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8", "provider": "c", "tablespace": "pg_default"}


def is_create(args):
    return "/usr/lib/postgresql/16/bin/createdb" in args and "--version" not in args


class CreateTempTests(unittest.TestCase):
    def probe(self, source_changes=None, temp_changes=None, free=60_000_000_000, fail=None):
        src, dst = source_meta(), temp_meta()
        src.update(source_changes or {})
        dst.update(temp_changes or {})
        trace = []

        def checked(args, gate, timeout=20):
            trace.append((args, gate, timeout))
            if fail == gate:
                raise M.GateFailure(gate)
            if fail == "secret_exception":
                raise RuntimeError("FAKE_PASSWORD_ONLY")
            if args[:2] == ["/usr/bin/locale", "-a"]:
                return "C\nC.utf8\nen_US.utf8"
            if args[:2] == ["/usr/lib/postgresql/16/bin/createdb", "--version"]:
                return "createdb (PostgreSQL) 16.15"
            if "/usr/lib/postgresql/16/bin/createdb" in args:
                return ""
            if "-d" in args:
                database = args[args.index("-d") + 1]
                if database == M.SOURCE:
                    return json.dumps(src)
                if any("default_transaction_read_only=off" in arg for arg in args):
                    return ""
                return json.dumps(dst)
            raise AssertionError("Unexpected command")

        base = MagicMock()
        base.is_dir.return_value = True
        base.is_symlink.return_value = False
        base.resolve.return_value = base
        runtime_error = M.GateFailure("STAGING_RUNTIME") if fail == "runtime" else None
        fingerprint = (1, 2, 0o100600, 0, 0, M.ARCHIVE_BYTES, M.ARCHIVE_MTIME_NS)
        archive_error = M.GateFailure("ARCHIVE_HASH_UNCHANGED") if fail == "archive" else None
        output = io.StringIO()
        with patch.object(M.os, "geteuid", return_value=0), patch.object(M.sys, "argv", ["-", "--create"]), \
                patch.object(M, "runtime_guard", side_effect=runtime_error), \
                patch.object(M, "instance_guard", return_value="3993835"), \
                patch.object(M, "archive_guard", side_effect=archive_error, return_value=fingerprint), \
                patch.object(M, "BASE", base), patch.object(M.shutil, "disk_usage", return_value=SimpleNamespace(free=free)), \
                patch.object(M, "checked", side_effect=checked), redirect_stdout(output):
            code = M.main()
        return code, output.getvalue(), trace

    def assert_no_create(self, trace):
        self.assertFalse(any(is_create(args)
                             for args, _, _ in trace))

    def test_creates_only_named_empty_private_temp_db(self):
        code, output, trace = self.probe()
        self.assertEqual(code, 0)
        self.assertIn("STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)", output)
        create = [args for args, _, _ in trace if is_create(args)]
        self.assertEqual(len(create), 1)
        self.assertEqual(create[0][-1], M.TEMP)
        for option in ("--maintenance-db=" + M.SOURCE, "--template=template0", "--owner=postgres",
                       "--encoding=UTF8", "--locale-provider=libc", "--lc-collate=en_US.UTF-8",
                       "--lc-ctype=en_US.UTF-8", "--tablespace=pg_default"):
            self.assertIn(option, create[0])
        for args, _, _ in trace:
            joined = " ".join(args)
            self.assertNotIn("pg_restore", joined)
            self.assertNotIn("pg_dump", joined)
            self.assertNotIn("dropdb", joined)
            self.assertNotIn("FAKE_PASSWORD_ONLY", joined)
            if "/usr/lib/postgresql/16/bin/psql" in args and any("read_only=off" in arg for arg in args):
                self.assertEqual(args[args.index("-d") + 1], M.TEMP)
                self.assertIn('REVOKE CONNECT ON DATABASE "' + M.TEMP + '"', args[-1])

    def test_bad_source_metadata_or_existing_temp_blocks_create(self):
        for changes in ({"database": "production"}, {"version_num": 140024}, {"port": 5433},
                        {"data_directory": "/production/data"}, {"read_only": "off"}, {"schema033": False},
                        {"temp_exists": True}, {"temp_exists": None}, {"provider": "i"},
                        {"collate": "C"}, {"tablespace": "unreviewed"}, {"bytes": 0}, {"oid": True}):
            with self.subTest(changes=changes):
                code, output, trace = self.probe(source_changes=changes)
                self.assertEqual(code, 1)
                self.assert_no_create(trace)
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)

    def test_runtime_archive_and_space_faults_block_create(self):
        for fail in ("runtime", "archive", "SOURCE_LOCALE_AVAILABLE", "CREATEDB_VERSION", "secret_exception"):
            with self.subTest(fail=fail):
                code, output, trace = self.probe(fail=fail)
                self.assertEqual(code, 1)
                self.assert_no_create(trace)
                self.assertNotIn("FAKE_PASSWORD_ONLY", output)
        code, _, trace = self.probe(free=5 * 1024**3 - 1)
        self.assertEqual(code, 1)
        self.assert_no_create(trace)

    def test_create_or_postcreate_fault_holds_without_cleanup_or_retry(self):
        for fail in ("TEMP_CREATE_COMMAND", "TEMP_METADATA_WRITE"):
            with self.subTest(fail=fail):
                code, output, trace = self.probe(fail=fail)
                self.assertEqual(code, 1)
                self.assertIn("HOLD: CREATE attempted", output)
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
                self.assertEqual(sum(is_create(args) for args, _, _ in trace), 1)
        for changes in ({"database": M.SOURCE}, {"owner": "application"}, {"oid": 17000},
                        {"public_connect": True}, {"user_tables": 1}, {"label": "other task"}):
            with self.subTest(changes=changes):
                code, output, _ = self.probe(temp_changes=changes)
                self.assertEqual(code, 1)
                self.assertIn("HOLD: CREATE attempted", output)
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)

    def test_write_query_cannot_target_source_or_any_other_db(self):
        with patch.object(M, "checked") as checked:
            for database in (M.SOURCE, "postgres", "production"):
                with self.subTest(database=database), self.assertRaises(M.GateFailure):
                    M.query(database, "SELECT 1", write=True)
            checked.assert_not_called()

    def test_unknown_profile_and_invalid_name_stop_before_io(self):
        for argv in (["-"], ["-", "--production"], ["-", "--create", "production"]):
            with self.subTest(argv=argv), patch.object(M.os, "geteuid", return_value=0), \
                    patch.object(M.sys, "argv", argv), patch.object(M, "runtime_guard") as runtime, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(M.main(), 1)
                runtime.assert_not_called()
        for name in (M.SOURCE, "production", "search_tools_restore_test_20261001_064530;DROP", "FAKE_PASSWORD_ONLY"):
            output = io.StringIO()
            with self.subTest(name=name), patch.object(M, "TEMP", name), \
                    patch.object(M.os, "geteuid", return_value=0), patch.object(M.sys, "argv", ["-", "--create"]), \
                    patch.object(M, "runtime_guard") as runtime, redirect_stdout(output):
                self.assertEqual(M.main(), 1)
                runtime.assert_not_called()
                self.assertNotIn("FAKE_PASSWORD_ONLY", output.getvalue())

    def test_archive_guard_real_fixture_rejects_changed_or_symlink(self):
        with tempfile.TemporaryDirectory(prefix="create-archive-test-") as folder:
            base = Path(folder).resolve()
            path = base / "fake.dump"
            content = b"SYNTHETIC archive fixture; no company data"
            path.write_bytes(content)
            os.utime(path, ns=(M.ARCHIVE_MTIME_NS, M.ARCHIVE_MTIME_NS))
            with patch.object(M, "ARCHIVE", path), patch.object(M, "ARCHIVE_BYTES", len(content)), \
                    patch.object(M, "ARCHIVE_SHA", hashlib.sha256(content).hexdigest()):
                self.assertEqual(M.archive_guard(), M.archive_guard())
                path.write_bytes(b"x" * len(content))
                os.utime(path, ns=(M.ARCHIVE_MTIME_NS, M.ARCHIVE_MTIME_NS))
                with self.assertRaises(M.GateFailure):
                    M.archive_guard()
            link = base / "link.dump"
            link.symlink_to(path)
            with patch.object(M, "ARCHIVE", link), self.assertRaises(M.GateFailure):
                M.archive_guard()

    def test_runtime_guards_wrong_db_host_port_and_inactive_service(self):
        class Live:
            def is_dir(self): return True
            def is_symlink(self): return False
            def resolve(self, strict=True): return self
            def __str__(self): return "/srv/search-tools"
        live = Live()
        dsn = b"postgresql://app:FAKE_PASSWORD_ONLY@127.0.0.1:5432/search_tools_staging"
        for raw, state in ((dsn, "active"), (dsn.replace(b"/search_tools_staging", b"/production"), "active"),
                           (dsn.replace(b"127.0.0.1", b"production.invalid"), "active"),
                           (dsn.replace(b":5432", b":0"), "active"), (dsn + b"?host=production.invalid", "active"),
                           (dsn, "inactive")):
            with self.subTest(raw=raw, state=state):
                def read_command(args, gate):
                    if args[0] == "systemctl":
                        return f"ActiveState={state}\nSubState=running\nUser=deploy\nMainPID=101"
                    self.assertEqual(args[0], "git")
                    return M.COMMIT
                fake_path = SimpleNamespace(resolve=lambda strict=True: live,
                                            read_bytes=lambda: b"DATABASE_URL=" + raw + b"\0")
                with patch.object(M, "LIVE", live), patch.object(M, "Path", return_value=fake_path), \
                        patch.object(M, "checked", side_effect=read_command):
                    if raw == dsn and state == "active":
                        M.runtime_guard()
                    else:
                        with self.assertRaises(M.GateFailure):
                            M.runtime_guard()

    def test_instance_guard_matches_pid_not_substring_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory(prefix="create-instance-test-") as folder:
            data = Path(folder).resolve()
            path = data / "postmaster.pid"
            path.write_bytes(b"3993835\nsynthetic metadata\n")
            for pid, valid in (("3993835", True), ("39938350", False), ("777", False)):
                line = f'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid={pid},fd=7))'
                with self.subTest(pid=pid), patch.object(M, "DATA", data), patch.object(M, "checked", return_value=line):
                    if valid:
                        self.assertEqual(M.instance_guard(), "3993835")
                    else:
                        with self.assertRaises(M.GateFailure):
                            M.instance_guard()
            path.unlink()
            path.symlink_to(data / "other")
            with patch.object(M, "DATA", data), self.assertRaises(OSError):
                M.instance_guard()

    def test_checked_stops_on_zero_exit_warning_without_leaking_secret(self):
        warning = 'WARNING: collation mismatch FAKE_PASSWORD_ONLY\n'
        result = subprocess.CompletedProcess(["fake"], 0, "valid metadata", warning)
        with patch.object(M.subprocess, "run", return_value=result) as run:
            with self.assertRaises(M.GateFailure) as caught:
                M.checked(["fake"], "STAGING_METADATA_READ")
            self.assertEqual(str(caught.exception), "STAGING_METADATA_READ_STDERR_UNEXPECTED")
            self.assertNotIn("FAKE_PASSWORD_ONLY", str(caught.exception))
            self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)

    def test_real_local_warning_is_rejected_and_quiet_output_is_accepted(self):
        with self.assertRaises(M.GateFailure):
            M.checked([sys.executable, "-B", "-c",
                       "import sys; sys.stderr.write('WARNING: synthetic warning\\n'); print('ok')"], "LOCAL_CHECK")
        self.assertEqual(M.checked([sys.executable, "-B", "-c", "print('ok')"], "LOCAL_CHECK"), "ok")

    def test_all_tool_outputs_have_strict_shape_and_reject_diagnostics(self):
        cases = [
            ([], "WEB_SERVICE_READ", "ActiveState=active\nSubState=running\nMainPID=101\nUser=deploy"),
            ([], "WEB_COMMIT_READ", M.COMMIT),
            ([], "STAGING_LISTENER_READ", 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=1,fd=7))'),
            ([], "SOURCE_LOCALE_AVAILABLE", "C\nC.utf8\nen_US.utf8"),
            ([], "CREATEDB_VERSION", "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)"),
            ([], "TEMP_CREATE_COMMAND", ""),
            ([], "TEMP_METADATA_WRITE", "REVOKE\nCOMMENT"),
            (["psql", "-d", M.SOURCE], "STAGING_METADATA_READ", json.dumps(source_meta())),
            (["psql", "-d", M.TEMP], "STAGING_METADATA_READ", json.dumps(temp_meta())),
        ]
        for args, gate, valid in cases:
            with self.subTest(gate=gate):
                M.validate_output(args, gate, valid)
                for extra in ("WARNING: FAKE_PASSWORD_ONLY", "unexpected diagnostic with spaces"):
                    with self.assertRaises(M.GateFailure) as caught:
                        M.validate_output(args, gate, valid + "\n" + extra)
                    self.assertNotIn("FAKE_PASSWORD_ONLY", str(caught.exception))

    def test_metadata_duplicate_or_extra_fields_are_rejected(self):
        for text in (json.dumps(source_meta())[:-1] + ', "database":"search_tools_staging"}',
                     json.dumps(dict(source_meta(), diagnostic="FAKE_PASSWORD_ONLY"))):
            with self.subTest(text=text), self.assertRaises(M.GateFailure):
                M.validate_output(["psql", "-d", M.SOURCE], "STAGING_METADATA_READ", text)


if __name__ == "__main__":
    unittest.main()
