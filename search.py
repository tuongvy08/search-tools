import csv
import ipaddress
import json
import os
import zipfile
from io import BytesIO, StringIO
from typing import Optional
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_cors import CORS
from openpyxl import Workbook, load_workbook
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from middleware_access import register_ip_access_control

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

base_path = os.environ.get("ACCESS_CONTROL_BASE_PATH", "/home/deploy/myapps")
register_ip_access_control(app, base_path=base_path)


@app.after_request
def _static_no_cache_js_css(response):
    """Tránh trình duyệt giữ bản cũ của script.js / styles.css sau khi deploy."""
    try:
        path = request.path or ""
        if path.startswith("/static/") and path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
    except Exception:
        pass
    return response


MANAGER_PASSWORD = os.environ.get("APP_PASSWORD_MANAGER", "Truong@2004")
STAFF_PASSWORD = os.environ.get("APP_PASSWORD_STAFF", "Truong@123")

IMPORT_PREVIEWS = {}


def _default_exchange_rates_from_json() -> dict[str, float]:
    path = os.path.join(app.root_path, "static", "exchange_rates.json")
    out: dict[str, float] = {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = json.load(file)
        for k, v in (raw or {}).items():
            try:
                out[str(k).strip()] = float(v)
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return out


def _exchange_rate_map(conn) -> dict[str, float]:
    """JSON làm mặc định; dòng trong bảng exchange_rates ghi đè theo brand."""
    base = _default_exchange_rates_from_json()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT brand, rate FROM exchange_rates")
            for b, r in cur.fetchall():
                if b is None or str(b).strip() == "":
                    continue
                try:
                    base[str(b).strip()] = float(r)
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass
    return base


def _visibility_sql(alias: str):
    if session.get("is_admin"):
        return "", ()
    tid = session.get("team_id")
    if tid is None:
        return " AND FALSE", ()
    return (f" AND {alias}.brand IN (SELECT brand FROM team_brands WHERE team_id = %s)", (tid,))


def _warning_css_type(label: Optional[str]) -> Optional[str]:
    if label == "CẤM NHẬP":
        return "warning-cam-nhap"
    if label == "Phụ lục II":
        return "warning-phu-luc-ii"
    if label == "Phụ lục III":
        return "warning-phu-luc-iii"
    if label == "TỒN KHO":
        return "warning-ton-kho"
    return None


def _norm(v):
    return (v or "").strip()


def _split_multi_items(text: str, max_items: int = 2000) -> list[str]:
    """
    Tách danh sách nhiều dòng từ textarea/input.
    Cho phép xuống dòng và tách thêm bởi dấu phẩy/dấu chấm phẩy.
    Giữ thứ tự xuất hiện (không bỏ trùng), để output khớp đúng với list bạn paste.
    """
    if not text:
        return []
    out: list[str] = []
    for line in str(text).splitlines():
        line = (line or "").strip()
        if not line or line.startswith("#"):
            continue
        for part in line.replace(";", ",").split(","):
            item = (part or "").strip()
            if not item:
                continue
            out.append(item)
            if len(out) >= max_items:
                return out
    return out


def _brands_from_text(text: str) -> list[str]:
    """Tách danh sách brand từ textarea (dòng/;/,), trim, bỏ trống, không làm mất Unicode."""
    items = _split_multi_items(text, max_items=2000)
    return [x.strip() for x in items if x and x.strip()]


def _excel_cell_to_str(val) -> str:
    """Chuyển giá trị ô Excel thành chuỗi, giữ Unicode (tiếng Việt, ký tự đặc biệt)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(int(val)) if val.is_integer() else str(val)
    try:
        from openpyxl.cell.rich_text import CellRichText

        if isinstance(val, CellRichText):
            return "".join(str(t) for t in val).strip()
    except ImportError:
        pass
    return str(val).strip()


def _is_ooxml_xlsx(raw: bytes) -> bool:
    if len(raw) < 64 or raw[:2] != b"PK":
        return False
    try:
        with zipfile.ZipFile(BytesIO(raw), "r") as z:
            return "[Content_Types].xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _is_old_binary_xls(raw: bytes) -> bool:
    return len(raw) >= 8 and raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _decode_text_flexible(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1258", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _maybe_interpret_as_csv(raw: bytes, filename: str) -> Optional[str]:
    """Nếu không phải .xlsx chuẩn, thử coi là CSV (UTF-8 / Windows)."""
    if _is_ooxml_xlsx(raw) or _is_old_binary_xls(raw):
        return None
    fn = (filename or "").lower()
    if fn.endswith(".csv"):
        return _decode_text_flexible(raw)
    text = _decode_text_flexible(raw)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    head = lines[0]
    if any(d in head for d in (",", ";", "\t")):
        return text
    return None


def _read_csv_dicts(text: str) -> tuple[list[dict], set[str]]:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";\t,")
    except csv.Error:
        dialect = csv.excel
    f = StringIO(text)
    reader = csv.reader(f, dialect)
    try:
        header_row = next(reader)
    except StopIteration:
        raise ValueError("CSV rỗng.")

    headers = [str(x).strip() for x in header_row]
    keys = [h.lower() for h in headers]
    header_cols = {k for k in keys if k}
    out: list[dict] = []
    for parts in reader:
        if not parts or all(not (c or "").strip() for c in parts):
            continue
        row: dict[str, str] = {}
        empty = True
        for i, k in enumerate(keys):
            if not k:
                continue
            val = parts[i] if i < len(parts) else ""
            s = "" if val is None else str(val).strip()
            if s != "":
                empty = False
            row[k] = s
        if not empty:
            out.append(row)
    return out, header_cols


def _read_xlsx_bytes(raw: bytes) -> tuple[list[dict], set[str]]:
    bio = BytesIO(raw)
    wb = load_workbook(bio, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            headers = [_excel_cell_to_str(x) for x in next(rows)]
        except StopIteration:
            raise ValueError("File Excel rỗng (không có dòng nào).")

        keys = [h.lower() for h in headers]
        header_cols = {k for k in keys if k}
        out: list[dict] = []
        for r in rows:
            if r is None:
                continue
            row: dict[str, str] = {}
            empty = True
            for i, k in enumerate(keys):
                if not k:
                    continue
                val = r[i] if i < len(r) else None
                s = _excel_cell_to_str(val)
                if s != "":
                    empty = False
                row[k] = s
            if not empty:
                out.append(row)
        return out, header_cols
    finally:
        wb.close()


def _read_excel_dicts(file_storage):
    """
    Đọc sheet đầu của .xlsx (Office Open XML) hoặc CSV.
    Dòng 1 = tiêu đề (tên cột, không phân biệt hoa thường).
    Trả về (danh_sách_dòng_dữ_liệu, tập_tên_cột_từ_tiêu_đề).
    """
    try:
        file_storage.seek(0)
    except Exception:
        pass
    raw = file_storage.read()
    filename = getattr(file_storage, "filename", None) or "upload.xlsx"

    if _is_old_binary_xls(raw):
        raise ValueError(
            "File là Excel cũ (.xls nhị phân). Vui lòng mở bằng Excel/LibreOffice và "
            "File → Save As / Lưu thành → **Excel Workbook (.xlsx)** — không dùng .xls."
        )

    if _is_ooxml_xlsx(raw):
        try:
            return _read_xlsx_bytes(raw)
        except zipfile.BadZipFile as e:
            raise ValueError(f"File .xlsx bị hỏng hoặc không đầy đủ: {e}") from e

    csv_text = _maybe_interpret_as_csv(raw, filename)
    if csv_text is not None:
        return _read_csv_dicts(csv_text)

    if raw[:2] == b"PK":
        raise ValueError(
            "File có đuôi .xlsx nhưng **không phải Excel .xlsx chuẩn** (thiếu [Content_Types].xml). "
            "Thường gặp khi: đổi đuôi file CSV/HTML thành .xlsx, hoặc xuất sai định dạng. "
            "Cách xử lý: mở bằng Excel → **File → Save As → Excel Workbook (.xlsx)**; "
            "hoặc lưu dưới dạng **CSV UTF-8** rồi đổi đuôi thành .csv và upload lại."
        )

    raise ValueError(
        "Không đọc được file: không phải .xlsx hợp lệ và không nhận dạng được CSV. "
        "Hãy dùng đúng file .xlsx (Excel / LibreOffice) hoặc .csv có dòng đầu là tên cột (UTF-8)."
    )


def _require_admin_page():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return "Admin only", 403
    return None


def _current_actor():
    if session.get("user_id"):
        return f"user:{session.get('user_id')}"
    return session.get("role") or "unknown"


def _client_ip_from_request() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def _host_cidr(ip_str: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(ip_str)
        return f"{ip}/32" if ip.version == 4 else f"{ip}/128"
    except ValueError:
        return None


def _parse_brand_list(text: str) -> list[str]:
    """Mỗi dòng một hoặc nhiều brand, phân tách bởi dấu phẩy/chấm phẩy; bỏ trùng giữ thứ tự."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        for part in line.replace(";", ",").split(","):
            b = part.strip()
            if b and b not in seen:
                seen.add(b)
                out.append(b)
    return out


def _ip_looks_non_public(ip_str: str) -> bool:
    """True nếu có vẻ là IP nội bộ / loopback — thường do proxy chưa truyền IP WAN thật."""
    s = (ip_str or "").strip()
    if not s:
        return False
    try:
        ip = ipaddress.ip_address(s)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False


def _insert_import_job(cur, **kwargs):
    cur.execute(
        """
        INSERT INTO import_jobs
            (dataset, mode, status, filename, row_count, inserted_count, updated_count, deleted_count,
             error_message, created_by, meta_json)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            kwargs.get("dataset"), kwargs.get("mode"), kwargs.get("status"), kwargs.get("filename"),
            kwargs.get("row_count", 0), kwargs.get("inserted_count", 0), kwargs.get("updated_count", 0), kwargs.get("deleted_count", 0),
            kwargs.get("error_message"), kwargs.get("created_by"), json.dumps(kwargs.get("meta", {}), ensure_ascii=False),
        ),
    )


def _preview_hints(dataset, mode, rows):
    hints = []
    if dataset == "products":
        brands = sorted({_norm(r.get("brand")) for r in rows if _norm(r.get("brand"))})
        hints.append(f"Distinct brands in file: {len(brands)}")
        if mode == "replace_by_brand":
            hints.append("Apply sẽ xóa products theo các brand trong file rồi insert lại")
        elif mode == "upsert":
            hints.append("Upsert key: code + brand (không phân biệt hoa thường)")
    else:
        types_ = sorted({_norm(r.get("rule_type")).upper() for r in rows if _norm(r.get("rule_type"))})
        hints.append(f"Rule types in file: {', '.join(types_) if types_ else 'none'}")
        if mode == "replace_by_type":
            hints.append("Apply sẽ xóa rules của các rule_type trong file rồi insert lại")
    return hints


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if username:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, password_hash, team_id, is_admin, ip_bypass_allowlist "
                        "FROM app_users WHERE username = %s",
                        (username,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()

            if row and check_password_hash(row[1], password):
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session["user_id"] = row[0]
                session["team_id"] = row[2]
                session["is_admin"] = bool(row[3])
                session["ip_bypass_allowlist"] = bool(row[4])
                session["role"] = "admin" if row[3] else "user"
                return redirect(url_for("home"))
            return render_template("login.html", error="Sai tên đăng nhập hoặc mật khẩu."), 401

        if password == MANAGER_PASSWORD:
            session.clear()
            session["authenticated"] = True
            session["username"] = "__legacy_manager__"
            session["is_admin"] = True
            session["ip_bypass_allowlist"] = False
            session["role"] = "manager"
            return redirect(url_for("home"))
        if password == STAFF_PASSWORD:
            session.clear()
            session["authenticated"] = True
            session["username"] = "__legacy_staff__"
            session["is_admin"] = False
            session["ip_bypass_allowlist"] = False
            team_id = int(os.environ.get("LEGACY_STAFF_TEAM_ID", "1"))
            session["team_id"] = team_id
            session["role"] = "staff"
            return redirect(url_for("home"))
        return render_template("login.html", error="Sai mật khẩu."), 403

    return render_template("login.html")


@app.route("/")
def home():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/admin/imports", methods=["GET"])
def admin_imports():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    token = request.args.get("preview")
    preview = IMPORT_PREVIEWS.get(token) if token else None

    conn = get_connection()
    recent_jobs = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, dataset, mode, status, row_count, inserted_count, updated_count, deleted_count, created_at
                FROM import_jobs
                ORDER BY id DESC
                LIMIT 20
                """
            )
            recent_jobs = [
                {
                    "id": r[0], "dataset": r[1], "mode": r[2], "status": r[3],
                    "row_count": r[4], "inserted_count": r[5], "updated_count": r[6], "deleted_count": r[7], "created_at": r[8],
                }
                for r in cur.fetchall()
            ]
    except Exception:
        recent_jobs = []
    finally:
        conn.close()

    return render_template("admin_imports.html", preview=preview, recent_jobs=recent_jobs, message=request.args.get("msg"), error=request.args.get("err"))


@app.route("/admin/imports/preview", methods=["POST"])
def admin_imports_preview():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    dataset = (request.form.get("dataset") or "").strip()
    mode = (request.form.get("mode") or "").strip()
    file = request.files.get("file")
    if not file:
        return redirect(url_for("admin_imports", err="Thiếu file upload"))

    try:
        rows, header_cols = _read_excel_dicts(file)
    except Exception as e:
        return redirect(url_for("admin_imports", err=f"Không đọc được Excel: {e}"))

    if dataset not in {"products", "regulatory_rules"}:
        return redirect(url_for("admin_imports", err="Dataset không hợp lệ"))

    if dataset == "products":
        # Cho phép import thiếu nhiều cột; chỉ cần có cột brand để
        # hỗ trợ replace_by_brand và giữ dữ liệu nhất quán theo team/brand.
        required = ["brand"]
        valid_modes = {"upsert", "replace_by_brand", "append"}
    else:
        required = ["rule_type", "rule_label", "match_field", "match_value", "priority", "is_active", "note"]
        valid_modes = {"upsert", "replace_by_type"}

    if mode not in valid_modes:
        return redirect(url_for("admin_imports", err="Mode không hợp lệ"))

    missing = [c for c in required if c not in header_cols]
    if missing:
        return redirect(
            url_for(
                "admin_imports",
                err="Thiếu cột trong dòng tiêu đề (dòng 1): "
                + ", ".join(missing)
                + ". Tải file mẫu và giữ đúng tên cột tiếng Anh, không dấu.",
            )
        )

    if not rows:
        return redirect(
            url_for(
                "admin_imports",
                err="File có tiêu đề nhưng không có dòng dữ liệu nào. Thêm ít nhất một dòng dưới tiêu đề (ô không được để trống hoàn toàn).",
            )
        )

    token = str(uuid4())
    IMPORT_PREVIEWS[token] = {
        "token": token,
        "dataset": dataset,
        "mode": mode,
        "filename": file.filename or "upload.xlsx",
        "rows": rows,
        "row_count": len(rows),
        "sample_rows": rows[:10],
        "hints": _preview_hints(dataset, mode, rows),
    }
    return redirect(url_for("admin_imports", preview=token))


@app.route("/admin/imports/apply", methods=["GET", "POST"])
def admin_imports_apply():
    guard = _require_admin_page()
    if guard is not None:
        return guard
    if request.method != "POST":
        return redirect(url_for("admin_imports", err="Vui lòng bấm 'Xem trước' rồi mới 'Xác nhận ghi vào database'."))

    token = request.form.get("preview_token")
    data = IMPORT_PREVIEWS.pop(token, None)
    if not data:
        return redirect(url_for("admin_imports", err="Preview hết hạn, vui lòng upload lại"))

    dataset = data["dataset"]
    mode = data["mode"]
    rows = data["rows"]
    filename = data.get("filename")

    inserted = updated = deleted = 0
    actor = _current_actor()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                if dataset == "products":
                    if mode == "replace_by_brand":
                        brands = sorted({_norm(r.get("brand")) for r in rows if _norm(r.get("brand"))})
                        if not brands:
                            raise ValueError("Mode replace_by_brand yêu cầu ít nhất 1 brand hợp lệ trong file.")
                        # Xóa theo brand không phân biệt hoa thường và bỏ khoảng trắng thừa.
                        brands_norm = sorted({b.strip().upper() for b in brands if b.strip()})
                        cur.execute(
                            """
                            DELETE FROM products
                            WHERE UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
                            """,
                            (brands_norm,),
                        )
                        deleted = cur.rowcount

                    for r in rows:
                        vals = (
                            _norm(r.get("name")), _norm(r.get("code")), _norm(r.get("cas")), _norm(r.get("brand")),
                            _norm(r.get("size")), _norm(r.get("ship")), _norm(r.get("price")), _norm(r.get("note")),
                        )
                        if mode == "append":
                            cur.execute(
                                """
                                INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                vals,
                            )
                            inserted += 1
                        else:
                            code, brand = vals[1], vals[3]
                            if not code or not brand:
                                cur.execute(
                                    """
                                    INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                    """,
                                    vals,
                                )
                                inserted += 1
                                continue
                            cur.execute(
                                """
                                SELECT id FROM products
                                WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                                  AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                                LIMIT 1
                                """,
                                (code, brand),
                            )
                            ex = cur.fetchone()
                            if ex:
                                cur.execute(
                                    """
                                    UPDATE products
                                       SET name=%s, code=%s, cas=%s, brand=%s, size=%s, ship=%s, price=%s, note=%s
                                     WHERE id=%s
                                    """,
                                    vals + (ex[0],),
                                )
                                updated += 1
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                    """,
                                    vals,
                                )
                                inserted += 1

                else:
                    parsed = []
                    for r in rows:
                        rule_type = _norm(r.get("rule_type")).upper()
                        match_field = _norm(r.get("match_field")).lower()
                        if rule_type not in {"CAM_NHAP", "PHU_LUC_II", "PHU_LUC_III", "TON_KHO"}:
                            raise ValueError(f"rule_type không hợp lệ: {rule_type}")
                        if match_field not in {"cas", "name", "code"}:
                            raise ValueError(f"match_field không hợp lệ: {match_field}")
                        priority_raw = _norm(r.get("priority")) or "100"
                        is_active_raw = _norm(r.get("is_active")).lower()
                        is_active = is_active_raw in {"1", "true", "yes", "y", "on"}
                        parsed.append(
                            {
                                "rule_type": rule_type,
                                "rule_label": _norm(r.get("rule_label")),
                                "match_field": match_field,
                                "match_value": _norm(r.get("match_value")),
                                "priority": int(float(priority_raw)),
                                "is_active": is_active,
                                "note": _norm(r.get("note")),
                            }
                        )

                    if mode == "replace_by_type":
                        types_ = sorted({x["rule_type"] for x in parsed})
                        cur.execute("DELETE FROM regulatory_rules WHERE rule_type = ANY(%s)", (types_,))
                        deleted = cur.rowcount

                    for r in parsed:
                        if mode == "replace_by_type":
                            cur.execute(
                                """
                                INSERT INTO regulatory_rules (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                """,
                                (r["rule_type"], r["rule_label"], r["match_field"], r["match_value"], r["priority"], r["is_active"], r["note"]),
                            )
                            inserted += 1
                        else:
                            cur.execute(
                                """
                                SELECT id FROM regulatory_rules
                                WHERE rule_type=%s AND match_field=%s AND UPPER(TRIM(match_value))=UPPER(TRIM(%s))
                                LIMIT 1
                                """,
                                (r["rule_type"], r["match_field"], r["match_value"]),
                            )
                            ex = cur.fetchone()
                            if ex:
                                cur.execute(
                                    """
                                    UPDATE regulatory_rules
                                       SET rule_label=%s, match_value=%s, priority=%s, is_active=%s, note=%s, updated_at=NOW()
                                     WHERE id=%s
                                    """,
                                    (r["rule_label"], r["match_value"], r["priority"], r["is_active"], r["note"], ex[0]),
                                )
                                updated += 1
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO regulatory_rules (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                                    """,
                                    (r["rule_type"], r["rule_label"], r["match_field"], r["match_value"], r["priority"], r["is_active"], r["note"]),
                                )
                                inserted += 1

                _insert_import_job(
                    cur,
                    dataset=dataset,
                    mode=mode,
                    status="success",
                    filename=filename,
                    row_count=len(rows),
                    inserted_count=inserted,
                    updated_count=updated,
                    deleted_count=deleted,
                    created_by=actor,
                    meta={"preview_token": token},
                )

        return redirect(url_for("admin_imports", msg=f"Import OK: inserted={inserted}, updated={updated}, deleted={deleted}"))
    except Exception as e:
        try:
            with conn:
                with conn.cursor() as cur:
                    _insert_import_job(
                        cur,
                        dataset=dataset,
                        mode=mode,
                        status="failed",
                        filename=filename,
                        row_count=len(rows),
                        inserted_count=inserted,
                        updated_count=updated,
                        deleted_count=deleted,
                        error_message=str(e),
                        created_by=actor,
                        meta={"preview_token": token},
                    )
        except Exception:
            pass
        return redirect(url_for("admin_imports", err=f"Import failed: {e}"))
    finally:
        conn.close()


def _xlsx_response(wb: Workbook, download_name: str):
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/templates/products.xlsx")
def admin_template_products():
    guard = _require_admin_page()
    if guard is not None:
        return guard
    wb = Workbook()
    ws = wb.active
    ws.title = "products"
    ws.append(["name", "code", "cas", "brand", "size", "ship", "price", "note"])
    return _xlsx_response(wb, "products_import_template.xlsx")


@app.route("/admin/templates/regulatory_rules.xlsx")
def admin_template_regulatory_rules():
    guard = _require_admin_page()
    if guard is not None:
        return guard
    wb = Workbook()
    ws = wb.active
    ws.title = "regulatory_rules"
    ws.append(["rule_type", "rule_label", "match_field", "match_value", "priority", "is_active", "note"])
    ws.append(["CAM_NHAP", "CẤM NHẬP", "cas", "123-45-6", 10, "TRUE", ""])
    return _xlsx_response(wb, "regulatory_rules_import_template.xlsx")


@app.route("/admin/exchange-rates", methods=["GET", "POST"])
def admin_exchange_rates():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    msg = err = None
    if request.method == "POST":
        conn = get_connection()
        try:
            if request.form.get("seed_json"):
                base = _default_exchange_rates_from_json()
                if not base:
                    err = "Không đọc được static/exchange_rates.json hoặc file rỗng."
                else:
                    with conn.cursor() as cur:
                        for brand, rate in base.items():
                            cur.execute(
                                """
                                INSERT INTO exchange_rates (brand, rate)
                                VALUES (%s, %s)
                                ON CONFLICT (brand) DO UPDATE SET rate = EXCLUDED.rate, updated_at = NOW()
                                """,
                                (brand, rate),
                            )
                    conn.commit()
                    msg = f"Đã đồng bộ {len(base)} brand từ file JSON vào database."
            elif request.form.get("delete_brand"):
                b = (request.form.get("delete_brand") or "").strip()
                if b:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM exchange_rates WHERE brand = %s", (b,))
                    conn.commit()
                    msg = f"Đã xóa tỷ giá cho brand: {b}"
            elif request.form.get("bulk_same_apply"):
                brands = _parse_brand_list(request.form.get("bulk_brands") or "")
                rate_raw = (request.form.get("bulk_rate") or "").strip().replace(",", ".")
                if not brands:
                    err = "Nhập danh sách brand (mỗi dòng hoặc cách nhau bởi dấu phẩy)."
                elif not rate_raw:
                    err = "Nhập tỷ giá chung."
                elif len(brands) > 2000:
                    err = "Tối đa 2000 brand mỗi lần."
                else:
                    try:
                        rate = float(rate_raw)
                    except ValueError:
                        err = "Tỷ giá không phải số."
                    else:
                        with conn.cursor() as cur:
                            for brand in brands:
                                cur.execute(
                                    """
                                    INSERT INTO exchange_rates (brand, rate)
                                    VALUES (%s, %s)
                                    ON CONFLICT (brand) DO UPDATE SET rate = EXCLUDED.rate, updated_at = NOW()
                                    """,
                                    (brand, rate),
                                )
                        conn.commit()
                        msg = f"Đã áp dụng tỷ giá {rate} cho {len(brands)} brand."
            elif request.form.get("bulk_lines_apply"):
                text = request.form.get("bulk_lines") or ""
                rows_parsed: list[tuple[str, float]] = []
                bad_lines: list[str] = []
                for raw in text.splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "\t" in line and "," not in line:
                        parts = [p.strip() for p in line.split("\t", 1)]
                    else:
                        parts = [p.strip() for p in line.split(",", 1)]
                    if len(parts) < 2 or not parts[0]:
                        bad_lines.append(raw.strip()[:80])
                        continue
                    b, rraw = parts[0], parts[1].replace(",", ".")
                    try:
                        r = float(rraw)
                    except ValueError:
                        bad_lines.append(raw.strip()[:80])
                        continue
                    rows_parsed.append((b, r))
                if len(rows_parsed) > 2000:
                    err = "Tối đa 2000 dòng mỗi lần."
                elif not rows_parsed:
                    err = "Không có dòng hợp lệ. Định dạng: brand,tỷ_giá (mỗi dòng một cặp)."
                else:
                    with conn.cursor() as cur:
                        for brand, rate in rows_parsed:
                            cur.execute(
                                """
                                INSERT INTO exchange_rates (brand, rate)
                                VALUES (%s, %s)
                                ON CONFLICT (brand) DO UPDATE SET rate = EXCLUDED.rate, updated_at = NOW()
                                """,
                                (brand, rate),
                            )
                    conn.commit()
                    msg = f"Đã cập nhật {len(rows_parsed)} dòng."
                    if bad_lines:
                        msg += f" Bỏ qua {len(bad_lines)} dòng không đọc được."
            else:
                brand = (request.form.get("brand") or "").strip()
                rate_raw = (request.form.get("rate") or "").strip().replace(",", ".")
                if not brand or not rate_raw:
                    err = "Nhập đủ brand và rate."
                else:
                    try:
                        rate = float(rate_raw)
                    except ValueError:
                        err = "Rate không phải số."
                    else:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO exchange_rates (brand, rate)
                                VALUES (%s, %s)
                                ON CONFLICT (brand) DO UPDATE SET rate = EXCLUDED.rate, updated_at = NOW()
                                """,
                                (brand, rate),
                            )
                        conn.commit()
                        msg = f"Đã lưu tỷ giá {brand} = {rate}"
        except Exception as e:
            err = str(e)
        finally:
            conn.close()

    rows = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT brand, rate, updated_at FROM exchange_rates ORDER BY brand ASC LIMIT 500")
            rows = [{"brand": r[0], "rate": r[1], "updated_at": r[2]} for r in cur.fetchall()]
    except Exception as e:
        err = err or f"Không đọc được bảng exchange_rates (đã chạy migration_005?): {e}"
    finally:
        conn.close()

    return render_template(
        "admin_exchange_rates.html",
        rows=rows,
        message=msg or request.args.get("msg"),
        error=err or request.args.get("err"),
        json_fallback_count=len(_default_exchange_rates_from_json()),
    )


@app.route("/admin/network", methods=["GET", "POST"])
def admin_network():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    msg = err = None
    if request.method == "POST":
        conn = get_connection()
        try:
            if request.form.get("add_my_ip"):
                ip_s = _client_ip_from_request()
                cidr = _host_cidr(ip_s)
                if not cidr:
                    err = f"Không suy ra được CIDR từ IP: {ip_s!r}"
                else:
                    label = (request.form.get("my_ip_label") or "auto").strip() or "auto"
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO office_ip_allowlist (cidr, label)
                            VALUES (%s, %s)
                            ON CONFLICT (cidr) DO UPDATE SET label = EXCLUDED.label, is_active = TRUE
                            """,
                            (cidr, label),
                        )
                    conn.commit()
                    msg = (
                        f"Đã thêm/kích hoạt {cidr}. Đây là IP công khai (WAN) mà máy chủ nhận được khi truy cập từ "
                        f"mạng hiện tại — thường là IP modem/router văn phòng (chung cho cả LAN), không phải IP máy nội bộ 192.168.x.x."
                    )
            elif request.form.get("delete_id"):
                try:
                    rid = int(request.form.get("delete_id"))
                except (TypeError, ValueError):
                    err = "ID không hợp lệ."
                else:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM office_ip_allowlist WHERE id = %s", (rid,))
                    conn.commit()
                    msg = "Đã xóa rule."
            else:
                cidr = (request.form.get("cidr") or "").strip()
                label = (request.form.get("label") or "").strip() or None
                if not cidr:
                    err = "Nhập CIDR hoặc IP (ví dụ 203.0.113.0/24 hoặc 203.0.113.10)."
                else:
                    try:
                        ipaddress.ip_network(cidr, strict=False)
                    except ValueError:
                        err = "CIDR/IP không hợp lệ."
                    else:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO office_ip_allowlist (cidr, label)
                                VALUES (%s, %s)
                                ON CONFLICT (cidr) DO UPDATE SET label = EXCLUDED.label, is_active = TRUE
                                """,
                                (cidr, label),
                            )
                        conn.commit()
                        msg = f"Đã thêm/kích hoạt {cidr}"
        except Exception as e:
            err = str(e)
        finally:
            conn.close()

    rules = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, cidr, label, is_active, created_at FROM office_ip_allowlist ORDER BY id DESC LIMIT 200"
            )
            rules = [
                {"id": r[0], "cidr": r[1], "label": r[2], "is_active": r[3], "created_at": r[4]}
                for r in cur.fetchall()
            ]
    except Exception as e:
        err = err or f"Không đọc được office_ip_allowlist (migration_006?): {e}"
    finally:
        conn.close()

    disable = os.environ.get("DISABLE_IP_ALLOWLIST", "").lower() in ("1", "true", "yes", "on")
    env_list = [x.strip() for x in (os.environ.get("OFFICE_IP_ALLOWLIST") or "").split(",") if x.strip()]
    seen_ip = _client_ip_from_request()
    seen_ip_non_public = _ip_looks_non_public(seen_ip)

    return render_template(
        "admin_network.html",
        rules=rules,
        message=msg or request.args.get("msg"),
        error=err or request.args.get("err"),
        disable_allowlist=disable,
        env_allowlist=env_list,
        seen_ip=seen_ip,
        seen_ip_non_public=seen_ip_non_public,
    )


