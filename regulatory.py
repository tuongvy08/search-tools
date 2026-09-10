"""Shared Phase 6D1 regulatory normalization, SQL resolution and policy helpers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from typing import Any, Optional


EXPORT_ALLOW = "ALLOW"
EXPORT_BLOCK = "BLOCK"
REGULATORY_LOCK_KEY = 62402601
MATCH_FIELDS = ("cas", "code", "name")
MATCH_FIELD_LABELS = {"cas": "CAS", "code": "Code", "name": "Tên"}

_CAS_RE = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")


class RegulatoryError(ValueError):
    """Safe user-facing validation or concurrency failure."""


def clean_text(value: Any, *, max_chars: int = 4000) -> str:
    text = "" if value is None else unicodedata.normalize("NFC", str(value)).strip()
    if len(text) > max_chars:
        raise RegulatoryError(f"Giá trị vượt quá {max_chars} ký tự.")
    return text


def acquire_regulatory_lock(cur) -> None:
    """Serialize status/rule mutations with product imports using those statuses."""
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (REGULATORY_LOCK_KEY,))


def normalize_cas(value: Any) -> str:
    """Validate a single CAS Registry Number and return canonical hyphen form."""
    text = clean_text(value, max_chars=32)
    match = _CAS_RE.fullmatch(text)
    if not match:
        raise RegulatoryError("CAS phải có dạng 2-7 chữ số, dấu gạch, 2 chữ số, dấu gạch, 1 chữ số.")
    body = match.group(1) + match.group(2)
    expected = sum(int(digit) * weight for weight, digit in enumerate(reversed(body), start=1)) % 10
    if expected != int(match.group(3)):
        raise RegulatoryError("CAS không đạt checksum.")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def normalize_match_value(field: str, value: Any) -> str:
    field = clean_text(field, max_chars=16).lower()
    if field not in MATCH_FIELDS:
        raise RegulatoryError("Trường đối chiếu không hợp lệ.")
    if field == "cas":
        return normalize_cas(value)
    text = clean_text(value, max_chars=500)
    if not text:
        raise RegulatoryError(f"{MATCH_FIELD_LABELS[field]} không được để trống.")
    return text


def normalized_identity(value: str) -> str:
    """Exact identity for status labels, CAS and Code; accents are preserved."""
    return unicodedata.normalize("NFC", value).strip().casefold()


def stable_key_for_label(label: str) -> str:
    # Identity must not be recycled when a status is renamed and its old label
    # is later used for a genuinely new status.
    return "CUSTOM_" + uuid.uuid4().hex.upper()


def policy_css(export_policy: Optional[str], stable_key: Optional[str] = None) -> str:
    if export_policy == EXPORT_BLOCK:
        return "warning-cam-nhap"
    known = {
        "PHU_LUC_II": "warning-phu-luc-ii",
        "PHU_LUC_III": "warning-phu-luc-iii",
        "DUOC_BAN": "warning-duoc-ban",
    }
    return known.get(stable_key or "", "warning-regulatory" if stable_key else "")


def resolved_result(
    label: Any = None,
    note: Any = None,
    export_policy: Any = None,
    source: Any = None,
    stable_key: Any = None,
    status_id: Any = None,
) -> dict[str, Any]:
    label_text = clean_text(label) if label is not None else ""
    # Individual rule notes are bounded at ingestion. A resolved product may
    # legitimately aggregate several such notes, so never reapply that
    # per-field limit (or truncate) at the presentation boundary.
    note_text = "" if note is None else unicodedata.normalize("NFC", str(note)).strip()
    policy = str(export_policy or EXPORT_ALLOW)
    result = {
        "compliance": label_text,
        "compliance_note": note_text,
        "compliance_css": policy_css(policy, str(stable_key or "")),
        "compliance_source": str(source or ("automatic" if label_text else "none")),
        "compliance_export_policy": policy,
        "compliance_status_id": status_id,
        "compliance_stable_key": str(stable_key or ""),
    }
    return result


def is_export_blocked(result: dict[str, Any]) -> bool:
    return result.get("compliance_export_policy") == EXPORT_BLOCK


def product_resolver_lateral(
    product_alias: str = "p",
    setting_alias: str = "bcs",
    *,
    manual_enabled_expr: Optional[str] = None,
) -> str:
    """Return the one shared LATERAL resolver used by all product entrypoints.

    Note ordering is lexical after exact-text deduplication. It is deterministic
    and deliberately independent of match field (CAS/Code/Name).
    """
    p = product_alias
    b = setting_alias
    enabled = manual_enabled_expr or f"{b}.manual_compliance_priority"
    return f"""
        LEFT JOIN LATERAL (
            WITH manual_choice AS (
                SELECT s.id AS status_id, s.stable_key, s.label AS rule_label,
                       s.export_policy, NULLIF(btrim({p}.manual_compliance_note), '') AS note,
                       'manual'::text AS source
                FROM regulatory_statuses s
                WHERE COALESCE({enabled}, FALSE)
                  AND NULLIF(btrim(COALESCE({p}.manual_compliance, '')), '') IS NOT NULL
                  AND s.id = {p}.manual_compliance_status_id
            ), matched AS (
                SELECT r.id, r.status_id, NULLIF(btrim(r.note), '') AS note,
                       s.priority, s.stable_key, s.label, s.export_policy
                FROM regulatory_rules r
                JOIN regulatory_statuses s ON s.id = r.status_id
                WHERE r.is_active = TRUE
                  AND (
                    (r.match_field = 'cas' AND NULLIF(btrim({p}.cas), '') IS NOT NULL
                      AND upper(btrim({p}.cas)) = upper(btrim(r.match_value)))
                    OR (r.match_field = 'code' AND NULLIF(btrim({p}.code), '') IS NOT NULL
                      AND upper(btrim({p}.code)) = upper(btrim(r.match_value)))
                    OR (r.match_field = 'name' AND NULLIF(btrim({p}.name), '') IS NOT NULL
                      AND strpos(upper(btrim({p}.name)), upper(btrim(r.match_value))) > 0)
                  )
            ), winning_status AS (
                SELECT status_id FROM matched ORDER BY priority, status_id LIMIT 1
            ), automatic_choice AS (
                SELECT s.id AS status_id, s.stable_key, s.label AS rule_label,
                       s.export_policy,
                       string_agg(DISTINCT m.note, E'\n' ORDER BY m.note)
                           FILTER (WHERE m.note IS NOT NULL) AS note,
                       'automatic'::text AS source
                FROM winning_status w
                JOIN regulatory_statuses s ON s.id = w.status_id
                JOIN matched m ON m.status_id = w.status_id
                GROUP BY s.id, s.stable_key, s.label, s.export_policy
            ), broken_manual AS (
                SELECT NULL::bigint AS status_id, 'DATA_ERROR'::text AS stable_key,
                       'Lỗi dữ liệu quản lý'::text AS rule_label,
                       'BLOCK'::text AS export_policy,
                       'Ngoại lệ thủ công không còn liên kết với danh mục tình trạng.'::text AS note,
                       'error'::text AS source
                WHERE COALESCE({enabled}, FALSE)
                  AND NULLIF(btrim(COALESCE({p}.manual_compliance, '')), '') IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM manual_choice)
            )
            SELECT * FROM manual_choice
            UNION ALL SELECT * FROM broken_manual
            UNION ALL SELECT * FROM automatic_choice
                WHERE NOT EXISTS (SELECT 1 FROM manual_choice)
                  AND NOT EXISTS (SELECT 1 FROM broken_manual)
            LIMIT 1
        ) rr ON TRUE
    """


def cas_resolver_lateral(input_expression: str = "i.cas_value") -> str:
    """CAS-only lookup independent from products and team brand scope."""
    return f"""
        LEFT JOIN LATERAL (
            WITH matched AS (
                SELECT r.status_id, NULLIF(btrim(r.note), '') AS note,
                       s.priority, s.stable_key, s.label, s.export_policy
                FROM regulatory_rules r
                JOIN regulatory_statuses s ON s.id = r.status_id
                WHERE r.is_active = TRUE AND r.match_field = 'cas'
                  AND upper(btrim(r.match_value)) = upper(btrim({input_expression}))
            ), winner AS (
                SELECT status_id FROM matched ORDER BY priority, status_id LIMIT 1
            )
            SELECT s.id AS status_id, s.stable_key, s.label AS rule_label,
                   s.export_policy,
                   string_agg(DISTINCT m.note, E'\n' ORDER BY m.note)
                       FILTER (WHERE m.note IS NOT NULL) AS note,
                   'automatic'::text AS source
            FROM winner w JOIN regulatory_statuses s ON s.id=w.status_id
            JOIN matched m ON m.status_id=w.status_id
            GROUP BY s.id,s.stable_key,s.label,s.export_policy
        ) rr ON TRUE
    """


def catalog_fingerprint(cur) -> str:
    cur.execute(
        """
        SELECT jsonb_build_object(
          'statuses', COALESCE((SELECT jsonb_agg(jsonb_build_array(id,stable_key,label,priority,export_policy)
                                     ORDER BY priority,id) FROM regulatory_statuses), '[]'::jsonb),
          'rules', COALESCE((SELECT jsonb_agg(jsonb_build_array(id,status_id,match_field,match_value,note,is_active)
                                  ORDER BY id) FROM regulatory_rules), '[]'::jsonb)
        )
        """
    )
    payload = cur.fetchone()[0]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
