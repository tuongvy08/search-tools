"""Source schema/count command review and local shell mocks; no server/DB I/O."""

from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
SOURCE = ROOT / "specs/staging-restore-rehearsal/SOURCE_CHECK.sql"
CLIENT = "/usr/lib/postgresql/16/bin/psql"


def remote_command():
    section = DOCUMENT.read_text().split("## S0.7 —", 1)[1]
    block = section.split("```bash\n", 1)[1].split("```", 1)[0].strip()
    args = shlex.split(block)
    if len(args) != 5 or args[:2] != ["ssh", "staging"] or args[3] != "<":
        raise AssertionError("Wrong SSH alias/redirection shape")
    if args[4] != str(SOURCE):
        raise AssertionError("Wrong SQL source")
    return args[2]


class SourceCommandTests(unittest.TestCase):
    def probe(self, ss_status=0, psql_status=0, metadata_status=0):
        with tempfile.TemporaryDirectory(prefix="source-check-test-") as folder:
            base = Path(folder)
            trace = base / "trace"
            stdin_capture = base / "stdin"
            sudo = base / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$TRACE"\n'
                'if test "$2" = "test"; then\n'
                f"exit {metadata_status}\n"
                'fi\n'
                'if test "$2" = "env"; then\n'
                'printf "LISTEN 0 100 127.0.0.1:5432 0.0.0.0:* users:((postgres,pid=101,fd=5))\\n"\n'
                f"exit {ss_status}\n"
                'fi\n'
                # Reading stdin is simulated by this fixture only; no psql.
                'while IFS= read -r line; do printf "%s\\n" "$line" >> "$STDIN_CAPTURE"; done\n'
                'printf "database_name | search_tools_staging\\npostmaster_pid | 101\\n"\n'
                f"exit {psql_status}\n"
            )
            sudo.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh", "-c", remote_command()],
                env={"PATH": str(base), "TRACE": str(trace), "STDIN_CAPTURE": str(stdin_capture)},
                input=SOURCE.read_text(), capture_output=True, text=True, timeout=5,
            )
            return (result, trace.read_text().splitlines() if trace.exists() else [],
                    stdin_capture.read_text() if stdin_capture.exists() else "")

    def test_readonly_listener_and_explicit_staging_sql(self):
        result, trace, sql = self.probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(trace), 5)
        self.assertEqual(trace[:3], [
            "-n test -f /var/lib/postgresql/16/main/postmaster.pid",
            "-n test ! -L /var/lib/postgresql/16/main/postmaster.pid",
            "-n test -s /var/lib/postgresql/16/main/postmaster.pid",
        ])
        self.assertIn("/usr/bin/ss -H -ltnp sport = :5432", trace[3])
        self.assertIn("-u postgres env -i LC_ALL=C LANG=C", trace[4])
        self.assertIn("default_transaction_read_only=on", trace[4])
        self.assertIn(CLIENT + " -X -w -h /var/run/postgresql -p 5432 -U postgres -d search_tools_staging", trace[4])
        self.assertIn("ON_ERROR_STOP=1", trace[4])
        self.assertEqual(sql, SOURCE.read_text())
        self.assertTrue(result.stdout.endswith("STAGING_SOURCE_CHECK_OK\n"))

    def test_listener_failure_stops_before_sql_without_retry(self):
        result, trace, sql = self.probe(ss_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 4)
        self.assertEqual(sql, "")
        self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_sql_partial_failure_never_prints_success(self):
        for code in (1, 2, 77, 127):
            with self.subTest(code=code):
                result, trace, _ = self.probe(psql_status=code)
                self.assertEqual(result.returncode, code)
                self.assertEqual(len(trace), 5)
                self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_missing_metadata_stops_before_listener_and_sql(self):
        result, trace, sql = self.probe(metadata_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 1)
        self.assertEqual(sql, "")
        self.assertNotIn("STAGING_SOURCE_CHECK_OK", result.stdout)

    def test_sql_has_only_selects_and_bounded_pid_metadata_read(self):
        sql = SOURCE.read_text()
        code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        statements = [item.strip() for item in code.split(";") if item.strip()]
        self.assertEqual(len(statements), 4)
        self.assertTrue(all(item.startswith("SELECT ") for item in statements))
        self.assertIsNone(re.search(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|COPY)\b", code, re.I))
        self.assertEqual(code.count("pg_read_file("), 1)
        self.assertIn("pg_read_file('postmaster.pid', 0, 64)", code)
        self.assertIn("first_line ~ '^[0-9]{1,10}$'", code)
        for table in ("products", "stock_items", "regulatory_rules", "regulatory_statuses"):
            self.assertIn("FROM public." + table, code)

    def test_complete_033_footprint_references(self):
        sql = SOURCE.read_text()
        for name in ("regulatory_rule_manual_keys", "regulatory_rule_manual_events", "manual_protected",
                     "revision", "update_regulatory_rule_revision", "zz_regulatory_rule_revision",
                     "regulatory_manual_key_identity", "regulatory_manual_key_owner", "regulatory_manual_event_history"):
            self.assertIn(name, sql)
        self.assertIn("tgenabled IN ('O','A') AND tgtype=19", sql)
        self.assertIn("indisvalid AND indisready AND indisunique", sql)
        self.assertNotIn("DATABASE_URL", remote_command() + sql)


if __name__ == "__main__":
    unittest.main()
