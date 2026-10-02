"""Independent verifier: production launcher/exec command-line recognition (2026-10-02).

LOCAL ONLY. Loads the out-of-repo ops scripts through an AST prefix (no module-level
main, no signal registration), redirects every '/proc/...' path into a temporary
directory, and fakes systemctl/git/psql/pg_dump/HTTP. Nothing touches SSH, systemd,
PostgreSQL or any server.

Fixtures use the command lines measured read-only on production (PO evidence 2026-10-02):
  web    ExecStart [LIVE/.venv/bin/python, -I, -B, LIVE-launchers-phase6d9/web.py]
         /proc     [LIVE/.venv/bin/python, -m, gunicorn, --workers, 2, --timeout, 180,
                    --graceful-timeout, 30, --bind, 127.0.0.1:5001, search:app]
  worker ExecStart [LIVE/.venv/bin/python, -I, -B, LIVE-launchers-phase6d9/worker.py]
         /proc     [LIVE/.venv/bin/python, LIVE/scripts/import_worker.py]
  LIVE/.venv/bin/python realpath = /usr/bin/python3.10 (here: <tmp>/usr/bin/python3.10)
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
CONTRACT = Path(__file__).resolve().parents[2] / 'specs/deploy-regulatory-manual-edit-production/VERIFICATION.md'
CANARY = 'VERIFIER_FAKE_DB_PASSWORD_MUST_NOT_PRINT'
REVIEWED = {
    OPS / 'prepare_regulatory_staging.py': 'b8e4ed37729e3af40dbd23a02bf5bcdddb954b23fef11a79570d2a3c5ef668db',
    OPS / 'cutover_regulatory_staging.py': 'dcc5e352520b76a0fb4e80c241bcfb5a4673433d42cb4d005196ac956ed47f28',
    LOCKED / 'prepare_regulatory_staging.py': 'dd668fb18377ade432503f58b9d3e451d0f7849b75cb7fd430f5f255b499840d',
    LOCKED / 'cutover_regulatory_staging.py': '240424034cd4f5b3768aaf757c0b3a917a2e55958a73789ed4c80cf7ef1991e8',
    CONTRACT: '81ff2ae8c08fec25d0b70c79029f2cea5da7ba16d4835ade991633599e2a33ab',
}


def load(path, args=('--production',)):
    tree = ast.parse(path.read_text(), filename=str(path))
    main = next(node for node in tree.body if isinstance(node, ast.Try))
    prefix = [n for n in tree.body[:tree.body.index(main)] if not isinstance(n, ast.Expr)]
    ns = {'__name__': 'verifier_' + path.stem}
    with mock.patch.object(sys, 'argv', [str(path), *args]):
        exec(compile(ast.Module(body=prefix, type_ignores=[]), str(path), 'exec'), ns)
    return ns, compile(ast.Module(body=[main], type_ignores=[]), str(path), 'exec')


def execstart(python, launcher):
    return ('{ path=%s ; argv[]=%s -I -B %s ; ignore_errors=no ; start_time=[n/a] ; '
            'stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }' % (python, python, launcher))


def enc(tokens):
    return [os.fsencode(str(t)) for t in tokens] + [b'']


class World:
    """Filesystem shaped like production: /opt releases, shared venv, python3.10 realpath."""

    def __init__(self, root):
        self.root = root
        usr = root / 'usr/bin'
        usr.mkdir(parents=True)
        (usr / 'python3.10').write_bytes(b'fake interpreter')
        (usr / 'python3').symlink_to('python3.10')
        (usr / 'python3.12').write_bytes(b'other interpreter')
        self.py310, self.py3, self.py312 = usr / 'python3.10', usr / 'python3', usr / 'python3.12'
        self.shared = root / 'venvs/shared'
        (self.shared / 'bin').mkdir(parents=True)
        (self.shared / 'bin/python').symlink_to(self.py310)
        opt = root / 'opt'
        opt.mkdir()
        self.old = opt / 'search-tools-pg-release-20260927T013108Z-6155f25-clean'
        self.old_l = opt / (self.old.name + '-launchers-phase6d9')
        self.new = opt / 'search-tools-pg-release-20261002T010203Z-2ec9b6c-clean'
        self.new_l = Path(str(self.new) + '-launchers-rme')
        for release, ldir in ((self.old, self.old_l), (self.new, self.new_l)):
            (release / 'scripts').mkdir(parents=True)
            (release / 'scripts/import_worker.py').write_text('# worker\n')
            (release / 'scripts/evil.py').write_text('# other\n')
            (release / '.venv').symlink_to(self.shared, target_is_directory=True)
            release.chmod(0o755)
            ldir.mkdir()
            for role in ('web', 'worker'):
                (ldir / (role + '.py')).write_text("ROOT='%s'\n" % release)
        (root / 'alias').symlink_to(self.old, target_is_directory=True)
        (root / 'iw.py').symlink_to(self.old / 'scripts/import_worker.py')

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


def proc_redirect(proc_root):
    real = Path

    def factory(*parts):
        path = real(*parts)
        text = str(path)
        if text == '/proc' or text.startswith('/proc/'):
            return proc_root.joinpath(*path.parts[2:]) if len(path.parts) > 2 else proc_root
        return path
    return types.SimpleNamespace(Path=factory)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verifier-launcher-exec-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.w = World(self.root)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)

    def ns(self, filename, live, locked=False, args=('--production',)):
        ns, main = load((LOCKED if locked else OPS) / filename, args)
        ns.update(LIVE=live)
        if 'OLD_LIVE' in ns:
            ns['OLD_LIVE'] = self.w.old
        return ns, main

    def spawn(self, pid, cwd, argv, env=None, cmdline_readable=True):
        directory = self.proc / str(pid)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
        (directory / 'cwd').symlink_to(cwd, target_is_directory=True)
        if cmdline_readable:
            (directory / 'cmdline').write_bytes(b'\0'.join(os.fsencode(str(a)) for a in argv) + b'\0'
                                                if argv else b'')
        (directory / 'environ').write_bytes(env if env is not None else self.environ())
        return pid

    def dsn(self, database='searchtools_pg_r1_rollback_20260906_153842'):
        return ('postgresql://app:' + CANARY + '@127.0.0.1:5432/' + database).encode()

    def environ(self, with_dsn=True):
        records = [b'PATH=/usr/bin:/bin', b'LANG=C.UTF-8']
        if with_dsn:
            records.append(b'DATABASE_URL=' + self.dsn())
        return b'\0'.join(records) + b'\0'


# ---------------------------------------------------------------------------------------
class ReviewedArtifactsAndDiffScope(Base):
    def test_hashes_of_reviewed_artifacts_and_locked_contract(self):
        for path, digest in REVIEWED.items():
            with self.subTest(path=path.name, parent=path.parent.name):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_diff_against_locked_only_adds_worker_exec_condition(self):
        expected_added = {
            "    # Observed on production 2026-10-02 (PO-approved change): the worker launcher execs into",
            "    # the real program, so the running worker is exactly [live venv python, LIVE/scripts/import_worker.py].",
            "    worker_exec = (name == WORKER and len(actual) == 2 and worker_python_matches(actual[0])",
            "                   and actual[1] == str(LIVE / 'scripts/import_worker.py'))",
            "    if not matches_launcher and not web_fallback and not worker_exec:",
        }
        for name in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
            old = (LOCKED / name).read_text().splitlines()
            new = (OPS / name).read_text().splitlines()
            diff = [l for l in difflib.unified_diff(old, new, lineterm='', n=0)
                    if not l.startswith(('---', '+++', '@@'))]
            added = {l[1:] for l in diff if l.startswith('+')}
            removed = {l[1:] for l in diff if l.startswith('-')}
            with self.subTest(script=name):
                self.assertEqual(added, expected_added)
                self.assertEqual(removed, {"    if not matches_launcher and not web_fallback:"})
            # Every top-level definition except verify_production_entrypoint is AST-identical.
            told = {getattr(n, 'name', None) or ast.dump(n): ast.dump(n) for n in ast.parse('\n'.join(old)).body}
            tnew = {getattr(n, 'name', None) or ast.dump(n): ast.dump(n) for n in ast.parse('\n'.join(new)).body}
            changed = {k for k in set(told) | set(tnew) if told.get(k) != tnew.get(k)}
            with self.subTest(script=name, check='ast'):
                self.assertEqual(changed, {'verify_production_entrypoint'})


# ---------------------------------------------------------------------------------------
class EntrypointMatrix(Base):
    """verify_entrypoint() with real production fixtures and adversarial process forms."""

    def accept(self, ns, unit, start, argv):
        return ns['verify_entrypoint'](unit, start, enc(argv))

    def reject(self, ns, unit, start, argv):
        with self.assertRaises(ns['GateFailure']) as ctx:
            ns['verify_entrypoint'](unit, start, enc(argv) if argv is not None else [b''])
        self.assertEqual(str(ctx.exception), 'PRODUCTION_LAUNCHER_CMDLINE')

    def pre_switch_namespaces(self):
        for filename in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
            yield filename, self.ns(filename, self.w.old)[0]

    def test_real_production_forms_accepted_before_switch(self):
        w = self.w
        for filename, ns in self.pre_switch_namespaces():
            web_start = execstart(w.py(w.old), w.old_l / 'web.py')
            worker_start = execstart(w.py(w.old), w.old_l / 'worker.py')
            with self.subTest(script=filename):
                self.assertEqual(self.accept(ns, ns['WEB'], web_start, w.web_exec(w.old)), w.old_l / 'web.py')
                self.assertEqual(self.accept(ns, ns['WORKER'], worker_start, w.worker_exec(w.old)),
                                 w.old_l / 'worker.py')
                # Moment right after start, process still in launcher form.
                self.accept(ns, ns['WEB'], web_start, w.launcher(w.old, w.old_l, 'web'))
                self.accept(ns, ns['WORKER'], worker_start, w.launcher(w.old, w.old_l, 'worker'))
                # Contract: Python identity by realpath (python3 symlink / direct python3.10).
                self.accept(ns, ns['WORKER'], worker_start, [w.py310, w.old / 'scripts/import_worker.py'])
                self.accept(ns, ns['WORKER'], worker_start, [w.py3, w.old / 'scripts/import_worker.py'])

    def test_invalid_worker_processes_rejected_before_switch(self):
        w = self.w
        script = w.old / 'scripts/import_worker.py'
        py = w.py(w.old)
        bad = {
            'new release script (python old)': [py, w.new / 'scripts/import_worker.py'],
            'new release python+script': [w.py(w.new), w.new / 'scripts/import_worker.py'],
            'other script same release': [py, w.old / 'scripts/evil.py'],
            'worker at release root': [py, w.old / 'import_worker.py'],
            'pyc variant': [py, str(script) + 'c'],
            'trailing slash': [py, str(script) + '/'],
            'other python realpath': [w.py312, script],
            'relative python': ['.venv/bin/python', script],
            'bare python3': ['python3', script],
            'relative script': [py, 'scripts/import_worker.py'],
            'dot relative script': [py, './scripts/import_worker.py'],
            'dotdot path': [py, str(w.old) + '/../' + w.old.name + '/scripts/import_worker.py'],
            'double slash': [py, str(w.old) + '//scripts/import_worker.py'],
            'symlinked release dir': [py, w.root / 'alias/scripts/import_worker.py'],
            'symlinked script file': [py, w.root / 'iw.py'],
            'extra argument': [py, script, '--once'],
            'flags -I -B before script': [py, '-I', '-B', script],
            'flag -u before script': [py, '-u', script],
            'python -c': [py, '-c', 'import runpy; runpy.run_path("%s")' % script],
            'python -m module': [py, '-m', 'scripts.import_worker'],
            'web gunicorn form on worker': w.web_exec(w.old),
            'web launcher on worker': w.launcher(w.old, w.old_l, 'web'),
            'new launcher before switch': w.launcher(w.old, w.new_l, 'worker'),
            'reversed order': [script, py],
            'python only': [py],
            'script only': [script],
            'empty cmdline': [],
            'systemd pre-exec title': ['(python)'],
        }
        for filename, ns in self.pre_switch_namespaces():
            start = execstart(py, w.old_l / 'worker.py')
            for case, argv in bad.items():
                with self.subTest(script=filename, case=case):
                    self.reject(ns, ns['WORKER'], start, argv)

    def test_invalid_web_processes_rejected_before_switch(self):
        w = self.w
        for filename, ns in self.pre_switch_namespaces():
            start = execstart(w.py(w.old), w.old_l / 'web.py')
            for case, argv in {
                'worker exec form on web': w.worker_exec(w.old),
                'worker launcher on web': w.launcher(w.old, w.old_l, 'worker'),
                'other python script on web': [w.py(w.old), w.old / 'scripts/evil.py'],
                'empty': [],
            }.items():
                with self.subTest(script=filename, case=case):
                    self.reject(ns, ns['WEB'], start, argv)

    def test_after_switch_cutover_accepts_only_new_release(self):
        w = self.w
        ns, _ = self.ns('cutover_regulatory_staging.py', w.new)
        web_start = execstart(w.py(w.new), w.new_l / 'web.py')
        worker_start = execstart(w.py(w.new), w.new_l / 'worker.py')
        self.assertEqual(self.accept(ns, ns['WORKER'], worker_start, w.worker_exec(w.new)), w.new_l / 'worker.py')
        self.accept(ns, ns['WORKER'], worker_start, w.launcher(w.new, w.new_l, 'worker'))
        self.accept(ns, ns['WEB'], web_start, w.web_exec(w.new))
        self.accept(ns, ns['WEB'], web_start, w.launcher(w.new, w.new_l, 'web'))
        for case, argv in {
            'old release script, new python': [w.py(w.new), w.old / 'scripts/import_worker.py'],
            'old release script, old python': w.worker_exec(w.old),
            'old launcher still running': w.launcher(w.old, w.old_l, 'worker'),
            'new script other python': [w.py312, w.new / 'scripts/import_worker.py'],
            'worker form on worker unit with extra arg': w.worker_exec(w.new) + ['x'],
        }.items():
            with self.subTest(case=case):
                self.reject(ns, ns['WORKER'], worker_start, argv)
        with self.subTest(case='worker form on web unit'):
            self.reject(ns, ns['WEB'], web_start, w.worker_exec(w.new))
        # ExecStart still pointing at OLD launchers after switch is a path failure.
        with self.assertRaises(ns['GateFailure']):
            ns['verify_entrypoint'](ns['WORKER'], execstart(w.py(w.new), w.old_l / 'worker.py'),
                                    enc(w.worker_exec(w.new)))

    def test_differential_vs_locked_only_worker_exec_form_newly_accepted(self):
        """Any input locked accepted is still accepted; anything newly accepted is exactly
        [python with venv realpath, LIVE/scripts/import_worker.py] on the WORKER unit."""
        w = self.w
        script = w.old / 'scripts/import_worker.py'
        candidates = [w.web_exec(w.old), w.worker_exec(w.old), w.launcher(w.old, w.old_l, 'web'),
                      w.launcher(w.old, w.old_l, 'worker'), [w.py312, script], [w.py310, script],
                      [w.py(w.old), w.new / 'scripts/import_worker.py'], [w.py(w.old), '-m', 'x'],
                      [w.py(w.old), script, 'search:app'], [], ['search:app'],
                      [w.py(w.old), script, '']]
        for filename in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
            cur, _ = self.ns(filename, w.old)
            old, _ = self.ns(filename, w.old, locked=True)
            for unit, role in ((cur['WEB'], 'web'), (cur['WORKER'], 'worker')):
                start = execstart(w.py(w.old), w.old_l / (role + '.py'))
                for argv in candidates:
                    results = []
                    for ns in (old, cur):
                        try:
                            ns['verify_entrypoint'](unit, start, enc(argv))
                            results.append(True)
                        except ns['GateFailure']:
                            results.append(False)
                    with self.subTest(script=filename, unit=role, argv=[str(a) for a in argv]):
                        if results[0]:
                            self.assertTrue(results[1], 'regression: locked accepted, current rejects')
                        if results[1] and not results[0]:
                            filtered = [str(a) for a in argv if str(a)]
                            self.assertEqual(role, 'worker')
                            self.assertEqual(len(filtered), 2)
                            self.assertEqual(filtered[1], str(script))
                            self.assertEqual(Path(filtered[0]).resolve(), w.py310)

    def test_staging_profile_identical_to_locked(self):
        w = self.w
        live = w.old
        (live / '.venv/bin').exists()
        argv_sets = [w.worker_exec(live), [w.py(live), 'scripts/import_worker.py'],
                     [w.py(live), '-u', w.old / 'scripts/import_worker.py'], w.web_exec(live), [b'search:app']]
        starts = ['{ path=%s ; argv[]=%s scripts/import_worker.py ; }' % (w.py(live), w.py(live)),
                  '{ path=%s/.venv/bin/gunicorn ; argv[]=%s/.venv/bin/gunicorn search:app ; }' % (live, live)]
        for filename in ('prepare_regulatory_staging.py', 'cutover_regulatory_staging.py'):
            cur, _ = self.ns(filename, live, args=())
            old, _ = self.ns(filename, live, locked=True, args=())
            self.assertFalse(cur['PRODUCTION'])
            for unit in (cur['WEB'], cur['WORKER']):
                for start in starts:
                    for argv in argv_sets:
                        outcome = []
                        for ns in (old, cur):
                            try:
                                ns['verify_entrypoint'](unit, start, enc(argv))
                                outcome.append('PASS')
                            except ns['GateFailure'] as error:
                                outcome.append(str(error))
                        with self.subTest(script=filename, unit=unit, start=start[:40], argv=str(argv)[:60]):
                            self.assertEqual(outcome[0], outcome[1])


# ---------------------------------------------------------------------------------------
class PrepareReadOnlyGatesWithFakeProc(Base):
    """Run prepare's real main through its read-only gates with /proc faked."""

    def run_prepare(self, worker_argv, locked=False, web_argv=None):
        w = self.w
        ns, main = self.ns('prepare_regulatory_staging.py', w.old, locked=locked)
        backup = self.root / 'backups'
        backup.mkdir(exist_ok=True)
        candidate = self.root / 'opt/search-tools-pg-release-20261002T020000Z-2ec9b6c-clean'
        ns.update(pathlib=proc_redirect(self.proc), BACKUP_ROOT=backup, CANDIDATE=candidate,
                  CHECKPOINT_DIR=backup / 'regulatory-manual-edit-2ec9b6c',
                  STATE=backup / 'regulatory-manual-edit-2ec9b6c/prepared.json',
                  BUNDLE=self.root / 'missing.bundle')
        self.spawn(1001, w.old, web_argv or w.web_exec(w.old))
        self.spawn(1002, w.old, worker_argv)
        pids = {ns['WEB']: 1001, ns['WORKER']: 1002}
        commands = []

        def checked(args, **kw):
            commands.append(args)
            self.assertEqual(args[:2], ['systemctl', 'show'])
            unit = args[2]
            if 'ExecStart' in args:
                role = 'web' if unit == ns['WEB'] else 'worker'
                return 'ExecStart=' + execstart(w.py(w.old), w.old_l / (role + '.py'))
            return 'ActiveState=active\nMainPID=%d\nUser=%s' % (pids[unit], ns['OWNER'])

        def as_deploy(*args, **kw):
            commands.append(args)
            self.assertNotIn('clone', args)
            return ns['BASE'] if 'rev-parse' in args else ''
        ns.update(checked=checked, as_deploy=as_deploy)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.object(subprocess, 'run', side_effect=AssertionError('no command')):
            with self.assertRaises(SystemExit):
                exec(main, ns)
        self.assertFalse(candidate.exists())
        self.assertFalse((backup / 'regulatory-manual-edit-2ec9b6c').exists())
        self.assertFalse(Path(str(candidate) + '-launchers-rme').exists())
        return out.getvalue(), ns

    def test_locked_prepare_reproduces_production_stop(self):
        out, _ = self.run_prepare(self.w.worker_exec(self.w.old), locked=True)
        self.assertIn('PREPARE STOP at read-only gates gate=PRODUCTION_LAUNCHER_CMDLINE', out)

    def test_current_prepare_passes_read_only_gates_with_real_forms(self):
        out, ns = self.run_prepare(self.w.worker_exec(self.w.old))
        # The fixture bundle is intentionally absent: reaching the bundle step proves every
        # read-only gate (both services, cwd, ExecStart, cmdline, live commit) passed.
        self.assertIn('PREPARE STOP at verify transferred bundle', out)
        self.assertNotIn('read-only gates gate=', out)
        self.assertEqual(ns['launchers'], {ns['WEB']: self.w.old_l / 'web.py',
                                           ns['WORKER']: self.w.old_l / 'worker.py'})

    def test_current_prepare_still_stops_on_foreign_worker(self):
        w = self.w
        for case, argv in {'new release worker': [w.py(w.old), w.new / 'scripts/import_worker.py'],
                           'other script': [w.py(w.old), w.old / 'scripts/evil.py'],
                           'other python': [w.py312, w.old / 'scripts/import_worker.py'],
                           'python -c': [w.py(w.old), '-c', 'pass'],
                           'web form on worker': w.web_exec(w.old)}.items():
            with self.subTest(case=case):
                out, _ = self.run_prepare(argv)
                self.assertIn('PREPARE STOP at read-only gates gate=PRODUCTION_LAUNCHER_CMDLINE', out)
        with self.subTest(case='worker form on web unit'):
            out, _ = self.run_prepare(w.worker_exec(w.old), web_argv=w.worker_exec(w.old))
            self.assertIn('PREPARE STOP at read-only gates gate=PRODUCTION_LAUNCHER_CMDLINE', out)


