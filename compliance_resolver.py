"""Pure response adapter for the shared database regulatory resolver."""

from __future__ import annotations

from typing import Any, Optional

from regulatory import EXPORT_ALLOW, EXPORT_BLOCK, policy_css, resolved_result


def compliance_css_type(label: Optional[str], export_policy: Optional[str] = None) -> Optional[str]:
    """Compatibility helper; runtime policy must be supplied by stable status."""
    if export_policy:
        return policy_css(export_policy) or None
    # Legacy-only adapter retained for pre-026 unit tests/data tooling. Runtime
    # quote eligibility never calls this label branch.
    if label in {"CẤM NHẬP", "Cấm nhập"}:
        return policy_css(EXPORT_BLOCK) or None
    if label == "Phụ lục II":
        return "warning-phu-luc-ii"
    if label == "Phụ lục III":
        return "warning-phu-luc-iii"
    if label == "Được bán":
        return "warning-duoc-ban"
    return None


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def resolve_compliance_precedence(
    *,
    brand_manual_enabled: bool,
    manual_compliance: Any,
    manual_compliance_note: Any,
    legacy_compliance: Any,
    legacy_compliance_note: Any,
    cas: Any,
    export_policy: Any = None,
    source: Any = None,
    stable_key: Any = None,
    status_id: Any = None,
) -> dict[str, Any]:
    """
    Resolve final compliance fields without database access.

    Manual note is effective only when a nonblank manual compliance is effective.
    Product note is deliberately not accepted here so it cannot merge with
    compliance_note.
    """
    # Post-026 queries already choose manual-vs-automatic in one shared SQL
    # resolver and supply its stable policy. This adapter must never re-derive
    # BLOCK/ALLOW from a display label.
    if export_policy is not None or source is not None or status_id is not None:
        return resolved_result(
            legacy_compliance,
            legacy_compliance_note,
            export_policy or EXPORT_ALLOW,
            source,
            stable_key,
            status_id,
        )

    # Compatibility for isolated pure unit tests that do not own a database.
    # It intentionally returns blank on no match per Phase 6D1. Production
    # eligibility uses only the stable policy branch above.
    manual_status = "" if _blank(manual_compliance) else str(manual_compliance).strip()
    if brand_manual_enabled and manual_status:
        policy = EXPORT_BLOCK if manual_status in {"CẤM NHẬP", "Cấm nhập"} else EXPORT_ALLOW
        keys = {"Được bán": "DUOC_BAN", "Phụ lục II": "PHU_LUC_II", "Phụ lục III": "PHU_LUC_III"}
        return resolved_result(manual_status, manual_compliance_note, policy, "manual", keys.get(manual_status))
    legacy_status = "" if _blank(legacy_compliance) else str(legacy_compliance).strip()
    if legacy_status:
        policy = EXPORT_BLOCK if legacy_status in {"CẤM NHẬP", "Cấm nhập"} else EXPORT_ALLOW
        keys = {"Phụ lục II": "PHU_LUC_II", "Phụ lục III": "PHU_LUC_III"}
        return resolved_result(legacy_status, legacy_compliance_note, policy, "automatic", keys.get(legacy_status))
    return resolved_result()
