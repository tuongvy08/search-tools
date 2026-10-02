"""Vietnamese admin UI for Phase 6D1 status catalog and rule imports."""

from __future__ import annotations

from io import BytesIO
import uuid

from flask import abort, jsonify, redirect, render_template, request, send_file, session, url_for
from openpyxl import Workbook
from psycopg2.extras import Json, RealDictCursor

from import_engine import ImportProblem, limit
from regulatory import (
    EXPORT_ALLOW,
    REGULATORY_SWATCHES,
    acquire_regulatory_lock,
    clean_text,
    color_pair,
    color_css,
    effective_color_key,
    effective_color_token,
    normalize_color_hex,
    stable_key_for_label,
    valid_color_hex,
)
import regulatory_import_jobs as jobs
import regulatory_manual as manual
from regulatory_presentation import vietnam_time
import session_security

STATUS_LABELS = {
    "queued": "Chờ xử lý", "running": "Đang xử lý", "completed": "Hoàn tất",
    "failed": "Thất bại", "cancelled": "Đã hủy",
}
PHASE_LABELS = {"preview": "Xem trước", "apply": "Áp dụng"}
MODE_LABELS = {"upsert": "Thêm / cập nhật", "replace_scoped": "Thay danh sách theo phạm vi"}
EVENT_LABELS = {
    "uploaded": "Đã tiếp nhận tệp", "cancel": "Đã yêu cầu hủy",
    "apply": "Đã yêu cầu áp dụng", "retry": "Đã yêu cầu xem trước lại",
    "preview_started": "Bắt đầu xem trước", "apply_started": "Bắt đầu áp dụng",
    "preview_completed": "Xem trước hoàn tất", "apply_completed": "Áp dụng hoàn tất",
    "crash_recovered": "Phát hiện lần xử lý bị gián đoạn", "stopped": "Đã dừng xử lý",
}
FIELD_LABELS = {"cas": "CAS", "code": "Mã sản phẩm", "name": "Tên sản phẩm"}


class _MutationAuthorizationError(Exception):
    """Safe fail-closed signal for an actor revoked during lock wait."""


def _revalidate_mutation_actor(cur, actor_user_id, expected_auth_version):
    """Recheck and fence the actor inside the regulatory transaction.

    Lock ordering is regulatory advisory lock first, then this actor row.
    User lifecycle/role mutations may hold the shared last-admin lock before
    touching this row, but no such path waits for the regulatory lock, so
    there is no reverse edge. ``FOR SHARE`` prevents a valid actor from
    being demoted, suspended or version-bumped between this check and commit.
    """
    import admin_permissions
    try:
        admin_permissions.require_actor(cur, actor_user_id, expected_auth_version, 'regulatory')
    except admin_permissions.PermissionDenied:
        raise _MutationAuthorizationError() from None


def _presentation_labels():
    return {
        "status_labels": STATUS_LABELS, "phase_labels": PHASE_LABELS,
        "mode_labels": MODE_LABELS, "event_labels": EVENT_LABELS,
        "field_labels": FIELD_LABELS,
    }


def _color_swatches():
    return [
        {"hex": seed, "label": label, "bg": color_pair(seed)[0], "fg": color_pair(seed)[1]}
        for seed, label in REGULATORY_SWATCHES
    ]


def _decorate_status(row):
    """Presentation colours for one status row (same resolution as the overview page)."""
    status = dict(row)
    status["color_hex"] = valid_color_hex(status.get("color_hex"))
    status["color_key"] = effective_color_key(status.get("color_key"), status.get("stable_key"))
    color_value = status["color_hex"] or status["color_key"]
    status["color_token"] = effective_color_token(color_value, status.get("stable_key"))
    status["color_css"] = color_css(color_value, status.get("stable_key"))
    status["color_bg"], status["color_fg"] = color_pair(color_value, status.get("stable_key"))
    return status


