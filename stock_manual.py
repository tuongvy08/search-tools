"""Single-row inventory edits, using immutable snapshots and existing domain locks."""
from __future__ import annotations

import hashlib
import json
import uuid

from psycopg2.extras import Json

import admin_permissions
from brand_gateway import acquire_products_import_lock
from import_engine import ImportProblem
import stock_import_jobs as jobs
from stock import acquire_stock_lock, active_snapshot, expiry_state, format_vnd, normalized_text


FIELDS = (
    ("name", "Tên hàng"), ("code", "Code"), ("cas", "CAS"), ("brand", "Brand"),
    ("size", "Size"), ("stock_price_vnd", "Giá tồn kho (VND)"),
    ("quantity", "Số lượng tồn"), ("expiry_date", "Hạn sử dụng"), ("stock_note", "Ghi chú"),
)
KEYS = tuple(key for key, _ in FIELDS)
COLUMNS = ",".join(KEYS)
LOCK_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT_MS = 20000


def bound_request(cur):
    # HTTP must not queue behind a long background import indefinitely.
    # SET LOCAL resets on commit/rollback and does not affect the importer.
    cur.execute("SELECT set_config('lock_timeout', %s, true), set_config('statement_timeout', %s, true)",
                (str(LOCK_TIMEOUT_MS), str(STATEMENT_TIMEOUT_MS)))


class StockConflict(ImportProblem):
    pass


def wire(row):
    """Exactly the nine public fields; decimals compare and review canonically."""
    if row is None:
        return None
    result = {key: "" if row[key] is None else str(row[key]) for key in KEYS}
    if row["stock_price_vnd"] is not None:
        result["stock_price_vnd"] = format(row["stock_price_vnd"], ".2f")
    return result


def parse_fields(data):
    if any(not isinstance(data.get(key, ""), str) for key in KEYS):
        raise ImportProblem("Các trường tồn kho phải là văn bản.")
    try:
        return jobs.validate_stock_row([data.get(key, "") for key in KEYS])
    except (ValueError, TypeError) as exc:
        raise ImportProblem(str(exc)) from None


def filters(args):
    try:
        q = jobs._clean(args.get("q", ""), "Từ khóa", 500, required=False)
        brand = jobs._clean(args.get("brand", ""), "Brand lọc", 180, required=False)
        page = int(args.get("page", 1))
        size = int(args.get("page_size", 25))
        if page < 1 or size < 1:
            raise ValueError()
    except (TypeError, ValueError):
        raise ImportProblem("Phân trang không hợp lệ.") from None
    return {"q": q, "brand": brand, "page": page, "page_size": min(size, 100)}


def matches(row, selected):
    # Used only for feedback, not for authorizing or writing rows.
    query = selected["q"].casefold()
    return (not selected["brand"] or row["brand"] == selected["brand"]) and (
        not query or any(query in (row[key] or "").casefold() for key in ("name", "code", "cas")))


def state_wire(state):
    return {"snapshot_id": state["id"], "revision": state["revision"],
            "fingerprint": jobs._snapshot_state_fingerprint(state["id"], state["revision"])}