# ---------------------------------------------------------------------------------------
class CutoverEndToEndWithFakeProc(Base):
    """Real cutover main + real unit()/env_dsn()/stray_worker_count()/activate_production_release()
    over a fake /proc and fake systemd. Mocked: psql/pg_dump/git/HTTP/env-source fingerprint."""

    def run_cutover(self, pre=None, post=None, locked=False):
        w = self.w
        ns, main = self.ns('cutover_regulatory_staging.py', w.old, locked=locked)
        ckpt = self.root / 'backups/regulatory-manual-edit-2ec9b6c'
        ckpt.mkdir(parents=True)
        systemd = self.root / 'systemd'
        ns.update(pathlib=proc_redirect(self.proc), BACKUP_ROOT=ckpt.parent, CHECKPOINT_DIR=ckpt,
                  STATE=ckpt / 'prepared.json', DUMP=ckpt / 'prechange.dump',
                  MILESTONE=ckpt / 'cutover-state.json', SYSTEMD_ROOT=systemd)
        WEB, WORKER = ns['WEB'], ns['WORKER']
        roles = {WEB: 'web', WORKER: 'worker'}
        for folder, name, data in (('sql', 'migration_033_regulatory_manual_edit.sql', b'BEGIN; COMMIT;'),
                                   ('static', 'regulatory_manual.js', b'console.log(1)')):
            (w.new / folder).mkdir(exist_ok=True)
            (w.new / folder / name).write_bytes(data)
        for unit, role in roles.items():
            (systemd / (unit + '.d')).mkdir(parents=True)
            (systemd / (unit + '.d') / 'zzzzzzzzz-phase6d9.conf').write_text('[Service]\n')
            (ckpt / (unit + '.d')).mkdir()
            (ckpt / (unit + '.d') / ns['DROPIN_NAME']).write_bytes(
                ns['dropin_content'](w.new, w.new_l / (role + '.py')))
        hashes = {n: hashlib.sha256((w.new_l / n).read_bytes()).hexdigest() for n in ('web.py', 'worker.py')}
        ns['STATE'].write_text(json.dumps({
            'profile': 'production', 'web': WEB, 'worker': WORKER, 'owner': ns['OWNER'],
            'live': str(w.old), 'candidate': str(w.new), 'baseline_commit': ns['BASE'],
            'target_commit': ns['TARGET'], 'database_name': ns['DATABASE'],
            'dropin_name': ns['DROPIN_NAME'], 'launchers_new': str(w.new_l),
            'launcher_hashes': hashes, 'venv_target': str(w.shared.resolve())}))

        sim = {'wd_new': False, 'committed': False, 'next_pid': 2001,
               'units': {WEB: 1001, WORKER: 1002}, 'trace': []}
        pre = pre or {}
        post = post or {}
        defaults_pre = {'web': w.web_exec(w.old), 'worker': w.worker_exec(w.old)}
        for unit, role in roles.items():
            spec = pre.get(role, {})
            self.spawn(sim['units'][unit], spec.get('cwd', w.old), spec.get('argv', defaults_pre[role]),
                       env=spec.get('env'), cmdline_readable=spec.get('readable', True))

        def release():
            return (w.new, w.new_l) if sim['wd_new'] else (w.old, w.old_l)

        def checked(args, env=None, timeout=60, gate='COMMAND_EXIT_ZERO'):
            if args[0] == 'systemctl':
                action = args[1]
                if action in ('start', 'stop'):
                    sim['trace'].append(action + ':' + ','.join(roles[u] for u in args[2:]))
                    for unit in args[2:]:
                        if action == 'stop':
                            pid = sim['units'][unit]
                            if pid:
                                shutil.rmtree(self.proc / str(pid))
                            sim['units'][unit] = 0
                        else:
                            rel, ldir = release()
                            role = roles[unit]
                            spec = post.get(role, {}) if sim['wd_new'] else {}
                            default = (w.web_exec(rel) if role == 'web' else w.worker_exec(rel))
                            argv = spec.get('argv', default)
                            argv = argv(w) if callable(argv) else argv
                            pid = sim['next_pid']
                            sim['next_pid'] += 1
                            self.spawn(pid, spec.get('cwd', rel), argv, env=spec.get('env'))
                            sim['units'][unit] = pid
                    return ''
                if action == 'daemon-reload':
                    sim['trace'].append('reload')
                    sim['wd_new'] = all((systemd / (u + '.d') / ns['DROPIN_NAME']).is_file() for u in roles)
                    return ''
                self.assertEqual(action, 'show')
                unit = args[2]
                rel, ldir = release()
                if 'WorkingDirectory' in args:
                    return str(rel)
                if 'ExecStart' in args:
                    value = execstart(w.py(rel), ldir / (roles[unit] + '.py'))
                    return value if '--value' in args else 'ExecStart=' + value
                pid = sim['units'][unit]
                user = post.get(roles[unit], {}).get('user', ns['OWNER']) if sim['wd_new'] else ns['OWNER']
                return ('ActiveState=%s\nSubState=x\nMainPID=%d\nUser=%s'
                        % ('active' if pid else 'inactive', pid, user))
            if args[0] in ('pg_dump', 'pg_restore'):
                if '--version' in args:
                    return args[0] + ' (PostgreSQL) 14.19'
                sim['trace'].append(args[0])
                if args[0] == 'pg_dump':
                    ns['DUMP'].write_bytes(b'PGDMP')
                    return ''
                return '\n'.join(['toc'] * 12)
            raise AssertionError('unexpected command ' + args[0])

        def as_deploy(*args, **kw):
            self.assertNotIn('checkout', args)
            self.assertNotIn('fetch', args)
            if 'status' in args:
                return ''
            return ns['TARGET'] if str(w.new) in args else ns['BASE']

        def run(args, **kw):
            if args[0] == 'sudo':
                return types.SimpleNamespace(returncode=0,
                                             stdout=(w.new / args[-1].split(':', 1)[1]).read_bytes())
            self.assertEqual(args[0], 'psql')
            sim['trace'].append('sql')
            sim['committed'] = True
            return types.SimpleNamespace(returncode=0)

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

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner):
                return (w.new / 'static/regulatory_manual.js').read_bytes()

        ns.update(checked=checked, as_deploy=as_deploy, query=query, assert_schema=schema,
                  pending=lambda pg: None, snapshot=lambda pg: '1|2|3|4|5',
                  assert_worker_cgroup_empty=lambda: None, listening_ports=lambda pid: {5001},
                  environment_source_fingerprint=lambda planned_switch=False: ('fp', planned_switch))
        out = io.StringIO()
        code = 0
        with mock.patch.object(os, 'geteuid', return_value=0), \
                mock.patch.object(subprocess, 'run', side_effect=run), \
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
        return code, text, sim, ns

    FULL = ['stop:worker', 'stop:web', 'pg_dump', 'pg_restore', 'sql', 'reload', 'start:web,worker']

    def assert_pass(self, result):
        code, text, sim, ns = result
        self.assertEqual(code, 0, text)
        self.assertIn('PRODUCTION TECHNICAL CUTOVER PASS', text)
        self.assertEqual(sim['trace'], self.FULL)
        self.assertEqual(ns['LIVE'], self.w.new)

    def assert_hold(self, result, gate):
        code, text, sim, ns = result
        self.assertEqual(code, 1)
        self.assertIn('gate=' + gate, text)
        self.assertIn('HOLD:', text)
        self.assertNotIn('TECHNICAL CUTOVER PASS', text)
        self.assertNotIn('old services restarted', text)
        self.assertEqual(sim['trace'], self.FULL + ['stop:worker', 'stop:web'])
        self.assertEqual(sim['units'], {ns['WEB']: 0, ns['WORKER']: 0})

    def assert_stopped_before_writes(self, result, gate):
        code, text, sim, ns = result
        self.assertEqual(code, 1)
        self.assertIn('CUTOVER STOP at: read-only preflight gate=' + gate, text)
        self.assertEqual(sim['trace'], [])
        self.assertTrue(all(sim['units'].values()))
        self.assertFalse(any((self.root / 'systemd' / (u + '.d') / ns['DROPIN_NAME']).exists()
                             for u in (ns['WEB'], ns['WORKER'])))

    # -- valid production processes ------------------------------------------------------
    def test_locked_cutover_would_stop_at_preflight_on_real_forms(self):
        self.assert_stopped_before_writes(self.run_cutover(locked=True), 'PRODUCTION_LAUNCHER_CMDLINE')

    def test_real_exec_forms_before_and_after_switch_pass(self):
        self.assert_pass(self.run_cutover())

    def test_launcher_form_right_after_start_passes(self):
        self.assert_pass(self.run_cutover(post={
            'web': {'argv': lambda w: w.launcher(w.new, w.new_l, 'web')},
            'worker': {'argv': lambda w: w.launcher(w.new, w.new_l, 'worker')}}))

    def test_mixed_forms_after_start_pass(self):
        self.assert_pass(self.run_cutover(post={
            'worker': {'argv': lambda w: w.launcher(w.new, w.new_l, 'worker')}}))

    # -- invalid processes before stop: nothing written ------------------------------------
    def test_preflight_worker_running_new_release_stops_before_writes(self):
        w = self.w
        self.assert_stopped_before_writes(self.run_cutover(pre={'worker': {
            'argv': [w.py(w.old), w.new / 'scripts/import_worker.py']}}), 'PRODUCTION_LAUNCHER_CMDLINE')

    def test_preflight_worker_cmdline_unreadable_stops(self):
        self.assert_stopped_before_writes(self.run_cutover(pre={'worker': {'readable': False}}),
                                          'WORKER_CMDLINE_READ')

    def test_preflight_worker_cwd_other_release_stops(self):
        self.assert_stopped_before_writes(self.run_cutover(pre={'worker': {'cwd': self.w.new}}),
                                          'WORKER_CWD_MATCH')

    def test_preflight_exec_without_inherited_dsn_stops(self):
        # Launcher that exec'd with a filtered environment: DSN check must fail closed.
        self.assert_stopped_before_writes(self.run_cutover(pre={'worker': {'env': self.environ(False)}}),
                                          'LIVE_PROCESS_CONNECTION_PRESENT')

    # -- invalid processes after COMMIT + start: HOLD -------------------------------------
    def test_after_start_worker_runs_old_release_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {
            'argv': lambda w: [w.py(w.new), w.old / 'scripts/import_worker.py']}}), 'PRODUCTION_LAUNCHER_CMDLINE')

    def test_after_start_worker_other_python_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {
            'argv': lambda w: [w.py312, w.new / 'scripts/import_worker.py']}}), 'PRODUCTION_LAUNCHER_CMDLINE')

    def test_after_start_web_unit_running_worker_form_holds(self):
        self.assert_hold(self.run_cutover(post={'web': {
            'argv': lambda w: w.worker_exec(w.new)}}), 'PRODUCTION_LAUNCHER_CMDLINE')

    def test_after_start_systemd_pre_exec_window_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {'argv': ['(python)']}}),
                         'PRODUCTION_LAUNCHER_CMDLINE')

    def test_after_start_worker_cwd_old_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {'cwd': self.w.old}}), 'WORKER_CWD_MATCH')

    def test_after_start_wrong_user_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {'user': 'root'}}), 'WORKER_SERVICE_USER')

    def test_after_start_exec_without_dsn_holds(self):
        self.assert_hold(self.run_cutover(post={'worker': {'env': self.environ(False)}}),
                         'LIVE_PROCESS_CONNECTION_PRESENT')


if __name__ == '__main__':
    unittest.main(verbosity=2)
