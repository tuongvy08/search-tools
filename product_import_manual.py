"""Validation and parsing for optional product import control columns."""

from __future__ import annotations

from typing import Any, Optional

HEADER_COMPLIANCE = "compliance"
HEADER_NOTE = "compliance_note"
HEADER_PREPARATION_TYPE = "preparation_type"

HEADER_MODE_ABSENT = "absent"
HEADER_MODE_BOTH = "both"
HEADER_MODE_PARTIAL = "partial"

MANUAL_COMPLIANCE_CANONICAL = (
    "Được bán",
    "Phụ lục II",
    "Phụ lục III",
    "Cần giấy phép",
    "Cấm nhập",
    "Chưa xác định",
)
PREPARATION_TYPE_CANONICAL = ("NEAT", "SOLUTION", "MIXTURE", "OTHER")

_CANONICAL_BY_LOWER = {value.casefold(): value for value in MANUAL_COMPLIANCE_CANONICAL}
_PREPARATION_ALIASES = {
    "NEAT": "NEAT",
    "PURE": "NEAT",
    "NGUYÊN CHẤT": "NEAT",
    "NGUYEN CHAT": "NEAT",
    "SOLUTION": "SOLUTION",
    "DUNG DỊCH": "SOLUTION",
    "DUNG DICH": "SOLUTION",
    "MIXTURE": "MIXTURE",
    "MIX": "MIXTURE",
    "HỖN HỢP": "MIXTURE",
    "HON HOP": "MIXTURE",
    "OTHER": "OTHER",
    "KHÁC": "OTHER",
    "KHAC": "OTHER",
}


def classify_manual_compliance_headers(header_cols: set[str]) -> str:
    has_compliance = HEADER_COMPLIANCE in header_cols
    has_note = HEADER_NOTE in header_cols
    if has_compliance and has_note:
        return HEADER_MODE_BOTH
    if has_compliance or has_note:
        return HEADER_MODE_PARTIAL
    return HEADER_MODE_ABSENT


def normalize_manual_compliance_value(raw: Any) -> Optional[str]:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    canonical = _CANONICAL_BY_LOWER.get(text.casefold())
    if canonical is None:
        raise ValueError(
            f"Giá trị Compliance không hợp lệ: {text!r}. "
            f"Cho phép: {', '.join(MANUAL_COMPLIANCE_CANONICAL)}."
        )
    return canonical


def normalize_preparation_type_value(raw: Any) -> Optional[str]:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    canonical = _PREPARATION_ALIASES.get(text.upper())
    if canonical is None:
        raise ValueError(
            f"Giá trị Preparation_Type không hợp lệ: {text!r}. "
            f"Cho phép: {', '.join(PREPARATION_TYPE_CANONICAL)}."
        )
    return canonical


def normalize_manual_compliance_note(raw: Any) -> Optional[str]:
    text = "" if raw is None else str(raw).strip()
    return text or None


def normalize_import_text(raw: Any) -> str:
    return "" if raw is None else str(raw).strip()


def product_identity_key(code: str, brand: str) -> Optional[tuple[str, str]]:
    """Same identity semantics as importer upsert: UPPER(TRIM(code)), UPPER(TRIM(brand))."""
    code_key = normalize_import_text(code).upper()
    brand_key = normalize_import_text(brand).upper()
    if not code_key or not brand_key:
        return None
    return code_key, brand_key


