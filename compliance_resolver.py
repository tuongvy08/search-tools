"""Pure compliance precedence resolver shared by search response builders."""

from __future__ import annotations

from typing import Any, Optional

from product_import_manual import normalize_manual_compliance_value


LEGACY_NO_CAS_STATUS = "Chưa xác định"
LEGACY_NO_MATCH_STATUS = "Không phát hiện hạn chế"


def compliance_css_type(label: Optional[str]) -> Optional[str]:
    if label == "CẤM NHẬP":
        return "warning-cam-nhap"
    if label == "Phụ lục II":
        return "warning-phu-luc-ii"
    if label == "Phụ lục III":
        return "warning-phu-luc-iii"
    if label == "TỒN KHO":
        return "warning-ton-kho"
    if label == "Được bán":
        return "warning-duoc-ban"
    if label == LEGACY_NO_CAS_STATUS:
        return "warning-chua-xac-dinh"
    if label == LEGACY_NO_MATCH_STATUS:
        return "warning-khong-phat-hien"
    return None


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _canonical_manual_compliance(value: Any) -> Optional[str]:
    try:
        return normalize_manual_compliance_value(value)
    except ValueError:
        text = "" if value is None else str(value).strip()
        return text or None


def resolve_compliance_precedence(
    *,
    brand_manual_enabled: bool,
    manual_compliance: Any,
    manual_compliance_note: Any,
    legacy_compliance: Any,
    legacy_compliance_note: Any,
    cas: Any,
) -> dict[str, str]:
    """
    Resolve final compliance fields without database access.

    Manual note is effective only when a nonblank manual compliance is effective.
    Product note is deliberately not accepted here so it cannot merge with
    compliance_note.
    """
    manual_status = _canonical_manual_compliance(manual_compliance)
    if brand_manual_enabled and manual_status:
        note = "" if _blank(manual_compliance_note) else str(manual_compliance_note).strip()
        return {
            "compliance": manual_status,
            "compliance_note": note,
            "compliance_css": compliance_css_type(manual_status) or "",
            "compliance_source": "manual",
        }

    legacy_status = "" if _blank(legacy_compliance) else str(legacy_compliance).strip()
    if legacy_status:
        note = "" if _blank(legacy_compliance_note) else str(legacy_compliance_note).strip()
        return {
            "compliance": legacy_status,
            "compliance_note": note,
            "compliance_css": compliance_css_type(legacy_status) or "",
            "compliance_source": "legacy",
        }

    unresolved_status = LEGACY_NO_CAS_STATUS if _blank(cas) else LEGACY_NO_MATCH_STATUS
    return {
        "compliance": unresolved_status,
        "compliance_note": "",
        "compliance_css": compliance_css_type(unresolved_status) or "",
        "compliance_source": "unresolved",
    }
