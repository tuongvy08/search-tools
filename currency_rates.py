"""Central Currency Rate Resolver & Admin Update Helpers (Phase 6B2B2).

Single source of truth for RUNTIME brand -> currency -> rate lookups:

    Product -> brand_master.currency_code -> currency_rates.rate_vnd

This module is the ONLY place new code should compute "what rate applies to
this brand". It intentionally does NOT read the legacy per-brand
`exchange_rates` table for pricing once the new schema (`brand_master` +
`currency_rates`, migrations 017 + 018) is present -- that table is left
untouched purely so a rollback to pre-6B2B2 application code can keep
reading it (see migration_018 header comment).

Backward compatibility (pre-migration-017/018 databases):
    Most of this repository's existing automated tests build a throwaway
    Postgres schema that predates migration_017 (no `brand_master`, no
    `currency_rates` -- see `tests/pg_temp_db.py`). Those fixtures insert
    arbitrary ad-hoc brand strings directly into `products.brand`.

    So: when NEITHER `brand_master` NOR `currency_rates` exists yet (the
    genuine pre-6B2B2 legacy schema), `CurrencyRateResolver` transparently
    falls back to loading the exact same legacy overlay
    `search._exchange_rate_map()` computes (JSON defaults overlaid by
    legacy per-brand `exchange_rates` rows). This mirrors the identical
    compatibility pattern `brand_gateway.BrandGatewayCache` already
    established in Phase 6B2B1 for the exact same reason. As of Phase
    6B2B2-R2, this legacy path is ALSO fail-closed for any brand not
    present in the supplied legacy map: `resolve()` returns
    `is_valid=False`, `rate=None`, `status=LEGACY_RATE_MISSING` for that
    brand instead of the old silent-1.0-compatible default. A caller that
    supplies no `legacy_rate_map`/`legacy_rate_map_loader` at all gets an
    empty map, which means EVERY brand fails closed in that path -- there
    is no code path left that can hand out an implicit rate of 1.0.

    Once BOTH tables exist (any fully-migrated-017+018 database, including
    a real deploy), the resolver switches to strict, fail-closed
    currency-based lookup: an unmapped brand or a currency with no positive
    rate returns `is_valid=False` and `rate=None`. Callers MUST NOT
    substitute 1.0 or any other implicit default in that case -- see
    `search.py`'s pricing call sites for how each surface (Search, Quick
    Quote, export) fails closed instead.

    Partial migration (EXACTLY ONE of the two tables exists) -- Phase
    6B2B2-R2 change: this is NOT treated as the legacy schema anymore. Even
    though migration_017's Section 7 happens to rewrite the legacy
    `exchange_rates` table with correct canonical-brand rates (which made
    the old "bridge" behavior empirically safe), relying on that as a
    resolver-level guarantee was fragile and is no longer how this phase
    defines correctness: the required deployment order already wraps the
    017-then-018 window in maintenance mode (code checkpoint -> backup ->
    maintenance -> 017 -> 018 -> restart), so Search/Quick Quote are not
    expected to serve live pricing traffic during that window. Accordingly,
    `load()` sets `schema_incomplete=True` and `resolve()` returns
    `is_valid=False`, `rate=None`, `status=CURRENCY_SCHEMA_INCOMPLETE` for
    EVERY brand -- no `exchange_rates` read, no static JSON, no rate of any
    kind -- plus a loud operational warning.

    Important: table existence is checked ONCE via `to_regclass()` and is
    the ONLY thing allowed to select between "legacy", "partial/incomplete"
    and "fully migrated". If both tables are confirmed to exist but a
    *subsequent* query against them fails for any other reason (permission
    error, dropped connection, UndefinedTable from a half-applied migration,
    etc.), that is a genuine operational error, not a "legacy schema" or
    "partial migration" signal -- `load()` records it in `load_error` and
    `resolve()` fails closed (`STATUS_RESOLVER_LOAD_ERROR`) for every brand
    on that request instead of silently reusing the legacy JSON/
    `exchange_rates` overlay, which would otherwise quote every non-VND
    brand at an implicit rate of 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional
import re

SEEDED_CURRENCIES = ("VND", "AUD", "USD", "EUR", "GBP")
CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$", re.ASCII)

STATUS_OK = "OK"
STATUS_LEGACY_SCHEMA = "LEGACY_SCHEMA"
STATUS_BRAND_UNKNOWN = "BRAND_UNKNOWN"
STATUS_CURRENCY_MISSING = "CURRENCY_MISSING"
STATUS_RATE_MISSING = "RATE_MISSING"
STATUS_RESOLVER_LOAD_ERROR = "RESOLVER_LOAD_ERROR"
# Legacy (pre-017/018) schema, but the requested brand has no entry at all
# in the legacy overlay map -- fails closed, never silently 1.0.
STATUS_LEGACY_RATE_MISSING = "LEGACY_RATE_MISSING"
# Exactly ONE of `brand_master`/`currency_rates` exists: a partial
# Phase 6B2B2 migration is in progress. Fails closed for every brand.
STATUS_CURRENCY_SCHEMA_INCOMPLETE = "CURRENCY_SCHEMA_INCOMPLETE"

STATUS_LABELS_VI = {
    STATUS_BRAND_UNKNOWN: "Giá không khả dụng",
    STATUS_CURRENCY_MISSING: "Brand chưa được gán tiền tệ; chưa thể tính giá.",
    STATUS_RATE_MISSING: "Tiền tệ của brand chưa có tỷ giá VND hợp lệ.",
    STATUS_RESOLVER_LOAD_ERROR: "Giá không khả dụng",
    STATUS_LEGACY_RATE_MISSING: "Giá không khả dụng",
    STATUS_CURRENCY_SCHEMA_INCOMPLETE: "Giá không khả dụng",
}


def currency_status_label_vi(status: str) -> str:
    return STATUS_LABELS_VI.get(status, "Giá không khả dụng")


@dataclass(frozen=True)
class RateResolution:
    requested_brand: str
    canonical_brand: Optional[str]
    currency_code: Optional[str]
    rate: Optional[Decimal]
    is_valid: bool
    status: str
    source: str


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (table_name,))
    row = cur.fetchone()
    return bool(row and row[0])


class CurrencyRateResolver:
    """Per-request cache of brand->currency and currency->rate maps."""

    def __init__(self) -> None:
        self.schema_ready: bool = False
        self.brand_currency: dict[str, str] = {}
        self.currency_rate: dict[str, Decimal] = {}
        self.legacy_rate_map: dict[str, float] = {}
        self.warnings: list[str] = []
        # Set only when `brand_master`/`currency_rates` are BOTH confirmed to
        # exist but a subsequent query against them still failed (permission
        # error, dropped connection, corrupted catalog, etc.). This is
        # deliberately kept separate from `schema_ready=False` (see `load`
        # below) -- `resolve()` must fail closed for EVERY brand in this
        # case, never silently reinterpret it as "pre-migration-017/018
        # legacy schema" and fall back to an implicit rate.
        self.load_error: Optional[str] = None
        # Set only when EXACTLY ONE of `brand_master`/`currency_rates`
        # exists: a partial Phase 6B2B2 migration is in progress (deploy
        # window between applying migration_017 and migration_018).
        # `resolve()` fails closed for EVERY brand in this case too -- no
        # legacy `exchange_rates` read, no static JSON, no rate of any kind.
        self.schema_incomplete: bool = False

    def load(
        self,
        conn,
        root_path: str,
        legacy_rate_map: Optional[dict] = None,
        legacy_rate_map_loader: Optional[Callable[[], dict]] = None,
    ) -> "CurrencyRateResolver":
        brand_master_exists = False
        currency_rates_exists = False
        try:
            with conn.cursor() as cur:
                # Single round-trip existence check (both tables at once)
                # instead of two separate `to_regclass()` queries -- this
                # runs on every pricing request, so avoiding an extra
                # network round-trip matters.
                cur.execute("SELECT to_regclass('brand_master'), to_regclass('currency_rates')")
                row = cur.fetchone()
                brand_master_exists = bool(row and row[0])
                currency_rates_exists = bool(row and row[1])
        except Exception:
            # Genuinely can't even tell if the tables exist (e.g. the whole
            # connection is unusable) -- treat as "neither exists" same as a
            # pre-migration-017/018 database, which is the only case this
            # existence check itself is expected to ever fail/return NULL.
            brand_master_exists = False
            currency_rates_exists = False

        if brand_master_exists and currency_rates_exists:
            # Both tables are CONFIRMED to exist: this is a fully-migrated
            # schema. Any failure loading their actual contents from here on
            # is an unexpected DB error, not a "legacy schema" signal -- it
            # must NOT be silently folded into the legacy fallback path
            # (that would let `resolve()` quote a foreign-currency brand at
            # rate=1.0, the exact silent-corruption bug this module exists
            # to prevent).
            self.schema_ready = True
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, currency_code FROM brand_master WHERE is_active = TRUE")
                    for name, currency in cur.fetchall():
                        self.brand_currency[(name or "").strip()] = currency

                    cur.execute("SELECT currency_code, rate_vnd FROM currency_rates")
                    for currency, rate in cur.fetchall():
                        try:
                            self.currency_rate[currency] = Decimal(rate)
                        except Exception:
                            continue
            except Exception as e:
                # Tables exist but we could not read them (permission
                # error, dropped connection mid-request, UndefinedTable from
                # a broken partial migration, etc.). Fail closed for every
                # brand this request via `load_error`/
                # STATUS_RESOLVER_LOAD_ERROR instead of falling back to
                # `legacy_rate_map`/static JSON.
                self.load_error = repr(e)
                self.warnings.append(
                    "CurrencyRateResolver: 'brand_master'/'currency_rates' exist but failed to "
                    f"load their contents ({e!r}). Pricing will fail closed (no rate resolved) "
                    "for every brand on this request instead of silently falling back to "
                    "legacy/static rates."
                )
            return self

        if brand_master_exists != currency_rates_exists:
            # Exactly ONE of the two tables exists: a partial Phase 6B2B2
            # migration is in progress (the deploy window between applying
            # migration_017 and migration_018, which the runbook requires
            # to happen behind maintenance mode). This is NOT the legacy
            # pre-6B2B2 schema and must NOT transparently fall back to it:
            # no `exchange_rates` read, no static JSON, no rate resolved
            # for ANY brand -- see module docstring for the Phase 6B2B2-R2
            # rationale for this being a hard fail-closed state rather than
            # a "bridge".
            self.schema_ready = False
            self.schema_incomplete = True
            present = "brand_master" if brand_master_exists else "currency_rates"
            missing = "currency_rates" if brand_master_exists else "brand_master"
            self.warnings.append(
                f"CurrencyRateResolver: '{present}' exists but '{missing}' does not -- "
                "PARTIAL Phase 6B2B2 migration detected. Failing closed (no rate resolved, "
                "no legacy/static fallback) for EVERY brand on this request until both "
                "migration_017_brand_master.sql and migration_018_currency_rates.sql are "
                "applied. This state must only ever occur transiently behind maintenance "
                "mode during a deploy, never as steady-state production traffic."
            )
            return self

        # Neither table exists: genuine pre-migration-017/018 legacy
        # schema (see module docstring). The loader is called lazily --
        # NEVER when either table exists -- so a fully-migrated or
        # partially-migrated request never pays for (or is influenced by)
        # an `exchange_rates` table scan + JSON file read.
        self.schema_ready = False
        if legacy_rate_map is not None:
            self.legacy_rate_map = dict(legacy_rate_map)
        elif legacy_rate_map_loader is not None:
            self.legacy_rate_map = dict(legacy_rate_map_loader() or {})
        else:
            self.legacy_rate_map = {}
        return self

    def resolve(self, raw_brand) -> RateResolution:
        bkey = ("" if raw_brand is None else str(raw_brand)).strip()

        if self.load_error is not None:
            # `brand_master`/`currency_rates` exist but failed to load --
            # fail closed for every brand, never substitute legacy/static
            # data or an implicit 1.0 (see `load()` for the full rationale).
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=None,
                currency_code=None,
                rate=None,
                is_valid=False,
                status=STATUS_RESOLVER_LOAD_ERROR,
                source="none",
            )

        if self.schema_incomplete:
            # Exactly one of `brand_master`/`currency_rates` exists --
            # partial migration in progress. Fail closed for every brand:
            # no legacy `exchange_rates` read, no static JSON, no rate.
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=None,
                currency_code=None,
                rate=None,
                is_valid=False,
                status=STATUS_CURRENCY_SCHEMA_INCOMPLETE,
                source="none",
            )

        if not self.schema_ready:
            # Legacy/back-compat path for pre-migration-017/018 databases
            # only (see module docstring). As of Phase 6B2B2-R2 this no
            # longer substitutes an implicit rate of 1.0 for a brand that
            # is not present in the supplied legacy map -- it fails closed
            # instead, exactly like the fully-migrated path does for an
            # unknown brand.
            raw_rate = self.legacy_rate_map.get(bkey)
            if raw_rate is None:
                return RateResolution(
                    requested_brand=bkey,
                    canonical_brand=bkey or None,
                    currency_code=None,
                    rate=None,
                    is_valid=False,
                    status=STATUS_LEGACY_RATE_MISSING,
                    source="none",
                )
            try:
                rate_dec = Decimal(str(raw_rate))
            except Exception:
                rate_dec = None
            if rate_dec is None or rate_dec <= 0:
                return RateResolution(
                    requested_brand=bkey,
                    canonical_brand=bkey or None,
                    currency_code=None,
                    rate=None,
                    is_valid=False,
                    status=STATUS_LEGACY_RATE_MISSING,
                    source="none",
                )
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=bkey or None,
                currency_code=None,
                rate=rate_dec,
                is_valid=True,
                status=STATUS_LEGACY_SCHEMA,
                source="legacy_exchange_rates",
            )

        if bkey not in self.brand_currency:
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=None,
                currency_code=None,
                rate=None,
                is_valid=False,
                status=STATUS_BRAND_UNKNOWN,
                source="none",
            )

        currency = self.brand_currency[bkey]
        if not currency:
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=bkey,
                currency_code=None,
                rate=None,
                is_valid=False,
                status=STATUS_CURRENCY_MISSING,
                source="none",
            )

        rate = self.currency_rate.get(currency)
        if rate is None or rate <= 0:
            return RateResolution(
                requested_brand=bkey,
                canonical_brand=bkey,
                currency_code=currency,
                rate=None,
                is_valid=False,
                status=STATUS_RATE_MISSING,
                source="none",
            )

        return RateResolution(
            requested_brand=bkey,
            canonical_brand=bkey,
            currency_code=currency,
            rate=Decimal(rate),
            is_valid=True,
            status=STATUS_OK,
            source="currency_rates",
        )

    def get(self, raw_brand) -> Optional[Decimal]:
        """Convenience accessor for simple call sites: returns the rate or
        None (NEVER a silent 1.0 default when `schema_ready` is True)."""
        return self.resolve(raw_brand).rate


def load_currency_rate_resolver(
    conn,
    root_path: str,
    legacy_rate_map: Optional[dict] = None,
    legacy_rate_map_loader: Optional[Callable[[], dict]] = None,
) -> CurrencyRateResolver:
    return CurrencyRateResolver().load(
        conn, root_path, legacy_rate_map=legacy_rate_map, legacy_rate_map_loader=legacy_rate_map_loader
    )


# ---------------------------------------------------------------------------
# Admin update helpers (used by the redesigned /admin/exchange-rates route)
# ---------------------------------------------------------------------------


class CurrencyRateError(ValueError):
    pass


def normalize_currency_code(currency_code: str) -> str:
    code = (currency_code or "").strip().upper()
    if not CURRENCY_CODE_RE.fullmatch(code):
        raise CurrencyRateError("Mã tiền tệ phải gồm đúng 3 chữ cái ASCII viết hoa, ví dụ JPY.")
    return code


def fetch_currency_rate_rows(conn) -> list[dict]:
    """Returns all dynamic currency rows for the admin currency table,
    each with the count of active canonical brands currently using it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                cr.currency_code,
                cr.rate_vnd,
                cr.updated_at,
                COALESCE(bm.brand_count, 0) AS brand_count
            FROM currency_rates cr
            LEFT JOIN (
                SELECT currency_code, COUNT(*) AS brand_count
                FROM brand_master
                WHERE is_active = TRUE
                GROUP BY currency_code
            ) bm ON bm.currency_code = cr.currency_code
            ORDER BY CASE cr.currency_code
                WHEN 'VND' THEN 0 WHEN 'USD' THEN 1 WHEN 'EUR' THEN 2
                WHEN 'GBP' THEN 3 WHEN 'AUD' THEN 4 ELSE 5 END
            """
        )
        rows = cur.fetchall()
    return [
        {
            "currency_code": r[0],
            "rate_vnd": r[1],
            "updated_at": r[2],
            "brand_count": int(r[3] or 0),
        }
        for r in rows
    ]


def apply_currency_rate_update(conn, currency_code: str, new_rate: Decimal, actor_user_id: Optional[int], source: str = "ADMIN_UI") -> Decimal:
    """Updates a single currency's rate with row-level locking + audit.

    Must run inside a transaction the caller commits (`with conn: ...`).
    Locks the target row with SELECT ... FOR UPDATE before updating so two
    concurrent admin submissions can never race each other's history entry.

    Raises `CurrencyRateError` for any business-rule violation (unknown
    currency, VND changed away from 1, non-positive rate) -- the DB-level
    CHECK constraints are the last line of defense, but the app validates
    first so the admin sees a clear Vietnamese message instead of a raw
    IntegrityError.
    """
    code = normalize_currency_code(currency_code)
    if code == "VND":
        raise CurrencyRateError("VND luôn cố định bằng 1 và không thể chỉnh sửa.")
    if new_rate is None or new_rate <= 0:
        raise CurrencyRateError("Tỷ giá phải là số dương lớn hơn 0.")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT rate_vnd FROM currency_rates WHERE currency_code = %s FOR UPDATE",
            (code,),
        )
        row = cur.fetchone()
        if row is None:
            raise CurrencyRateError(f"Currency '{code}' chưa tồn tại trong currency_rates.")
        old_rate = row[0]

        cur.execute(
            """
            UPDATE currency_rates
            SET rate_vnd = %s, updated_at = NOW(), updated_by = %s, update_source = %s
            WHERE currency_code = %s
            """,
            (new_rate, actor_user_id, source, code),
        )
        cur.execute(
            """
            INSERT INTO currency_rate_history (currency_code, old_rate, new_rate, actor_user_id, source)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (code, old_rate, new_rate, actor_user_id, source),
        )
    return new_rate


