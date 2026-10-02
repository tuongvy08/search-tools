"""[READ-ONLY] Explain why production prepare stopped at LIVE_CHECKOUT_CLEAN.

Runs the same `git status --porcelain --untracked-files=all` as prepare (as the release
owner, same safe.directory) on the live release and prints only the entry NAMES (never
file contents), plus how `.venv` is set up. No writes, no restarts, no DB access.
Run: ssh python 'sudo -n python3 -I -B -' < this_file
"""

import pathlib
import subprocess

LIVE = pathlib.Path("/opt/search-tools-pg-release-20260927T013108Z-6155f25-clean")
OWNER = "searchtools-pg"


def git(*args):
    result = subprocess.run(
        ["sudo", "-n", "-u", OWNER, "env", "GIT_TERMINAL_PROMPT=0", "GIT_PAGER=cat",
         "git", "-c", "safe.directory=" + str(LIVE), "-C", str(LIVE), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
    return result.returncode, result.stdout.rstrip("\n"), result.stderr.strip()[:200]


code, out, err = git("status", "--porcelain", "--untracked-files=all")
entries = out.splitlines() if out else []
print("git_status_rc=", code, "entries=", len(entries), "stderr=", err)
for line in entries[:40]:
    print("  entry:", line)
if len(entries) > 40:
    print("  ... more entries:", len(entries) - 40)
venv = LIVE / ".venv"
print("venv_exists=", venv.exists(), "venv_is_symlink=", venv.is_symlink(),
      "venv_is_real_dir=", venv.is_dir() and not venv.is_symlink())
code, out, err = git("check-ignore", "-v", "--no-index", ".venv")
print("check_ignore_.venv rc=", code, "rule=", out or "(not ignored)")
exclude = LIVE / ".git/info/exclude"
lines = [l for l in exclude.read_text().splitlines() if l.strip() and not l.startswith("#")] if exclude.is_file() else []
print("info_exclude_patterns=", lines)
code, out, _ = git("rev-parse", "HEAD")
print("head=", out)
print("DIAGNOSE DONE (read-only; names only, no file contents, no writes)")
