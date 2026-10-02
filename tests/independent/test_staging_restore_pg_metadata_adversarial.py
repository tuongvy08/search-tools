"""Independent S0.5 shell/env probes; never execute SSH, sudo or PostgreSQL.

Fixtures live only under tests/independent. Replace sudo with an argv-checking
double and the exact client path with a recorder; /usr/bin/env -i is real.
"""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
DOC = HERE.parents[1] / "specs/staging-restore-rehearsal/OPERATIONS.md"
CLIENT = "/usr/lib/postgresql/16/bin/psql"
CANARY = "INDEPENDENT_S05_FAKE_SECRET_NOT_REAL"
NORMAL = ("database_name=search_tools_staging\npostgres_version=PostgreSQL 16.15\n"
          "data_directory=/fixture/data\ntransaction_read_only=on\ndatabase_bytes=100000000\n")


def fragment():
    section = DOC.read_text().split("## S0.5 —", 1)[1].split("## Các bước còn lại", 1)[0]
    args = shlex.split(section.split("```bash\n", 1)[1].split("```", 1)[0])
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Unexpected SSH target/shape")
    return args[2], section


def probe(status=0, output=NORMAL, warning="", denied=False, missing_sudo=False):
    with tempfile.TemporaryDirectory(prefix="s05-independent-", dir=HERE) as folder:
        base = Path(folder)
        sudo_trace, client_trace = base / "sudo.json", base / "client.json"
        client = base / "fake-client"
        client.write_text(
            f"#!{sys.executable} -B\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(client_trace)!r}).write_text(json.dumps("
            "{'argv': sys.argv[1:], 'env': dict(os.environ)}))\n"
            f"sys.stdout.write({output!r}); sys.stderr.write({warning!r})\n"
            f"sys.exit({status})\n"
        )
        client.chmod(0o700)
        if not missing_sudo:
            sudo = base / "sudo"
            sudo.write_text(
                f"#!{sys.executable} -B\n"
                "import json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                f"pathlib.Path({str(sudo_trace)!r}).write_text(json.dumps(args))\n"
                "assert args[:5] == ['-n', '-u', 'postgres', 'env', '-i']\n"
                f"assert args.count({CLIENT!r}) == 1\n"
                + ("sys.exit(77)\n" if denied else
                   f"args[args.index({CLIENT!r})] = {str(client)!r}\n"
                   "os.execv('/usr/bin/env', ['env'] + args[4:])\n")
            )
            sudo.chmod(0o700)
        poison = {key: CANARY for key in (
            "PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGDATABASE",
            "PGUSER", "PGPASSWORD", "PGPASSFILE", "PGOPTIONS", "PSQLRC",
            "DATABASE_URL", "HOME", "BASH_ENV", "ENV")}
        result = subprocess.run(
            ["/bin/sh", "-c", fragment()[0]],
            env={"PATH": str(base), **poison}, capture_output=True, text=True, timeout=5,
        )
        sudo_args = json.loads(sudo_trace.read_text()) if sudo_trace.exists() else None
        client_record = json.loads(client_trace.read_text()) if client_trace.exists() else None
        return result, sudo_args, client_record


class PgMetadataAdversarialTests(unittest.TestCase):
    def test_real_env_i_removes_poisoned_routing_and_credentials(self):
        result, sudo_args, record = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        clean_env = dict(record["env"])
        # macOS/Python adds this platform variable after env -i exec; it is not
        # an inherited PG/application value and is absent on staging Linux.
        if sys.platform == "darwin":
            clean_env.pop("__CF_USER_TEXT_ENCODING", None)
        self.assertEqual(clean_env, {
            "LC_ALL": "C", "LANG": "C",
            "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=10000",
        })
        self.assertNotIn(CANARY, result.stdout + result.stderr + repr(record) + repr(sudo_args))
        self.assertTrue(result.stdout.endswith("STAGING_PG_METADATA_OK\n"))
        self.assertEqual(record["argv"][:16], [
            "-X", "-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
            "-d", "search_tools_staging", "-v", "ON_ERROR_STOP=1", "-P", "pager=off", "-c",
            "SELECT current_database() AS database_name, version() AS postgres_version; "
            "SHOW data_directory; SHOW transaction_read_only; "
            "SELECT pg_database_size(current_database()) AS database_bytes;",
        ])

    def test_partial_result_and_failure_never_emit_completion_or_retry(self):
        for status in (1, 2, 3, 124, 127, 130, 141, 143):
            with self.subTest(status=status):
                result, args, record = probe(status=status, output="database_name=search_tools_staging\n",
                                             warning="mock metadata failure\n")
                self.assertEqual(result.returncode, status)
                self.assertNotIn("STAGING_PG_METADATA_OK", result.stdout)
                self.assertEqual(result.stderr, "mock metadata failure\n")
                self.assertEqual(args.count(CLIENT), 1)
                self.assertIsNotNone(record)

    def test_missing_sudo_or_denied_privilege_does_not_reach_client(self):
        for kwargs, status in (({"missing_sudo": True}, 127), ({"denied": True}, 77)):
            with self.subTest(kwargs=kwargs):
                result, _, record = probe(**kwargs)
                self.assertEqual(result.returncode, status)
                self.assertIsNone(record)
                self.assertNotIn("STAGING_PG_METADATA_OK", result.stdout)

    def test_exit_zero_wrong_identity_or_readonly_off_is_not_server_acceptance(self):
        _, section = fragment()
        for output in (NORMAL.replace("search_tools_staging", "wrong_database"),
                       NORMAL.replace("read_only=on", "read_only=off"),
                       NORMAL.replace("16.15", "14.24"),
                       NORMAL.replace("100000000", "0")):
            with self.subTest(output=output):
                result, _, _ = probe(output=output)
                self.assertEqual(result.returncode, 0)
                self.assertIn("STAGING_PG_METADATA_OK", result.stdout)
        self.assertIn("Marker chỉ chứng minh lệnh exit 0", section)
        self.assertIn("bất thường/thiếu marker/readonly off/PG không đúng thì dừng", section)
        self.assertIn("database_bytes >0", section)
        self.assertIn("không phát lệnh ghi", section)

    def test_warning_survives_for_po_to_stop_not_hidden_as_success(self):
        result, _, _ = probe(warning="mock warning: unexpected state\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "mock warning: unexpected state\n")
        self.assertIn("STAGING_PG_METADATA_OK", result.stdout)
        self.assertIn("không phát lệnh ghi", fragment()[1])


if __name__ == "__main__":
    unittest.main()
