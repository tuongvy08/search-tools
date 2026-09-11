"""Admin inventory import, snapshot audit and restore routes."""

import hashlib
import json
from io import BytesIO
import uuid

from flask import abort, jsonify, redirect, render_template, request, send_file, session, url_for
from openpyxl import Workbook
from psycopg2.extras import RealDictCursor
from werkzeug.exceptions import RequestEntityTooLarge

from import_engine import ImportProblem, limit
import session_security
import stock_import_jobs as jobs


STATUS_LABELS = {
    "queued": "Đang chờ", "running": "Đang xử lý", "completed": "Hoàn tất",
    "failed": "Thất bại", "cancelled": "Đã hủy",
}
PHASE_LABELS = {"preview": "Xem trước", "apply": "Thay toàn bộ tồn"}
EVENT_LABELS = {
    "uploaded": "Đã tiếp nhận tệp", "preview_started": "Bắt đầu xem trước",
    "preview_completed": "Xem trước hoàn tất", "apply": "Đã xác nhận thay tồn",
    "apply_started": "Bắt đầu thay tồn", "apply_completed": "Đã kích hoạt snapshot mới",
    "cancel": "Đã yêu cầu hủy", "retry": "Đã yêu cầu xem trước lại",
    "failed": "Tác vụ thất bại", "crash_recovered": "Worker gián đoạn; cần xem trước lại",
}


def register(app, require_admin, actor):
    def guard(mutation=False):
        denied = require_admin()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM app_users WHERE id=%s AND is_admin
                   AND account_status='ACTIVE' AND auth_version=%s""",
                (session.get("user_id"), session.get("auth_version")),
            )
            if not cur.fetchone():
                abort(403)
        if mutation and not session_security.verify_csrf_token(
            request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        ):
            abort(400, description="CSRF token không hợp lệ hoặc đã hết hạn.")

    def _stock_state_signature():
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT active_snapshot_id, revision FROM stock_state WHERE singleton=TRUE")
            row = cur.fetchone() or {"active_snapshot_id": None, "revision": 0}
        revision = int(row["revision"])
        payload = json.dumps(
            {"id": str(row["active_snapshot_id"]) if row["active_snapshot_id"] else None, "revision": revision},
            sort_keys=True,
        )
        return revision, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def context(**values):
        if "stock_revision" not in values or "stock_fingerprint" not in values:
            values.update(dict(zip(("stock_revision", "stock_fingerprint"), _stock_state_signature())))
        return dict(
            status_labels=STATUS_LABELS, phase_labels=PHASE_LABELS, event_labels=EVENT_LABELS,
            max_mb=limit("STOCK_MAX_BYTES", 32 * 1024**2) // 1024**2,
            submission_key=str(uuid.uuid4()), **values,
        )

    @app.before_request
    def bound_stock_request():
        if request.path == "/admin/stock/upload" and request.method == "POST":
            maximum = limit("STOCK_MAX_BYTES", 32 * 1024**2) + 1024**2
            if request.content_length is None:
                abort(411)
            if request.content_length > maximum:
                raise RequestEntityTooLarge()

    @app.get("/admin/stock", endpoint="admin_stock")
    def index():
        denied = guard()
        if denied is not None:
            return denied
        stock_revision, stock_fingerprint = 0, ""
        try:
            stock_revision, stock_fingerprint = _stock_state_signature()
            recent_jobs = jobs.list_jobs()
            snapshots = jobs.list_snapshots()
            error = request.args.get("err")
        except Exception:
            if app.testing:
                raise
            recent_jobs, snapshots = [], []
            error = "Không tải được dữ liệu tồn kho. Kiểm tra migration 029 và kết nối database."
        return render_template(
            "admin_stock.html", jobs=recent_jobs, snapshots=snapshots, job=None, events=[],
            error=error, message=request.args.get("msg"),
            **context(stock_revision=stock_revision, stock_fingerprint=stock_fingerprint),
        )

    @app.get("/admin/stock/template", endpoint="stock_template")
    def template():
        denied = guard()
        if denied is not None:
            return denied
        wb = Workbook()
        ws = wb.active
        ws.title = "ton_kho"
        ws.append(["Name", "Code", "Cas", "Brand", "Size", "Giá tồn kho", "Số lượng tồn", "Hạn sử dụng"])
        ws.append(["Ví dụ — xóa dòng này", "STOCK-001", "50-00-0", "Brand mẫu", "100 mL", 125000, 4, "31/12/2027"])
        stream = BytesIO()
        wb.save(stream); wb.close(); stream.seek(0)
        return send_file(
            stream, as_attachment=True, download_name="mau_ton_kho.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.post("/admin/stock/upload", endpoint="stock_upload")
    def upload():
        denied = guard(True)
        if denied is not None:
            return denied
        try:
            file = request.files.get("file")
            if not file:
                raise ImportProblem("Hãy chọn workbook tồn kho.")
            job_id = jobs.submit(
                file, actor(), session["user_id"], session["auth_version"],
                request.form.get("submission_key"),
            )
        except ImportProblem as exc:
            return redirect(url_for("admin_stock", err=str(exc)))
        except Exception:
            if app.testing:
                raise
            return redirect(url_for("admin_stock", err="Không lưu được upload tồn kho."))
        return redirect(url_for("stock_job_detail", job_id=job_id))

    @app.get("/admin/stock/jobs/<uuid:job_id>", endpoint="stock_job_detail")
    def job_detail(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = jobs.fetch_job(cur, job_id)
            if not job:
                abort(404)
            cur.execute(
                """SELECT actor,event,detail,created_at FROM stock_import_events
                   WHERE job_id=%s ORDER BY id DESC LIMIT 40""",
                (str(job_id),),
            )
            events = cur.fetchall()
        return render_template(
            "admin_stock.html", jobs=[], snapshots=[], job=job, events=events,
            error=request.args.get("err"), message=request.args.get("msg"), **context(),
        )

    @app.get("/admin/stock/jobs/<uuid:job_id>/status", endpoint="stock_job_status")
    def job_status(job_id):
        denied = guard()
        if denied is not None:
            return denied
        with jobs.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = jobs.fetch_job(cur, job_id)
        if not job:
            abort(404)
        response = jsonify({key: job[key] for key in (
            "status", "phase", "processed_count", "row_count", "cancel_requested", "heartbeat_at"
        )})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/stock/jobs/<uuid:job_id>/<action>", endpoint="stock_job_control")
    def job_control(job_id, action):
        denied = guard(True)
        if denied is not None:
            return denied
        try:
            jobs.control(
                job_id, action, actor(), request.form.get("fingerprint", ""),
                request.form.get("confirm_replace", ""),
            )
        except ImportProblem as exc:
            return redirect(url_for("stock_job_detail", job_id=job_id, err=str(exc)))
        return redirect(url_for("stock_job_detail", job_id=job_id))

    @app.post("/admin/stock/snapshots/<uuid:snapshot_id>/restore", endpoint="stock_snapshot_restore")
    def snapshot_restore(snapshot_id):
        denied = guard(True)
        if denied is not None:
            return denied
        try:
            jobs.restore_snapshot(
                snapshot_id, actor(), session["user_id"], session["auth_version"],
                request.form.get("stock_revision"), request.form.get("stock_fingerprint", ""),
            )
        except ImportProblem as exc:
            return redirect(url_for("admin_stock", err=str(exc)))
        return redirect(url_for("admin_stock", msg="Đã khôi phục thành snapshot mới và kích hoạt nguyên khối."))
