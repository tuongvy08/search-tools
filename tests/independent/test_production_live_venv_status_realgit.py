"""Independent verifier: old-release `git status` tolerance for the shared-venv symlink (2026-10-02).

LOCAL ONLY. No SSH, no systemd, no PostgreSQL, no server. The out-of-repo ops scripts are loaded
through an AST prefix (no module-level main, no signal registration). Only the external boundary
`subprocess.run` is faked: every `sudo -n -u <owner> ...` command is executed for real WITHOUT sudo
(real Git, real `checked()`/`as_deploy()` including `.strip()`), systemctl/pg_dump/pg_restore/psql
are simulated, '/proc/...' is redirected into a temporary directory.

Production evidence reproduced here (PO, read-only, 2026-10-02):
  LIVE  = /opt/search-tools-pg-release-20260927T013108Z-6155f25-clean (HEAD 6155f25, .gitignore has
          `.venv/` and `venv/`), `.venv` is a SYMLINK to a shared venv, `git status --porcelain
          --untracked-files=all` == exactly `?? .venv`.
  worker /proc cmdline = [LIVE/.venv/bin/python, LIVE/scripts/import_worker.py]
  web    /proc cmdline = [LIVE/.venv/bin/python, -m, gunicorn, ..., search:app]
  LIVE/.venv/bin/python realpath = /usr/bin/python3.10
"""
import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

OPS = Path('/Volumes/DATA/Development/_ops/search-tools')
LOCKED = OPS / 'locked-2026-09-30'
REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / 'specs/deploy-regulatory-manual-edit-production/VERIFICATION.md'
BUNDLE_SRC = OPS / 'search-tools-regulatory-6155f25-to-2ec9b6c.bundle'
BASE = '6155f257dc685ff367cc4431d752b2adcb9fbe9a'
CANARY = 'VERIFIER_FAKE_DB_PASSWORD_MUST_NOT_PRINT'
REVIEWED = {
    OPS / 'prepare_regulatory_staging.py': 'afc6e63ea37c655d9f743b8e6b957f67bd48da6f592ca0b1e3f0c853e8405d50',
    OPS / 'cutover_regulatory_staging.py': '422274129971a01ec3bb0a25adcb27e2712e48ab3f036fafd27d1c20d694b194',
    LOCKED / 'prepare_regulatory_staging.py': 'dd668fb18377ade432503f58b9d3e451d0f7849b75cb7fd430f5f255b499840d',
    LOCKED / 'cutover_regulatory_staging.py': '240424034cd4f5b3768aaf757c0b3a917a2e55958a73789ed4c80cf7ef1991e8',
    CONTRACT: '81ff2ae8c08fec25d0b70c79029f2cea5da7ba16d4835ade991633599e2a33ab',
}
REAL_RUN = subprocess.run
# Isolate the fixture Git from the verifier's own global/system config (excludesFile etc.).
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
               GIT_TERMINAL_PROMPT='0')
STATUS = ['git', 'status', '--porcelain', '--untracked-files=all']


def load(path, args=('--production',)):
    tree = ast.parse(path.read_text(), filename=str(path))
    main = next(node for node in tree.body if isinstance(node, ast.Try))
    prefix = [n for n in tree.body[:tree.body.index(main)] if not isinstance(n, ast.Expr)]
    ns = {'__name__': 'verifier_' + path.stem}
    with mock.patch.object(sys, 'argv', [str(path), *args]):
        exec(compile(ast.Module(body=prefix, type_ignores=[]), str(path), 'exec'), ns)
    return ns, compile(ast.Module(body=[main], type_ignores=[]), str(path), 'exec')


def git(*args, cwd=None):
    run = REAL_RUN(['git', *args], cwd=cwd, env=GIT_ENV, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, text=True, timeout=180)
    if run.returncode:
        raise AssertionError('fixture git failed: ' + run.stderr)
    return run.stdout


def execstart(python, launcher):
    return ('{ path=%s ; argv[]=%s -I -B %s ; ignore_errors=no ; start_time=[n/a] ; '
            'stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }' % (python, python, launcher))


def proc_redirect(proc_root):
    real = Path

    def factory(*parts):
        path = real(*parts)
        text = str(path)
        if text == '/proc' or text.startswith('/proc/'):
            return proc_root.joinpath(*path.parts[2:]) if len(path.parts) > 2 else proc_root
        return path
    return types.SimpleNamespace(Path=factory)


