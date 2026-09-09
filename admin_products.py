"""Phase 6C3 admin product catalogue and recoverable destructive actions.

All writes share Brand Gateway's product advisory lock. Delete previews live in
PostgreSQL (not a web-worker process), expire quickly, are single-use, and are
revalidated against a streaming database fingerprint immediately before rows
are copied to the recovery table and deleted.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from flask import abort, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from psycopg2.extras import Json, RealDictCursor

from brand_gateway import acquire_products_import_lock, load_brand_gateway
from db import get_connection
from product_import_manual import (
    normalize_manual_compliance_note,
    normalize_manual_compliance_value,
    normalize_preparation_type_value,
)
import session_security


PAGE_SIZES = (25, 50, 100)
MAX_QUERY_CHARS = 200
MAX_SHORT_FIELD_CHARS = 500
MAX_LONG_FIELD_CHARS = 4000
CURSOR_MAX_AGE_SECONDS = 24 * 60 * 60
DELETE_PREVIEW_SECONDS = 5 * 60

PRODUCT_COLUMNS = (
    "name", "code", "cas", "brand", "size", "ship", "price", "note",
    "manual_compliance", "manual_compliance_note", "preparation_type", "source_brand",
)


@dataclass(frozen=True)
class Scope:
    scope_type: str
    product_id: int | None
    canonical_brand: str


class AdminAuthorizationError(Exception):
    """The actor no longer has the exact admin session used for this mutation."""


@contextmanager
def _connection():
    """Close explicitly: psycopg2's connection context does not close."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _clean(raw: Any, label: str, maximum: int = MAX_SHORT_FIELD_CHARS) -> str:
    value = "" if raw is None else str(raw).strip()
    if "\x00" in value:
        raise ValueError(f"{label} chứa ký tự không hợp lệ.")
    if len(value) > maximum:
        raise ValueError(f"{label} tối đa {maximum:,} ký tự.")
    return value


def _cursor_serializer(app):
    return URLSafeTimedSerializer(app.secret_key, salt="admin-products-keyset-v1")


