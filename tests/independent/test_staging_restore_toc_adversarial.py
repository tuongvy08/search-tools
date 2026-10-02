"""Independent S0.3 probes: shell only, real file predicates/hash, mocked TOC.

No SSH, real sudo, PostgreSQL, or external directory writes. pg_restore is not
installed in this verifier environment; the mock exercises shell failure gates,
not archive-format compatibility or a successful restore.
"""
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
ARCHIVE = "/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump"
CANARY = "INDEPENDENT_S03_FAKE_CREDENTIAL"


def fragment():
    section = DOC.read_text().split("## S0.3 —", 1)[1]
    args = shlex.split(section.split("```bash\n", 1)[1].split("```", 1)[0])
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Unexpected SSH target or shape")
    return args[2]


def probe(kind="regular", denied_at=0, hash_status=0, toc_status=0,
          toc="; dbname: search_tools_staging\n; TOC fixture only\n"):
    with tempfile.TemporaryDirectory(prefix="s03-independent-", dir=HERE) as folder:
        base = Path(folder)
        archive = base / "archive with spaces.dump"
        target = base / "target.dump"
        target.write_bytes(b"nonempty independent fixture, NOT a PostgreSQL dump")
        if kind == "regular":
            archive.write_bytes(target.read_bytes())
        elif kind == "empty":
            archive.touch()
        elif kind == "symlink":
            archive.symlink_to(target)
        elif kind == "dangling_symlink":
            archive.symlink_to(base / "absent")
        elif kind == "directory":
            archive.mkdir()
        elif kind != "missing":
            raise AssertionError(kind)
        before_target = (target.read_bytes(), target.stat().st_mtime_ns)
        before_archive = None
        if kind == "regular":
            before_archive = (archive.read_bytes(), archive.stat().st_mtime_ns)
        hash_code = (
            "import hashlib, pathlib, sys; "
            "print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()"
            " + '  ' + sys.argv[1])"
        )
        sudo = base / "sudo"
        sudo.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$TRACE"\n'
            'test "$1" = -n || exit 90\n'
            'case "$2 $3" in\n'
            '"test -f") step=1;;\n'
            '"test !") step=2;;\n'
            '"test -s") step=3;;\n'
            '"sha256sum --") step=4;;\n'
            '"pg_restore --list") step=5;;\n'
            '*) exit 91;;\n'
            'esac\n'
            'test "$step" != "$DENIED_AT" || exit 77\n'
            'if test "$step" = 4; then\n'
            f'  {shlex.quote(sys.executable)} -B -c {shlex.quote(hash_code)} "$4" || exit 92\n'
            '  exit "$HASH_STATUS"\n'
            'fi\n'
            'if test "$step" = 5; then\n'
            '  if test "$TOC_STATUS" = 127; then\n'
            '    printf "mock: pg_restore not found\\n" >&2; exit 127\n'
            '  fi\n'
            '  printf "%s" "$TOC"\n'
            '  exit "$TOC_STATUS"\n'
            'fi\n'
            'shift; shift\n'
            'exec /bin/test "$@"\n'
        )
        sudo.chmod(0o700)
        cmd = fragment()
        if cmd.count(ARCHIVE) != 1:
            raise AssertionError("Unexpected fixed path count")
        cmd = cmd.replace(ARCHIVE, shlex.quote(str(archive)))
        trace = base / "trace"
        result = subprocess.run(
            ["/bin/sh", "-c", cmd],
            env={"PATH": str(base), "TRACE": str(trace),
                 "DENIED_AT": str(denied_at), "HASH_STATUS": str(hash_status),
                 "TOC_STATUS": str(toc_status), "TOC": toc,
                 "DATABASE_URL": CANARY, "PGPASSWORD": CANARY,
                 "PGHOST": CANARY, "PGDATABASE": CANARY},
            capture_output=True, text=True, timeout=5,
        )
        events = trace.read_text().splitlines() if trace.exists() else []
        if before_target != (target.read_bytes(), target.stat().st_mtime_ns):
            raise AssertionError("Target contents/mtime changed")
        if before_archive is not None:
            if before_archive != (archive.read_bytes(), archive.stat().st_mtime_ns):
                raise AssertionError("Archive contents/mtime changed")
        expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        return result, events, expected_hash