class Template:
    """One real checkout of the production baseline commit, copied per test."""
    path = None
    holder = None

    @classmethod
    def get(cls):
        if cls.path is None:
            cls.holder = tempfile.TemporaryDirectory(prefix='verifier-venv-template-')
            cls.path = Path(cls.holder.name).resolve() / 'checkout'
            git('init', '--quiet', str(cls.path))
            git('-C', str(cls.path), 'fetch', '--quiet', '--no-tags', str(REPO), BASE)
            git('-C', str(cls.path), 'checkout', '--quiet', '--detach', BASE)
        return cls.path


class World:
    def __init__(self, root):
        self.root = root
        usr = root / 'usr/bin'
        usr.mkdir(parents=True)
        (usr / 'python3.10').write_bytes(b'fake interpreter')
        self.py310 = usr / 'python3.10'
        self.shared = root / 'venvs/shared'
        (self.shared / 'bin').mkdir(parents=True)
        (self.shared / 'bin/python').symlink_to(self.py310)
        self.other_venv = root / 'venvs/other-app'
        (self.other_venv / 'bin').mkdir(parents=True)
        (self.other_venv / 'bin/python').symlink_to(self.py310)
        self.opt = root / 'opt'
        self.opt.mkdir()
        self.old = self.opt / 'search-tools-pg-release-20260927T013108Z-6155f25-clean'
        self.old_l = self.opt / (self.old.name + '-launchers-phase6d9')
        self.new = self.opt / 'search-tools-pg-release-20261002T010203Z-2ec9b6c-clean'
        self.new_l = Path(str(self.new) + '-launchers-rme')
        shutil.copytree(Template.get(), self.old, symlinks=True)
        (self.old / '.venv').symlink_to(self.shared, target_is_directory=True)
        self.old.chmod(0o755)
        self.old_l.mkdir()
        for role in ('web', 'worker'):
            (self.old_l / (role + '.py')).write_text(
                "import os\nROOT='%s'\nSECRET='%s'\n" % (self.old, CANARY))
        self.backup = root / 'backups'
        self.backup.mkdir()
        self.checkpoint = self.backup / 'regulatory-manual-edit-2ec9b6c'
        self.systemd = root / 'systemd'
        self.proc = root / 'proc'
        self.proc.mkdir()
        self.bundle = root / 'tmp/search-tools-regulatory-6155f25-to-2ec9b6c.bundle'
        self.bundle.parent.mkdir()
        shutil.copyfile(BUNDLE_SRC, self.bundle)

    @staticmethod
    def py(release):
        return release / '.venv/bin/python'

    def web_exec(self, release):
        return [self.py(release), '-m', 'gunicorn', '--workers', '2', '--timeout', '180',
                '--graceful-timeout', '30', '--bind', '127.0.0.1:5001', 'search:app']

    def worker_exec(self, release):
        return [self.py(release), release / 'scripts/import_worker.py']

    def launcher(self, release, ldir, role):
        return [self.py(release), '-I', '-B', ldir / (role + '.py')]

    def spawn(self, pid, cwd, argv):
        directory = self.proc / str(pid)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
        (directory / 'cwd').symlink_to(cwd, target_is_directory=True)
        (directory / 'cmdline').write_bytes(b'\0'.join(os.fsencode(str(a)) for a in argv) + b'\0')
        dsn = ('postgresql://app:' + CANARY + '@127.0.0.1:5432/searchtools_pg_r1_rollback_20260906_153842')
        (directory / 'environ').write_bytes(b'PATH=/usr/bin\0DATABASE_URL=' + dsn.encode() + b'\0')

    def live_status(self, live=None):
        return git('-C', str(live or self.old), 'status', '--porcelain', '--untracked-files=all')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verifier-venv-status-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        self.w = World(self.root)


