"""Fresh verifier probes. Only local Python and fixtures; never SSH/PG/systemd."""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IDENTITY = load("fresh_identity_probe", "staging_restore_identity_readonly.py")
CREATE = load("fresh_create_probe", "staging_restore_create_temp.py")
CANARY = "FRESH_VERIFIER_FAKE_SECRET"
WARNING = "WARNING: unexpected diagnostic " + CANARY


def identity_probe(channel):
    trace = []

    def run(args, **kwargs):
        trace.append(args)
        stdout = ("ActiveState=active\nSubState=running\nMainPID=101\nUser=deploy\n"
                  if args[0] == "systemctl" else IDENTITY.TARGET)
        stderr = ""
        if len(trace) == 1:
            if channel == "stdout":
                stdout += WARNING + "\n"
            else:
                stderr = WARNING + "\n"
        # Match the actual behavior of DEVNULL, not a fictitious stderr result.
        if kwargs["stderr"] == subprocess.DEVNULL:
            stderr = None
        return subprocess.CompletedProcess(args, 0, stdout, stderr)

    output = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(IDENTITY.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(IDENTITY.sys, "argv", ["-"]))
        stack.enter_context(patch.object(IDENTITY, "expected_cwd", return_value="/srv/search-tools"))
        stack.enter_context(patch.object(IDENTITY, "process_cwd", return_value="/srv/search-tools"))
        stack.enter_context(patch.object(IDENTITY, "process_dsn", return_value=(
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging").encode()))
        stack.enter_context(patch.object(IDENTITY.subprocess, "run", side_effect=run))
        stack.enter_context(redirect_stdout(output))
        code = IDENTITY.main()
    return code, output.getvalue(), [args[0] for args in trace]


def listener_probe(inline_warning):
    trace = []
    with tempfile.TemporaryDirectory(prefix="fresh-listener-", dir=HERE) as folder:
        data = Path(folder).resolve()
        (data / "postmaster.pid").write_bytes(b"3993835\nfixture metadata\n")
        source = dict(database=CREATE.SOURCE, oid=17001, version_num=160015, port=5432,
                      data_directory=str(data), read_only="on", bytes=1228176407,
                      encoding="UTF8", provider="c", collate="en_US.UTF-8",
                      ctype="en_US.UTF-8", tablespace="pg_default", schema033=True,
                      temp_exists=False)
        dest = dict(database=CREATE.TEMP, oid=17002, owner="postgres", label=CREATE.LABEL,
                    encoding="UTF8", provider="c", collate="en_US.UTF-8",
                    ctype="en_US.UTF-8", tablespace="pg_default", public_connect=False,
                    user_tables=0)

        def run(args, **kwargs):
            if args[0] == "/usr/bin/ss":
                gate = "listener"
                stdout = 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7))'
                if inline_warning:
                    stdout += " " + WARNING
            elif args[0] == "/usr/bin/locale":
                gate, stdout = "locale", "C\nen_US.utf8"
            elif args[0] == "/usr/lib/postgresql/16/bin/createdb":
                gate, stdout = "version", "createdb (PostgreSQL) 16.15"
            elif "/usr/lib/postgresql/16/bin/createdb" in args:
                gate, stdout = "create", ""
            elif "/usr/lib/postgresql/16/bin/psql" in args:
                if args[args.index("-d") + 1] == CREATE.SOURCE:
                    gate, stdout = "source", json.dumps(source)
                elif any("read_only=off" in value for value in args):
                    gate, stdout = "acl", "REVOKE\nCOMMENT"
                else:
                    gate, stdout = "temp", json.dumps(dest)
            else:
                raise AssertionError("Unexpected executable")
            trace.append(gate)
            return subprocess.CompletedProcess(args, 0, stdout, "")

        base = MagicMock()
        base.is_dir.return_value = True
        base.is_symlink.return_value = False
        base.resolve.return_value = base
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(CREATE.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(CREATE.sys, "argv", ["-", "--create"]))
            stack.enter_context(patch.object(CREATE, "runtime_guard"))
            stack.enter_context(patch.object(CREATE, "DATA", data))
            stack.enter_context(patch.object(CREATE, "archive_guard", return_value=("unchanged",)))
            stack.enter_context(patch.object(CREATE, "BASE", base))
            stack.enter_context(patch.object(CREATE.shutil, "disk_usage", return_value=SimpleNamespace(free=60000000000)))
            stack.enter_context(patch.object(CREATE.subprocess, "run", side_effect=run))
            stack.enter_context(redirect_stdout(output))
            code = CREATE.main()
    return code, output.getvalue(), trace


class FreshOutputAdversarialTests(unittest.TestCase):
    def test_s04_real_local_stderr_warning_is_not_success(self):
        args = [sys.executable, "-B", "-c",
                "import sys; sys.stderr.write(" + repr(WARNING + "\n") + "); print('valid')"]
        with self.assertRaises(IDENTITY.GateFailure):
            IDENTITY.checked(args, "WEB_SERVICE_READ")

    def test_s04_warning_in_either_channel_stops_identity_flow(self):
        for channel in ("stdout", "stderr"):
            with self.subTest(channel=channel):
                code, output, trace = identity_probe(channel)
                self.assertNotIn(CANARY, output)
                self.assertEqual(code, 1, f"trace={trace}; output={output}")
                self.assertEqual(trace, ["systemctl"])
                self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", output)

    def test_s11_normal_listener_control_completes_empty_create(self):
        code, output, trace = listener_probe(False)
        self.assertEqual(code, 0, output)
        self.assertEqual(trace.count("create"), 1)
        self.assertIn("STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)", output)

    def test_s11_inline_listener_warning_stops_before_any_write(self):
        code, output, trace = listener_probe(True)
        self.assertNotIn(CANARY, output)
        self.assertEqual(code, 1, f"trace={trace}; output={output}")
        self.assertEqual(trace, ["listener"])
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)


if __name__ == "__main__":
    unittest.main()
