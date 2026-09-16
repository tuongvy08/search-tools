"""Phase 6D4: explicit endpoint registry and authoritative admin authorization.

Menu grants use stable keys. New administrative routes fail closed until listed.
Writers lock the actor after domain locks and hold it through commit; grants and
user administration serialize on the existing account-management advisory lock.
"""
from flask import abort, g, has_request_context, request, session, jsonify
from psycopg2.extras import RealDictCursor
from db import get_connection

MENU_LABELS = {
    'products': 'Sản phẩm', 'imports': 'Nhập dữ liệu',
    'teams': 'Team & quyền truy cập', 'users': 'Người dùng',
    'network': 'Mạng/IP', 'quote_templates': 'Mẫu báo giá',
    'exchange_rates': 'Tỷ giá', 'regulatory': 'Quy tắc quản lý',
    'stock': 'Quản lý tồn kho', 'manual_priority': 'Ưu tiên thủ công',
    'login_history': 'Lịch sử đăng nhập',
}
_ENDPOINT_GROUPS = {
    'products': '''admin_products admin_product_detail admin_product_update
        admin_product_create admin_product_delete_apply admin_product_restore
        admin_product_delete_preview admin_product_new''',
    'imports': '''admin_imports admin_imports_apply import_detail import_control
        import_status admin_imports_preview quick_delete_preview
        admin_imports_quick_product admin_imports_quick_product_delete
        admin_imports_quick_rule admin_imports_quick_rule_delete admin_import_tools
        import_upload admin_template_products admin_template_regulatory_rules''',
    'teams': '''admin_teams.index admin_lifecycle.confirm_team_archive
        admin_lifecycle.preview_team_archive admin_teams.confirm_permissions
        admin_teams.create_team admin_teams.preview_permissions admin_teams.rename_team
        admin_lifecycle.restore_team''',
    'users': '''admin_users admin_google_users.approve admin_google_users.invite
        admin_google_users.reactivate admin_google_users.revoke_sessions
        admin_google_users.suspend admin_google_users.update
        admin_lifecycle.archive_local_user admin_lifecycle.restore_local_user
        admin_rbac_update''',
    'network': 'admin_network',
    'quote_templates': '''admin_quote_templates_page admin_quote_template_assignment_update
        admin_quote_template_contexts admin_quote_templates_list admin_quote_templates_upload
        admin_quote_templates_activate admin_quote_templates_archive
        admin_quote_templates_download admin_quote_templates_inspect''',
    'exchange_rates': 'admin_exchange_rates',
    'regulatory': '''admin_regulatory regulatory_job_detail regulatory_job_control
        regulatory_job_status regulatory_status_action regulatory_template regulatory_upload''',
    'stock': '''admin_stock stock_job_detail stock_job_control stock_job_status
        stock_snapshot_restore stock_template stock_upload''',
    'manual_priority': 'admin_brand_compliance',
    'login_history': 'admin_login_history.index',
}
ENDPOINT_PERMISSIONS = {ep: key for key, endpoints in _ENDPOINT_GROUPS.items() for ep in endpoints.split()}
ACCOUNT_LOCK = 891273465


class PermissionDenied(ValueError):
    def __init__(self):
        super().__init__('Phiên quản trị hoặc quyền thao tác không còn hợp lệ.')


def is_admin_path(path):
    return path == '/admin' or path.startswith(('/admin/', '/api/admin/')) or path == '/api/admin'


def _read_actor(cur, user_id, *, lock=False, exclusive=False):
    # Works with tuple and RealDict cursors used by existing modules.
    cur.execute('SELECT id, account_status, auth_version, is_admin, is_super_admin '
                'FROM app_users WHERE id=%s' + (' FOR UPDATE' if exclusive else ' FOR SHARE' if lock else ''), (user_id,))
    row = cur.fetchone()
    if row is None:
        return None
    if not isinstance(row, dict):
        row = dict(zip(('id', 'account_status', 'auth_version', 'is_admin', 'is_super_admin'), row))
    return row


def _granted(cur, actor, key):
    if actor['is_super_admin']:
        return True
    cur.execute('SELECT 1 FROM admin_menu_grants WHERE user_id=%s AND permission_key=%s', (actor['id'], key))
    return cur.fetchone() is not None