# ---------------------------------------------------------------------------------------------
class ArtifactsAndDiffScope(Base):
    def test_hashes(self):
        for path, digest in REVIEWED.items():
            with self.subTest(path=str(path)):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_textual_diff_vs_locked_is_worker_exec_plus_status_helper_only(self):
        helper = [
            "def live_checkout_status_clean(status):",
            '    """Observed on production 2026-10-02 (PO-approved change): the old release keeps its',
            "    shared venv as an untracked symlink `.venv` (only status entry; `.venv/` in .gitignore",
            '    does not match a symlink). Accept exactly that one entry, nothing else."""',
            "    if not status:", "        return True",
            "    if not PRODUCTION or status != '?? .venv':", "        return False",
            "    venv = LIVE / '.venv'", "    try:",
            "        return venv.is_symlink() and venv.resolve(strict=True).is_dir()",
            "    except (OSError, RuntimeError):",
        ]
        worker = [
            "    # Observed on production 2026-10-02 (PO-approved change): the worker launcher execs into",
            "    # the real program, so the running worker is exactly [live venv python, LIVE/scripts/import_worker.py].",
            "    worker_exec = (name == WORKER and len(actual) == 2 and worker_python_matches(actual[0])",
            "                   and actual[1] == str(LIVE / 'scripts/import_worker.py'))",
            "    if not matches_launcher and not web_fallback and not worker_exec:",
        ]
        for name in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
            old = (LOCKED / name).read_text().splitlines()
            new = (OPS / name).read_text().splitlines()
            diff = [l for l in difflib.unified_diff(old, new, lineterm='', n=0)
                    if not l.startswith(('---', '+++', '@@'))]
            added = {l[1:] for l in diff if l.startswith('+') and l[1:].strip()}
            removed = {l[1:] for l in diff if l.startswith('-')}
            with self.subTest(script=name):
                self.assertTrue(set(helper + worker) <= added)
                rest = added - set(helper + worker) - {"        return False"}
                # Remaining added lines are only the re-wrapped `git status` call sites.
                for line in rest:
                    self.assertTrue('live_checkout_status_clean' in line or "'--untracked-files=all'" in line
                                    or 'PRODUCTION_OLD_RELEASE_UNCHANGED' in line, line)
                self.assertTrue(all("'status'" in l or 'web_fallback' in l or "'rev-parse'" in l
                                    for l in removed), removed)

    def test_only_old_release_status_checks_are_relaxed(self):
        """AST: map every `if ... as_deploy(... 'status' ...)` to the gate it raises and whether
        it goes through live_checkout_status_clean. Candidate / post-switch checks must stay strict."""
        expected = {
            'prepare_regulatory_staging.py': {'LIVE_CHECKOUT_CLEAN': True,
                                              'PRODUCTION_OLD_RELEASE_UNCHANGED': True,
                                              'PRODUCTION_RELEASE_CLEAN': False, 'CANDIDATE_CLEAN': False},
            'cutover_regulatory_staging.py': {'LIVE_CHECKOUT_CLEAN': True, 'CANDIDATE_CLEAN': False,
                                              'LIVE_TARGET_CLEAN': False},
        }
        for name, want in expected.items():
            tree = ast.parse((OPS / name).read_text())
            found = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                calls = [c for c in ast.walk(node.test) if isinstance(c, ast.Call)]
                is_status = any(getattr(c.func, 'id', None) == 'as_deploy' and
                                any(isinstance(a, ast.Constant) and a.value == 'status' for a in c.args)
                                for c in calls)
                if not is_status:
                    continue
                wrapped = any(getattr(c.func, 'id', None) == 'live_checkout_status_clean' for c in calls)
                gates = [c.args[0].value for b in node.body for c in ast.walk(b)
                         if isinstance(c, ast.Call) and getattr(c.func, 'id', None) == 'GateFailure']
                found[gates[0]] = wrapped
            with self.subTest(script=name):
                self.assertEqual(found, want)
            # LIVE_CHECKOUT_CLEAN (cutover) precedes the first service stop in source order.
            src = (OPS / name).read_text()
            if name.startswith('cutover'):
                self.assertLess(src.index("raise GateFailure('LIVE_CHECKOUT_CLEAN')"),
                                src.index("checked(['systemctl', 'stop', WORKER]"))


# ---------------------------------------------------------------------------------------------
SCENARIOS = {}


def scenario(name, accepted):
    def wrap(fn):
        SCENARIOS[name] = (fn, accepted)
        return fn
    return wrap


@scenario('real production: .venv symlink to shared venv', True)
def _s(w): pass


@scenario('symlink + stray untracked file', False)
def _s(w): (w.old / 'stray.py').write_text('x')


@scenario('symlink + stray file in subdirectory', False)
def _s(w): (w.old / 'scripts/evil.py').write_text('x')


@scenario('symlink + modified tracked file', False)
def _s(w): (w.old / 'search.py').write_text((w.old / 'search.py').read_text() + '\n# drift\n')


