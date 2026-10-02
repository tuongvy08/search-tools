"""Local shell mocks for the archive metadata command; no SSH/DB/server I/O."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
ARCHIVE = "/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump"


def remote_command():
    section = DOCUMENT.read_text().split("## S0.2 —", 1)[1]
    block = section.split("```bash\n", 1)[1].split("```", 1)[0].strip()
    args = shlex.split(block)
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Wrong alias or SSH shape")
    return args[2]


class ArchiveCommandTests(unittest.TestCase):
    def probe(self, file_status=0, link_status=0, size_status=0, stat_status=0):
        with tempfile.TemporaryDirectory(prefix="restore-archive-test-") as folder:
            base = Path(folder)
            trace = base / "trace"
            sudo = base / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$TRACE"\n'
                'test "$1" = "-n" || exit 99\n'
                'case "$2 $3" in\n'
                f'"test -f") exit {file_status};;\n'
                f'"test !") exit {link_status};;\n'
                f'"test -s") exit {size_status};;\n'
                '"stat -c") printf "backup_bytes=37300000\\nbackup_modified=fixture\\n"; '
                f'exit {stat_status};;\n'
                '*) exit 99;;\n'
                'esac\n'
            )
            sudo.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh", "-c", remote_command()],
                env={"PATH": str(base), "TRACE": str(trace)},
                capture_output=True, text=True, timeout=5,
            )
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_success_only_fixed_archive_metadata(self):
        result, trace = self.probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(trace, [
            f"-n test -f {ARCHIVE}",
            f"-n test ! -L {ARCHIVE}",
            f"-n test -s {ARCHIVE}",
            f"-n stat -c backup_bytes=%s%nbackup_modified=%y -- {ARCHIVE}",
        ])
        self.assertIn("backup_bytes=", result.stdout)
        self.assertIn("backup_modified=", result.stdout)
        self.assertIn("ARCHIVE_METADATA_OK", result.stdout)

    def test_missing_file_stops_without_stat_or_retry(self):
        result, trace = self.probe(file_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 1)
        self.assertEqual(result.stdout, "")

    def test_symlink_stops_without_stat_or_retry(self):
        result, trace = self.probe(link_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 2)

    def test_empty_file_stops_without_stat_or_retry(self):
        result, trace = self.probe(size_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 3)

    def test_permission_failure_stops_at_first_gate(self):
        result, trace = self.probe(file_status=77)
        self.assertEqual(result.returncode, 77)
        self.assertEqual(len(trace), 1)

    def test_stat_failure_is_not_success(self):
        result, trace = self.probe(stat_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 4)
        self.assertNotIn("ARCHIVE_METADATA_OK", result.stdout)

    def test_no_database_backup_restore_or_service_commands(self):
        for token in ("psql", "pg_dump", "pg_restore", "systemctl", ".env", "DATABASE_URL"):
            self.assertNotIn(token, remote_command())


if __name__ == "__main__":
    unittest.main()