def require_actor(cur, user_id, auth_version, key, *, super_only=False):
    """Fresh read AFTER domain locks; actor SHARE lock fences revocation.

    Grant writers must lock the target app_users row FOR UPDATE before editing
    grants. Separate grant SELECT runs after any actor-lock wait at READ COMMITTED.
    """
    actor = _read_actor(cur, user_id, lock=True, exclusive=key in ('users', 'teams'))
    if (not actor or actor['account_status'] != 'ACTIVE' or not actor['is_admin']
            or actor['auth_version'] != auth_version
            or (super_only and not actor['is_super_admin'])
            or not _granted(cur, actor, key)):
        raise PermissionDenied()
    cur.execute("SELECT set_config('app.rbac_actor', %s, true)", (str(user_id),))
    return actor


def require_request_actor(cur, key=None):
    if not has_request_context():
        return  # CLI has its own explicit operational authorization.
    key = key or ENDPOINT_PERMISSIONS.get(request.endpoint)
    if key is None:
        raise PermissionDenied()
    return require_actor(cur, session.get('user_id'), session.get('auth_version'), key)


def guard_user_mutation(cur, actor):
    """Called under ACCOUNT_LOCK by all existing account-management handlers."""
    if not has_request_context() or ENDPOINT_PERMISSIONS.get(request.endpoint) != 'users':
        return
    if request.endpoint == 'admin_rbac_update':
        return
    target_id = request.form.get('user_id')
    target = None
    if target_id:
        try:
            target = _read_actor(cur, int(target_id), lock=True)
        except (TypeError, ValueError):
            raise PermissionDenied()
    requested_admin = request.form.get('role', '').lower() in {'admin', 'super_admin', '1', 'true', 'yes', 'on'}
    if not actor['is_super_admin'] and (requested_admin or (target and target['is_admin'])):
        raise PermissionDenied()


def current_permissions():
    if hasattr(g, 'admin_rbac'):
        return g.admin_rbac
    result = {'is_super_admin': False, 'keys': frozenset()}
    if session.get('authenticated') and session.get('is_admin') and session.get('user_id'):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                actor = _read_actor(cur, session['user_id'])
                if (actor and actor['account_status'] == 'ACTIVE' and actor['is_admin']
                        and actor['auth_version'] == session.get('auth_version')):
                    cur.execute('SELECT permission_key FROM admin_menu_grants WHERE user_id=%s', (actor['id'],))
                    result = {'is_super_admin': bool(actor['is_super_admin']),
                              'keys': frozenset(MENU_LABELS if actor['is_super_admin'] else (r[0] for r in cur.fetchall()))}
        finally:
            conn.close()
    g.admin_rbac = result
    return result


def can_endpoint(endpoint):
    key = ENDPOINT_PERMISSIONS.get(endpoint)
    return key is not None and key in current_permissions()['keys']


def enforce_admin_permissions():
    endpoint = request.endpoint
    key = ENDPOINT_PERMISSIONS.get(endpoint)
    if key is None:
        if endpoint and is_admin_path(request.path):
            abort(403)
        return
    if not session.get('authenticated'):
        from flask import redirect, url_for
        if request.path.startswith('/api/'):
            return jsonify(ok=False, message='Chưa đăng nhập.', action='', label=''), 401
        if endpoint.startswith(('admin_google_users.', 'admin_lifecycle.', 'admin_teams.')):
            abort(403)
        return redirect(url_for('login'))
    if not session.get('is_admin') or not can_endpoint(endpoint):
        if request.path.startswith('/api/'):
            return jsonify(ok=False, message='Không có quyền menu quản trị này.', action='', label=''), 403
        abort(403)
    if endpoint == 'admin_rbac_update' and not current_permissions()['is_super_admin']:
        abort(403)


def enforce_admin_csrf():
    # Registered after upload-size hooks: do not parse multipart first.
    if request.endpoint not in ENDPOINT_PERMISSIONS:
        return
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        from session_security import verify_csrf_token
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        if not verify_csrf_token(token):
            abort(400)


def init_app(app):
    app.before_request(enforce_admin_permissions)
    app.register_error_handler(PermissionDenied, lambda error: (jsonify(error=str(error)), 403))
    app.context_processor(lambda: {
        'admin_can': can_endpoint,
        'admin_is_super': current_permissions()['is_super_admin'],
        'admin_menu_labels': MENU_LABELS,
        'admin_has_menus': bool(current_permissions()['keys']),
    })


