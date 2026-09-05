"""Brand Gateway & Import Safety Module (Phase 6B2B1-C).

Enforces canonical brand normalization, alias resolution, ambiguous match detection,
and safe replacement scoping for all product imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Fixed advisory-lock key used to serialize EVERY product-mutating import
# apply path (bulk upsert/replace_by_brand/append via /admin/imports/apply,
# and single-row quick-product upsert/delete). Any transaction that may
# INSERT/UPDATE/DELETE `products` as part of the import/quick-edit flow MUST
# call `acquire_products_import_lock(cur)` as its very first statement,
# before candidate scans, ambiguity checks, or scope/count computation.
#
# `pg_advisory_xact_lock` is session/transaction-scoped and auto-releases at
# COMMIT/ROLLBACK -- it only serializes OTHER transactions that also request
# this exact key. It does NOT block plain reads/writes from sessions that
# never call it (e.g. the standalone `scripts/import_excel.py` CLI tool,
# which bypasses the Brand Gateway entirely and is intentionally NOT part of
# this lock's coordination domain -- see Phase 6B2B1-E report).
PRODUCTS_IMPORT_LOCK_KEY = 872316401


def acquire_products_import_lock(cur) -> None:
    """Acquires the transaction-scoped products-import advisory lock.

    Must be called first, before any candidate scan or mutation, inside the
    same DB transaction that will perform the import apply / quick-product
    upsert or delete. Blocks until any other transaction holding the same
    key commits or rolls back; automatically released at end of transaction.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))


@dataclass(frozen=True)
class BrandResolution:
    canonical_brand: Optional[str]
    currency_code: Optional[str]
    source_brand: Optional[str]
    matched_alias: Optional[str]
    is_valid: bool
    error_message: Optional[str] = None


class BrandGatewayCache:
    """In-memory cache of canonical brands and aliases loaded from PostgreSQL."""

    def __init__(self) -> None:
        self._aliases: dict[str, tuple[str, str, str]] = {}
        self._canonical: dict[str, tuple[str, str]] = {}
        self._loaded: bool = False
        self._table_exists: bool = False

    def load(self, cur) -> None:
        """Loads canonical brands and aliases from database."""
        self._aliases.clear()
        self._canonical.clear()

        # Check if table brand_master exists
        cur.execute("SELECT to_regclass('brand_master')")
        row = cur.fetchone()
        if not row or not row[0]:
            self._table_exists = False
            self._loaded = True
            return

        self._table_exists = True

        # Load canonical brands
        cur.execute(
            """
            SELECT normalized_name, name, currency_code
            FROM brand_master
            WHERE is_active = TRUE
            """
        )
        for norm_name, name, curr in cur.fetchall():
            self._canonical[norm_name.strip().upper()] = (name, curr)

        # Load aliases if table exists
        cur.execute("SELECT to_regclass('brand_aliases')")
        alias_row = cur.fetchone()
        if alias_row and alias_row[0]:
            try:
                cur.execute(
                    """
                    SELECT a.normalized_alias, bm.name, bm.currency_code, a.alias
                    FROM brand_aliases a
                    JOIN brand_master bm ON a.brand_id = bm.id
                    WHERE a.is_active = TRUE AND bm.is_active = TRUE
                    """
                )
                for norm_alias, can_name, curr, orig_alias in cur.fetchall():
                    self._aliases[norm_alias.strip().upper()] = (can_name, curr, orig_alias)
            except Exception:
                pass

        self._loaded = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def table_exists(self) -> bool:
        return self._table_exists

    def resolve(self, raw_brand: Any, raw_source_brand: Any = None) -> BrandResolution:
        brand_str = "" if raw_brand is None else str(raw_brand).strip()
        if not brand_str:
            return BrandResolution(
                canonical_brand=None,
                currency_code=None,
                source_brand=None,
                matched_alias=None,
                is_valid=False,
                error_message="Trường brand không được để trống.",
            )

        source_str = "" if raw_source_brand is None else str(raw_source_brand).strip()

        # If brand_master table does not exist in target database, passthrough
        if not self._table_exists:
            return BrandResolution(
                canonical_brand=brand_str,
                currency_code=None,
                source_brand=source_str or brand_str,
                matched_alias=None,
                is_valid=True,
            )

        norm_key = brand_str.upper()

        # Check aliases first
        if norm_key in self._aliases:
            can_name, curr, orig_alias = self._aliases[norm_key]
            # If explicit source_brand provided in file, use it; otherwise use the alias string
            effective_source = source_str if source_str else orig_alias
            return BrandResolution(
                canonical_brand=can_name,
                currency_code=curr,
                source_brand=effective_source,
                matched_alias=orig_alias,
                is_valid=True,
            )

        # Check canonical master
        if norm_key in self._canonical:
            can_name, curr = self._canonical[norm_key]
            effective_source = source_str if source_str else can_name
            return BrandResolution(
                canonical_brand=can_name,
                currency_code=curr,
                source_brand=effective_source,
                matched_alias=can_name,
                is_valid=True,
            )

        return BrandResolution(
            canonical_brand=None,
            currency_code=None,
            source_brand=None,
            matched_alias=None,
            is_valid=False,
            error_message=f"Brand không tồn tại trong danh mục Brand Master: '{brand_str}'.",
        )