class TocAdversarialTests(unittest.TestCase):
    def test_exact_readonly_surface(self):
        self.assertEqual(shlex.split(fragment()), [
            "p=" + ARCHIVE + ";", "sudo", "-n", "test", "-f", "$p",
            "&&", "sudo", "-n", "test", "!", "-L", "$p",
            "&&", "sudo", "-n", "test", "-s", "$p",
            "&&", "sudo", "-n", "sha256sum", "--", "$p",
            "&&", "sudo", "-n", "pg_restore", "--list", "$p",
            "&&", "printf", "%s\\n", "ARCHIVE_TOC_OK",
        ])

    def test_real_hash_no_modification_or_environment_credential_leak(self):
        result, events, digest = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(events), 5)
        self.assertTrue(result.stdout.startswith(digest + "  "))
        self.assertTrue(result.stdout.endswith("ARCHIVE_TOC_OK\n"))
        self.assertEqual(result.stderr, "")
        self.assertNotIn(CANARY, result.stdout + result.stderr + str(events))

    def test_real_symlink_stops_before_hash_or_list(self):
        result, events, _ = probe(kind="symlink")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 2)
        self.assertEqual(result.stdout, "")

    def test_other_invalid_file_kinds_stop_before_hash_or_list(self):
        for kind, calls in [("missing", 1), ("dangling_symlink", 1),
                            ("directory", 1), ("empty", 3)]:
            with self.subTest(kind=kind):
                result, events, _ = probe(kind=kind)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(events), calls)
                self.assertEqual(result.stdout, "")

    def test_permission_failure_at_each_gate_no_retry_or_marker(self):
        for step in range(1, 6):
            with self.subTest(step=step):
                result, events, _ = probe(denied_at=step)
                self.assertEqual(result.returncode, 77)
                self.assertEqual(len(events), step)
                self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_hash_printed_then_failure_stops_before_list(self):
        result, events, digest = probe(hash_status=2)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(events), 4)
        self.assertIn(digest, result.stdout)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_corrupt_or_unsupported_archive_list_nonzero_no_success(self):
        result, events, _ = probe(toc_status=1, toc="; partial TOC\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 5)
        self.assertIn("; partial TOC", result.stdout)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_missing_pg_restore_no_success_or_fallback(self):
        result, events, _ = probe(toc_status=127)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(len(events), 5)
        self.assertIn("not found", result.stderr)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_interrupted_tool_status_no_marker_or_retry(self):
        result, events, _ = probe(toc_status=143)
        self.assertEqual(result.returncode, 143)
        self.assertEqual(len(events), 5)
        self.assertNotIn("ARCHIVE_TOC_OK", result.stdout)

    def test_wrong_source_marker_is_not_identity_or_restore_validation(self):
        result, events, _ = probe(toc="; dbname: wrong_source\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(events), 5)
        self.assertIn("ARCHIVE_TOC_OK", result.stdout)
        section = DOC.read_text().split("## S0.3 —", 1)[1].split("## Các bước còn lại", 1)[0]
        self.assertIn("database nguồn không đúng `search_tools_staging` thì dừng", section)
        self.assertIn("không chứng minh dữ liệu nonempty/restore thành công", section)

    def test_missing_main_tables_marker_is_not_counts_or_schema_evidence(self):
        result, _, _ = probe(toc="; dbname: search_tools_staging\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ARCHIVE_TOC_OK", result.stdout)
        section = DOC.read_text().split("## S0.3 —", 1)[1].split("## Các bước còn lại", 1)[0]
        self.assertIn("TOC bất thường/thiếu bảng chính", section)
        self.assertIn("row counts và footprint đầy đủ phải kiểm ở DB tạm sau restore", section)


if __name__ == "__main__":
    unittest.main()
