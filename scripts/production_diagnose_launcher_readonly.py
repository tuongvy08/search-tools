"""[READ-ONLY] Explain why production prepare stopped at PRODUCTION_LAUNCHER_CMDLINE.

Re-evaluates, for both production units, each condition of the prepare script's
verify_production_entrypoint() and prints True/False per condition plus paths.
No writes, no restarts, no DB access. Any token containing '=' is masked so no
configuration value is shown. Run: ssh python 'sudo -n python3 -I -B -' < this_file
"""

import pathlib
import re
import shlex
import subprocess

LIVE = pathlib.Path("/opt/search-tools-pg-release-20260927T013108Z-6155f25-clean")
UNITS = (("search-tools-pg.service", "web"), ("search-tools-import-worker.service", "worker"))
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}


def run(args):
    result = subprocess.run(args, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=20, check=False)
    return result.returncode, result.stdout.strip()


def mask(token):
    return token.split("=", 1)[0] + "=***" if "=" in token else token


def python_matches(executable):
    try:
        if not pathlib.Path(executable).is_absolute():
            return False
        expected = (LIVE / ".venv/bin/python").resolve(strict=True)
        return expected.is_file() and pathlib.Path(executable).resolve(strict=True) == expected
    except (OSError, ValueError, RuntimeError):
        return False


def safe_resolve(path):
    try:
        return str(pathlib.Path(path).resolve(strict=True))
    except (OSError, RuntimeError):
        return "UNRESOLVABLE"


print("expected_venv_python_realpath=", safe_resolve(LIVE / ".venv/bin/python"))
for unit, role in UNITS:
    print("===", unit)
    _, out = run(["systemctl", "show", unit, "--no-pager", "-p", "MainPID", "-p", "User", "-p", "ActiveState"])
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    pid = info.get("MainPID", "0")
    print("active=", info.get("ActiveState"), "user=", info.get("User"), "main_pid=", pid)
    _, start = run(["systemctl", "show", unit, "--no-pager", "-p", "ExecStart"])
    path = re.search(r"\bpath=([^ ;]+)", start)
    argv = re.search(r"\bargv\[\]=([^;]+)", start)
    tokens = shlex.split(argv.group(1).strip()) if argv else []
    print("execstart_path=", mask(path.group(1)) if path else None)
    print("execstart_argv=", [mask(t) for t in tokens])
    print("check_execstart_4_tokens=", len(tokens) == 4,
          "check_execstart_-I_-B=", tokens[1:3] == ["-I", "-B"],
          "check_execstart_path_is_live_venv_python=", bool(path) and python_matches(path.group(1)),
          "check_execstart_argv0_is_live_venv_python=", bool(tokens) and python_matches(tokens[0]))
    launcher = pathlib.Path(tokens[3]) if len(tokens) == 4 else None
    if launcher is not None:
        print("launcher=", launcher,
              "name_ok=", launcher.name == role + ".py",
              "parent_dir_ok=", launcher.parent.parent == LIVE.parent
              and launcher.parent.name.startswith(LIVE.name + "-launchers-"),
              "is_file=", launcher.is_file(), "not_symlinked=", safe_resolve(launcher) == str(launcher))
    try:
        raw = pathlib.Path("/proc", pid, "cmdline").read_bytes().split(b"\0")
        actual = [token.decode(errors="replace") for token in raw if token]
    except OSError:
        actual = []
        print("process_cmdline= UNREADABLE")
    print("process_cmdline=", [mask(t) for t in actual])
    print("process_exe_realpath=", safe_resolve(pathlib.Path("/proc", pid, "exe")))
    print("check_cmdline_4_tokens=", len(actual) == 4,
          "check_cmdline_-I_-B=", actual[1:3] == ["-I", "-B"],
          "check_cmdline_argv0_is_live_venv_python=", bool(actual) and python_matches(actual[0]),
          "check_cmdline_argv3_is_launcher=", len(actual) == 4 and launcher is not None and actual[3] == str(launcher))
    if role == "web":
        # Same semantics as prepare: whole cmdline elements, not substrings.
        tokens_raw = raw if actual else []
        print("web_fallback_token_search_app=", tokens_raw.count(b"search:app"),
              "web_fallback_token_gunicorn_master=", tokens_raw.count(b"gunicorn: master [search:app]"))
print("DIAGNOSE DONE (read-only; no writes, no restarts, no DB)")
