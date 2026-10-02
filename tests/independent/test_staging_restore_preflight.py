"""Independent S0 safety probes. All process/filesystem/server access is mocked.

Execute with .venv/bin/python -B -m unittest discover -s tests/independent
-p test_staging_restore_preflight.py -v. No app imports or dotenv loading.
"""
import ast
import contextlib
import hashlib
import io
import pathlib
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


SOURCE_PATH = pathlib.Path(
    "/Volumes/DATA/Development/_ops/search-tools/preflight_readonly.py"
)
SOURCE_BYTES = SOURCE_PATH.read_bytes()
SOURCE = SOURCE_BYTES.decode()
CODE = compile(ast.parse(SOURCE), str(SOURCE_PATH), "exec")
PIN = "6453e9010056aa46ca52567320705d589a2a772b1e23dd90e37d208fea941430"
WEB = "search-tools-staging.service"
WORKER = "search-tools-import-worker.service"
FAKE_SECRET = "INDEPENDENT_FAKE_PASSWORD_NOT_REAL"
STAGING_DSN = (
    "postgresql://staging_user:" + FAKE_SECRET
    + "@127.0.0.1:5432/search_tools_staging"
)


def probe(*, dsn=STAGING_DSN, worker_dsn=None, pid="101", git_failure=False,
          query_failure=False, query_timeout=False, schema033=True,
          restore_version_failure=False):
    calls = []
    output = io.StringIO()

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs))
        command = args[0]
        rc = 0
        if command == "systemctl":
            process_id = pid if args[2] == WEB else "102"
            text = (
                "ActiveState=active\nSubState=running\nMainPID="
                + process_id + "\nUser=search-tools"
            )
        elif command == "git":
            rc = 1 if git_failure else 0
            text = "a" * 40
        elif command in ("pg_dump", "pg_restore"):
            rc = 1 if command == "pg_restore" and restore_version_failure else 0
            text = command + " (PostgreSQL) 16.4"
        elif command == "psql":
            if query_timeout:
                raise subprocess.TimeoutExpired(args, 20, stderr=FAKE_SECRET)
            rc = 1 if query_failure else 0
            sql = args[args.index("-c") + 1]
            if "current_database()" in sql:
                text = kwargs["env"]["PGDATABASE"] + " | 200 MB | 16.4"
            elif "UNION ALL" in sql:
                text = "030 | t\n031 | t\n032 | t\n033 | " + (
                    "t" if schema033 else "f"
                )
            elif "to_regclass" in sql:
                text = "t"
            else:
                text = "0 | 0"
        else:
            raise AssertionError("Unmocked subprocess: " + command)
        return SimpleNamespace(returncode=rc, stdout=text, stderr=FAKE_SECRET)

    def fake_environ(path):
        if str(path) == "/proc/101/environ":
            connection = dsn
        elif str(path) == "/proc/102/environ":
            connection = dsn if worker_dsn is None else worker_dsn
        else:
            raise AssertionError("Unexpected read: " + str(path))
        return ("DATABASE_URL=" + connection + "\0OTHER_SECRET="
                + FAKE_SECRET + "\0").encode()

    exit_code = 0
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(sys, "argv", [
            "preflight", WEB, WORKER, "/srv/backups/search-tools"
        ]))
        stack.enter_context(mock.patch("subprocess.run", side_effect=fake_run))
        stack.enter_context(mock.patch.object(
            pathlib.Path, "read_bytes", fake_environ
        ))
        stack.enter_context(mock.patch.object(
            pathlib.Path, "resolve", return_value=pathlib.Path("/srv/search-tools")
        ))
        stack.enter_context(mock.patch.object(pathlib.Path, "is_dir", return_value=True))
        stack.enter_context(mock.patch.object(pathlib.Path, "exists", return_value=True))
        stack.enter_context(mock.patch("shutil.disk_usage", return_value=SimpleNamespace(
            free=100 * 1024 ** 3
        )))
        stack.enter_context(mock.patch("os.path.isdir", return_value=True))
        stack.enter_context(mock.patch("os.walk", return_value=[]))
        stack.enter_context(contextlib.redirect_stdout(output))
        try:
            exec(CODE, {"__name__": "__main__"})
        except SystemExit as error:
            exit_code = error.code
    return SimpleNamespace(exit_code=exit_code, output=output.getvalue(), calls=calls)


