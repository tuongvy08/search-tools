"""Local CLI mocks only; never execute sudo/psql/SSH or open a database."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
CLIENT = "/usr/lib/postgresql/16/bin/psql"
SQL = ("SELECT current_database() AS database_name, version() AS postgres_version; "
       "SHOW data_directory; SHOW transaction_read_only; "
       "SELECT pg_database_size(current_database()) AS database_bytes;")


def remote_command():
    section = DOCUMENT.read_text().split("## S0.5 —", 1)[1]
    block = section.split("```bash\n", 1)[1].split("```", 1)[0].strip()
    args = shlex.split(block)
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Wrong SSH alias/shape")
    return args[2]


class PgMetadataCommandTests(unittest.TestCase):
    def probe(self, exit_code=0):
        with tempfile.TemporaryDirectory(prefix="pg-metadata-test-") as folder:
            base = Path(folder)
            trace = base / "trace"
            sudo = base / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$@" >> "$TRACE"\n'
                'printf "database_name=search_tools_staging\\npostgres_version=PostgreSQL 16.15\\n"\n'
                'printf "data_directory=/fixture/data\\ntransaction_read_only=on\\ndatabase_bytes=100000000\\n"\n'
                f"exit {exit_code}\n"
            )
            sudo.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh", "-c", remote_command()],
                env={"PATH": str(base), "TRACE": str(trace),
                     "PGHOST": "production.invalid", "PGSERVICE": "production",
                     "PGPASSWORD": "FAKE_PASSWORD_ONLY", "PGOPTIONS": "-c default_transaction_read_only=off"},
                capture_output=True, text=True, timeout=5,
            )
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_fixed_target_and_readonly_metadata_only(self):
        result, trace = self.probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(trace, [
            "-n", "-u", "postgres", "env", "-i", "LC_ALL=C", "LANG=C",
            "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000",
            CLIENT, "-X", "-w", "-h", "/var/run/postgresql", "-p", "5432",
            "-U", "postgres", "-d", "search_tools_staging", "-v", "ON_ERROR_STOP=1",
            "-P", "pager=off", "-c", SQL,
        ])
        self.assertTrue(result.stdout.endswith("STAGING_PG_METADATA_OK\n"))
        self.assertNotIn("FAKE_PASSWORD_ONLY", result.stdout + result.stderr + repr(trace))

    def test_partial_output_error_never_prints_success(self):
        for code in (1, 2, 77, 127):
            with self.subTest(code=code):
                result, trace = self.probe(exit_code=code)
                self.assertEqual(result.returncode, code)
                self.assertNotIn("STAGING_PG_METADATA_OK", result.stdout)
                self.assertEqual(trace.count(CLIENT), 1)

    def test_sql_has_only_metadata_select_and_show(self):
        statements = [statement.strip() for statement in SQL.split(";") if statement.strip()]
        self.assertEqual(len(statements), 4)
        for statement in statements:
            self.assertTrue(statement.startswith(("SELECT ", "SHOW ")))
        for token in ("pg_dump", "pg_restore", "DATABASE_URL", ".env", "DROP", "CREATE", "ALTER"):
            self.assertNotIn(token, remote_command())


if __name__ == "__main__":
    unittest.main()