def fetch_user_roles():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''SELECT a.id, a.username, a.auth_provider, a.is_admin,
                a.is_super_admin, a.account_status,
                COALESCE(array_agg(g.permission_key ORDER BY g.permission_key)
                         FILTER (WHERE g.permission_key IS NOT NULL), ARRAY[]::text[]) AS grants
                FROM app_users a LEFT JOIN admin_menu_grants g ON g.user_id=a.id
                GROUP BY a.id ORDER BY a.id''')
            return {r['id']: dict(r) for r in cur.fetchall()}
    finally:
        conn.close()


def update_admin_access(cur, actor_id, expected_version, target_id, level, grants):
    """Caller owns one transaction. Same lock order as LOCAL/GOOGLE lifecycle."""
    from psycopg2.extras import Json
    if level not in ('admin', 'super_admin') or not set(grants) <= MENU_LABELS.keys():
        raise ValueError('Vai trò hoặc quyền menu không hợp lệ.')
    cur.execute('SELECT pg_advisory_xact_lock(%s)', (ACCOUNT_LOCK,))
    require_actor(cur, actor_id, expected_version, 'users', super_only=True)
    cur.execute('''SELECT is_admin,is_super_admin,account_status FROM app_users
                   WHERE id=%s FOR UPDATE''', (target_id,))
    row = cur.fetchone()
    if not row or not row[0] or row[2] != 'ACTIVE':
        raise ValueError('Chỉ cấp quyền menu cho tài khoản quản trị đang hoạt động.')
    cur.execute('SELECT permission_key FROM admin_menu_grants WHERE user_id=%s ORDER BY permission_key', (target_id,))
    old_keys = [r[0] for r in cur.fetchall()]
    new_keys = sorted(set(grants)) if level == 'admin' else []
    before = {'is_super_admin': row[1], 'menus': old_keys}
    after = {'is_super_admin': level == 'super_admin', 'menus': new_keys}
    if before == after:
        return
    # User trigger protects the last ACTIVE super admin under ACCOUNT_LOCK.
    cur.execute('UPDATE app_users SET is_super_admin=%s, auth_version=auth_version+1 WHERE id=%s',
                (level == 'super_admin', target_id))
    cur.execute('DELETE FROM admin_menu_grants WHERE user_id=%s', (target_id,))
    for key in new_keys:
        cur.execute('INSERT INTO admin_menu_grants(user_id,permission_key,granted_by) VALUES(%s,%s,%s)',
                    (target_id, key, actor_id))
    cur.execute('''INSERT INTO admin_rbac_events(actor_user_id,target_user_id,event,before_state,after_state)
                   VALUES(%s,%s,'menu_access_changed',%s,%s)''',
                (actor_id,target_id,Json(before),Json(after)))


def register_routes(app):
    from flask import redirect, url_for

    @app.post('/admin/users/admin-access', endpoint='admin_rbac_update')
    def update():
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                update_admin_access(cur, session.get('user_id'), session.get('auth_version'),
                                    int(request.form.get('user_id', '')),
                                    request.form.get('admin_level'), request.form.getlist('admin_menus'))
        except PermissionDenied:
            abort(403)
        except (ValueError, TypeError):
            return redirect(url_for('admin_users', err='Vai trò, tài khoản hoặc quyền menu không hợp lệ.'))
        except Exception:
            return redirect(url_for('admin_users', err='Không thể đổi quyền. Phải còn ít nhất một Super Admin đang hoạt động.'))
        finally:
            conn.close()
        return redirect(url_for('admin_users', msg='Đã cập nhật quyền quản trị; mọi phiên cũ đã bị vô hiệu hoá.'))


def require_job_actor(cur, user_id, auth_version, key):
    """Preserve the import service's controlled error contract."""
    from import_engine import ImportProblem
    try:
        return require_actor(cur, user_id, auth_version, key)
    except PermissionDenied:
        raise ImportProblem('Quyền quản trị hoặc phiên đăng nhập đã thay đổi.') from None


def require_job_request_actor(cur, key):
    from import_engine import ImportProblem
    try:
        return require_request_actor(cur, key)
    except PermissionDenied:
        raise ImportProblem('Quyền quản trị hoặc phiên đăng nhập đã thay đổi.') from None
