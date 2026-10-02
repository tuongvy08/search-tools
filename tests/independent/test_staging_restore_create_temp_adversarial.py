"""Independent S1.1 probes: local stdlib/subprocess fixtures only, never PG/SSH.

Red tests represent the contract's stop-on-abnormal-output requirement.
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "independent_s11_create", ROOT / "scripts/staging_restore_create_temp.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
WARNING = ('WARNING: database "search_tools_staging" has a collation version mismatch\n'
           'DETAIL: The database was created using collation version 2.35, '
           'but the operating system provides version 2.39.\n')
CANARY = "INDEPENDENT_S11_FAKE_SECRET"


def probe(source_change=None, free=60_000_000_000, fault=None, warning_gate=None):
    source = dict(database=M.SOURCE, oid=17001, version_num=160015, port=5432,
                  data_directory=str(M.DATA), read_only="on", bytes=1228176407,
                  encoding="UTF8", provider="c", collate="en_US.UTF-8",
                  ctype="en_US.UTF-8", tablespace="pg_default", schema033=True,
                  temp_exists=False)
    source.update(source_change or {})
    dest = dict(database=M.TEMP, oid=17002, owner="postgres", label=M.LABEL,
                encoding="UTF8", provider="c", collate="en_US.UTF-8",
                ctype="en_US.UTF-8", tablespace="pg_default", public_connect=False,
                user_tables=0)
    trace, emitted = [], []

    def run(args, **kwargs):
        trace.append((args, kwargs))
        if args[0] == "/usr/bin/locale":
            gate, output = "locale", "C\nen_US.utf8"
        elif args[0] == "/usr/lib/postgresql/16/bin/createdb":
            gate, output = "version", "createdb (PostgreSQL) 16.15"
        elif "/usr/lib/postgresql/16/bin/createdb" in args:
            gate, output = "create", ""
        elif "/usr/lib/postgresql/16/bin/psql" in args:
            database = args[args.index("-d") + 1]
            if database == M.SOURCE:
                gate, output = "source", json.dumps(source)
            elif any("read_only=off" in value for value in args):
                gate, output = "acl", ""
            else:
                gate, output = "temp", json.dumps(dest)
        else:
            raise AssertionError("Unapproved executable")
        if fault == gate:
            raise subprocess.TimeoutExpired(args, 20, stderr=CANARY)
        warning = WARNING if warning_gate == gate else ""
        if warning:
            emitted.append((gate, warning, kwargs["stderr"]))
        # DEVNULL accurately means subprocess.run returns stderr=None.
        return subprocess.CompletedProcess(args, 0, output,
            None if kwargs["stderr"] == subprocess.DEVNULL else warning)

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
                                        return_value=SimpleNamespace(free=free)))
        stack.enter_context(patch.object(M.subprocess, "run", side_effect=run))
        stack.enter_context(redirect_stdout(output))
        code = M.main()
    return code, output.getvalue(), trace, emitted


def writes(trace):
    return [args for args, _ in trace
            if any("read_only=off" in value for value in args)]


class CreateIndependentTests(unittest.TestCase):
    def test_existing_db_and_wrong_server_never_write(self):
        for change in ({"temp_exists": True}, {"database": "production"},
                       {"port": 5433}, {"data_directory": "/srv/other/data"},
                       {"schema033": False}, {"oid": True}, {"bytes": True}):
            with self.subTest(change=change):
                code, output, trace, _ = probe(source_change=change)
                self.assertEqual(code, 1)
                self.assertEqual(writes(trace), [])
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)

    def test_buffer_scales_with_source_not_compressed_dump(self):
        for size in (1228176407, 4 * 1024**3):
            required = max(5 * 1024**3, 4 * size)
            for free in (required - 1, required):
                with self.subTest(size=size, free=free):
                    code, output, trace, _ = probe(source_change={"bytes": size}, free=free)
                    self.assertEqual(code, 1 if free < required else 0)
                    if free < required:
                        self.assertEqual(writes(trace), [])
                        self.assertIn("DEFAULT_TABLESPACE_FREE_SPACE", output)

    def test_create_acl_postcheck_timeouts_hold_without_delete_or_rerun(self):
        for fault in ("create", "acl", "temp"):
            with self.subTest(fault=fault):
                code, output, trace, _ = probe(fault=fault)
                self.assertEqual(code, 1)
                self.assertIn("HOLD: CREATE attempted", output)
                self.assertNotIn(CANARY, output)
                self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
                self.assertEqual(sum("/usr/lib/postgresql/16/bin/createdb" in args
                                     for args, _ in trace), 2)  # version + one attempt
                self.assertFalse(any("dropdb" in str(args) or "DROP DATABASE" in str(args)
                                     for args, _ in trace))

    def test_source_read_error_does_not_write_or_leak(self):
        code, output, trace, _ = probe(fault="source")
        self.assertEqual(code, 1)
        self.assertEqual(writes(trace), [])
        self.assertNotIn(CANARY, output)
        self.assertNotIn("HOLD:", output)

    def test_real_archive_missing_permission_symlink_and_mutation(self):
        with tempfile.TemporaryDirectory(prefix="s11-archive-", dir=HERE) as folder:
            path = Path(folder).resolve() / "archive.dump"
            with patch.object(M, "ARCHIVE", path), self.assertRaises(FileNotFoundError):
                M.archive_guard()
            content = b"independent fixture, no company data"
            path.write_bytes(content)
            os.utime(path, ns=(M.ARCHIVE_MTIME_NS, M.ARCHIVE_MTIME_NS))
            with patch.object(M, "ARCHIVE", path), patch.object(M, "ARCHIVE_BYTES", len(content)), \
                    patch.object(M, "ARCHIVE_SHA", hashlib.sha256(content).hexdigest()):
                M.archive_guard()
                with patch.object(M.os, "open", side_effect=PermissionError(CANARY)), \
                        self.assertRaises(PermissionError):
                    M.archive_guard()
                path.write_bytes(b"x" * len(content))
                os.utime(path, ns=(M.ARCHIVE_MTIME_NS, M.ARCHIVE_MTIME_NS))
                with self.assertRaises(M.GateFailure):
                    M.archive_guard()
            link = path.parent / "link.dump"
            link.symlink_to(path)
            with patch.object(M, "ARCHIVE", link), self.assertRaises(M.GateFailure):
                M.archive_guard()

    def test_bad_names_and_query_write_scope_stop_before_io(self):
        for name in (M.SOURCE, "production", "search_tools_restore_test_20261001", CANARY):
            with self.subTest(name=name), patch.object(M, "TEMP", name), \
                    self.assertRaises(M.GateFailure):
                M.validate_name()
        with patch.object(M.subprocess, "run") as run:
            for name in (M.SOURCE, "postgres", "production"):
                with self.subTest(name=name), self.assertRaises(M.GateFailure):
                    M.query(name, "SELECT 1", write=True)
            run.assert_not_called()

    def test_runtime_different_worker_missing_pid_and_wrong_host_rejected(self):
        live = MagicMock()
        live.is_dir.return_value = True
        live.is_symlink.return_value = False
        live.resolve.return_value = live
        live.__str__.return_value = "/srv/search-tools"
        good = ("postgresql://deploy:" + CANARY
                + "@127.0.0.1:5432/search_tools_staging").encode()
        for fault in ("worker_temp", "pid_missing", "wrong_host", "dsn_override"):
            with self.subTest(fault=fault):
                def checked(args, gate):
                    if args[0] == "systemctl":
                        pid = "0" if fault == "pid_missing" else (
                            "101" if args[2] == M.WEB else "102")
                        return "ActiveState=active\nSubState=running\nUser=deploy\nMainPID=" + pid
                    self.assertEqual(args[0], "git")
                    return M.COMMIT

                def path(*parts):
                    raw = good
                    if fault == "worker_temp" and parts[1] == "102":
                        raw = raw.replace(M.SOURCE.encode(), M.TEMP.encode())
                    elif fault == "wrong_host":
                        raw = raw.replace(b"127.0.0.1", b"production.invalid")
                    elif fault == "dsn_override":
                        raw += b"?%68ost=production.invalid"
                    return SimpleNamespace(resolve=lambda strict=True: live,
                        read_bytes=lambda: b"DATABASE_URL=" + raw + b"\0")

                with patch.object(M, "LIVE", live), patch.object(M, "Path", side_effect=path), \
                        patch.object(M, "checked", side_effect=checked), self.assertRaises(M.GateFailure):
                    M.runtime_guard()

    def test_real_pid_fixture_wrong_listener_or_pid_prefix_rejected(self):
        with tempfile.TemporaryDirectory(prefix="s11-pid-", dir=HERE) as folder:
            data = Path(folder).resolve()
            (data / "postmaster.pid").write_bytes(b"3993835\nmetadata only\n")
            for host, pid in (("127.0.0.1", "39938350"), ("127.0.0.1", "202"),
                              ("192.0.2.1", "3993835")):
                line = f'LISTEN 0 200 {host}:5432 0.0.0.0:* users:(("postgres",pid={pid},fd=7))'
                with self.subTest(host=host, pid=pid), patch.object(M, "DATA", data), \
                        patch.object(M, "checked", return_value=line), self.assertRaises(M.GateFailure):
                    M.instance_guard()

    def test_no_delete_command_without_po_approval(self):
        code, output, trace, _ = probe(fault="acl")
        self.assertEqual(code, 1)
        joined = repr([args for args, _ in trace])
        for prohibited in ("DROP DATABASE", "dropdb", "pg_terminate_backend", "FORCE",
                           "pg_dump", "pg_restore", "restart", "stop", "CREATE ROLE"):
            self.assertNotIn(prohibited, joined)
        self.assertIn("no automatic drop/cleanup/rerun", output)

    def test_zero_exit_source_warning_must_stop_before_create(self):
        code, output, trace, emitted = probe(warning_gate="source")
        self.assertEqual(len(emitted), 1)
        # Requirement: an abnormal result at ANY step must stop, even exit=0.
        self.assertEqual(code, 1,
            f"source warning discarded; writes={len(writes(trace))}; output={output}")
        self.assertEqual(writes(trace), [])

    def test_real_local_zero_exit_warning_must_not_be_silently_accepted(self):
        # Actual subprocess I/O, but only local Python emitting a synthetic warning.
        args = [str(ROOT / ".venv/bin/python"), "-B", "-c",
                "import sys; sys.stderr.write(" + repr(WARNING) + "); print('metadata_ok')"]
        with self.assertRaises(M.GateFailure):
            M.checked(args, "STAGING_METADATA_READ")


if __name__ == "__main__":
    unittest.main()
