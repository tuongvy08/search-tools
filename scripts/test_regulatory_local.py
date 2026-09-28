"""Run unittest only against the isolated Compose test project, without reading .env.

Usage: .venv/bin/python scripts/test_regulatory_local.py test_regulatory_manual_migration -v
The caller starts Compose with --env-file /dev/null -p search-tools-regulatory-test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'search-tools-regulatory-test'
CONTAINER = PROJECT + '-db-1'


def preflight():
    host = subprocess.check_output(['docker', 'context', 'inspect', '--format',
                                    '{{.Endpoints.docker.Host}}'], text=True).strip()
    if not host.startswith('unix://') or os.environ.get('DOCKER_HOST', host) != host:
        raise SystemExit('Refusing nonlocal Docker endpoint')
    info = json.loads(subprocess.check_output(['docker', 'inspect', '--format',
        '{"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"image":{{json .Config.Image}},"ports":{{json .NetworkSettings.Ports}}}', CONTAINER], text=True))
    assert info['project'] == PROJECT and info['service'] == 'db'
    assert info['image'] == 'postgres:16-alpine'
    assert any(p['HostPort'] == '5432' for p in info['ports']['5432/tcp'])
    subprocess.run(['docker', 'exec', CONTAINER, 'pg_isready', '-U', 'searchlocal', '-d', 'postgres'], check=True)
    print('LOCAL VERIFIED: Compose test project, db, Docker unix socket, port 5432; tests create disposable databases.', flush=True)


if __name__ == '__main__':
    preflight()  # before importing any application, test module, dotenv, or DB driver
    env = os.environ.copy()
    env.update(REGULATORY_LOCAL_TEST='1', REGULATORY_TEST_COMPOSE_PROJECT=PROJECT,
               DATABASE_URL='postgresql://searchlocal:searchlocal@127.0.0.1:5432/postgres',
               FLASK_SECRET_KEY='regulatory-local-test-only', DISABLE_IP_ALLOWLIST='1',
               GOOGLE_AUTH_ENABLED='false', ENABLE_LEGACY_PASSWORD_LOGIN='false',
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
                   str(ROOT / 'tests/no_dotenv'), str(ROOT), str(ROOT / 'tests')]))
    # Uploads are temporary too; never /var/lib or a developer's retained files.
    with tempfile.TemporaryDirectory(prefix='regulatory-tests-') as uploads:
        env['IMPORT_UPLOAD_DIR'] = uploads
        result = subprocess.run([sys.executable, '-m', 'unittest'] + sys.argv[1:], cwd=ROOT, env=env)
    sys.exit(result.returncode)