@scenario('symlink + deleted tracked file', False)
def _s(w): (w.old / 'search.py').unlink()


@scenario('symlink + staged new file', False)
def _s(w):
    (w.old / 'staged.py').write_text('x')
    git('-C', str(w.old), 'add', 'staged.py')


@scenario('symlink + .gitignore modified', False)
def _s(w): (w.old / '.gitignore').write_text((w.old / '.gitignore').read_text() + 'extra\n')


@scenario('symlink + nested sub/.venv symlink', False)
def _s(w): (w.old / 'scripts/.venv').symlink_to(w.shared, target_is_directory=True)


@scenario('symlink + file named ".venv " (trailing space)', False)
def _s(w): (w.old / '.venv ').write_text('x')


@scenario('symlink + file named ".venv\\r"', False)
def _s(w): (w.old / '.venv\r').write_text('x')


@scenario('symlink + file named with newline "x\\n?? .venv"', False)
def _s(w): (w.old / 'x\n?? .venv').write_text('x')


@scenario('symlink + file named ".venv/x" impossible; ".venv_" sibling', False)
def _s(w): (w.old / '.venv_').write_text('x')


@scenario('.venv is a regular file', False)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').write_text('not a venv')


@scenario('.venv symlink to a file', False)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').symlink_to(w.py310)


@scenario('.venv dangling symlink', False)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').symlink_to(w.root / 'missing-venv', target_is_directory=True)


@scenario('.venv self loop', False)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').symlink_to('.venv')


@scenario('.venv two-step loop', False)
def _s(w):
    (w.old / '.venv').unlink()
    (w.root / 'loop-a').symlink_to(w.old / '.venv')
    (w.old / '.venv').symlink_to(w.root / 'loop-a')


# --- states the status gate does NOT catch (documented, see VERIFICATION_RESULT) ---------------
@scenario('OBSERVED-ACCEPT: .venv real directory (ignored by .venv/ -> empty status)', True)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv/bin').mkdir(parents=True)
    (w.old / '.venv/bin/python').symlink_to(w.py310)


@scenario('OBSERVED-ACCEPT: .venv symlink to ANOTHER venv dir', True)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').symlink_to(w.other_venv, target_is_directory=True)


@scenario('OBSERVED-ACCEPT: .venv symlink to ignored venv/ dir inside LIVE', True)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / 'venv/bin').mkdir(parents=True)
    (w.old / 'venv/injected.py').write_text('x')
    (w.old / '.venv').symlink_to('venv', target_is_directory=True)


@scenario('OBSERVED-ACCEPT: .venv symlink to "/" (any directory)', True)
def _s(w):
    (w.old / '.venv').unlink()
    (w.old / '.venv').symlink_to('/', target_is_directory=True)


class RealGitStatusMatrix(Base):
    """Real `git status` output -> real checked().strip() -> live_checkout_status_clean()."""

    def status_via_checked(self, ns, live):
        return ns['checked'](['git', '-C', str(live), 'status', '--porcelain', '--untracked-files=all'])

    def test_matrix_prepare_cutover_locked_staging(self):
        for case, (mutate, accepted) in SCENARIOS.items():
            with self.subTest(case=case):
                w = World(Path(tempfile.mkdtemp(dir=self.root)))
                mutate(w)
                raw = w.live_status()
                for filename in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
                    cur, _ = load(OPS / filename)
                    cur['LIVE'] = w.old
                    with mock.patch.dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1'):
                        status = self.status_via_checked(cur, w.old)
                    self.assertEqual(status, raw.strip())
                    got = cur['live_checkout_status_clean'](status)
                    self.assertEqual(got, accepted, '%s %s raw=%r' % (filename, case, raw))
                    # Differential vs locked semantics (accept iff empty): only `?? .venv` + symlink->dir is new.
                    if got and status:
                        self.assertEqual(status, '?? .venv')
                        self.assertTrue((w.old / '.venv').is_symlink())
                        self.assertTrue((w.old / '.venv').resolve(strict=True).is_dir())
                    # Staging profile: exactly the locked rule (status must be empty).
                    stg, _ = load(OPS / filename, ())
                    stg['LIVE'] = w.old
                    self.assertEqual(stg['live_checkout_status_clean'](status), status == '')

    def test_real_production_status_is_exactly_question_venv(self):
        self.assertEqual(self.w.live_status(), '?? .venv\n')
        check = REAL_RUN(['git', 'check-ignore', '-q', '--no-index', '.venv'], cwd=self.w.old, env=GIT_ENV)
        self.assertEqual(check.returncode, 1)  # not ignored, as observed on production

    def test_unrealistic_unit_input_for_real_directory(self):
        """The main agent's unit case feeds '?? .venv' for a real directory; real Git never prints
        that for a real `.venv/` directory: it prints nothing (ignored by `.venv/`)."""
        mutate, _ = SCENARIOS['OBSERVED-ACCEPT: .venv real directory (ignored by .venv/ -> empty status)']
        mutate(self.w)
        self.assertEqual(self.w.live_status(), '')


