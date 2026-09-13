"""Inventory snapshot helpers shared by imports and read-only search surfaces."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import unicodedata


STOCK_LOCK_KEY = 62402901


def normalized_text(value) -> str:
    return unicodedata.normalize("NFC", "" if value is None else str(value)).strip().casefold()


def acquire_stock_lock(cur) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (STOCK_LOCK_KEY,))


def active_snapshot(cur, *, for_update: bool = False) -> dict:
    suffix = " FOR UPDATE" if for_update else ""
    cur.execute(
        "SELECT active_snapshot_id,revision,updated_at FROM stock_state WHERE singleton=TRUE" + suffix
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("stock_state singleton is missing")
    if isinstance(row, dict):
        snapshot_id, revision, updated_at = row["active_snapshot_id"], row["revision"], row["updated_at"]
    else:
        snapshot_id, revision, updated_at = row
    return {"id": str(snapshot_id) if snapshot_id else None, "revision": int(revision), "updated_at": updated_at}


def snapshot_fingerprint(cur) -> str:
    state = active_snapshot(cur)
    payload = json.dumps({"id": state["id"], "revision": state["revision"]}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def expiry_state(expiry, *, today: date | None = None) -> tuple[str, str]:
    if expiry is None:
        return "missing", "Không có hạn sử dụng"
    today = today or date.today()
    if expiry < today:
        return "expired", "Đã hết hạn"
    if expiry <= today + timedelta(days=30):
        return "near_expiry", "Sắp hết hạn trong 30 ngày"
    return "current", "Còn hạn"


def format_vnd(value) -> str:
    if value is None:
        return ""
    amount = Decimal(value)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}".replace(",", ".") + " ₫"
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " ₫"


def _visible_stock_sql(alias: str, *, is_admin: bool, team_id) -> tuple[str, tuple]:
    if is_admin:
        return "", ()
    if team_id is None:
        return " AND FALSE", ()
    return (
        f" AND {alias}.brand IN ("
        "SELECT tb.brand FROM team_brands tb JOIN teams t ON t.id=tb.team_id "
        "WHERE tb.team_id=%s AND t.lifecycle_status='ACTIVE')",
        (team_id,),
    )


def fetch_stock_options(
    cur,
    *,
    codes=(),
    cas_values=(),
    is_admin: bool,
    team_id,
    grants,
    allow_same_cas: bool,
) -> dict:
    """Fetch all active stock candidates with one bulk query.

    Returned dictionaries are already field-redacted. Callers must not derive
    brand/CAS/price counts before this boundary.
    """
    code_norms = sorted({normalized_text(value) for value in codes if normalized_text(value)})
    cas_norms = sorted({normalized_text(value) for value in cas_values if normalized_text(value)}) if allow_same_cas else []
    if not code_norms and not cas_norms:
        return {"by_code": {}, "by_cas": {}, "all": []}
    visibility_sql, visibility_params = _visible_stock_sql("i", is_admin=is_admin, team_id=team_id)
    cur.execute(
        f"""
        SELECT i.id,i.name,i.code,i.cas,i.brand,i.size,i.stock_price_vnd,
               i.quantity,i.expiry_date,i.code_norm,i.cas_norm
        FROM stock_state state
        JOIN stock_items i ON i.snapshot_id=state.active_snapshot_id
        WHERE state.singleton=TRUE
          AND (i.code_norm=ANY(%s) OR i.cas_norm=ANY(%s))
          {visibility_sql}
        ORDER BY i.code_norm,i.brand_norm,i.size_norm,i.expiry_date NULLS LAST,i.id
        """,
        (code_norms, cas_norms) + visibility_params,
    )
    grants = frozenset(grants or ())
    by_code: dict[str, list[dict]] = {}
    by_cas: dict[str, list[dict]] = {}
    all_items: list[dict] = []
    for row in cur.fetchall():
        (item_id, name, code, cas, brand, size, price, quantity, expiry,
         code_norm, cas_norm) = row
        state, warning = expiry_state(expiry)
        item = {
            "Stock_Item_Id": int(item_id),
            "Stock_Quantity": int(quantity),
            "Stock_Expiry": expiry.isoformat() if expiry else "",
            "Stock_Expiry_Label": expiry.strftime("%d/%m/%Y") if expiry else "",
            "Stock_State": state,
            "Stock_Warning": warning,
        }
        if "VIEW_NAME" in grants:
            item["Name"] = name or ""
        if "VIEW_CODE" in grants:
            item["Code"] = code or ""
        if "VIEW_CAS" in grants:
            item["Cas"] = cas or ""
        if "VIEW_BRAND" in grants:
            item["Brand"] = brand or ""
        if "VIEW_SIZE" in grants:
            item["Size"] = size or ""
        if "VIEW_PRICE" in grants:
            item["Stock_Price"] = format_vnd(price)
            item["Stock_Price_Vnd"] = str(price) if price is not None else ""
        all_items.append(item)
        by_code.setdefault(code_norm, []).append(item)
        if cas_norm:
            by_cas.setdefault(cas_norm, []).append(item)
    return {"by_code": by_code, "by_cas": by_cas, "all": all_items}


def options_for_product(stock_data: dict, *, code, cas, include_same_cas: bool) -> list[dict]:
    exact = stock_data["by_code"].get(normalized_text(code), [])
    seen = {item["Stock_Item_Id"] for item in exact}
    result = [dict(item, Stock_Match="exact_code") for item in exact]
    if include_same_cas and normalized_text(cas):
        for item in stock_data["by_cas"].get(normalized_text(cas), []):
            if item["Stock_Item_Id"] not in seen:
                seen.add(item["Stock_Item_Id"])
                result.append(dict(item, Stock_Match="same_cas"))
    return result
