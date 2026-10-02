"""Exercise the documented first command locally; never invoke SSH or PostgreSQL."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"


class InitialCommandTests(unittest.TestCase):
    def command(self):
        block = DOCUMENT.read_text().split("```bash\n", 1)[1].split("```", 1)[0]
        args = shlex.split(block.strip())
        self.assertEqual(args[:2], ["ssh", "staging"])
        self.assertEqual(len(args), 3)
        self.assertEqual(
            args[2],
            "systemctl is-active search-tools-staging.service && "
            "systemctl is-active search-tools-import-worker.service && df -h -- "
            "/srv/search-tools /srv/backups/search-tools",
        )
        return args[2]

    def run_mock(self, web="active", worker="active", disk_status=0):
        with tempfile.TemporaryDirectory(prefix="restore-command-test-") as folder:
            base = Path(folder)
            trace = base / "trace"
            scripts = {
                "systemctl": (
                    "#!/bin/sh\n"
                    'printf "%s\\n" "systemctl $*" >> "$TRACE"\n'
                    'test "$1" = "is-active" || exit 64\n'
                    'shift\n'
                    # Actual is-active semantics: print one state per requested
                    # unit, succeed if at least one requested unit is active.
                    'any_active=0\n'
                    'for unit in "$@"; do\n'
                    'case "$unit" in\n'
                    'search-tools-staging.service) state="$WEB_STATE";;\n'
                    'search-tools-import-worker.service) state="$WORKER_STATE";;\n'
                    '*) exit 64;;\n'
                    'esac\n'
                    'printf "%s\\n" "$state"\n'
                    'if test "$state" = "active"; then any_active=1; fi\n'
                    'done\n'
                    'test "$any_active" = "1" && exit 0\n'
                    'exit 3\n'
                ),
                "df": (
                    "#!/bin/sh\n"
                    'printf "%s\\n" "df $*" >> "$TRACE"\n'
                    'printf "Filesystem Available\\nmock 20G\\n"\n'
                    f"exit {disk_status}\n"
                ),
            }
            for name, content in scripts.items():
                executable = base / name
                executable.write_text(content)
                executable.chmod(0o700)
            # Only these two fixture executables are discoverable. Absolute /bin/sh
            # evaluates the remote fragment; no ssh, systemctl, df or DB is real.
            result = subprocess.run(
                ["/bin/sh", "-c", self.command()],
                env={"PATH": str(base), "TRACE": str(trace),
                     "WEB_STATE": web, "WORKER_STATE": worker},
                capture_output=True,
                text=True,
                timeout=5,
            )
            events = trace.read_text().splitlines() if trace.exists() else []
            return result, events

    def test_success_only_queries_services_and_disk(self):
        result, events = self.run_mock()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [
            "systemctl is-active search-tools-staging.service",
            "systemctl is-active search-tools-import-worker.service",
            "df -h -- /srv/search-tools /srv/backups/search-tools",
        ])

    def test_failed_web_active_worker_stops_before_worker_and_disk(self):
        result, events = self.run_mock(web="failed", worker="active")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(events, ["systemctl is-active search-tools-staging.service"])

    def test_active_web_inactive_worker_stops_before_disk(self):
        result, events = self.run_mock(web="active", worker="inactive")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(events, [
            "systemctl is-active search-tools-staging.service",
            "systemctl is-active search-tools-import-worker.service",
        ])

    def test_both_inactive_stop_without_retry(self):
        result, events = self.run_mock(web="inactive", worker="failed")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(events), 1)

    def test_disk_error_is_not_success_or_retry(self):
        result, events = self.run_mock(disk_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 3)

    def test_no_database_runtime_or_secret_access(self):
        command = self.command()
        for forbidden in ("psql", "pg_dump", "pg_restore", "/proc", ".env", "DATABASE_URL"):
            self.assertNotIn(forbidden, command)
        self.assertNotIn("postgres", command)


if __name__ == "__main__":
    unittest.main()