def browse(selected):
    with jobs.connection() as conn, conn.cursor() as cur:
        state = active_snapshot(cur)
        where = "snapshot_id=%s"
        params = [state["id"]]
        if selected["q"]:
            pattern = "%" + selected["q"].replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            where += " AND (name ILIKE %s ESCAPE '!' OR code ILIKE %s ESCAPE '!' OR cas ILIKE %s ESCAPE '!')"
            params.extend([pattern] * 3)
        if selected["brand"]:
            where += " AND brand=%s"
            params.append(selected["brand"])
        cur.execute("SELECT count(*) FROM stock_items WHERE " + where, params)
        total = cur.fetchone()[0]
        pages = max(1, (total + selected["page_size"] - 1) // selected["page_size"])
        page = min(selected["page"], pages)
        cur.execute("SELECT id," + COLUMNS + " FROM stock_items WHERE " + where +
                    " ORDER BY brand_norm,code_norm,size_norm,expiry_date NULLS LAST,id LIMIT %s OFFSET %s",
                    params + [selected["page_size"], (page - 1) * selected["page_size"]])
        rows = []
        for record in cur.fetchall():
            row = dict(zip(KEYS, record[1:]))
            item = wire(row)
            item.update(id=record[0], price_label=format_vnd(row["stock_price_vnd"]),
                        quantity_label=f"{row['quantity']:,}".replace(",", "."),
                        expiry_label=row["expiry_date"].strftime("%d/%m/%Y") if row["expiry_date"] else "—")
            item["expiry_state"], item["expiry_warning"] = expiry_state(row["expiry_date"])
            rows.append(item)
        cur.execute("SELECT DISTINCT brand FROM stock_items WHERE snapshot_id=%s ORDER BY brand", (state["id"],))
        return {"rows": rows, "total": total, "page": page, "pages": pages,
                "brands": [r[0] for r in cur.fetchall()], **state_wire(state)}


def form_context(item_id=None):
    with jobs.connection() as conn, conn.cursor() as cur:
        state = active_snapshot(cur)
        row = read_item(cur, state, item_id) if item_id is not None else None
        cur.execute("SELECT name FROM brand_master WHERE is_active ORDER BY name")
        return {"values": wire(row) or {key: "" for key in KEYS},
                "item_id": item_id, "brands": [r[0] for r in cur.fetchall()],
                "request_id": str(uuid.uuid4()), **state_wire(state)}


def read_item(cur, state, item_id):
    if not isinstance(item_id, int) or isinstance(item_id, bool) or not 0 < item_id <= 9223372036854775807:
        raise StockConflict("Dòng tồn không còn hợp lệ. Hãy mở lại danh sách.")
    cur.execute("SELECT " + COLUMNS + " FROM stock_items WHERE id=%s AND snapshot_id=%s", (item_id, state["id"]))
    row = cur.fetchone()
    if row is None:
        raise StockConflict("Dòng tồn không thuộc snapshot hiện hành. Hãy tìm và mở lại dòng.")
    return dict(zip(KEYS, row))


def check_state(state, expected):
    if state_wire(state) != {key: expected.get(key) for key in ("snapshot_id", "revision", "fingerprint")}:
        raise StockConflict("Tồn kho đã thay đổi. Hãy tải lại danh sách và kiểm tra lại trước khi lưu.")


def plan(cur, state, item_id, data):
    before = read_item(cur, state, item_id) if item_id is not None else None
    row = parse_fields(data)
    prepared, new_brands = jobs._prepare_rows(cur, [row], register=False)
    row = prepared[0]
    cur.execute("""SELECT id FROM stock_items WHERE snapshot_id=%s
        AND brand_norm=%s AND code_norm=%s AND size_norm=%s
        AND expiry_date IS NOT DISTINCT FROM %s::date AND (%s IS NULL OR id<>%s) LIMIT 1""",
        (state["id"], normalized_text(row["brand"]), normalized_text(row["code"]),
         normalized_text(row["size"]), row["expiry_date"], item_id, item_id))
    if cur.fetchone():
        raise ImportProblem("Trùng Brand + Code + Size + Hạn sử dụng; không tự cộng số lượng.")
    return {"before": wire(before), "after": wire(row), "new_brands": new_brands,
            "changed": before is None or any(before[k] != row[k] for k in KEYS)}, row


def review(data, item_id, expected, request_id, actor_id, auth_version):
    try:
        request_id = str(uuid.UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError):
        raise ImportProblem("Request ID không hợp lệ. Hãy mở lại form.") from None
    with jobs.connection() as conn, conn, conn.cursor() as cur:
        # Read-only preview; no domain locks or brand registration. Immutable
        # snapshot selected once makes before/after coherent; save revalidates.
        bound_request(cur)
        admin_permissions.require_actor(cur, actor_id, auth_version, "stock")
        state = active_snapshot(cur)
        check_state(state, expected)
        preview, _ = plan(cur, state, item_id, data)
        return {**preview, **state_wire(state), "item_id": item_id,
                "action": "add" if item_id is None else "edit", "request_id": request_id,
                "actor_id": actor_id, "auth_version": auth_version}


def _clone(cur, source_id, target_id, exclude_id):
    cur.execute("""INSERT INTO stock_items
        (snapshot_id,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,stock_note,
         brand_norm,code_norm,size_norm,cas_norm)
        SELECT %s,name,code,cas,brand,size,stock_price_vnd,quantity,expiry_date,stock_note,
               brand_norm,code_norm,size_norm,cas_norm FROM stock_items
        WHERE snapshot_id=%s AND (%s IS NULL OR id<>%s)""", (target_id, source_id, exclude_id, exclude_id))


def save(reviewed, actor, actor_id, auth_version):
    if reviewed.get("actor_id") != actor_id or reviewed.get("auth_version") != auth_version:
        raise admin_permissions.PermissionDenied()
    payload_hash = hashlib.sha256(json.dumps(reviewed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with jobs.connection() as conn, conn, conn.cursor() as cur:
        # Same ordering as stock import; never hold actor while waiting for a
        # domain lock. Grant/account writers do not acquire these domain locks.
        bound_request(cur)
        acquire_products_import_lock(cur)
        acquire_stock_lock(cur)
        admin_permissions.require_actor(cur, actor_id, auth_version, "stock")
        cur.execute("""SELECT payload_sha256,result_snapshot_id,result_item_id,result_revision,changed
                       FROM stock_manual_requests WHERE actor_user_id=%s AND request_id=%s""",
                    (actor_id, reviewed["request_id"]))
        receipt = cur.fetchone()
        if receipt:
            if receipt[0] != payload_hash:
                raise StockConflict("Request ID đã dùng cho nội dung khác. Hãy mở lại form.")
            return {"snapshot_id": str(receipt[1]), "item_id": receipt[2], "revision": receipt[3],
                    "changed": receipt[4], "replayed": True, "after": reviewed["after"]}
        state = active_snapshot(cur, for_update=True)
        check_state(state, reviewed)
        item_id = reviewed["item_id"]
        if reviewed["action"] != ("add" if item_id is None else "edit"):
            raise StockConflict("Loại thao tác không hợp lệ.")
        actual, row = plan(cur, state, item_id, reviewed["after"])
        if any(actual[key] != reviewed[key] for key in ("before", "after", "new_brands", "changed")):
            raise StockConflict("Danh mục hoặc dữ liệu đã đổi từ lúc xem thay đổi. Hãy kiểm tra lại.")
        target_id, result_item, revision = state["id"], item_id, state["revision"]
        if actual["changed"]:
            registered, created = jobs._prepare_rows(cur, [row], register=True)
            if wire(registered[0]) != actual["after"] or created != actual["new_brands"]:
                raise StockConflict("Brand đã thay đổi. Hãy kiểm tra lại.")
            target_id = str(uuid.uuid4())
            # Header and clone remain invisible until the final commit. Digest
            # and count below are computed from the actual copied snapshot.
            cur.execute("""INSERT INTO stock_snapshots(id,source_kind,replaced_snapshot_id,actor,row_count,content_sha256)
                           VALUES (%s,'MANUAL',%s,%s,0,'')""", (target_id, state["id"], actor))
            _clone(cur, state["id"], target_id, item_id)
            cur.execute("INSERT INTO stock_items(snapshot_id," + COLUMNS + ",brand_norm,code_norm,size_norm,cas_norm) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (target_id, *(row[key] for key in KEYS), normalized_text(row["brand"]),
                         normalized_text(row["code"]), normalized_text(row["size"]), normalized_text(row["cas"]) or None))
            result_item = cur.fetchone()[0]
            # Sorting payload strings is required by the existing import hash
            # contract. SQL does the copy; this O(n) digest never goes to UI.
            with conn.cursor(name="stock_manual_digest") as stream:
                stream.itersize = 1000
                stream.execute("SELECT " + COLUMNS + " FROM stock_items WHERE snapshot_id=%s", (target_id,))
                digest = jobs.content_digest(dict(zip(KEYS, record)) for record in stream)
            cur.execute("""UPDATE stock_snapshots SET content_sha256=%s,
                row_count=(SELECT count(*) FROM stock_items WHERE snapshot_id=%s) WHERE id=%s""",
                (digest, target_id, target_id))
            cur.execute("""UPDATE stock_state SET active_snapshot_id=%s,revision=revision+1,updated_at=now()
                           WHERE singleton=TRUE RETURNING revision""", (target_id,))
            revision = cur.fetchone()[0]
            cur.execute("""INSERT INTO stock_snapshot_events(snapshot_id,actor,event,detail) VALUES (%s,%s,%s,%s)""",
                        (target_id, actor, "manual_added" if item_id is None else "manual_edited", Json({
                            "before": actual["before"], "after": actual["after"], "actor_user_id": actor_id,
                            "auth_version": auth_version, "source_snapshot_id": state["id"],
                            "target_snapshot_id": target_id, "source_item_id": item_id,
                            "target_item_id": result_item, "request_id": reviewed["request_id"],
                            "new_brands": actual["new_brands"],
                        })))
        cur.execute("""INSERT INTO stock_manual_requests
            (actor_user_id,request_id,actor_auth_version,payload_sha256,source_snapshot_id,
             result_snapshot_id,result_item_id,result_revision,changed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (actor_id, reviewed["request_id"], auth_version, payload_hash, state["id"],
             target_id, result_item, revision, actual["changed"]))
        return {"snapshot_id": target_id, "item_id": result_item, "revision": revision,
                "changed": actual["changed"], "replayed": False, "after": actual["after"]}
