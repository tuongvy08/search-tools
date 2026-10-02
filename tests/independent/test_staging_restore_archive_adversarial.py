"""Independent S0.2 shell probes. No SSH, sudo privilege, or database access.

Only substitute the archive path for a local fixture. /bin/test evaluates real
file predicates. GNU stat output is mocked because the verifier runs on macOS.
All fixture writes are confined to tests/independent and removed afterwards.
"""
import hashlib
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARCHIVE = "/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump"
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
CANARY = "INDEPENDENT_ARCHIVE_FAKE_SECRET"


def fragment():
    section = DOCUMENT.read_text().split("## S0.2 —", 1)[1]
    args = shlex.split(section.split("```bash\n", 1)[1].split("```", 1)[0])
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Unexpected SSH target/shape")
    return args[2]


def probe(kind="regular", denied_at=0, stat_status=0):
    with tempfile.TemporaryDirectory(prefix="s02-independent-", dir=HERE) as folder:
        base = Path(folder)
        archive = base / "archive with space.dump"
        target = base / "target.dump"
        target.write_bytes(b"independent fixture archive, not real backup")
        if kind == "regular":
            archive.write_bytes(target.read_bytes())
        elif kind == "empty":
            archive.touch()
        elif kind == "directory":
            archive.mkdir()
        elif kind == "symlink":
            archive.symlink_to(target)
        elif kind == "dangling_symlink":
            archive.symlink_to(base / "missing-target")
        elif kind != "missing":
            raise AssertionError(kind)
        before = (target.read_bytes(), target.stat())
        archive_before = None
        if kind == "regular":
            archive_before = (hashlib.sha256(archive.read_bytes()).hexdigest(),
                              archive.stat().st_size, archive.stat().st_mtime_ns)
        sudo = base / "sudo"
        sudo.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$TRACE"\n'
            'test "$1" = -n || exit 90\n'
            'case "$2 $3" in\n'
            '"test -f") step=1;;\n'
            '"test !") step=2;;\n'
            '"test -s") step=3;;\n'
            '"stat -c") step=4;;\n'
            '*) exit 91;;\n'
            'esac\n'
            'test "$step" != "$DENIED_AT" || exit 77\n'
            'shift\n'
            'if test "$step" = 4; then\n'
            '  printf "backup_bytes=44\\nbackup_modified=fixture-utc\\n"\n'
            '  exit "$STAT_STATUS"\n'
            'fi\n'
            'shift\n'
            'exec /bin/test "$@"\n'
        )
        sudo.chmod(0o700)
        cmd = fragment()
        if cmd.count(ARCHIVE) != 1:
            raise AssertionError("Unexpected archive path occurrence")
        cmd = cmd.replace(ARCHIVE, shlex.quote(str(archive)))
        trace = base / "trace"
        result = subprocess.run(
            ["/bin/sh", "-c", cmd],
            env={"PATH": str(base), "TRACE": str(trace),
                 "DENIED_AT": str(denied_at), "STAT_STATUS": str(stat_status),
                 "DATABASE_URL": CANARY, "PGPASSWORD": CANARY},
            capture_output=True, text=True, timeout=5,
        )
        events = trace.read_text().splitlines() if trace.exists() else []
        after = (target.read_bytes(), target.stat())
        if before[0] != after[0] or before[1].st_mtime_ns != after[1].st_mtime_ns:
            raise AssertionError("Target file changed")
        if archive_before is not None:
            archive_after = (hashlib.sha256(archive.read_bytes()).hexdigest(),
                             archive.stat().st_size, archive.stat().st_mtime_ns)
            if archive_before != archive_after:
                raise AssertionError("Archive content/size/mtime changed")
        return result, events


class ArchiveAdversarialTests(unittest.TestCase):
    def test_real_nonempty_file_with_space_preserved_and_no_secret_leak(self):
        result, events = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(events), 4)
        self.assertEqual(result.stdout, "backup_bytes=44\nbackup_modified=fixture-utc\nARCHIVE_METADATA_OK\n")
        self.assertEqual(result.stderr, "")
        self.assertNotIn(CANARY, result.stdout + result.stderr + str(events))

    def test_real_symlink_to_nonempty_file_is_rejected(self):
        result, events = probe("symlink")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 2)
        self.assertEqual(result.stdout, "")

    def test_real_dangling_symlink_is_rejected(self):
        result, events = probe("dangling_symlink")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(result.stdout, "")

    def test_real_directory_not_accepted_as_archive(self):
        result, events = probe("directory")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(result.stdout, "")

    def test_real_missing_archive_stops(self):
        result, events = probe("missing")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 1)

    def test_real_empty_archive_stops(self):
        result, events = probe("empty")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 3)
        self.assertEqual(result.stdout, "")

    def test_privilege_failure_at_every_gate_stops_without_retry(self):
        for step in range(1, 5):
            with self.subTest(step=step):
                result, events = probe(denied_at=step)
                self.assertEqual(result.returncode, 77)
                self.assertEqual(len(events), step)
                self.assertNotIn("ARCHIVE_METADATA_OK", result.stdout)

    def test_partial_stat_output_then_failure_not_marked_complete(self):
        result, events = probe(stat_status=2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(events), 4)
        self.assertIn("backup_bytes=", result.stdout)
        self.assertNotIn("ARCHIVE_METADATA_OK", result.stdout)

    def test_command_surface_only_fixed_metadata_checks(self):
        self.assertEqual(shlex.split(fragment()), [
            "p=" + ARCHIVE + ";", "sudo", "-n", "test", "-f", "$p",
            "&&", "sudo", "-n", "test", "!", "-L", "$p",
            "&&", "sudo", "-n", "test", "-s", "$p",
            "&&", "sudo", "-n", "stat", "-c", "backup_bytes=%s%nbackup_modified=%y",
            "--", "$p", "&&", "printf", "%s\\n", "ARCHIVE_METADATA_OK",
        ])


if __name__ == "__main__":
    unittest.main()