def apply_currency_create(
    conn,
    currency_code: str,
    rate_vnd: Decimal,
    actor_user_id: Optional[int],
    source: str = "ADMIN_UI",
) -> str:
    """Create one dynamic currency and its initial audit row atomically."""
    code = normalize_currency_code(currency_code)
    if code == "VND" and rate_vnd != 1:
        raise CurrencyRateError("VND luôn cố định bằng 1.")
    if rate_vnd is None or rate_vnd <= 0:
        raise CurrencyRateError("Tỷ giá phải là số dương lớn hơn 0.")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO currency_rates
                (currency_code, rate_vnd, updated_at, updated_by, update_source)
            VALUES (%s, %s, NOW(), %s, %s)
            ON CONFLICT (currency_code) DO NOTHING
            RETURNING currency_code
            """,
            (code, rate_vnd, actor_user_id, source),
        )
        if cur.fetchone() is None:
            raise CurrencyRateError(f"Currency '{code}' đã tồn tại.")
        cur.execute(
            """
            INSERT INTO currency_rate_history
                (currency_code, old_rate, new_rate, actor_user_id, source)
            VALUES (%s, NULL, %s, %s, %s)
            """,
            (code, rate_vnd, actor_user_id, source),
        )
    return code


def fetch_brand_currency_rows(conn, search_query: Optional[str] = None, currency_filter: Optional[str] = None) -> list[dict]:
    """Returns canonical brand rows (id, name, currency_code) for the brand
    mapping admin table, optionally filtered by name substring / currency.
    """
    sql = "SELECT id, name, currency_code FROM brand_master WHERE is_active = TRUE"
    params: list = []
    if search_query:
        sql += " AND name ILIKE %s"
        params.append(f"%{search_query.strip()}%")
    if currency_filter:
        code = currency_filter.strip().upper()
        if CURRENCY_CODE_RE.fullmatch(code):
            sql += " AND currency_code = %s"
            params.append(code)
        elif code == "__UNASSIGNED__":
            sql += " AND currency_code IS NULL"
    sql += " ORDER BY name ASC"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "currency_code": r[2]} for r in rows]


def apply_brand_currency_update(conn, brand_id: int, new_currency_code: str, actor_user_id: Optional[int]) -> str:
    """Reassigns a canonical brand's currency, with row locking + audit.

    Must run inside a transaction the caller commits. Takes effect
    immediately for the next read (no product rows are touched -- pricing
    is resolved live from `brand_master.currency_code` + `currency_rates`).
    """
    code = normalize_currency_code(new_currency_code)

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM currency_rates WHERE currency_code = %s", (code,))
        if cur.fetchone() is None:
            raise CurrencyRateError(f"Currency '{code}' chưa tồn tại trong Currency Master.")
        cur.execute(
            "SELECT id, currency_code FROM brand_master WHERE id = %s FOR UPDATE",
            (brand_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise CurrencyRateError(f"Brand id={brand_id} không tồn tại trong brand_master.")
        old_currency = row[1]

        cur.execute(
            "UPDATE brand_master SET currency_code = %s, updated_at = NOW() WHERE id = %s",
            (code, brand_id),
        )
        cur.execute(
            """
            INSERT INTO brand_currency_history (brand_id, old_currency_code, new_currency_code, actor_user_id)
            VALUES (%s, %s, %s, %s)
            """,
            (brand_id, old_currency, code, actor_user_id),
        )
    return code