def load_brand_gateway(cur) -> BrandGatewayCache:
    """Helper to instantiate and populate a BrandGatewayCache."""
    cache = BrandGatewayCache()
    cache.load(cur)
    return cache


def validate_import_rows_brands(
    rows: list[dict], gateway: BrandGatewayCache
) -> tuple[list[dict], list[str]]:
    """Validates and normalizes brand/source_brand for every row in the import batch.

    Returns:
        (resolved_rows, errors): If errors is non-empty, import must fail closed.
    """
    resolved_rows = []
    errors = []

    for idx, r in enumerate(rows, start=2):
        raw_brand = r.get("brand")
        raw_source = r.get("source_brand")

        res = gateway.resolve(raw_brand, raw_source)
        if not res.is_valid:
            errors.append(f"Dòng {idx}: {res.error_message}")
        else:
            row_copy = dict(r)
            row_copy["canonical_brand"] = res.canonical_brand
            row_copy["source_brand"] = res.source_brand
            # Always ensure brand field in row is canonical for downstream consistency
            row_copy["brand"] = res.canonical_brand
            resolved_rows.append(row_copy)

    return resolved_rows, errors


def resolve_product_candidates(
    cur,
    code: str,
    canonical_brand: str,
    source_brand: Optional[str] = None,
    size: Optional[str] = None,
) -> list[tuple]:
    """Finds matching product candidates for upsert without using arbitrary LIMIT 1.

    Returns list of candidate product rows: (id, name, code, cas, brand, size, ship, price, note, source_brand).
    - If empty: new product to insert.
    - If 1 item: unambiguous match to update.
    - If > 1 items: ambiguous match (caller must NOT update arbitrarily).
    """
    if not code or not canonical_brand:
        return []

    cur.execute(
        """
        SELECT id, name, code, cas, brand, size, ship, price, note, source_brand
        FROM products
        WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
          AND UPPER(TRIM(brand)) = UPPER(TRIM(%s))
        """,
        (code, canonical_brand),
    )
    candidates = cur.fetchall()
    if len(candidates) <= 1:
        return candidates

    # Multiple candidates exist. Attempt disambiguation using source_brand
    if source_brand and source_brand.strip():
        norm_source = source_brand.strip().upper()
        by_source = [
            c for c in candidates
            if (c[9] or "").strip().upper() == norm_source
        ]
        if len(by_source) == 1:
            return by_source
        if len(by_source) > 1:
            candidates = by_source

    # Attempt disambiguation using size
    if size and size.strip():
        norm_size = size.strip().upper()
        by_size = [
            c for c in candidates
            if (c[5] or "").strip().upper() == norm_size
        ]
        if len(by_size) == 1:
            return by_size
        if len(by_size) > 1:
            candidates = by_size

    return candidates


