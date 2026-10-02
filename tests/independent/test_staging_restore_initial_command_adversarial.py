"""Independent S0.1 probes: execute only a shell with local fake commands.

The systemctl fixture models is-active's multi-unit semantics: success when
at least one specified unit is active. No SSH, real systemctl, or DB access.
"""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "specs/staging-restore-rehearsal/OPERATIONS.md"


def command():
    block = DOCUMENT.read_text().split("```bash\n", 1)[1].split("```", 1)[0]
    args = shlex.split(block.strip())
    if len(args) != 3 or args[:2] != ["ssh", "staging"]:
        raise AssertionError("Unexpected initial command shape")
    return args[2]


def probe(web, worker, disk_status=0, missing_systemctl=False, service_error=None):
    with tempfile.TemporaryDirectory(
        prefix="s01-fixture-", dir=Path(__file__).resolve().parent
    ) as folder:
        base = Path(folder)
        trace = base / "trace"
        scripts = {
            "systemctl": (
                '#!/bin/sh\n'
                'printf "systemctl %s\\n" "$*" >> "$TRACE"\n'
                'test "$1" = is-active || exit 64\n'
                'shift\n'
                'if test -n "$SERVICE_ERROR"; then\n'
                '  printf "mock systemctl transport error\\n" >&2\n'
                '  exit "$SERVICE_ERROR"\n'
                'fi\n'
                'status=3\n'
                'for unit in "$@"; do\n'
                '  case "$unit" in\n'
                '    search-tools-staging.service) state="$WEB";;\n'
                '    search-tools-import-worker.service) state="$WORKER";;\n'
                '    *) printf "unexpected unit\\n" >&2; exit 64;;\n'
                '  esac\n'
                '  printf "%s\\n" "$state"\n'
                '  if test "$state" = active; then status=0; fi\n'
                'done\n'
                'exit "$status"\n'
            ),
            "df": (
                '#!/bin/sh\n'
                'printf "df %s\\n" "$*" >> "$TRACE"\n'
                'printf "Filesystem Available\\nmock 20G\\n"\n'
                f'exit {disk_status}\n'
            ),
        }
        for name, text in scripts.items():
            if name == "systemctl" and missing_systemctl:
                continue
            executable = base / name
            executable.write_text(text)
            executable.chmod(0o700)
        result = subprocess.run(
            ["/bin/sh", "-c", command()],
            env={"PATH": str(base), "TRACE": str(trace), "WEB": web,
                 "WORKER": worker,
                 "SERVICE_ERROR": "" if service_error is None else str(service_error),
                 "DATABASE_URL": "fake-secret-canary-never-to-be-printed"},
            capture_output=True, text=True, timeout=5,
        )
        events = trace.read_text().splitlines() if trace.exists() else []
        return result, events


class InitialCommandAdversarialTests(unittest.TestCase):
    def assert_stops_on_inactive_unit(self, web, worker):
        result, events = probe(web, worker)
        observation = (
            f"output={result.stdout!r}; exit_code={result.returncode}; "
            f"calls={events!r}"
        )
        self.assertNotEqual(result.returncode, 0, observation)
        expected = ["systemctl is-active search-tools-staging.service"]
        if web == "active":
            expected.append("systemctl is-active search-tools-import-worker.service")
        self.assertEqual(events, expected, observation)
        self.assertNotIn("df ", "\n".join(events), observation)
        self.assertEqual(result.stdout, f"{web}\n" + (f"{worker}\n" if web == "active" else ""))

    def test_active_web_inactive_worker_must_stop_before_disk(self):
        self.assert_stops_on_inactive_unit("active", "inactive")

    def test_failed_web_active_worker_must_stop_before_disk(self):
        self.assert_stops_on_inactive_unit("failed", "active")

    def test_both_inactive_stop_without_retry(self):
        result, events = probe("inactive", "failed")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(events), 1)

    def test_missing_disk_path_error_is_propagated_without_retry(self):
        result, events = probe("active", "active", disk_status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(events), 3)

    def test_unknown_worker_must_stop_before_disk(self):
        self.assert_stops_on_inactive_unit("active", "unknown")

    def test_service_command_error_stops_without_retry(self):
        result, events = probe("active", "active", service_error=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(events, ["systemctl is-active search-tools-staging.service"])
        self.assertEqual(result.stderr, "mock systemctl transport error\n")

    def test_missing_systemctl_stops_without_disk(self):
        result, events = probe("active", "active", missing_systemctl=True)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(events, [])

    def test_active_services_do_not_read_or_print_secret_canary(self):
        result, events = probe("active", "active")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "active\nactive\nFilesystem Available\nmock 20G\n")
        self.assertEqual(result.stderr, "")
        self.assertNotIn("fake-secret-canary", result.stdout + result.stderr)
        self.assertEqual(events, [
            "systemctl is-active search-tools-staging.service",
            "systemctl is-active search-tools-import-worker.service",
            "df -h -- /srv/search-tools /srv/backups/search-tools",
        ])

    def test_command_is_only_readonly_services_and_disk(self):
        tokens = shlex.split(command())
        self.assertEqual(tokens, [
            "systemctl", "is-active", "search-tools-staging.service",
            "&&", "systemctl", "is-active",
            "search-tools-import-worker.service", "&&", "df", "-h", "--",
            "/srv/search-tools", "/srv/backups/search-tools",
        ])


if __name__ == "__main__":
    unittest.main()
