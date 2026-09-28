"""Transactional rule editing. Caller owns transaction; no UI preview is trusted."""
from __future__ import annotations

import hashlib
import json
import re
import uuid

import admin_permissions
from regulatory import acquire_regulatory_lock, clean_text, normalize_match_value

PAGE_SIZE = 50


class ManualProblem(ValueError):
    def __init__(self, message, status=400, rule_id=None):
        super().__init__(message)
        self.status = status
        self.rule_id = rule_id


def require_schema(cur):
    cur.execute("""SELECT to_regclass('regulatory_rule_manual_keys') IS NOT NULL
        AND to_regclass('regulatory_rule_manual_events') IS NOT NULL
        AND (SELECT count(*)=2 FROM information_schema.columns
             WHERE table_schema=current_schema() AND table_name='regulatory_rules'
             AND column_name IN ('revision','manual_protected'))
        AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('regulatory_rules')
                    AND tgname='zz_regulatory_rule_revision' AND tgenabled<>'D') AS ready""")
    row = cur.fetchone()
    if not (row['ready'] if isinstance(row, dict) else row[0]):
        raise ManualProblem('Chưa có migration 033; không thể ghi quy tắc. Liên hệ quản trị hệ thống.', 503)


def positive_int(value, label='Giá trị'):
    raw = str(value)
    if not re.fullmatch(r'[0-9]{1,18}', raw) or int(raw) < 1:
        raise ManualProblem(label + ' không hợp lệ.')
    return int(raw)


def text_input(value, max_chars):
    if isinstance(value, (dict, list, tuple)):
        raise ManualProblem('Giá trị phải là văn bản.')
    try:
        text = clean_text(value, max_chars=max_chars)
    except ValueError as exc:
        raise ManualProblem(str(exc)) from None
    if '\x00' in text:
        raise ManualProblem('Văn bản chứa ký tự không hợp lệ.')
    return text


def snapshot(row):
    if row is None:
        return {}
    keys = ('id', 'status_id', 'rule_label', 'rule_type', 'match_field', 'match_value',
            'note', 'is_active', 'manual_protected', 'revision', 'priority', 'created_at', 'updated_at')
    return {key: (row[key].isoformat() if hasattr(row[key], 'isoformat') else row[key]) for key in keys}


def get_rule(cur, rule_id, lock=False):
    cur.execute('SELECT * FROM regulatory_rules WHERE id=%s' + (' FOR UPDATE' if lock else ''), (rule_id,))
    row = cur.fetchone()
    if not row:
        raise ManualProblem('Không tìm thấy quy tắc.', 404)
    return row


def collision(cur, status_id, field, value, owner=None):
    cur.execute("""SELECT id FROM regulatory_rules
        WHERE status_id=%s AND match_field=%s AND upper(btrim(match_value))=upper(btrim(%s))
        AND (%s::bigint IS NULL OR id<>%s)
        UNION SELECT rule_id FROM regulatory_rule_manual_keys
        WHERE status_id=%s AND match_field=%s AND upper(btrim(match_value))=upper(btrim(%s))
        AND (%s::bigint IS NULL OR rule_id<>%s) LIMIT 1""",
        (status_id, field, value, owner, owner, status_id, field, value, owner, owner))
    row = cur.fetchone()
    if row:
        raise ManualProblem('Khóa này đã có hoặc được giữ từ lần sửa trước. Mở quy tắc có sẵn để sửa/khôi phục.',
                            409, row['id'])