# ---------------------------------------------------------------------------------------------
class EndToEnd(Base):
    """Real prepare main then real cutover main, real Git, fake /proc + systemd + PG + HTTP."""

    def make_fake_run(self, ns, sim, hooks=None):
        hooks = hooks or {}
        w = self.w

        def fake_run(args, **kw):
            args = list(args)
            if args[0] == 'sudo':
                self.assertEqual(args[1:4], ['-n', '-u', ns['OWNER']])
                real = args[4:]
                self.assertNotIn('push', real)
                kw = dict(kw)
                kw['env'] = GIT_ENV
                result = REAL_RUN(real, **kw)
                for key, hook in hooks.items():
                    if key in real:
                        hook(real)
                sim['git'].append(real)
                return result
            text = kw.get('text')
            out = sim['handler'](args, kw)
            if isinstance(out, types.SimpleNamespace):
                return out
            return subprocess.CompletedProcess(args, 0, stdout=out if text else out.encode())
        return fake_run

    # -- prepare -------------------------------------------------------------------------------
    def run_prepare(self, locked=False, worker_form='exec', hooks=None):
        w = self.w
        ns, main = load((LOCKED if locked else OPS) / 'prepare_regulatory_staging.py')
        ns.update(LIVE=w.old, pathlib=proc_redirect(w.proc), BACKUP_ROOT=w.backup, CANDIDATE=w.new,
                  CHECKPOINT_DIR=w.checkpoint, STATE=w.checkpoint / 'prepared.json',
                  BUNDLE=w.bundle, SYSTEMD_ROOT=w.systemd)
        if worker_form == 'exec':
            w.spawn(1001, w.old, w.web_exec(w.old))
            w.spawn(1002, w.old, w.worker_exec(w.old))
        else:
            w.spawn(1001, w.old, w.launcher(w.old, w.old_l, 'web'))
            w.spawn(1002, w.old, w.launcher(w.old, w.old_l, 'worker'))
        pids = {ns['WEB']: 1001, ns['WORKER']: 1002}
        sim = {'git': [], 'system': []}

        def handler(args, kw):
            sim['system'].append(args)
            self.assertEqual(args[:2], ['systemctl', 'show'])
            unit = args[2]
            if 'ExecStart' in args:
                role = 'web' if unit == ns['WEB'] else 'worker'
                return 'ExecStart=' + execstart(w.py(w.old), w.old_l / (role + '.py'))
            return 'ActiveState=active\nMainPID=%d\nUser=%s\n' % (pids[unit], ns['OWNER'])
        sim['handler'] = handler
        owner = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        out = io.StringIO()
        code = 0
        live_before = self.snapshot_live()
        with mock.patch.object(subprocess, 'run', side_effect=self.make_fake_run(ns, sim, hooks)), \
                mock.patch('pwd.getpwnam', return_value=owner), \
                mock.patch.object(shutil, 'disk_usage', return_value=types.SimpleNamespace(free=50 * 1024**3)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                exec(main, ns)
            except SystemExit as error:
                code = error.code
        text = out.getvalue()
        self.assertNotIn(CANARY, text)
        # Old release never modified by prepare (except by explicit test hooks).
        if not hooks:
            self.assertEqual(self.snapshot_live(), live_before)
        self.assertFalse(w.systemd.exists())
        self.assertFalse(any(c[1] in ('stop', 'start', 'restart', 'daemon-reload') for c in sim['system']))
        return code, text, ns, sim

    def snapshot_live(self):
        w = self.w
        return (git('-C', str(w.old), 'rev-parse', 'HEAD'), w.live_status(),
                os.readlink(w.old / '.venv') if (w.old / '.venv').is_symlink() else 'not-symlink')

    # -- cutover -------------------------------------------------------------------------------
    def run_cutover(self, locked=False, worker_form='exec'):
        w = self.w
        ns, main = load((LOCKED if locked else OPS) / 'cutover_regulatory_staging.py')
        ns.update(LIVE=w.old, OLD_LIVE=w.old, pathlib=proc_redirect(w.proc), BACKUP_ROOT=w.backup,
                  CHECKPOINT_DIR=w.checkpoint, STATE=w.checkpoint / 'prepared.json',
                  DUMP=w.checkpoint / 'prechange.dump', MILESTONE=w.checkpoint / 'cutover-state.json',
                  SYSTEMD_ROOT=w.systemd)
        WEB, WORKER = ns['WEB'], ns['WORKER']
        roles = {WEB: 'web', WORKER: 'worker'}
        for unit in roles:
            (w.systemd / (unit + '.d')).mkdir(parents=True, exist_ok=True)
            (w.systemd / (unit + '.d') / 'zzzzzzzzz-phase6d9.conf').write_text('[Service]\n')
        sim = {'git': [], 'trace': [], 'wd_new': False, 'committed': False, 'next': 2001,
               'units': {WEB: 1001, WORKER: 1002}}
        if worker_form == 'exec':
            w.spawn(1001, w.old, w.web_exec(w.old))
            w.spawn(1002, w.old, w.worker_exec(w.old))
        else:
            w.spawn(1001, w.old, w.launcher(w.old, w.old_l, 'web'))
            w.spawn(1002, w.old, w.launcher(w.old, w.old_l, 'worker'))

        def release():
            return (w.new, w.new_l) if sim['wd_new'] else (w.old, w.old_l)

        def handler(args, kw):
            if args[0] == 'systemctl':
                action = args[1]
                if action in ('start', 'stop'):
                    sim['trace'].append(action + ':' + ','.join(roles[u] for u in args[2:]))
                    for unit in args[2:]:
                        if action == 'stop':
                            if sim['units'][unit]:
                                shutil.rmtree(w.proc / str(sim['units'][unit]))
                            sim['units'][unit] = 0
                        else:
                            rel, _ = release()
                            pid = sim['next']
                            sim['next'] += 1
                            w.spawn(pid, rel, w.web_exec(rel) if roles[unit] == 'web' else w.worker_exec(rel))
                            sim['units'][unit] = pid
                    return ''
                if action == 'daemon-reload':
                    sim['trace'].append('reload')
                    sim['wd_new'] = all((w.systemd / (u + '.d') / ns['DROPIN_NAME']).is_file() for u in roles)
                    return ''
                self.assertEqual(action, 'show')
                unit = args[2]
                rel, ldir = release()
                if 'ControlGroup' in args:
                    return ''
                if 'WorkingDirectory' in args:
                    return str(rel)
                if 'ExecStart' in args and 'ActiveState' not in args:
                    value = execstart(w.py(rel), ldir / (roles[unit] + '.py'))
                    return value if '--value' in args else 'ExecStart=' + value
                pid = sim['units'][unit]
                return ('ActiveState=%s\nSubState=x\nMainPID=%d\nUser=%s\n'
                        % ('active' if pid else 'inactive', pid, ns['OWNER']))
            if args[0] in ('pg_dump', 'pg_restore'):
                if '--version' in args:
                    return args[0] + ' (PostgreSQL) 14.19'
                sim['trace'].append(args[0])
                if args[0] == 'pg_dump':
                    ns['DUMP'].write_bytes(b'PGDMP')
                    return ''
                return '\n'.join(['toc'] * 12)
            if args[0] == 'psql':
                sim['trace'].append('sql')
                sim['committed'] = True
                return types.SimpleNamespace(returncode=0, stdout=b'')
            raise AssertionError('unexpected command ' + args[0])
        sim['handler'] = handler

        def query(pg, sql, **kw):
            answers = {'SELECT current_database()': ns['DATABASE'], 'SHOW server_version': '14.19',
                       'SELECT pg_database_size(current_database())': '1000'}
            if sql in answers:
                return answers[sql]
            if 'pg_stat_activity' in sql:
                return '0'
            if 'manual_protected' in sql:
                return '4|4|4|0|0'
            raise AssertionError('unexpected SQL')

        def schema(pg, migrated):
            if migrated != sim['committed']:
                raise ns['GateFailure']('FIXTURE_SCHEMA')

        class Response:
            status = 200

            def __enter__(inner):
                return inner

            def __exit__(inner, *a):
                return False

            def read(inner):
                return (w.new / 'static/regulatory_manual.js').read_bytes()

        ns.update(query=query, assert_schema=schema, pending=lambda pg: None,
                  snapshot=lambda pg: '1|2|3|4|5', listening_ports=lambda pid: {5001},
                  environment_source_fingerprint=lambda planned_switch=False: ('fp', planned_switch))
        out = io.StringIO()
        code = 0
        with mock.patch.object(os, 'geteuid', return_value=0), \
                mock.patch.object(subprocess, 'run', side_effect=self.make_fake_run(ns, sim)), \
                mock.patch.object(shutil, 'disk_usage', return_value=types.SimpleNamespace(free=50 * 1024**3)), \
                mock.patch('urllib.request.build_opener',
                           return_value=types.SimpleNamespace(open=lambda url, timeout=0: Response())), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                exec(main, ns)
            except SystemExit as error:
                code = error.code
        text = out.getvalue()
        self.assertNotIn(CANARY, text)
        return code, text, ns, sim

    FULL = ['stop:worker', 'stop:web', 'pg_dump', 'pg_restore', 'sql', 'reload', 'start:web,worker']

    def assert_cutover_stopped_before_writes(self, result, gate):
        code, text, ns, sim = result
        self.assertEqual(code, 1)
        self.assertIn('CUTOVER STOP at: read-only preflight gate=' + gate, text)
        self.assertEqual(sim['trace'], [])
        self.assertTrue(all(sim['units'].values()))
        self.assertFalse(ns['MILESTONE'].exists())
        self.assertFalse(any((self.w.systemd / (u + '.d') / ns['DROPIN_NAME']).exists()
                             for u in (ns['WEB'], ns['WORKER'])))

    # -- positive: both real production states -------------------------------------------------
    def test_prepare_then_cutover_pass_with_real_production_states(self):
        w = self.w
        self.assertEqual(w.live_status(), '?? .venv\n')
        code, text, ns, _ = self.run_prepare()
        self.assertEqual(code, 0, text)
        self.assertIn('PREPARE complete', text)
        self.assertEqual(git('-C', str(w.new), 'rev-parse', 'HEAD').strip(), ns['TARGET'])
        self.assertEqual(w.live_status(w.new), '')            # candidate strictly clean
        self.assertEqual(w.live_status(), '?? .venv\n')       # old release untouched
        self.assertEqual((w.new / '.venv').resolve(), w.shared.resolve())
        self.assertFalse(w.systemd.exists())
        code, text, ns, sim = self.run_cutover()
        self.assertEqual(code, 0, text)
        self.assertIn('PRODUCTION TECHNICAL CUTOVER PASS', text)
        self.assertEqual(sim['trace'], self.FULL)
        self.assertEqual(ns['LIVE'], w.new)
        statuses = [g for g in sim['git'] if 'status' in g]
        self.assertEqual([g[g.index('-C') + 1] for g in statuses], [str(w.new), str(w.old), str(w.new)])

    # -- reproduction of production run 2 with the locked scripts --------------------------------
    def test_locked_prepare_reproduces_live_checkout_clean_stop(self):
        code, text, _, _ = self.run_prepare(locked=True, worker_form='launcher')
        self.assertEqual(code, 1)
        self.assertIn('PREPARE STOP at read-only gates gate=LIVE_CHECKOUT_CLEAN', text)
        self.assertFalse(self.w.new.exists())

    # -- negatives: prepare --------------------------------------------------------------------
    def test_prepare_stray_file_stops_at_read_only_gate_without_writes(self):
        for case, mutate in {'stray untracked': lambda w: (w.old / 'stray.py').write_text('x'),
                             'modified tracked': lambda w: (w.old / 'search.py').write_text('drift'),
                             'deleted tracked': lambda w: (w.old / 'search.py').unlink(),
                             'dangling venv': lambda w: ((w.old / '.venv').unlink(),
                                                         (w.old / '.venv').symlink_to(w.root / 'none'))}.items():
            with self.subTest(case=case):
                self.tearDown_world()
                mutate(self.w)
                code, text, _, _ = self.run_prepare()
                self.assertEqual(code, 1)
                self.assertIn('PREPARE STOP at read-only gates gate=', text)
                self.assertFalse(self.w.new.exists())
                self.assertFalse(self.w.checkpoint.exists())

    def tearDown_world(self):
        sub = Path(tempfile.mkdtemp(dir=self.root))
        self.w = World(sub)

    def test_prepare_old_release_changed_mid_run_stops_before_checkpoint_file(self):
        w = self.w

        def after_clone(real):
            if 'clone' in real:
                (w.old / 'appeared-during-prepare.py').write_text('x')
        code, text, ns, _ = self.run_prepare(hooks={'clone': after_clone})
        self.assertEqual(code, 1)
        self.assertIn('gate=PRODUCTION_OLD_RELEASE_UNCHANGED', text)
        self.assertFalse((w.checkpoint / 'prepared.json').exists())

    def test_prepare_real_venv_directory_not_stopped_by_status_gate(self):
        """Observed: a real `.venv/` dir is invisible to `git status` (ignored), so it passes the
        read-only status gate and is only stopped later (SHARED_VENV_SOURCE) after the candidate
        release directory was already created. Same as the locked script (pre-existing)."""
        w = self.w
        (w.old / '.venv').unlink()
        (w.old / '.venv/bin').mkdir(parents=True)
        (w.old / '.venv/bin/python').symlink_to(w.py310)
        code, text, _, _ = self.run_prepare()
        self.assertEqual(code, 1)
        self.assertIn('gate=SHARED_VENV_SOURCE', text)
        self.assertTrue(w.new.exists())                       # candidate written before the stop
        self.assertFalse(w.checkpoint.exists())

    def test_prepare_accepts_venv_retargeted_to_another_venv(self):
        """Observed: nothing anchors `.venv` to THE shared venv; any directory with bin/python whose
        realpath matches passes, and the new release inherits that target."""
        w = self.w
        (w.old / '.venv').unlink()
        (w.old / '.venv').symlink_to(w.other_venv, target_is_directory=True)
        code, text, _, _ = self.run_prepare()
        self.assertEqual(code, 0, text)
        self.assertEqual((w.new / '.venv').resolve(), w.other_venv.resolve())

    # -- negatives: cutover after a successful prepare -------------------------------------------
    def test_cutover_blocks_old_release_drift_after_prepare(self):
        mutations = {
            'stray untracked': (lambda w: (w.old / 'stray.py').write_text('x'), 'LIVE_CHECKOUT_CLEAN'),
            'modified tracked': (lambda w: (w.old / 'search.py').write_text('drift'), 'LIVE_CHECKOUT_CLEAN'),
            'deleted tracked': (lambda w: (w.old / 'search.py').unlink(), 'LIVE_CHECKOUT_CLEAN'),
            'nested .venv': (lambda w: (w.old / 'scripts/.venv').symlink_to(w.shared), 'LIVE_CHECKOUT_CLEAN'),
            '.venv retargeted': (lambda w: ((w.old / '.venv').unlink(),
                                            (w.old / '.venv').symlink_to(w.other_venv)), 'PRODUCTION_SHARED_VENV'),
            '.venv real directory': (lambda w: ((w.old / '.venv').unlink(), (w.old / '.venv').mkdir()),
                                     'PRODUCTION_SHARED_VENV'),
            '.venv regular file': (lambda w: ((w.old / '.venv').unlink(), (w.old / '.venv').write_text('x')),
                                   None),
            '.venv dangling': (lambda w: ((w.old / '.venv').unlink(),
                                          (w.old / '.venv').symlink_to(w.root / 'none')), None),
        }
        for case, (mutate, gate) in mutations.items():
            with self.subTest(case=case):
                self.tearDown_world()
                code, text, _, _ = self.run_prepare()
                self.assertEqual(code, 0, text)
                mutate(self.w)
                result = self.run_cutover()
                if gate:
                    self.assert_cutover_stopped_before_writes(result, gate)
                else:
                    code, text, ns, sim = result
                    self.assertEqual(code, 1)
                    self.assertIn('CUTOVER STOP at: read-only preflight', text)
                    self.assertEqual(sim['trace'], [])
                    self.assertTrue(all(sim['units'].values()))

    def test_locked_cutover_reproduces_live_checkout_clean_stop(self):
        code, text, _, _ = self.run_prepare()
        self.assertEqual(code, 0, text)
        self.assert_cutover_stopped_before_writes(self.run_cutover(locked=True, worker_form='launcher'),
                                                  'LIVE_CHECKOUT_CLEAN')


if __name__ == '__main__':
    unittest.main(verbosity=2)
