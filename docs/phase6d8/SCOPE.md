# Phase 6D8 — Product Search stock-only filter

Status: local implementation only; review/staging UAT/production approval pending.

## Contract

- Product Search alone exposes a default-off checkbox: **Chỉ hiện kết quả có tồn kho**.
- A catalog result qualifies only when active-snapshot stock visible to the current user has `quantity > 0` and matches the existing code matcher, or the existing CAS matcher when both `SEARCH_BY_CAS` and `VIEW_CAS` are granted.
- Stock-only Phase 6D7 name/code results remain available when positive and visible. They remain ineligible for quote export.
- Expiry is informational: expired/near-expiry warnings remain visible and are not sale-eligibility decisions.
- Find Code, Advanced Search, Quick Quote and Check License business semantics are unchanged.

## Safety boundaries

- `GET /search` accepts `in_stock_only=0|1`; invalid values and true+blank query fail closed.
- The enabled path uses one `REPEATABLE READ READ ONLY` transaction for catalog qualification and bulk stock options.
- Team/brand visibility and `quantity > 0` are applied before membership matching and the direct-stock 1,000-row limit.
- Printable ASCII code/CAS uses `COLLATE "C"`, ASCII lower and space trim for hashed SQL membership. Non-ASCII/control-whitespace product identities use a conservative SQL slow lane, followed by the exact application NFC+strip+casefold matcher.
- Empty normalized CAS values never qualify a catalog row.
- No migration, new index, cross-team cache or stock-revision cache is introduced.
