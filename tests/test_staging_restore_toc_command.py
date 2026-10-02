"""Exercise documented TOC/hash command with fake sudo; no SSH/PG/server I/O."""

from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
ARCHIVE = "/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump"


def remote_command():
    section = DOCUMENT.read_text().split("## S0.3 —", 1)[1]
    block = section.split("```bash\n", 1)[1].split("```", 1)[0].strip()
    args = shlex.split(block)
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Wrong alias or SSH shape")
    return args[2]


class TocCommandTests(unittest.TestCase):
    def probe(self, file_status=0, link_status=0, size_status=0, hash_status=0, toc_status=0):
        with tempfile.TemporaryDirectory(prefix="restore-toc-test-") as folder:
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
                '"sha256sum --") printf "%064d  %s\\n" 0 "$4"; '
                f'exit {hash_status};;\n'
                '"pg_restore --list") printf "; dbname: search_tools_staging\\n; TOC fixture\\n"; '
                f'exit {toc_status};;\n'
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

    def test_success_only_fixed_archive_hash_and_list(self):
        result, trace = self.probe()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(trace, [
            f"-n test -f {ARCHIVE}", f"-n test ! -L {ARCHIVE}",
            f"-n test -s {ARCHIVE}", f"-n sha256sum -- {ARCHIVE}",
            f"-n pg_restore --list {ARCHIVE}",
        ])
        self.assertTrue(result.stdout.endswith("ARCHIVE_TOC_OK\n"))

    def test_missing_file_stops_before_hash_and_list(self):
        result, trace = self.probe(file_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 1)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_symlink_stops_before_hash_and_list(self):
        result, trace = self.probe(link_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 2)

    def test_empty_file_stops_before_hash_and_list(self):
        result, trace = self.probe(size_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 3)

    def test_partial_hash_failure_stops_before_list(self):
        result, trace = self.probe(hash_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 4)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_partial_list_failure_never_prints_success(self):
        result, trace = self.probe(toc_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(trace), 5)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_no_connection_or_restore_options(self):
        command = remote_command()
        for token in ("psql", "pg_dump", "--dbname", "--create", "--clean", "systemctl", ".env", "DATABASE_URL"):
            self.assertNotIn(token, command)
        self.assertIn("pg_restore --list", command)


if __name__ == "__main__":
    unittest.main()