def validate_product_import_rows(rows: list[dict], header_cols: set[str]) -> None:
    mode = classify_manual_compliance_headers(header_cols)
    if mode == HEADER_MODE_PARTIAL:
        missing = []
        if HEADER_COMPLIANCE not in header_cols:
            missing.append("Compliance")
        if HEADER_NOTE not in header_cols:
            missing.append("Compliance_Note")
        raise ValueError(
            "File products thiếu cột đi kèm: cần cả Compliance và Compliance_Note, "
            f"hoặc bỏ cả hai. Thiếu: {', '.join(missing)}."
        )
    has_preparation_type = HEADER_PREPARATION_TYPE in header_cols

    for row_index, row in enumerate(rows, start=2):
        compliance_text = normalize_import_text(row.get(HEADER_COMPLIANCE, ""))
        note_text = normalize_import_text(row.get(HEADER_NOTE, ""))
        preparation_text = normalize_import_text(row.get(HEADER_PREPARATION_TYPE, ""))
        code_text = normalize_import_text(row.get("code", ""))

        if mode != HEADER_MODE_ABSENT and note_text and not compliance_text:
            raise ValueError(
                f"Dòng {row_index}: Compliance_Note không được có giá trị khi Compliance đang trống."
            )
        if mode != HEADER_MODE_ABSENT and (compliance_text or note_text) and not code_text:
            raise ValueError(
                f"Dòng {row_index}: Cần Code khi có Compliance hoặc Compliance_Note."
            )
        if mode != HEADER_MODE_ABSENT and compliance_text:
            try:
                normalize_manual_compliance_value(compliance_text)
            except ValueError as e:
                raise ValueError(f"Dòng {row_index}: {e}") from e
        if has_preparation_type:
            if preparation_text and not code_text:
                raise ValueError(f"Dòng {row_index}: Cần Code khi có Preparation_Type.")
            try:
                normalize_preparation_type_value(preparation_text)
            except ValueError as e:
                raise ValueError(f"Dòng {row_index}: {e}") from e


def parse_manual_compliance_row(row: dict) -> tuple[Optional[str], Optional[str]]:
    """Parse row when both Compliance headers exist. Blank Compliance clears override."""
    compliance = normalize_manual_compliance_value(row.get(HEADER_COMPLIANCE, ""))
    if compliance is None:
        return None, None
    note = normalize_manual_compliance_note(row.get(HEADER_NOTE, ""))
    return compliance, note


def parse_preparation_type_row(row: dict) -> Optional[str]:
    """Parse row when Preparation_Type header exists. Blank clears the value."""
    return normalize_preparation_type_value(row.get(HEADER_PREPARATION_TYPE, ""))


def fetch_manual_compliance_snapshot(cur, brands_norm: list[str]) -> dict[tuple[str, str], tuple[Optional[str], Optional[str]]]:
    if not brands_norm:
        return {}
    cur.execute(
        """
        SELECT
            UPPER(TRIM(code)) AS code_key,
            UPPER(TRIM(brand)) AS brand_key,
            manual_compliance,
            manual_compliance_note
        FROM products
        WHERE UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
          AND NULLIF(TRIM(code), '') IS NOT NULL
        """,
        (brands_norm,),
    )
    return {(code_key, brand_key): (manual_c, manual_n) for code_key, brand_key, manual_c, manual_n in cur.fetchall()}


def fetch_preparation_type_snapshot(cur, brands_norm: list[str]) -> dict[tuple[str, str], Optional[str]]:
    if not brands_norm:
        return {}
    cur.execute(
        """
        SELECT
            UPPER(TRIM(code)) AS code_key,
            UPPER(TRIM(brand)) AS brand_key,
            preparation_type
        FROM products
        WHERE UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
          AND NULLIF(TRIM(code), '') IS NOT NULL
        """,
        (brands_norm,),
    )
    return {(code_key, brand_key): preparation_type for code_key, brand_key, preparation_type in cur.fetchall()}


def resolve_manual_fields_for_write(
    *,
    header_mode: str,
    row: dict,
    code: str,
    brand: str,
    snapshot: dict[tuple[str, str], tuple[Optional[str], Optional[str]]],
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (include_manual, manual_compliance, manual_compliance_note).
    include_manual=False means caller must omit manual_* columns from SQL (preserve on update).
    """
    if header_mode == HEADER_MODE_BOTH:
        manual_c, manual_n = parse_manual_compliance_row(row)
        return True, manual_c, manual_n

    key = product_identity_key(code, brand)
    if key and key in snapshot:
        manual_c, manual_n = snapshot[key]
        return True, manual_c, manual_n
    return False, None, None


def resolve_preparation_type_for_write(
    *,
    header_cols: set[str],
    row: dict,
    code: str,
    brand: str,
    snapshot: dict[tuple[str, str], Optional[str]],
) -> tuple[bool, Optional[str]]:
    """
    Returns (include_preparation_type, preparation_type).
    include_preparation_type=False means caller must omit column from SQL (preserve on update).
    """
    if HEADER_PREPARATION_TYPE in header_cols:
        return True, parse_preparation_type_row(row)

    key = product_identity_key(code, brand)
    if key and key in snapshot:
        return True, snapshot[key]
    return False, None
