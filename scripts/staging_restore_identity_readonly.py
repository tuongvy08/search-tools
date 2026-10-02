"""Inspect staging runtime identity only. No PostgreSQL calls or writes.

PO runs via stdin on staging with sudo and Python -I -B. Credentials are read
only from the two live processes, validated in memory, never printed/passed
to another process. This does not verify schema, DB disk, or restore success.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit


WEB = "search-tools-staging.service"
WORKER = "search-tools-import-worker.service"
LIVE = Path("/srv/search-tools")
TARGET = "2ec9b6c940c89802cce9fc77150e4f2b8dcfff64"
DATABASE = "search_tools_staging"
READ_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}


class GateFailure(RuntimeError):
    pass


def checked(args, gate):
    try:
        result = subprocess.run(
            args, env=READ_ENV, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=20, check=False,
        )
    except Exception:
        raise GateFailure(gate) from None
    if result.returncode:
        raise GateFailure(gate)
    # Any diagnostic is an anomaly: stop without echoing it (may hold secrets).
    if (result.stderr or "").strip():
        raise GateFailure(gate + "_STDERR_UNEXPECTED")
    output = (result.stdout or "").strip()
    validate_output(gate, output)
    return output


def validate_output(gate, output):
    """Accept only the exact known shape; anything else is data we do not trust."""
    ok = False
    if gate in ("WEB_SERVICE_READ", "WORKER_SERVICE_READ"):
        pairs = [line.split("=", 1) for line in output.splitlines()]
        ok = (len(pairs) == 4 and all(len(pair) == 2 and re.fullmatch(r"[A-Za-z0-9_.-]*", pair[1])
                                      for pair in pairs)
              and {pair[0] for pair in pairs} == {"ActiveState", "SubState", "MainPID", "User"})
    elif gate in ("WEB_COMMIT_READ", "WORKER_COMMIT_READ"):
        ok = re.fullmatch(r"[0-9a-f]{40}", output) is not None
    if not ok:
        raise GateFailure(gate + "_STDOUT_UNEXPECTED")


def expected_cwd():
    if not LIVE.is_dir() or LIVE.is_symlink():
        raise GateFailure("STAGING_LIVE_DIRECTORY")
    if LIVE.resolve(strict=True) != LIVE:
        raise GateFailure("STAGING_LIVE_DIRECTORY")
    return str(LIVE)


def process_cwd(pid):
    return str(Path("/proc", str(pid), "cwd").resolve(strict=True))


def process_dsn(pid):
    entries = Path("/proc", str(pid), "environ").read_bytes().split(b"\0")
    matches = [entry.split(b"=", 1)[1] for entry in entries
               if entry.startswith(b"DATABASE_URL=")]
    if len(matches) != 1 or not matches[0]:
        raise GateFailure("RUNTIME_DATABASE_URL_PRESENT")
    return matches[0]


def staging_endpoint(raw):
    try:
        parsed = urlsplit(raw.decode("utf-8"))
        if parsed.scheme not in ("postgres", "postgresql") or not parsed.username:
            raise GateFailure("STAGING_DSN_SHAPE")
        if unquote(parsed.path) != "/" + DATABASE or parsed.fragment:
            raise GateFailure("STAGING_DATABASE_ONLY")
        host = parsed.hostname
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise GateFailure("STAGING_LOCAL_HOST_ONLY")
        port = parsed.port if parsed.port is not None else 5432
        if not 1 <= port <= 65535:
            raise GateFailure("STAGING_PORT_VALID")
        options = (parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
                   if parsed.query else {})
        allowed = {"sslmode", "sslrootcert", "connect_timeout", "target_session_attrs"}
        if set(options) - allowed or any(len(value) != 1 or not value[0]
                                        for value in options.values()):
            raise GateFailure("STAGING_DSN_OPTIONS")
        return host, port
    except GateFailure:
        raise
    except Exception:
        raise GateFailure("STAGING_DSN_SHAPE") from None


def inspect_identity():
    cwd_expected = expected_cwd()
    dsns = []
    for role, service in (("WEB", WEB), ("WORKER", WORKER)):
        info = dict(line.split("=", 1) for line in checked([
            "systemctl", "show", service, "--no-pager",
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "User",
        ], role + "_SERVICE_READ").splitlines() if "=" in line)
        if (info.get("ActiveState") != "active" or info.get("SubState") != "running"
                or info.get("User") != "deploy"):
            raise GateFailure(role + "_STAGING_SERVICE")
        pid = info.get("MainPID", "")
        if not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
            raise GateFailure(role + "_PID_PRESENT")
        if process_cwd(pid) != cwd_expected:
            raise GateFailure(role + "_STAGING_CWD")
        commit = checked([
            "git", "-c", "safe.directory=" + cwd_expected,
            "-C", cwd_expected, "rev-parse", "HEAD",
        ], role + "_COMMIT_READ")
        if commit != TARGET:
            raise GateFailure(role + "_STAGING_TARGET")
        dsn = process_dsn(pid)
        endpoint = staging_endpoint(dsn)
        dsns.append(dsn)
    if dsns[0] != dsns[1]:
        raise GateFailure("STAGING_WEB_WORKER_SAME_DSN")
    # Only whitelist-validated host/port and fixed identities leave this function.
    return {"live_database": DATABASE, "live_host": endpoint[0],
            "live_port": endpoint[1], "live_cwd": cwd_expected, "live_commit": TARGET}


def main():
    try:
        if len(sys.argv) != 1 or os.geteuid() != 0:
            raise GateFailure("ROOT_NO_EXTRA_ARGUMENTS")
        result = inspect_identity()
        print("inspected_at_utc=" + datetime.now(timezone.utc).isoformat(), flush=True)
        print("web_active=YES worker_active=YES web_worker_same_database=YES", flush=True)
        for key, value in result.items():
            print(f"{key}={value}", flush=True)
        print("STAGING_RUNTIME_IDENTITY_OK (no DB connection, no changes)", flush=True)
        return 0
    except GateFailure as error:
        print("DUNG: " + str(error) + " (no secret output, no retry)", flush=True)
        return 1
    except Exception:
        print("DUNG: RUNTIME_READ_FAILED (no secret output, no retry)", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
