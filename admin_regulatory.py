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
    REGULATORY_COLORS,
    acquire_regulatory_lock,
    clean_text,
    color_css,
    effective_color_key,
    normalize_color_key,
    stable_key_for_label,
)
import regulatory_import_jobs as jobs
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
    there is no reverse edge. ``FOR UPDATE`` prevents a valid actor from
    being demoted, suspended or version-bumped between this check and commit.
    """
    cur.execute(
        """SELECT account_status,is_admin,auth_version
           FROM app_users WHERE id=%s FOR UPDATE""",
        (actor_user_id,),
    )
    row = cur.fetchone()
    if (not row or row["account_status"] != "ACTIVE" or not row["is_admin"]
            or row["auth_version"] != expected_auth_version):
        raise _MutationAuthorizationError()


def _presentation_labels():
    return {
        "status_labels": STATUS_LABELS, "phase_labels": PHASE_LABELS,
        "mode_labels": MODE_LABELS, "event_labels": EVENT_LABELS,
        "field_labels": FIELD_LABELS,
    }


def _status_snapshot(row):
    return {
        "id": row["id"], "stable_key": row["stable_key"], "label": row["label"],
        "priority": row["priority"], "export_policy": row["export_policy"],
        "color_key": effective_color_key(row.get("color_key"), row.get("stable_key")),
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def register(app, require_admin, actor):
    def guard(mutation=False):
        denied = require_admin()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor() as cur:
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
                statuses = [dict(row) for row in cur.fetchall()]
                for status in statuses:
                    status["color_key"] = effective_color_key(
                        status.get("color_key"), status.get("stable_key")
                    )
                    status["color_css"] = color_css(status["color_key"])
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
            regulatory_colors=REGULATORY_COLORS,
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
                    color_key = normalize_color_key(request.form.get("color_key"))
                    revision = request.form.get("revision", "")
                    cur.execute(
                        """SELECT EXISTS (
                               SELECT 1 FROM information_schema.columns
                               WHERE table_schema=current_schema()
                                 AND table_name='regulatory_statuses' AND column_name='color_key'
                           )"""
                    )
                    if not cur.fetchone()["exists"]:
                        raise ImportProblem("Chưa thể đổi màu: cần chạy migration 027 rồi tải lại trang.")
                    cur.execute("SELECT * FROM regulatory_statuses WHERE id=%s FOR UPDATE", (status_id,))
                    before = cur.fetchone()
                    if not before or str(before["updated_at"]) != revision:
                        raise ImportProblem("Tình trạng đã thay đổi; hãy tải lại trang.")
                    cur.execute(
                        """UPDATE regulatory_statuses
                           SET color_key=%s,updated_at=now() WHERE id=%s RETURNING *""",
                        (color_key, status_id),
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
            job = jobs.fetch_job(cur, job_id)
            if not job:
                abort(404)
            cur.execute(
                "SELECT actor,event,detail,created_at FROM regulatory_import_events WHERE job_id=%s ORDER BY id DESC LIMIT 30",
                (str(job_id),),
            )
            events = cur.fetchall()
        return render_template("admin_regulatory.html", statuses=[], jobs=[], job=job, events=events,
                               error=request.args.get("err"), message=None, max_mb=0,
                               submission_key=str(uuid.uuid4()), regulatory_colors=REGULATORY_COLORS,
                               **_presentation_labels())

    @app.get("/admin/regulatory/jobs/<uuid:job_id>/status")
    def regulatory_job_status(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = jobs.fetch_job(cur, job_id)
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
        try:
            jobs.control(job_id, action, actor(), request.form.get("fingerprint", ""),
                         request.form.get("confirm_delete", ""))
        except ImportProblem as exc:
            return redirect(url_for("regulatory_job_detail", job_id=job_id, err=str(exc)))
        return redirect(url_for("regulatory_job_detail", job_id=job_id))
