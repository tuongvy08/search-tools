"""Read-only DB filesystem command tests with fake sudo, never SSH/server I/O."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"


def remote_command():
    section = DOCUMENT.read_text().split("## S0.6 —", 1)[1]
    block = section.split("```bash\n", 1)[1].split("```", 1)[0].strip()
    args = shlex.split(block)
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Wrong SSH target or command shape")
    return args[2]


class DbDiskCommandTests(unittest.TestCase):
    def probe(self, exit_code=0, available=57_000_000_000):
        with tempfile.TemporaryDirectory(prefix="db-disk-test-") as folder:
            base = Path(folder)
            trace = base / "trace"
            sudo = base / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$@" >> "$TRACE"\n'
                'printf "Filesystem 1B-blocks Used Available Use%% Mounted on\\n"\n'
                f'printf "/fixture 75000000000 15000000000 {available} 21%% /fixture-data\\n"\n'
                f"exit {exit_code}\n"
            )
            sudo.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh", "-c", remote_command()],
                env={"PATH": str(base), "TRACE": str(trace)},
                capture_output=True, text=True, timeout=5,
            )
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_only_exact_data_filesystem_is_read(self):
        result, trace = self.probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(trace, ["-n", "env", "-i", "LC_ALL=C", "LANG=C",
                                 "/usr/bin/df", "-B1", "--", "/var/lib/postgresql/16/main"])
        self.assertTrue(result.stdout.endswith("STAGING_DB_DISK_OK\n"))

    def test_error_after_partial_output_never_prints_marker(self):
        for code in (1, 77, 127):
            with self.subTest(code=code):
                result, trace = self.probe(exit_code=code)
                self.assertEqual(result.returncode, code)
                self.assertNotIn("STAGING_DB_DISK_OK", result.stdout)
                self.assertEqual(trace.count("/usr/bin/df"), 1)

    def test_low_space_is_visible_not_a_gate_pass(self):
        result, _ = self.probe(available=0)
        self.assertEqual(result.returncode, 0)
        self.assertIn("15000000000 0 21%", result.stdout)
        # Marker is CLI completion only; OPERATIONS explicitly requires human
        # comparison and stops the next step if buffer is not met.
        self.assertIn("5.368.709.120", DOCUMENT.read_text())

    def test_no_database_secret_or_service_actions(self):
        for token in ("psql", "pg_dump", "pg_restore", "systemctl", "DATABASE_URL", ".env", "DROP", "CREATE"):
            self.assertNotIn(token, remote_command())


if __name__ == "__main__":
    unittest.main()