def preserve_key(cur, row):
    cur.execute("""INSERT INTO regulatory_rule_manual_keys(rule_id,status_id,match_field,match_value)
        VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (row['id'], row['status_id'], row['match_field'], row['match_value']))


def mutate(cur, payload, actor_id, auth_version):
    acquire_regulatory_lock(cur)
    require_schema(cur)
    admin_permissions.require_actor(cur, actor_id, auth_version, 'regulatory')
    if not isinstance(payload, dict):
        raise ManualProblem('Dữ liệu không hợp lệ.')
    action = payload.get('action')
    if action not in ('create', 'edit', 'deactivate', 'restore'):
        raise ManualProblem('Thao tác không hợp lệ.')
    allowed = {'action', 'request_id', 'reason', 'csrf_token'}
    if action != 'create':
        allowed |= {'rule_id', 'expected_revision'}
    if action in ('create', 'edit'):
        allowed |= {'match_field', 'match_value', 'status_id', 'note'}
    if set(payload) - allowed:
        raise ManualProblem('Có trường không được phép sửa.')
    try:
        request_id = str(uuid.UUID(str(payload.get('request_id'))))
        reason = text_input(payload.get('reason'), 1000)
        normalized = {'action': action, 'reason': reason}
        if action != 'create':
            normalized.update(rule_id=positive_int(payload.get('rule_id'), 'ID'),
                              expected_revision=positive_int(payload.get('expected_revision'), 'Phiên bản'))
        if action in ('create', 'edit'):
            field = payload.get('match_field')
            if field not in ('cas', 'code', 'name'):
                raise ManualProblem('Loại đối chiếu không hợp lệ.')
            normalized.update(match_field=field,
                              match_value=normalize_match_value(field, text_input(payload.get('match_value'), 500)),
                              status_id=positive_int(payload.get('status_id'), 'Tình trạng'),
                              note=text_input(payload.get('note'), 4000))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ManualProblem(str(exc) if isinstance(exc, ValueError) and not isinstance(exc, (TypeError,)) else
                            'Dữ liệu hoặc mã yêu cầu không hợp lệ.') from None
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cur.execute('SELECT * FROM regulatory_rule_manual_events WHERE request_id=%s', (request_id,))
    old_event = cur.fetchone()
    if old_event:
        if old_event['actor_id_snapshot'] != actor_id or old_event['request_digest'] != digest:
            raise ManualProblem('Mã yêu cầu đã được dùng cho thao tác khác.', 409)
        return {'rule_id': old_event['rule_id'], 'replayed': True, 'changed': True}
    before = None if action == 'create' else get_rule(cur, normalized['rule_id'], lock=True)
    if before and before['revision'] != normalized['expected_revision']:
        raise ManualProblem('Quy tắc đã thay đổi. Hãy tải lại trang trước khi xác nhận.', 409)
    if action in ('create', 'edit'):
        field, value, status_id = (normalized[k] for k in ('match_field', 'match_value', 'status_id'))
        if before and field != before['match_field']:
            raise ManualProblem('Không được sửa loại đối chiếu.')
        cur.execute('SELECT id FROM regulatory_statuses WHERE id=%s', (status_id,))
        if not cur.fetchone():
            raise ManualProblem('Tình trạng không tồn tại.')
        if before and before['status_id'] != status_id and not reason:
            raise ManualProblem('Đổi tình trạng phải nhập lý do.')
        collision(cur, status_id, field, value, before['id'] if before else None)
        if before and (before['match_value'], before['status_id'], before['note'] or '') == (value, status_id, normalized['note']):
            return {'rule_id': before['id'], 'changed': False, 'replayed': False}
    else:
        if action == 'deactivate' and not reason:
            raise ManualProblem('Ngừng áp dụng phải nhập lý do.')
        if before['is_active'] == (action == 'restore'):
            return {'rule_id': before['id'], 'changed': False, 'replayed': False}
    if before:
        preserve_key(cur, before)
    if action == 'create':
        cur.execute("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value,
            note,is_active,manual_protected) VALUES (%s,'','',%s,%s,%s,true,true) RETURNING *""",
            (status_id, field, value, normalized['note'] or None))
    elif action == 'edit':
        cur.execute("""UPDATE regulatory_rules SET status_id=%s,match_value=%s,note=%s,
            manual_protected=true WHERE id=%s RETURNING *""",
            (status_id, value, normalized['note'] or None, before['id']))
    else:
        cur.execute('UPDATE regulatory_rules SET is_active=%s,manual_protected=true WHERE id=%s RETURNING *',
                    (action == 'restore', before['id']))
    after = cur.fetchone()
    preserve_key(cur, after)
    cur.execute('SELECT username FROM app_users WHERE id=%s', (actor_id,))
    actor_label = cur.fetchone()['username']
    from psycopg2.extras import Json
    cur.execute("""INSERT INTO regulatory_rule_manual_events(rule_id,actor_user_id,actor_id_snapshot,
        actor_label,event,reason,before_json,after_json,request_id,request_digest)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (after['id'], actor_id, actor_id, actor_label,
         {'create': 'created', 'edit': 'edited', 'deactivate': 'deactivated', 'restore': 'restored'}[action],
         reason, Json(snapshot(before)), Json(snapshot(after)), request_id, digest))
    return {'rule_id': after['id'], 'changed': True, 'replayed': False}


def list_rules(cur, args):
    require_schema(cur)
    page = positive_int(args.get('page', '1'), 'Trang')
    if page > 100000000:
        raise ManualProblem('Trang không hợp lệ.')
    state, field = args.get('state', 'active'), args.get('field', '')
    if state not in ('active', 'inactive', 'all') or field not in ('', 'cas', 'code', 'name'):
        raise ManualProblem('Bộ lọc không hợp lệ.')
    q = text_input(args.get('q'), 500)
    where, params = ['true'], []
    if state != 'all':
        where.append('r.is_active=%s'); params.append(state == 'active')
    if field:
        where.append('r.match_field=%s'); params.append(field)
    if args.get('status_id'):
        status_id = positive_int(args['status_id'], 'Tình trạng')
        cur.execute('SELECT 1 FROM regulatory_statuses WHERE id=%s', (status_id,))
        if not cur.fetchone():
            raise ManualProblem('Bộ lọc tình trạng không tồn tại.')
        where.append('r.status_id=%s'); params.append(status_id)
    if q:
        where.append("r.match_value ILIKE %s ESCAPE '\\'")
        params.append('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    sql_where = ' AND '.join(where)
    cur.execute('SELECT count(*) AS total FROM regulatory_rules r WHERE ' + sql_where, params)
    total = cur.fetchone()['total']
    cur.execute('SELECT r.* FROM regulatory_rules r WHERE ' + sql_where + ' ORDER BY r.id LIMIT %s OFFSET %s',
                params + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
    return {'rules': cur.fetchall(), 'page': page, 'total': total, 'has_next': page * PAGE_SIZE < total}


def related_rules(cur, field, value, status_id, owner=None):
    if field not in ('cas', 'code'):
        return []
    cur.execute("""SELECT id,rule_label,is_active FROM regulatory_rules
        WHERE match_field=%s AND upper(btrim(match_value))=upper(btrim(%s))
        AND status_id<>%s AND (%s::bigint IS NULL OR id<>%s) ORDER BY id""",
        (field, value, status_id, owner, owner))
    return cur.fetchall()