def _status_snapshot(row):
    color_hex = valid_color_hex(row.get("color_hex"))
    return {
        "id": row["id"], "stable_key": row["stable_key"], "label": row["label"],
        "priority": row["priority"], "export_policy": row["export_policy"],
        "color_key": effective_color_key(row.get("color_key"), row.get("stable_key")),
        "color_hex": color_hex or None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def register(app, require_admin, actor):
    app.add_template_filter(vietnam_time, 'regulatory_vn_time')
    @app.errorhandler(manual.ManualProblem)
    def manual_error(error):
        return jsonify(error=str(error), rule_id=error.rule_id,
                       rule_url=url_for('regulatory_rule_detail', rule_id=error.rule_id) if error.rule_id else None), error.status

    def guard(mutation=False):
        denied = require_admin()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor() as cur:
            if mutation:
                manual.require_schema(cur)
            cur.execute(
                """SELECT 1 FROM app_users WHERE id=%s AND is_admin=true
                   AND account_status='ACTIVE' AND auth_version=%s""",
                (session.get("user_id"), session.get("auth_version")),
            )
            if not cur.fetchone():
                abort(403)
        if mutation and not session_security.verify_csrf_token(
            request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        ):
            abort(400, description="CSRF token không hợp lệ hoặc đã hết hạn.")

    def reject_extra(allowed):
        if set(request.form) - allowed or request.args:
            raise manual.ManualProblem('Tham số không được hỗ trợ; import không cho ghi đè mục thủ công.')

    @app.get('/admin/regulatory/rules', endpoint='regulatory_rules')
    def rules():
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = manual.list_rules(cur, request.args)
            cur.execute('SELECT * FROM regulatory_statuses ORDER BY priority,id')
            statuses = [_decorate_status(item) for item in cur.fetchall()]
        return render_template('admin_regulatory_rules.html', statuses=statuses, filters=request.args,
                               field_labels=FIELD_LABELS, **result)

    def rule_form(rule_id=None):
        denied = guard()
        if denied is not None:
            return denied
        page = manual.positive_int(request.args.get('page', '1'), 'Trang lịch sử')
        if page > 100000000:
            raise manual.ManualProblem('Trang không hợp lệ.')
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            manual.require_schema(cur)
            row = manual.get_rule(cur, rule_id) if rule_id else None
            cur.execute('SELECT * FROM regulatory_statuses ORDER BY priority,id')
            statuses = [_decorate_status(item) for item in cur.fetchall()]
            cur.execute('SELECT count(*) AS total FROM regulatory_rule_manual_events WHERE rule_id=%s', (rule_id,))
            total = cur.fetchone()['total']
            cur.execute('''SELECT * FROM regulatory_rule_manual_events WHERE rule_id=%s
                ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET %s''', (rule_id, (page-1)*50))
            events = cur.fetchall()
        return render_template('admin_regulatory_rule.html', rule=row, statuses=statuses, events=events,
                               request_id=str(uuid.uuid4()), page=page, has_next=page*50 < total,
                               field_labels=FIELD_LABELS)

    @app.get('/admin/regulatory/rules/new', endpoint='regulatory_rule_new')
    def rule_new():
        return rule_form()

    @app.get('/admin/regulatory/rules/<int:rule_id>', endpoint='regulatory_rule_detail')
    def rule_detail(rule_id):
        return rule_form(rule_id)

    @app.post('/admin/regulatory/rules/save', endpoint='regulatory_rule_save')
    def rule_save():
        denied = guard(True)
        if denied is not None:
            return denied
        payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        with jobs.connection() as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = manual.mutate(cur, payload, session.get('user_id'), session.get('auth_version'))
        return jsonify(**result, url=url_for('regulatory_rule_detail', rule_id=result['rule_id']))

    @app.post('/admin/regulatory/rules/check', endpoint='regulatory_rule_check')
    def rule_check():
        denied = guard(True)
        if denied is not None:
            return denied
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {'match_field','match_value','status_id','rule_id'}:
            raise manual.ManualProblem('Dữ liệu kiểm tra không hợp lệ.')
        from regulatory import normalize_match_value
        field = data.get('match_field')
        if field not in ('cas', 'code', 'name'):
            raise manual.ManualProblem('Loại đối chiếu không hợp lệ.')
        try:
            value = normalize_match_value(field, manual.text_input(data.get('match_value'), 500))
        except ValueError as exc:
            raise manual.ManualProblem(str(exc)) from None
        status_id = manual.positive_int(data.get('status_id'), 'Tình trạng')
        owner = manual.positive_int(data['rule_id'], 'ID') if data.get('rule_id') else None
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT 1 FROM regulatory_statuses WHERE id=%s', (status_id,))
            if not cur.fetchone():
                raise manual.ManualProblem('Tình trạng không tồn tại.')
            manual.collision(cur, status_id, field, value, owner)
            related = manual.related_rules(cur, field, value, status_id, owner)
        return jsonify(related=related)

    @app.get("/admin/regulatory", endpoint="admin_regulatory")
    def index():
        denied = guard()
        if denied is not None:
            return denied
        error = request.args.get("err")
        try:
            with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """SELECT s.*,count(r.id) FILTER (WHERE r.is_active) AS rule_count
                       FROM regulatory_statuses s LEFT JOIN regulatory_rules r ON r.status_id=s.id
                       GROUP BY s.id ORDER BY s.priority,s.id"""
                )
                statuses = [_decorate_status(item) for item in cur.fetchall()]
            recent = jobs.list_jobs()
        except Exception:
            statuses, recent = [], []
            error = "Không tải được module quy tắc. Kiểm tra migration 026 và kết nối database."
        return render_template(
            "admin_regulatory.html",
            statuses=statuses,
            jobs=recent,
            job=None,
            submission_key=str(uuid.uuid4()),
            error=error,
            message=request.args.get("msg"),
            max_mb=limit("REGULATORY_MAX_BYTES", 16 * 1024**2) // 1024**2,
            regulatory_swatches=_color_swatches(),
            **_presentation_labels(),
        )

    @app.post("/admin/regulatory/statuses", endpoint="regulatory_status_action")
    def status_action():
        denied = guard(True)
        if denied is not None:
            return denied
        action = request.form.get("action", "")
        actor_user_id = session.get("user_id")
        expected_auth_version = session.get("auth_version")
        try:
            with jobs.connection() as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                acquire_regulatory_lock(cur)
                _revalidate_mutation_actor(cur, actor_user_id, expected_auth_version)
                if action == "add":
                    label = clean_text(request.form.get("label"), max_chars=120)
                    if not label:
                        raise ImportProblem("Tên tình trạng không được để trống.")
                    cur.execute("SELECT COALESCE(max(priority),0)+10 AS next_priority FROM regulatory_statuses")
                    priority = cur.fetchone()["next_priority"]
                    cur.execute(
                        """INSERT INTO regulatory_statuses(stable_key,label,priority,export_policy)
                           VALUES (%s,%s,%s,%s) RETURNING *""",
                        (stable_key_for_label(label), label, priority, EXPORT_ALLOW),
                    )
                    created = cur.fetchone()
                    cur.execute(
                        """INSERT INTO regulatory_status_events(status_id,actor,event,after_json)
                           VALUES (%s,%s,'created',%s)""",
                        (created["id"], actor(), Json(_status_snapshot(created))),
                    )
                elif action == "rename":
                    status_id = int(request.form.get("status_id", "0"))
                    label = clean_text(request.form.get("label"), max_chars=120)
                    revision = request.form.get("revision", "")
                    if not label:
                        raise ImportProblem("Tên tình trạng không được để trống.")
                    cur.execute("SELECT * FROM regulatory_statuses WHERE id=%s FOR UPDATE", (status_id,))
                    before = cur.fetchone()
                    if not before or str(before["updated_at"]) != revision:
                        raise ImportProblem("Tình trạng đã thay đổi; hãy tải lại trang.")
                    cur.execute(
                        "UPDATE regulatory_statuses SET label=%s,updated_at=now() WHERE id=%s RETURNING *",
                        (label, status_id),
                    )
                    after = dict(cur.fetchone())
                    # These are denormalized compatibility/display fields only;
                    # stable links remain status_id based.
                    cur.execute("UPDATE regulatory_rules SET rule_label=%s,updated_at=now() WHERE status_id=%s", (label, status_id))
                    cur.execute("UPDATE products SET manual_compliance=%s WHERE manual_compliance_status_id=%s", (label, status_id))
                    cur.execute(
                        """INSERT INTO regulatory_status_events(status_id,actor,event,before_json,after_json)
                           VALUES (%s,%s,'renamed',%s,%s)""",
                        (status_id, actor(), Json(_status_snapshot(before)), Json(_status_snapshot(after))),
                    )
                elif action == "set_color":
                    status_id = int(request.form.get("status_id", "0"))
                    color_hex = normalize_color_hex(request.form.get("color_hex"))
                    revision = request.form.get("revision", "")
                    cur.execute(
                        """SELECT EXISTS (
                               SELECT 1 FROM information_schema.columns
                               WHERE table_schema=current_schema()
                                 AND table_name='regulatory_statuses' AND column_name='color_hex'
                           )"""
                    )
                    if not cur.fetchone()["exists"]:
                        raise ImportProblem("Chưa thể đổi màu: cần chạy migration 028 rồi tải lại trang.")
                    cur.execute("SELECT * FROM regulatory_statuses WHERE id=%s FOR UPDATE", (status_id,))
                    before = cur.fetchone()
                    if not before or str(before["updated_at"]) != revision:
                        raise ImportProblem("Tình trạng đã thay đổi; hãy tải lại trang.")
                    cur.execute(
                        """UPDATE regulatory_statuses
                           SET color_hex=%s,updated_at=now() WHERE id=%s RETURNING *""",
                        (color_hex, status_id),
                    )
                    after = dict(cur.fetchone())
                    cur.execute(
                        """INSERT INTO regulatory_status_events
                               (status_id,actor,event,before_json,after_json)
                           VALUES (%s,%s,'color_changed',%s,%s)""",
                        (status_id, actor(), Json(_status_snapshot(before)),
                         Json(_status_snapshot(after))),
                    )
                elif action in ("up", "down"):
                    status_id = int(request.form.get("status_id", "0"))
                    direction = "<" if action == "up" else ">"
                    ordering = "DESC" if action == "up" else "ASC"
                    cur.execute("SELECT * FROM regulatory_statuses WHERE id=%s FOR UPDATE", (status_id,))
                    current = cur.fetchone()
                    if not current:
                        raise ImportProblem("Không tìm thấy tình trạng.")
                    cur.execute(
                        f"SELECT * FROM regulatory_statuses WHERE priority {direction} %s ORDER BY priority {ordering},id {ordering} LIMIT 1 FOR UPDATE",
                        (current["priority"],),
                    )
                    neighbor = cur.fetchone()
                    if neighbor:
                        cur.execute("SELECT max(priority)+10 AS temporary_priority FROM regulatory_statuses")
                        temporary = cur.fetchone()["temporary_priority"]
                        cur.execute("UPDATE regulatory_statuses SET priority=%s WHERE id=%s", (temporary, current["id"]))
                        cur.execute("UPDATE regulatory_statuses SET priority=%s,updated_at=now() WHERE id=%s", (current["priority"], neighbor["id"]))
                        cur.execute("UPDATE regulatory_statuses SET priority=%s,updated_at=now() WHERE id=%s", (neighbor["priority"], current["id"]))
                        cur.execute(
                            """UPDATE regulatory_rules r SET priority=s.priority,updated_at=now()
                               FROM regulatory_statuses s WHERE r.status_id=s.id AND s.id=ANY(%s)""",
                            ([current["id"], neighbor["id"]],),
                        )
                        cur.execute(
                            """INSERT INTO regulatory_status_events(status_id,actor,event,before_json,after_json)
                               VALUES (%s,%s,'reordered',%s,%s)""",
                            (current["id"], actor(), Json(_status_snapshot(current)),
                             Json({"priority": neighbor["priority"]})),
                        )
                else:
                    raise ImportProblem("Thao tác tình trạng không hợp lệ.")
        except _MutationAuthorizationError:
            abort(403, description="Phiên quản trị không còn hợp lệ, vui lòng đăng nhập lại.")
        except (ValueError, ImportProblem) as exc:
            return redirect(url_for("admin_regulatory", err=str(exc)))
        except Exception:
            if app.testing:
                raise
            return redirect(url_for("admin_regulatory", err="Không cập nhật được; tên có thể đã tồn tại."))
        return redirect(url_for("admin_regulatory", msg="Đã cập nhật danh mục tình trạng."))

    @app.get("/admin/regulatory/templates/<field>", endpoint="regulatory_template")
    def template(field):
        denied = guard()
        if denied is not None:
            return denied
        labels = {"cas": "CAS", "code": "Code", "name": "Tên"}
        if field not in labels:
            abort(404)
        wb = Workbook()
        ws = wb.active
        ws.title = "quy_tac_" + field
        ws.append([labels[field], "Tình trạng quản lý", "Ghi chú quản lý"])
        samples = {"cas": "50-00-0", "code": "ABC-001", "name": "Formaldehyde"}
        ws.append([samples[field], "CẤM NHẬP", "Ví dụ — hãy xóa trước khi nhập dữ liệu thật"])
        stream = BytesIO()
        wb.save(stream)
        stream.seek(0)
        return send_file(stream, as_attachment=True,
                         download_name=f"mau_quy_tac_{field}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.post("/admin/regulatory/upload", endpoint="regulatory_upload")
    def upload():
        denied = guard(True)
        if denied is not None:
            return denied
        reject_extra({'csrf_token','mode','submission_key'})
        try:
            file = request.files.get("file")
            if not file:
                raise ImportProblem("Hãy chọn workbook quy tắc.")
            job_id = jobs.submit(file, request.form.get("mode"), actor(), session["user_id"],
                                 session["auth_version"], request.form.get("submission_key"))
        except ImportProblem as exc:
            return redirect(url_for("admin_regulatory", err=str(exc)))
        except Exception:
            return redirect(url_for("admin_regulatory", err="Không lưu được upload quy tắc."))
        return redirect(url_for("regulatory_job_detail", job_id=job_id))

    @app.get("/admin/regulatory/jobs/<uuid:job_id>")
    def regulatory_job_detail(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = jobs.fetch_job(cur, job_id, summary=True)
            if not job:
                abort(404)
            cur.execute(
                """SELECT e.id,e.actor,e.event,e.created_at,u.username AS actor_username
                   FROM regulatory_import_events e
                   LEFT JOIN app_users u ON e.actor='user:' || u.id::text
                   WHERE e.job_id=%s ORDER BY e.id DESC LIMIT 30""",
                (str(job_id),),
            )
            events = cur.fetchall()
        return render_template("admin_regulatory.html", statuses=[], jobs=[], job=job, events=events,
                               error=request.args.get("err"), message=None, max_mb=0,
                               submission_key=str(uuid.uuid4()),
                               **_presentation_labels())

    @app.get("/admin/regulatory/jobs/<uuid:job_id>/status")
    def regulatory_job_status(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = jobs.fetch_job(cur, job_id, summary=True)
        if not job:
            abort(404)
        data = {key: job[key] for key in
                ("status", "phase", "processed_count", "row_count", "cancel_requested", "heartbeat_at")}
        data["status_label"] = STATUS_LABELS.get(job["status"], "Không xác định")
        data["phase_label"] = PHASE_LABELS.get(job["phase"], "Không xác định")
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/regulatory/jobs/<uuid:job_id>/<action>")
    def regulatory_job_control(job_id, action):
        denied = guard(True)
        if denied is not None:
            return denied
        reject_extra({'csrf_token','fingerprint','confirm_delete'})
        try:
            jobs.control(job_id, action, actor(), request.form.get("fingerprint", ""),
                         request.form.get("confirm_delete", ""))
        except ImportProblem as exc:
            return redirect(url_for("regulatory_job_detail", job_id=job_id, err=str(exc)))
        return redirect(url_for("regulatory_job_detail", job_id=job_id))

    @app.get('/admin/regulatory/jobs/<uuid:job_id>/protection', endpoint='regulatory_job_protection')
    def job_protection(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = jobs.protection_page(cur, job_id, request.args.get('page', '1'), request.args.get('event_id'))
        return render_template('admin_regulatory_protection.html', job_id=job_id, field_labels=FIELD_LABELS, **result)