def inspect_replace_by_brand_scopes(
    cur,
    rows: list[dict],
    gateway: BrandGatewayCache,
) -> tuple[dict[str, set[str]], list[str], int]:
    """Inspects replace_by_brand scopes to prevent catastrophic over-deletion.

    Rules:
    - Canonical brand with multiple source_brands in DB requires explicit source_brand scope.
    - Brand with single source_brand in DB is permitted if unambiguous.
    - Returns (brand_to_sources_map, errors, total_rows_to_delete).
    """
    # Group file rows by (canonical_brand, source_brand)
    brand_to_sources: dict[str, set[str]] = {}
    for r in rows:
        res = gateway.resolve(r.get("brand"), r.get("source_brand"))
        if not res.is_valid:
            continue
        c_brand = res.canonical_brand
        s_brand = res.source_brand
        if c_brand not in brand_to_sources:
            brand_to_sources[c_brand] = set()
        if s_brand:
            brand_to_sources[c_brand].add(s_brand)

    errors = []
    total_deletable = 0

    for can_brand, file_sources in brand_to_sources.items():
        # Query distinct source_brands in database for this canonical brand
        cur.execute(
            """
            SELECT DISTINCT COALESCE(source_brand, brand)
            FROM products
            WHERE UPPER(TRIM(brand)) = UPPER(TRIM(%s))
            """,
            (can_brand,),
        )
        db_sources = {row[0] for row in cur.fetchall() if row[0]}

        if len(db_sources) > 1:
            # Multi-source brand: file MUST specify which source_brand(s) to replace
            # If the file didn't specify any source or file_sources is empty
            if not file_sources or file_sources == {can_brand}:
                errors.append(
                    f"Mode replace_by_brand bị từ chối: Canonical brand '{can_brand}' có {len(db_sources)} "
                    f"nguồn catalog (source_brand) khác nhau trong hệ thống. "
                    f"Cần chỉ định rõ source_brand hoặc alias cụ thể trong file để tránh xóa nhầm toàn bộ brand."
                )
                continue

            # Check rows that will be deleted for specified sources
            cur.execute(
                """
                SELECT COUNT(*)
                FROM products
                WHERE UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(COALESCE(source_brand, brand))) = ANY(%s)
                """,
                (can_brand, [s.strip().upper() for s in file_sources]),
            )
            count = cur.fetchone()[0]
            total_deletable += count
        else:
            # Single-source brand: safe to replace all products of this canonical brand
            cur.execute(
                """
                SELECT COUNT(*)
                FROM products
                WHERE UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                """,
                (can_brand,),
            )
            count = cur.fetchone()[0]
            total_deletable += count

    return brand_to_sources, errors, total_deletable


def resolve_replace_by_brand_target_ids(
    cur,
    brand_to_sources: dict[str, set[str]],
) -> dict[str, list[int]]:
    """Resolves the EXACT product IDs to delete for each canonical brand scope.

    MUST be called while holding the products-import advisory lock, and as close
    as possible to the immediately-following DELETE statement. This guarantees
    the DELETE only ever targets the precise row set that was just verified
    (never a broader predicate-based scope that a concurrent write landing
    between "compute scope" and "delete" could silently widen or narrow).
    """
    result: dict[str, list[int]] = {}
    for can_brand, sources in brand_to_sources.items():
        if sources:
            cur.execute(
                """
                SELECT id FROM products
                WHERE UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(COALESCE(source_brand, brand))) = ANY(%s)
                """,
                (can_brand, [s.strip().upper() for s in sources]),
            )
        else:
            cur.execute(
                """
                SELECT id FROM products
                WHERE UPPER(TRIM(brand)) = UPPER(TRIM(%s))
                """,
                (can_brand,),
            )
        result[can_brand] = [row[0] for row in cur.fetchall()]
    return result
