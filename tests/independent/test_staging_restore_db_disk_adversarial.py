"""Independent S0.6 probes: local shell/env only; no SSH, sudo or real df."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"
TARGET = "/var/lib/postgresql/16/main"
CANARY = "INDEPENDENT_S06_FAKE_SECRET_NOT_REAL"
BUFFER = max(5 * 1024**3, 4 * 1228176407)


def command():
    section = DOC.read_text().split("## S0.6 —", 1)[1].split("## Các bước còn lại", 1)[0]
    args = shlex.split(section.split("```bash\n", 1)[1].split("```", 1)[0])
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Wrong target or command shape")
    return args[2], section


def probe(status=0, available=BUFFER, output=None, warning="", denied=False,
          missing_sudo=False):
    if output is None:
        output = ("Filesystem 1B-blocks Used Available Use% Mounted on\n"
                  f"/dev/mock 75000000000 15000000000 {available} 21% /mock-pg\n")
    with tempfile.TemporaryDirectory(prefix="s06-probe-", dir=HERE) as folder:
        base = Path(folder)
        trace = base / "trace.json"
        sudo_trace = base / "sudo.json"
        fake_df = base / "fake-df"
        fake_df.write_text(
            f"#!{sys.executable} -B\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(trace)!r}).write_text(json.dumps("
            "{'argv': sys.argv[1:], 'env': dict(os.environ)}))\n"
            f"sys.stdout.write({output!r}); sys.stderr.write({warning!r})\n"
            f"sys.exit({status})\n"
        )
        fake_df.chmod(0o700)
        if not missing_sudo:
            fake_sudo = base / "sudo"
            fake_sudo.write_text(
                f"#!{sys.executable} -B\n"
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                f"pathlib.Path({str(sudo_trace)!r}).write_text(json.dumps(args))\n"
                "assert args == ['-n', 'env', '-i', 'LC_ALL=C', 'LANG=C', "
                f"'/usr/bin/df', '-B1', '--', {TARGET!r}]\n"
                + ("sys.exit(77)\n" if denied else
                   f"args[5] = {str(fake_df)!r}\n"
                   "os.execv('/usr/bin/env', ['env'] + args[2:])\n")
            )
            fake_sudo.chmod(0o700)
        result = subprocess.run(
            ["/bin/sh", "-c", command()[0]],
            env={"PATH": str(base), **{key: CANARY for key in
                 ("PGHOST", "PGPASSWORD", "PGDATABASE", "DATABASE_URL", "HOME")}},
            capture_output=True, text=True, timeout=5,
        )
        record = json.loads(trace.read_text()) if trace.exists() else None
        args = json.loads(sudo_trace.read_text()) if sudo_trace.exists() else None
        return result, record, args


class DbDiskIndependentTests(unittest.TestCase):
    def test_exact_data_path_bytes_and_clean_environment(self):
        result, record, args = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(record["argv"], ["-B1", "--", TARGET])
        env = dict(record["env"])
        if sys.platform == "darwin":
            env.pop("__CF_USER_TEXT_ENCODING", None)
        self.assertEqual(env, {"LC_ALL": "C", "LANG": "C"})
        self.assertNotIn(CANARY, result.stdout + result.stderr + repr(record) + repr(args))
        self.assertTrue(result.stdout.endswith("STAGING_DB_DISK_OK\n"))

    def test_partial_df_failure_interrupt_and_timeout_status_stop_marker(self):
        for status in (1, 2, 77, 124, 127, 130, 141, 143):
            with self.subTest(status=status):
                result, record, args = probe(status=status, warning="mock df failure\n")
                self.assertEqual(result.returncode, status)
                self.assertNotIn("STAGING_DB_DISK_OK", result.stdout)
                self.assertEqual(result.stderr, "mock df failure\n")
                self.assertEqual(args.count("/usr/bin/df"), 1)
                self.assertIsNotNone(record)

    def test_missing_tool_or_privilege_never_reaches_df(self):
        for kwargs, expected in (({"missing_sudo": True}, 127), ({"denied": True}, 77)):
            with self.subTest(kwargs=kwargs):
                result, record, _ = probe(**kwargs)
                self.assertEqual(result.returncode, expected)
                self.assertIsNone(record)
                self.assertNotIn("STAGING_DB_DISK_OK", result.stdout)

    def test_insufficient_space_and_boundary_are_not_machine_acceptance(self):
        self.assertEqual(BUFFER, 5368709120)
        self.assertEqual(4 * 1228176407, 4912705628)
        for free in (0, 37333943 * 4, BUFFER - 1, BUFFER, BUFFER + 1):
            with self.subTest(free=free):
                result, _, _ = probe(available=free)
                self.assertEqual(result.returncode, 0)
                self.assertIn(f"15000000000 {free} 21%", result.stdout)
        section = command()[1]
        self.assertIn("max(5×1024³,4×DB nguồn)", section)
        self.assertIn("Thiếu free space", section)
        self.assertIn("dừng báo PO", section)
        self.assertIn("Marker chỉ exit 0, không chứng minh đủ đĩa", section)
        self.assertIn("không dùng compressed dump 37 MB", section)

    def test_warning_and_unusable_output_are_visible_not_auto_disk_pass(self):
        for output, warning in (("", ""), ("not a df table\n", ""),
                                ("Filesystem only\n", "mock warning\n")):
            with self.subTest(output=output):
                result, _, _ = probe(output=output, warning=warning)
                self.assertEqual(result.stdout, output + "STAGING_DB_DISK_OK\n")
                self.assertEqual(result.stderr, warning)
        self.assertIn("cảnh báo/lỗi, output không đọc được hoặc thiếu marker", command()[1])

    def test_no_write_retry_database_or_secret_read_in_command(self):
        text, section = command()
        self.assertEqual(text.count("sudo"), 1)
        self.assertEqual(text.count("/usr/bin/df"), 1)
        self.assertNotIn("||", text)
        for token in ("psql", "pg_dump", "pg_restore", "systemctl", ".env", "rm ", "tee "):
            self.assertNotIn(token, text)
        self.assertIn("tablespace và instance đích cần xác minh riêng trước ghi", section)

    def test_identity_source_matches_documented_pin(self):
        digest = hashlib.sha256((ROOT / "scripts/staging_restore_identity_readonly.py").read_bytes()).hexdigest()
        self.assertEqual(digest, "d0a32e3e11e023da62ca37361fa9e34cebcaefde6e9583f34d7159fe2225ec5a")
        self.assertIn(digest, DOC.read_text())


if __name__ == "__main__":
    unittest.main()