class StagingRestorePreflightTests(unittest.TestCase):
    def assert_safe_calls(self, result):
        self.assertNotIn(FAKE_SECRET, result.output)
        for args, kwargs in result.calls:
            self.assertNotIn(FAKE_SECRET, " ".join(args))
            if args[0] == "systemctl":
                self.assertEqual(args[1], "show")
                self.assertIn(args[2], (WEB, WORKER))
            elif args[0] == "git":
                self.assertEqual(args[-2:], ["rev-parse", "HEAD"])
            elif args[0] in ("pg_dump", "pg_restore"):
                self.assertEqual(args[1:], ["--version"])
            elif args[0] == "psql":
                sql = args[args.index("-c") + 1]
                self.assertTrue(sql.strip().startswith("SELECT"))
                self.assertIn("default_transaction_read_only=on", kwargs["env"]["PGOPTIONS"])
                self.assertIn("-X", args)
            else:
                self.fail("Not a read-only command")

    def assert_stopped(self, result):
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("DỪNG:", result.output)
        self.assertNotIn("PREFLIGHT COMPLETED", result.output)
        self.assert_safe_calls(result)

    def test_hash_and_syntax(self):
        self.assertEqual(hashlib.sha256(SOURCE_BYTES).hexdigest(), PIN)
        ast.parse(SOURCE)

    def test_normal_readonly_and_secret_not_in_output_or_argv(self):
        result = probe()
        self.assertEqual(result.exit_code, 0)
        self.assertIn("filesystem_free_GiB= 100.0", result.output)
        self.assert_safe_calls(result)

    def test_web_worker_database_mismatch_stops_before_psql(self):
        result = probe(worker_dsn=STAGING_DSN + "_other")
        self.assert_stopped(result)
        self.assertFalse(any(args[0] == "psql" for args, _ in result.calls))

    def test_missing_pid_stops_before_psql(self):
        result = probe(pid="0")
        self.assert_stopped(result)
        self.assertEqual(len(result.calls), 1)

    def test_malformed_secret_configuration_is_redacted(self):
        result = probe(dsn=STAGING_DSN + "?password=" + FAKE_SECRET)
        self.assert_stopped(result)
        self.assertFalse(any(args[0] == "psql" for args, _ in result.calls))

    def test_query_nonzero_stops_without_retry(self):
        result = probe(query_failure=True)
        self.assert_stopped(result)
        self.assertEqual(sum(args[0] == "psql" for args, _ in result.calls), 1)

    def test_query_timeout_stops_without_retry_and_redacts(self):
        result = probe(query_timeout=True)
        self.assert_stopped(result)
        self.assertEqual(sum(args[0] == "psql" for args, _ in result.calls), 1)

    def test_pg_restore_version_error_stops(self):
        result = probe(restore_version_failure=True)
        self.assert_stopped(result)

    def test_033_false_is_visible_not_proof_of_completion(self):
        result = probe(schema033=False)
        self.assertIn("033 | f", result.output)
        self.assert_safe_calls(result)
        # OPERATIONS requires agent review of this output, not machine PASS.

    def test_failed_git_command_must_stop_before_database_access(self):
        result = probe(git_failure=True)
        self.assert_stopped(result)
        self.assertFalse(any(args[0] == "psql" for args, _ in result.calls))

    def test_staging_services_pointing_to_production_must_not_query_it(self):
        result = probe(dsn=(
            "postgresql://staging_user:" + FAKE_SECRET
            + "@production.invalid:5432/search_tools_production"
        ))
        pg_calls = [(args, kwargs) for args, kwargs in result.calls if args[0] == "psql"]
        self.assertEqual(len(pg_calls), 0, (
            "Mock recorded %s psql calls targeting production.invalid / "
            "search_tools_production; exit_code=%s" % (len(pg_calls), result.exit_code)
        ))
        self.assert_stopped(result)


if __name__ == "__main__":
    unittest.main()
