"""Admin-only routes; long-running work lives in import_jobs, never HTTP."""
import uuid
from flask import abort, jsonify, redirect, render_template, request, session, url_for
from psycopg2.extras import RealDictCursor
from werkzeug.exceptions import RequestEntityTooLarge
import import_jobs
from import_engine import ImportProblem, limit
import session_security


def register(app, require_admin, actor):
    def guard(mutation=False):
        denied=require_admin()
        if denied is not None:
            return denied
        # Recheck role as well as the shared middleware's session liveness.
        with import_jobs.connection() as conn,conn.cursor() as cur:
            cur.execute("SELECT 1 FROM app_users WHERE id=%s AND is_admin AND account_status='ACTIVE' AND auth_version=%s", (session.get('user_id'),session.get('auth_version')))
            if not cur.fetchone():
                abort(403)
        if mutation and not session_security.verify_csrf_token(request.headers.get('X-CSRF-Token','') or request.form.get('csrf_token','')):
            abort(400,description='CSRF token không hợp lệ hoặc đã hết hạn.')

    @app.before_request
    def bound_import_request():
        if request.path.startswith('/admin/imports'):
            maximum=limit('MAX_BYTES',128*1024**2)+1024**2
            # Werkzeug 2 uses app-wide fallback; enforce before multipart parse.
            if request.content_length is None and request.method=='POST' and request.path in ('/admin/imports/upload','/admin/imports/preview'):
                abort(411)
            if request.content_length and request.content_length>maximum:
                raise RequestEntityTooLarge()

    @app.route('/admin/imports',endpoint='admin_imports')
    def index():
        denied=guard()
        if denied is not None:
            return denied
        try:
            jobs=import_jobs.list_jobs()
            error=None
        except Exception:
            jobs=[]
            error='Không tải được hàng đợi. Kiểm tra migration 024 và kết nối database.'
        return render_template('admin_import_center.html', jobs=jobs, job=None, submission_key=str(uuid.uuid4()),
                               max_mb=limit('MAX_BYTES',128*1024**2)//1024**2,error=error or request.args.get('err'))

    @app.post('/admin/imports/quick-<kind>/delete-preview')
    def quick_delete_preview(kind):
        denied=guard(True)
        if denied is not None:
            return denied
        if kind == 'rule':
            return jsonify(ok=False,message='Quy tắc đã chuyển sang module Quy tắc quản lý.'),410
        import import_quick_delete
        try:
            with import_jobs.connection() as conn,conn.cursor() as cur:
                return jsonify(import_quick_delete.preview(cur,kind,request.form))
        except ValueError as exc:
            return jsonify(ok=False,message=str(exc)),400

    @app.post('/admin/imports/upload')
    def import_upload():
        denied=guard(True)
        if denied is not None:
            return denied
        try:
            file=request.files.get('file')
            if not file:
                raise ImportProblem('Hãy chọn workbook cần nhập.')
            job_id=import_jobs.submit(file,request.form.get('mode'),actor(),session['user_id'],session['auth_version'],request.form.get('submission_key'))
        except ImportProblem as exc:
            return redirect(url_for('admin_imports',err=str(exc)))
        except Exception:
            return redirect(url_for('admin_imports',err='Không lưu được upload. Kiểm tra dung lượng, quyền thư mục và migration.'))
        return redirect(url_for('import_detail',job_id=job_id))

    @app.get('/admin/imports/jobs/<uuid:job_id>')
    def import_detail(job_id):
        denied=guard()
        if denied is not None:
            return denied
        with import_jobs.connection() as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
            job=import_jobs.fetch_job(cur,job_id)
            if not job:
                abort(404)
            cur.execute('SELECT actor,event,created_at FROM product_import_events WHERE job_id=%s ORDER BY id DESC LIMIT 20',(str(job_id),))
            events=cur.fetchall()
        return render_template('admin_import_center.html',job=job,jobs=[],events=events,error=request.args.get('err'),max_mb=0)

    @app.get('/admin/imports/jobs/<uuid:job_id>/status')
    def import_status(job_id):
        denied=guard()
        if denied is not None:
            return denied
        with import_jobs.connection() as conn,conn.cursor(cursor_factory=RealDictCursor) as cur:
            job=import_jobs.fetch_job(cur,job_id)
        if not job:
            abort(404)
        # No file paths, staged rows or workbook content in polling response.
        response=jsonify({k:job[k] for k in ('status','phase','processed_count','row_count','cancel_requested','heartbeat_at')})
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/admin/imports/jobs/<uuid:job_id>/<action>')
    def import_control(job_id,action):
        denied=guard(True)
        if denied is not None:
            return denied
        try:
            import_jobs.control(job_id,action,actor(),request.form.get('fingerprint',''),request.form.get('confirm_delete',''))
        except ImportProblem as exc:
            return redirect(url_for('import_detail',job_id=job_id,err=str(exc)))
        return redirect(url_for('import_detail',job_id=job_id))