@app.route("/admin/users", methods=["GET", "POST"])
def admin_users():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    msg = err = None

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "create_user":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            role = (request.form.get("role") or "user").strip().lower()
            is_admin = role in {"admin", "1", "true", "yes", "on"}
            ip_bypass_allowlist = (request.form.get("ip_bypass_allowlist") or "").strip().lower() in {"1", "true", "yes", "on"}
            brands_text = request.form.get("brands") or ""
            brands = _brands_from_text(brands_text)

            if not username:
                err = "Thiếu username."
            elif not password:
                err = "Thiếu mật khẩu."
            elif is_admin:
                brands = []
            else:
                if not brands:
                    err = "User thường cần gán ít nhất 1 brand (để lọc dữ liệu)."

            if not err:
                conn = get_connection()
                try:
                    with conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id FROM app_users WHERE username = %s", (username,))
                            if cur.fetchone():
                                raise ValueError(f"Username đã tồn tại: {username}")

                            password_hash = generate_password_hash(password)
                            if is_admin:
                                cur.execute(
                                    """
                                    INSERT INTO app_users (username, password_hash, team_id, is_admin, ip_bypass_allowlist)
                                    VALUES (%s, %s, NULL, TRUE, %s)
                                    """,
                                    (username, password_hash, ip_bypass_allowlist),
                                )
                            else:
                                team_name = f"User:{username}"
                                cur.execute(
                                    """
                                    INSERT INTO teams (name)
                                    VALUES (%s)
                                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                                    RETURNING id
                                    """,
                                    (team_name,),
                                )
                                team_id = cur.fetchone()[0]
                                cur.execute(
                                    """
                                    INSERT INTO app_users (username, password_hash, team_id, is_admin, ip_bypass_allowlist)
                                    VALUES (%s, %s, %s, FALSE, %s)
                                    """,
                                    (username, password_hash, team_id, ip_bypass_allowlist),
                                )
                                for b in brands:
                                    cur.execute(
                                        """
                                        INSERT INTO team_brands (team_id, brand)
                                        VALUES (%s, %s)
                                        ON CONFLICT (team_id, brand) DO NOTHING
                                        """,
                                        (team_id, b),
                                    )
                    msg = "Tạo user thành công."
                except Exception as e:
                    err = str(e)
                finally:
                    conn.close()

        elif action == "update_user":
            conn = get_connection()
            try:
                user_id_s = (request.form.get("user_id") or "").strip()
                if not user_id_s:
                    err = "Thiếu user_id."
                else:
                    user_id = int(user_id_s)
                    password = request.form.get("password") or ""
                    brands = _brands_from_text(request.form.get("brands") or "")
                    role = (request.form.get("role") or "user").strip().lower()
                    set_is_admin = role in {"admin", "1", "true", "yes", "on"}
                    set_ip_bypass_allowlist = (request.form.get("ip_bypass_allowlist") or "").strip().lower() in {"1", "true", "yes", "on"}

                    if (not set_is_admin) and not brands:
                        raise ValueError("User thường cần gán ít nhất 1 brand.")

                    with conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT username, team_id, is_admin FROM app_users WHERE id = %s", (user_id,))
                            row = cur.fetchone()
                            if not row:
                                raise ValueError("Không tìm thấy user.")

                            username, team_id, _old_is_admin = row

                            if password.strip():
                                cur.execute(
                                    "UPDATE app_users SET password_hash = %s WHERE id = %s",
                                    (generate_password_hash(password.strip()), user_id),
                                )

                            if set_is_admin:
                                cur.execute(
                                    "UPDATE app_users SET is_admin = TRUE, team_id = NULL, ip_bypass_allowlist = %s WHERE id = %s",
                                    (set_ip_bypass_allowlist, user_id),
                                )
                            else:
                                # Nếu trước đó là admin (team_id NULL) thì tạo team riêng theo username.
                                if not team_id:
                                    team_name = f"User:{username}"
                                    cur.execute(
                                        """
                                        INSERT INTO teams (name)
                                        VALUES (%s)
                                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                                        RETURNING id
                                        """,
                                        (team_name,),
                                    )
                                    team_id = cur.fetchone()[0]

                                cur.execute(
                                    "UPDATE app_users SET is_admin = FALSE, team_id = %s, ip_bypass_allowlist = %s WHERE id = %s",
                                    (team_id, set_ip_bypass_allowlist, user_id),
                                )

                                # Thay toàn bộ brands đã gán cho team này
                                cur.execute("DELETE FROM team_brands WHERE team_id = %s", (team_id,))
                                for b in brands:
                                    cur.execute(
                                        """
                                        INSERT INTO team_brands (team_id, brand)
                                        VALUES (%s, %s)
                                        ON CONFLICT (team_id, brand) DO NOTHING
                                        """,
                                        (team_id, b),
                                    )

                    msg = "Cập nhật user thành công."
            except Exception as e:
                err = str(e)
            finally:
                conn.close()
        else:
            err = "Hành động không hợp lệ."

        if msg or err:
            return redirect(url_for("admin_users", msg=msg, err=err))

    # GET: load users + brands
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.username, a.is_admin, a.team_id, t.name, a.ip_bypass_allowlist
                FROM app_users a
                LEFT JOIN teams t ON t.id = a.team_id
                ORDER BY a.id DESC
                """
            )
            user_rows = cur.fetchall()

            users = []
            for (uid, username, is_admin, team_id, team_name, ip_bypass_allowlist) in user_rows:
                assigned_brands = []
                if (not is_admin) and team_id:
                    cur.execute(
                        "SELECT brand FROM team_brands WHERE team_id = %s ORDER BY brand",
                        (team_id,),
                    )
                    assigned_brands = [r[0] for r in cur.fetchall()]

                users.append(
                    {
                        "id": uid,
                        "username": username,
                        "is_admin": bool(is_admin),
                        "ip_bypass_allowlist": bool(ip_bypass_allowlist),
                        "team_id": team_id,
                        "team_name": team_name,
                        "brands_text": "\n".join(assigned_brands),
                        "brands_count": len(assigned_brands),
                    }
                )

        # (Tuỳ chọn) danh sách brand đang có trong products để admin nhìn nhanh.
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT brand FROM products ORDER BY brand")
            distinct_brands = [r[0] for r in cur.fetchall() if r[0] is not None]

    except Exception as e:
        users = []
        distinct_brands = []
        err = str(e)
    finally:
        conn.close()

    return render_template(
        "admin_users.html",
        users=users,
        distinct_brands=distinct_brands,
        message=msg or request.args.get("msg"),
        error=err or request.args.get("err"),
    )


@app.route("/search", methods=["GET"])
def search_products():
    search_query = request.args.get("query") or ""
    vis, vis_params = _visibility_sql("p")
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            query = f"""
                SELECT
                    p.name,
                    p.code,
                    p.cas,
                    p.brand,
                    p.size,
                    p.ship,
                    p.price,
                    p.note,
                    rr.rule_label AS compliance_status,
                    rr.note AS compliance_note
                FROM products p
                LEFT JOIN LATERAL (
                    SELECT r.rule_label, r.note
                    FROM regulatory_rules r
                    WHERE r.is_active = TRUE
                      AND (
                        (r.match_field = 'cas' AND NULLIF(TRIM(p.cas), '') IS NOT NULL AND UPPER(TRIM(p.cas)) = UPPER(TRIM(r.match_value)))
                        OR (r.match_field = 'name' AND NULLIF(TRIM(p.name), '') IS NOT NULL AND UPPER(TRIM(p.name)) = UPPER(TRIM(r.match_value)))
                        OR (r.match_field = 'code' AND NULLIF(TRIM(p.code), '') IS NOT NULL AND UPPER(TRIM(p.code)) = UPPER(TRIM(r.match_value)))
                      )
                    ORDER BY r.priority ASC, r.id ASC
                    LIMIT 1
                ) rr ON TRUE
                WHERE (p.name ILIKE %s OR p.code ILIKE %s OR p.cas ILIKE %s)
                {vis}
                ORDER BY
                    UPPER(TRIM(COALESCE(p.brand, ''))) ASC,
                    UPPER(TRIM(COALESCE(p.size, ''))) ASC,
                    UPPER(TRIM(COALESCE(p.name, ''))) ASC,
                    UPPER(TRIM(COALESCE(p.code, ''))) ASC,
                    p.id ASC
            """
            pattern = f"%{search_query}%"
            cursor.execute(query, (pattern, pattern, pattern) + vis_params)
            products = cursor.fetchall()

        rate_map = _exchange_rate_map(conn)
        results = []
        for product in products:
            name, code, cas, brand, size, ship, price, note, compliance_status, compliance_note = product
            try:
                ship = float(ship) if ship is not None else 0
            except (TypeError, ValueError):
                ship = 0
            try:
                price = float(price) if price is not None else 0
            except (TypeError, ValueError):
                price = 0

            bkey = (brand or "").strip()
            exchange_rate = rate_map.get(bkey, 1.0)
            unit_price = round(price * ship * exchange_rate, -3)
            formatted_unit_price = "{:,.0f}".format(unit_price)

            results.append(
                {
                    "Name": name,
                    "Code": code,
                    "Cas": cas,
                    "Brand": brand,
                    "Size": size,
                    "Unit_Price": formatted_unit_price,
                    "Note": note,
                    "Compliance_Status": compliance_status,
                    "Compliance_Note": compliance_note,
                    "Compliance_Css": _warning_css_type(compliance_status),
                }
            )

        return jsonify({"results": results})
    finally:
        conn.close()


@app.route("/check_cas", methods=["GET"])
def check_cas():
    cas = request.args.get("cas")
    if not cas:
        return jsonify({"warning": False})

    vis, vis_params = _visibility_sql("p")
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            query = f"""
                SELECT r.rule_label
                FROM regulatory_rules r
                WHERE r.is_active = TRUE
                  AND r.match_field = 'cas'
                  AND UPPER(TRIM(r.match_value)) = UPPER(TRIM(%s))
                  AND EXISTS (
                    SELECT 1
                    FROM products p
                    WHERE UPPER(TRIM(p.cas)) = UPPER(TRIM(%s))
                    {vis}
                  )
                ORDER BY r.priority ASC, r.id ASC
                LIMIT 1
            """
            cursor.execute(query, (cas, cas) + vis_params)
            row = cursor.fetchone()

        warning = row[0] if row else None
        if warning:
            return jsonify({"warning": True, "warning_type": warning, "message": f"CAS {cas} thuộc danh mục {warning}."})
        return jsonify({"warning": False})
    finally:
        conn.close()


@app.route("/check_cas_batch", methods=["GET", "POST"])
def check_cas_batch():
    cas_text = request.values.get("cas") or request.values.get("cas_list") or ""
    cas_items = _split_multi_items(cas_text, max_items=2000)
    if not cas_items:
        return jsonify({"results": [], "error": "Thiếu CAS."})

    cas_upper = [c.upper() for c in cas_items]

    vis, vis_params = _visibility_sql("p")
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # Tránh EXISTS lồng trong LATERAL (mỗi CAS quét products nặng).
            # eligible: CAS có ít nhất 1 dòng products khớp + visibility — dùng index UPPER(TRIM(cas)).
            query = f"""
                WITH input AS (
                    SELECT u.ord, u.cas_u
                    FROM unnest(%s::text[]) WITH ORDINALITY AS u(cas_u, ord)
                ),
                eligible AS (
                    SELECT DISTINCT ON (i.ord)
                        i.ord,
                        i.cas_u
                    FROM input i
                    INNER JOIN products p ON UPPER(TRIM(p.cas)) = i.cas_u
                    WHERE TRUE
                    {vis}
                    ORDER BY i.ord, p.id ASC
                )
                SELECT
                    i.ord,
                    i.cas_u,
                    rr.rule_label AS compliance_status,
                    rr.note AS compliance_note
                FROM input i
                LEFT JOIN eligible e ON e.ord = i.ord AND e.cas_u = i.cas_u
                LEFT JOIN LATERAL (
                    SELECT r.rule_label, r.note
                    FROM regulatory_rules r
                    WHERE e.ord IS NOT NULL
                      AND r.is_active = TRUE
                      AND r.match_field = 'cas'
                      AND UPPER(TRIM(r.match_value)) = i.cas_u
                    ORDER BY r.priority ASC, r.id ASC
                    LIMIT 1
                ) rr ON TRUE
                ORDER BY i.ord
            """
            cursor.execute(query, (cas_upper,) + vis_params)
            rows = cursor.fetchall()

        # Luôn trả về đúng số dòng bằng input (kể cả CAS không có match)
        results = [
            {"Cas": original, "Compliance_Status": "", "Compliance_Note": ""}
            for original in cas_items
        ]
        for ord_, _cas_u, compliance_status, compliance_note in rows:
            idx = int(ord_) - 1
            if 0 <= idx < len(results):
                results[idx]["Compliance_Status"] = compliance_status or ""
                results[idx]["Compliance_Note"] = compliance_note or ""

        return jsonify({"results": results})
    finally:
        conn.close()


@app.route("/find_code_batch", methods=["GET", "POST"])
def find_code_batch():
    codes_text = request.values.get("codes") or request.values.get("code_list") or ""
    codes_items = _split_multi_items(codes_text, max_items=2000)
    if not codes_items:
        return jsonify({"results": [], "error": "Thiếu code."})

    codes_upper = [c.upper() for c in codes_items]

    vis, vis_params = _visibility_sql("p")
    conn = get_connection()
    try:
        rate_map = _exchange_rate_map(conn)
        with conn.cursor() as cursor:
            # DISTINCT ON + JOIN giúp planner dùng index (UPPER(TRIM(code))) tốt hơn
            # so với LEFT JOIN LATERAL lồng nhau trên bảng lớn.
            query = f"""
                WITH input AS (
                    SELECT u.ord, u.code_u
                    FROM unnest(%s::text[]) WITH ORDINALITY AS u(code_u, ord)
                ),
                picked AS (
                    SELECT DISTINCT ON (i.ord)
                        i.ord,
                        p.id AS product_id
                    FROM input i
                    INNER JOIN products p ON UPPER(TRIM(p.code)) = i.code_u
                    WHERE TRUE
                    {vis}
                    ORDER BY i.ord, p.id ASC
                )
                SELECT
                    i.ord,
                    p.name,
                    p.code,
                    p.cas,
                    p.brand,
                    p.size,
                    p.ship,
                    p.price,
                    p.note,
                    rr.rule_label AS compliance_status,
                    rr.note AS compliance_note
                FROM input i
                LEFT JOIN picked pk ON pk.ord = i.ord
                LEFT JOIN products p ON p.id = pk.product_id
                LEFT JOIN LATERAL (
                    SELECT r.rule_label, r.note
                    FROM regulatory_rules r
                    WHERE p.id IS NOT NULL
                      AND r.is_active = TRUE
                      AND (
                        (r.match_field = 'cas' AND NULLIF(TRIM(p.cas), '') IS NOT NULL
                            AND UPPER(TRIM(p.cas)) = UPPER(TRIM(r.match_value)))
                        OR (r.match_field = 'name' AND NULLIF(TRIM(p.name), '') IS NOT NULL
                            AND UPPER(TRIM(p.name)) = UPPER(TRIM(r.match_value)))
                        OR (r.match_field = 'code' AND NULLIF(TRIM(p.code), '') IS NOT NULL
                            AND UPPER(TRIM(p.code)) = UPPER(TRIM(r.match_value)))
                      )
                    ORDER BY r.priority ASC, r.id ASC
                    LIMIT 1
                ) rr ON TRUE
                ORDER BY i.ord
            """
            cursor.execute(query, (codes_upper,) + vis_params)
            rows = cursor.fetchall()

        # Trả về mảng luôn đúng thứ tự/độ dài input
        results = [
            {
                "Name": "",
                "Code": original,
                "Cas": "",
                "Brand": "",
                "Size": "",
                "Unit_Price": "",
                "Note": "",
                "Compliance_Status": "",
                "Compliance_Note": "",
                "Compliance_Css": "",
            }
            for original in codes_items
        ]

        for row in rows:
            (
                ord_,
                name,
                code,
                cas,
                brand,
                size,
                ship,
                price,
                note,
                compliance_status,
                compliance_note,
            ) = row
            idx = int(ord_) - 1
            if not (0 <= idx < len(results)):
                continue

            results[idx]["Name"] = name or ""
            # Code giữ nguyên theo input để đảm bảo copy dễ
            results[idx]["Cas"] = cas or ""
            results[idx]["Brand"] = brand or ""
            results[idx]["Size"] = size or ""
            results[idx]["Note"] = note or ""
            results[idx]["Compliance_Status"] = compliance_status or ""
            results[idx]["Compliance_Note"] = compliance_note or ""
            results[idx]["Compliance_Css"] = _warning_css_type(compliance_status) or ""

            # Unit price chỉ tính nếu có đủ số
            try:
                ship_f = float(ship) if ship is not None else None
            except (TypeError, ValueError):
                ship_f = None
            try:
                price_f = float(price) if price is not None else None
            except (TypeError, ValueError):
                price_f = None

            if ship_f is not None and price_f is not None:
                bkey = (brand or "").strip()
                exchange_rate = rate_map.get(bkey, 1.0)
                unit_price = round(price_f * ship_f * exchange_rate, -3)
                results[idx]["Unit_Price"] = "{:,.0f}".format(unit_price)

        return jsonify({"results": results})
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