def _decode_cursor(app, token: str, *, query: str, brand: str, page_size: int) -> tuple[int | None, str]:
    if not token:
        return None, "next"
    try:
        claim = _cursor_serializer(app).loads(token, max_age=CURSOR_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        raise ValueError("Trang kết quả đã hết hạn. Hãy tìm lại.") from None
    if claim.get("q") != query or claim.get("brand") != brand or claim.get("size") != page_size:
        raise ValueError("Bộ lọc đã thay đổi. Hãy tìm lại từ trang đầu.")
    try:
        anchor = int(claim["anchor"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Con trỏ phân trang không hợp lệ.") from None
    direction = claim.get("direction")
    if anchor <= 0 or direction not in ("next", "prev"):
        raise ValueError("Con trỏ phân trang không hợp lệ.")
    return anchor, direction


def _encode_cursor(app, *, query: str, brand: str, page_size: int, anchor: int, direction: str) -> str:
    return _cursor_serializer(app).dumps(
        {"q": query, "brand": brand, "size": page_size, "anchor": anchor, "direction": direction}
    )


def _require_current_admin(cur) -> None:
    cur.execute(
        """
        SELECT 1 FROM app_users
        WHERE id=%s AND is_admin=true AND account_status='ACTIVE' AND auth_version=%s
        """,
        (session.get("user_id"), session.get("auth_version")),
    )
    if not cur.fetchone():
        raise AdminAuthorizationError()


def _csrf_or_400():
    token = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
    if not session_security.verify_csrf_token(token):
        abort(400, description="CSRF token không hợp lệ hoặc đã hết hạn.")


def _audit(cur, action: str, actor: str, *, product_id=None, brand=None, count=0, batch_id=None, metadata=None):
    cur.execute(
        """
        INSERT INTO product_admin_events
            (action,actor_user_id,actor,product_id,canonical_brand,row_count,batch_id,metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            action, session.get("user_id"), actor, product_id, brand, count,
            str(batch_id) if batch_id else None, Json(metadata or {}),
        ),
    )


def _canonical_brand(cur, raw_brand: Any, raw_source_brand: Any = None) -> tuple[str, str]:
    resolution = load_brand_gateway(cur).resolve(raw_brand, raw_source_brand)
    if not resolution.is_valid:
        raise ValueError(resolution.error_message or "Brand không hợp lệ.")
    if not resolution.canonical_brand:
        raise ValueError("Brand không hợp lệ.")
    return resolution.canonical_brand, resolution.source_brand or resolution.canonical_brand


def _validated_product(cur, form, *, current_source_brand: str | None = None) -> dict[str, Any]:
    code = _clean(form.get("code"), "Code")
    raw_brand = _clean(form.get("brand"), "Brand")
    if not code:
        raise ValueError("Code là bắt buộc.")
    if not raw_brand:
        raise ValueError("Brand là bắt buộc.")

    source_input = _clean(form.get("source_brand"), "Source brand")
    if not source_input and current_source_brand:
        source_input = current_source_brand
    canonical_brand, source_brand = _canonical_brand(cur, raw_brand, source_input or None)

    compliance_raw = _clean(form.get("manual_compliance"), "Tình trạng quản lý")
    compliance_note = normalize_manual_compliance_note(
        _clean(form.get("manual_compliance_note"), "Ghi chú quản lý", MAX_LONG_FIELD_CHARS)
    )
    manual_compliance = normalize_manual_compliance_value(compliance_raw)
    if compliance_note and manual_compliance is None:
        raise ValueError("Ghi chú quản lý chỉ được nhập khi có tình trạng quản lý thủ công.")

    preparation_type = normalize_preparation_type_value(
        _clean(form.get("preparation_type"), "Dạng sản phẩm")
    )
    return {
        "name": _clean(form.get("name"), "Tên sản phẩm", MAX_LONG_FIELD_CHARS),
        "code": code,
        "cas": _clean(form.get("cas"), "CAS"),
        "brand": canonical_brand,
        "size": _clean(form.get("size"), "Quy cách"),
        "ship": _clean(form.get("ship"), "Ship"),
        "price": _clean(form.get("price"), "Giá"),
        "note": _clean(form.get("note"), "Ghi chú", MAX_LONG_FIELD_CHARS),
        "manual_compliance": manual_compliance,
        "manual_compliance_note": compliance_note,
        "preparation_type": preparation_type,
        "source_brand": source_brand,
    }


def _identity_conflicts(cur, product: dict, *, exclude_id: int | None = None) -> list[int]:
    params: list[Any] = [product["code"], product["brand"]]
    exclude_sql = ""
    if exclude_id is not None:
        exclude_sql = " AND id<>%s"
        params.append(exclude_id)
    cur.execute(
        """
        SELECT id FROM products
        WHERE UPPER(TRIM(code))=UPPER(TRIM(%s))
          AND UPPER(TRIM(brand))=UPPER(TRIM(%s))
        """ + exclude_sql + " ORDER BY id LIMIT 3",
        params,
    )
    return [row[0] for row in cur.fetchall()]


def _scope_where(scope: Scope, alias: str = "p") -> tuple[str, tuple[Any, ...]]:
    if scope.scope_type == "product":
        return f"{alias}.id=%s", (scope.product_id,)
    return f"{alias}.brand=%s", (scope.canonical_brand,)


def _fingerprint_sql(table: str, where_sql: str, *, backup: bool = False) -> str:
    prefix = "original_" if backup else ""
    id_col = f"{prefix}product_id" if backup else "id"
    xmin_col = "original_xmin" if backup else "xmin::text::bigint"
    field = lambda name: f"COALESCE({name},'')"
    values = [f"{id_col}::text", f"{xmin_col}::text"] + [field(name) for name in PRODUCT_COLUMNS]
    row_text = "concat_ws(chr(31)," + ",".join(values) + ")"
    return f"""
        SELECT COUNT(*)::bigint,
               COALESCE(MIN({id_col}),0)::bigint,
               COALESCE(MAX({id_col}),0)::bigint,
               COALESCE(SUM({id_col}::numeric),0)::text,
               COALESCE(SUM(hashtextextended({row_text},624025)::numeric),0)::text
        FROM {table}
        WHERE {where_sql}
    """


def _fingerprint(cur, scope: Scope) -> tuple[int, str]:
    where, params = _scope_where(scope)
    cur.execute(_fingerprint_sql("products p", where), params)
    row = cur.fetchone()
    count = int(row[0])
    return count, ":".join(str(value) for value in row)


def _backup_fingerprint(cur, batch_id: uuid.UUID) -> tuple[int, str]:
    cur.execute(
        _fingerprint_sql("product_deleted_rows", "batch_id=%s", backup=True),
        (str(batch_id),),
    )
    row = cur.fetchone()
    return int(row[0]), ":".join(str(value) for value in row)


def _scope_from_request(cur) -> Scope:
    scope_type = _clean(request.form.get("scope_type"), "Phạm vi")
    if scope_type == "product":
        try:
            product_id = int(request.form.get("product_id", ""))
        except (TypeError, ValueError):
            raise ValueError("Mã sản phẩm không hợp lệ.") from None
        if product_id <= 0:
            raise ValueError("Mã sản phẩm không hợp lệ.")
        cur.execute("SELECT brand FROM products WHERE id=%s", (product_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Sản phẩm không còn tồn tại.")
        return Scope("product", product_id, row[0])
    if scope_type == "brand":
        canonical, _ = _canonical_brand(cur, request.form.get("brand"))
        return Scope("brand", None, canonical)
    raise ValueError("Phạm vi xóa không hợp lệ.")


def _query_products(cur, *, query: str, brand: str, page_size: int, anchor: int | None, direction: str):
    clauses = ["TRUE"]
    params: list[Any] = []
    if brand:
        clauses.append("p.brand=%s")
        params.append(brand)
    if query:
        pattern = "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        clauses.append("(p.name ILIKE %s ESCAPE '!' OR p.code ILIKE %s ESCAPE '!' OR p.cas ILIKE %s ESCAPE '!')")
        params.extend([pattern, pattern, pattern])
    order = "ASC"
    if anchor is not None:
        if direction == "prev":
            clauses.append("p.id<%s")
            order = "DESC"
        else:
            clauses.append("p.id>%s")
        params.append(anchor)
    params.append(page_size + 1)
    cur.execute(
        f"""
        SELECT p.id,p.name,p.code,p.cas,p.brand,p.size,p.ship,p.price,p.note,
               p.manual_compliance,p.manual_compliance_note,p.preparation_type,p.source_brand,
               p.xmin::text AS revision
        FROM products p
        WHERE {' AND '.join(clauses)}
        ORDER BY p.id {order}
        LIMIT %s
        """,
        params,
    )
    rows = cur.fetchall()
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    if direction == "prev":
        rows.reverse()
    return rows, has_more


def _row_dict(cur, row):
    if row is None or isinstance(row, dict):
        return row
    return {column[0]: value for column, value in zip(cur.description, row)}


def register(app, require_admin, actor):
    def guard():
        denied = require_admin()
        if denied is not None:
            return denied
        try:
            with _connection() as conn, conn.cursor() as cur:
                _require_current_admin(cur)
        except AdminAuthorizationError:
            abort(403)
        return None

    def product_row(cur, product_id: int, *, lock=False):
        suffix = " FOR UPDATE" if lock else ""
        cur.execute(
            """
            SELECT id,name,code,cas,brand,size,ship,price,note,manual_compliance,
                   manual_compliance_note,preparation_type,source_brand,xmin::text AS revision
            FROM products WHERE id=%s
            """ + suffix,
            (product_id,),
        )
        return _row_dict(cur, cur.fetchone())

    @app.get("/admin/products", endpoint="admin_products")
    def index():
        denied = guard()
        if denied is not None:
            return denied
        error = request.args.get("error")
        try:
            query = _clean(request.args.get("q"), "Từ khóa", MAX_QUERY_CHARS)
            brand = _clean(request.args.get("brand"), "Brand")
        except ValueError as exc:
            query, brand, error = "", "", str(exc)
        try:
            page_size = int(request.args.get("page_size", 50))
        except ValueError:
            page_size = 50
        if page_size not in PAGE_SIZES:
            page_size = 50
        with _connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_current_admin(cur)
            cur.execute("SELECT name FROM brand_master WHERE is_active=true ORDER BY normalized_name")
            brands = [row["name"] for row in cur.fetchall()]
            if brand and brand not in brands:
                error = "Brand lọc không còn hoạt động."
                brand = ""
            try:
                anchor, direction = _decode_cursor(
                    app, request.args.get("cursor", ""), query=query, brand=brand, page_size=page_size
                )
            except ValueError as exc:
                anchor, direction, error = None, "next", str(exc)
            rows, has_more = _query_products(
                cur, query=query, brand=brand, page_size=page_size, anchor=anchor, direction=direction
            )
            cur.execute(
                """
                SELECT b.id,b.scope_type,b.product_id,b.canonical_brand,b.row_count,b.actor,
                       b.deleted_at,b.restored_at
                FROM product_delete_batches b
                ORDER BY b.deleted_at DESC LIMIT 12
                """
            )
            batches = cur.fetchall()
        next_cursor = prev_cursor = None
        if rows:
            if direction == "prev":
                prev_cursor = _encode_cursor(app, query=query, brand=brand, page_size=page_size, anchor=rows[0]["id"], direction="prev") if has_more else None
                next_cursor = _encode_cursor(app, query=query, brand=brand, page_size=page_size, anchor=rows[-1]["id"], direction="next")
            else:
                prev_cursor = _encode_cursor(app, query=query, brand=brand, page_size=page_size, anchor=rows[0]["id"], direction="prev") if anchor is not None else None
                next_cursor = _encode_cursor(app, query=query, brand=brand, page_size=page_size, anchor=rows[-1]["id"], direction="next") if has_more else None
        return render_template(
            "admin_products.html", products=rows, brands=brands, batches=batches,
            q=query, selected_brand=brand, page_size=page_size,
            next_cursor=next_cursor, prev_cursor=prev_cursor,
            error=error, message=request.args.get("message"),
        )

    @app.get("/admin/products/new", endpoint="admin_product_new")
    def new_product():
        denied = guard()
        if denied is not None:
            return denied
        with _connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_current_admin(cur)
            cur.execute("SELECT name FROM brand_master WHERE is_active=true ORDER BY normalized_name")
            brands = [row["name"] for row in cur.fetchall()]
        return render_template("admin_product_form.html", product=None, brands=brands, error=request.args.get("error"))

    @app.get("/admin/products/<int:product_id>", endpoint="admin_product_detail")
    def detail(product_id):
        denied = guard()
        if denied is not None:
            return denied
        with _connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            _require_current_admin(cur)
            product = product_row(cur, product_id)
            if not product:
                abort(404)
            cur.execute("SELECT name FROM brand_master WHERE is_active=true ORDER BY normalized_name")
            brands = [row["name"] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT action,actor,row_count,created_at,metadata_json
                FROM product_admin_events
                WHERE product_id=%s ORDER BY created_at DESC LIMIT 12
                """,
                (product_id,),
            )
            events = cur.fetchall()
        return render_template(
            "admin_product_form.html", product=product, brands=brands, events=events,
            error=request.args.get("error"), message=request.args.get("message"),
        )

    @app.post("/admin/products/create", endpoint="admin_product_create")
    def create_product():
        denied = guard()
        if denied is not None:
            return denied
        _csrf_or_400()
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                acquire_products_import_lock(cur)  # first statement in the product-write transaction
                _require_current_admin(cur)
                product = _validated_product(cur, request.form)
                conflicts = _identity_conflicts(cur, product)
                if conflicts:
                    raise ValueError(
                        f"Code + canonical brand đã tồn tại (ID {conflicts[0]}). Mở sản phẩm đó để sửa."
                    )
                cur.execute(
                    """
                    INSERT INTO products
                        (name,code,cas,brand,size,ship,price,note,manual_compliance,
                         manual_compliance_note,preparation_type,source_brand)
                    VALUES (%(name)s,%(code)s,%(cas)s,%(brand)s,%(size)s,%(ship)s,%(price)s,
                            %(note)s,%(manual_compliance)s,%(manual_compliance_note)s,
                            %(preparation_type)s,%(source_brand)s)
                    RETURNING id
                    """,
                    product,
                )
                product_id = cur.fetchone()[0]
                _audit(
                    cur, "create", actor(), product_id=product_id, brand=product["brand"], count=1,
                    metadata={"code": product["code"], "source_brand": product["source_brand"]},
                )
            return redirect(url_for("admin_product_detail", product_id=product_id, message="Đã thêm sản phẩm."))
        except AdminAuthorizationError:
            conn.rollback()
            abort(403)
        except ValueError as exc:
            conn.rollback()
            return redirect(url_for("admin_product_new", error=str(exc)))
        finally:
            conn.close()

    @app.post("/admin/products/<int:product_id>/update", endpoint="admin_product_update")
    def update_product(product_id):
        denied = guard()
        if denied is not None:
            return denied
        _csrf_or_400()
        revision = _clean(request.form.get("revision"), "Phiên bản")
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                acquire_products_import_lock(cur)  # first statement in the product-write transaction
                _require_current_admin(cur)
                current = product_row(cur, product_id, lock=True)
                if not current:
                    abort(404)
                if revision != current["revision"]:
                    raise ValueError("Sản phẩm đã thay đổi từ lúc bạn mở form. Tải lại rồi kiểm tra trước khi lưu.")
                product = _validated_product(cur, request.form, current_source_brand=current["source_brand"])
                conflicts = _identity_conflicts(cur, product, exclude_id=product_id)
                if conflicts:
                    raise ValueError(
                        f"Code + canonical brand trùng sản phẩm khác (ID {conflicts[0]}). Không ghi đè tùy ý."
                    )
                changed = [name for name in PRODUCT_COLUMNS if current[name] != product[name]]
                cur.execute(
                    """
                    UPDATE products SET
                        name=%(name)s,code=%(code)s,cas=%(cas)s,brand=%(brand)s,size=%(size)s,
                        ship=%(ship)s,price=%(price)s,note=%(note)s,
                        manual_compliance=%(manual_compliance)s,
                        manual_compliance_note=%(manual_compliance_note)s,
                        preparation_type=%(preparation_type)s,source_brand=%(source_brand)s
                    WHERE id=%(id)s AND xmin::text=%(revision)s
                    """,
                    {**product, "id": product_id, "revision": revision},
                )
                if cur.rowcount != 1:
                    raise ValueError("Sản phẩm vừa thay đổi. Tải lại rồi thử lại.")
                _audit(
                    cur, "update", actor(), product_id=product_id, brand=product["brand"], count=1,
                    metadata={"changed_fields": changed},
                )
            return redirect(url_for("admin_product_detail", product_id=product_id, message="Đã lưu thay đổi."))
        except AdminAuthorizationError:
            conn.rollback()
            abort(403)
        except ValueError as exc:
            conn.rollback()
            return redirect(url_for("admin_product_detail", product_id=product_id, error=str(exc)))
        finally:
            conn.close()

    @app.post("/admin/products/delete-preview", endpoint="admin_product_delete_preview")
    def delete_preview():
        denied = guard()
        if denied is not None:
            return denied
        _csrf_or_400()
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                acquire_products_import_lock(cur)  # first statement; same ordering as import apply
                _require_current_admin(cur)
                scope = _scope_from_request(cur)
                count, fingerprint = _fingerprint(cur, scope)
                if count == 0:
                    raise ValueError("Phạm vi không còn sản phẩm để xóa.")
                if scope.scope_type == "product" and count != 1:
                    raise ValueError("Phạm vi sản phẩm không còn chính xác.")
                token = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO product_delete_previews
                        (token,actor_user_id,actor_auth_version,scope_type,product_id,
                         canonical_brand,expected_count,fingerprint,expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()+%s*interval '1 second')
                    """,
                    (
                        str(token), session["user_id"], session["auth_version"], scope.scope_type,
                        scope.product_id, scope.canonical_brand, count, fingerprint,
                        DELETE_PREVIEW_SECONDS,
                    ),
                )
                _audit(
                    cur, "delete_preview", actor(), product_id=scope.product_id,
                    brand=scope.canonical_brand, count=count,
                    metadata={"scope_type": scope.scope_type},
                )
            return jsonify(
                ok=True, token=str(token), count=count, scope_type=scope.scope_type,
                brand=scope.canonical_brand, expires_in=DELETE_PREVIEW_SECONDS,
                confirmation=f"XOA {count}",
            )
        except AdminAuthorizationError:
            conn.rollback()
            abort(403)
        except ValueError as exc:
            conn.rollback()
            return jsonify(ok=False, message=str(exc)), 400
        finally:
            conn.close()

    @app.post("/admin/products/delete-apply", endpoint="admin_product_delete_apply")
    def delete_apply():
        denied = guard()
        if denied is not None:
            return denied
        _csrf_or_400()
        try:
            token = uuid.UUID(request.form.get("token", ""))
        except (ValueError, TypeError, AttributeError):
            return jsonify(ok=False, message="Xác nhận xóa không hợp lệ. Hãy xem trước lại."), 400
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                acquire_products_import_lock(cur)  # first statement; fences import/update/delete
                _require_current_admin(cur)
                cur.execute("SELECT * FROM product_delete_previews WHERE token=%s FOR UPDATE", (str(token),))
                preview = _row_dict(cur, cur.fetchone())
                if not preview or preview["consumed_at"] is not None or preview["expires_at"].timestamp() <= time.time():
                    raise ValueError("Xác nhận đã dùng hoặc hết hạn. Hãy xem trước lại.")
                if preview["actor_user_id"] != session.get("user_id") or preview["actor_auth_version"] != session.get("auth_version"):
                    raise ValueError("Xác nhận không thuộc phiên quản trị hiện tại.")
                if request.form.get("confirmation", "").strip() != f"XOA {preview['expected_count']}":
                    raise ValueError(f"Nhập chính xác XOA {preview['expected_count']} để xác nhận phạm vi.")
                scope = Scope(preview["scope_type"], preview["product_id"], preview["canonical_brand"])
                count, fingerprint = _fingerprint(cur, scope)
                if count != preview["expected_count"] or fingerprint != preview["fingerprint"]:
                    raise ValueError("Dữ liệu đã thay đổi sau khi xem trước. Hãy kiểm tra và xem trước lại.")

                batch_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO product_delete_batches
                        (id,preview_token,scope_type,product_id,canonical_brand,row_count,actor_user_id,actor)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(batch_id), str(token), scope.scope_type, scope.product_id,
                        scope.canonical_brand, count, session["user_id"], actor(),
                    ),
                )
                where, params = _scope_where(scope)
                cur.execute(
                    f"""
                    WITH locked AS MATERIALIZED (
                        SELECT p.id,p.xmin::text::bigint AS source_xmin,
                               p.name,p.code,p.cas,p.brand,p.size,p.ship,p.price,p.note,
                               p.manual_compliance,p.manual_compliance_note,p.preparation_type,p.source_brand
                        FROM products p WHERE {where}
                        FOR UPDATE OF p
                    )
                    INSERT INTO product_deleted_rows
                        (batch_id,original_product_id,original_xmin,name,code,cas,brand,size,ship,
                         price,note,manual_compliance,manual_compliance_note,preparation_type,source_brand)
                    SELECT %s,id,source_xmin,name,code,cas,brand,size,ship,price,note,
                           manual_compliance,manual_compliance_note,preparation_type,source_brand
                    FROM locked
                    """,
                    (*params, str(batch_id)),
                )
                copied_count, copied_fingerprint = _backup_fingerprint(cur, batch_id)
                if copied_count != count or copied_fingerprint != fingerprint:
                    raise ValueError("Dữ liệu thay đổi trong lúc khóa phạm vi. Chưa xóa; hãy xem trước lại.")
                cur.execute(
                    """
                    DELETE FROM products p USING product_deleted_rows d
                    WHERE d.batch_id=%s AND p.id=d.original_product_id
                    """,
                    (str(batch_id),),
                )
                if cur.rowcount != count:
                    raise RuntimeError("delete_count_mismatch")
                cur.execute("UPDATE product_delete_previews SET consumed_at=now() WHERE token=%s", (str(token),))
                _audit(
                    cur, "delete", actor(), product_id=scope.product_id, brand=scope.canonical_brand,
                    count=count, batch_id=batch_id, metadata={"scope_type": scope.scope_type},
                )
            return jsonify(
                ok=True, message=f"Đã xóa {count:,} sản phẩm. Bản khôi phục: {batch_id}.",
                count=count, batch_id=str(batch_id),
            )
        except AdminAuthorizationError:
            conn.rollback()
            abort(403)
        except ValueError as exc:
            conn.rollback()
            return jsonify(ok=False, message=str(exc)), 400
        except Exception:
            conn.rollback()
            return jsonify(ok=False, message="Không thể hoàn tất xóa; giao dịch đã được hoàn tác."), 500
        finally:
            conn.close()

    @app.post("/admin/products/delete-batches/<uuid:batch_id>/restore", endpoint="admin_product_restore")
    def restore(batch_id):
        denied = guard()
        if denied is not None:
            return denied
        _csrf_or_400()
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                acquire_products_import_lock(cur)  # first statement; restore is also a product write
                _require_current_admin(cur)
                cur.execute("SELECT * FROM product_delete_batches WHERE id=%s FOR UPDATE", (str(batch_id),))
                batch = _row_dict(cur, cur.fetchone())
                if not batch:
                    raise ValueError("Không tìm thấy bản khôi phục.")
                if batch["restored_at"] is not None:
                    raise ValueError("Bản này đã được khôi phục trước đó.")
                if batch["scope_type"] == "brand":
                    cur.execute("SELECT count(*) FROM products WHERE brand=%s", (batch["canonical_brand"],))
                    if cur.fetchone()[0]:
                        raise ValueError("Brand đã có dữ liệu mới. Không tự khôi phục chồng lấp; hãy xử lý thủ công.")
                else:
                    cur.execute("SELECT 1 FROM products WHERE id=%s", (batch["product_id"],))
                    if cur.fetchone():
                        raise ValueError("ID sản phẩm đã được dùng lại. Không thể khôi phục tự động.")
                cur.execute(
                    """
                    INSERT INTO products
                        (id,name,code,cas,brand,size,ship,price,note,manual_compliance,
                         manual_compliance_note,preparation_type,source_brand)
                    SELECT original_product_id,name,code,cas,brand,size,ship,price,note,
                           manual_compliance,manual_compliance_note,preparation_type,source_brand
                    FROM product_deleted_rows WHERE batch_id=%s ORDER BY original_product_id
                    """,
                    (str(batch_id),),
                )
                if cur.rowcount != batch["row_count"]:
                    raise RuntimeError("restore_count_mismatch")
                cur.execute(
                    """
                    UPDATE product_delete_batches
                    SET restored_at=now(),restored_by_user_id=%s,restored_by=%s
                    WHERE id=%s
                    """,
                    (session["user_id"], actor(), str(batch_id)),
                )
                _audit(
                    cur, "restore", actor(), product_id=batch["product_id"],
                    brand=batch["canonical_brand"], count=batch["row_count"], batch_id=batch_id,
                    metadata={"scope_type": batch["scope_type"]},
                )
            return redirect(url_for("admin_products", message=f"Đã khôi phục {batch['row_count']:,} sản phẩm."))
        except AdminAuthorizationError:
            conn.rollback()
            abort(403)
        except ValueError as exc:
            conn.rollback()
            return redirect(url_for("admin_products", error=str(exc)))
        finally:
            conn.close()
