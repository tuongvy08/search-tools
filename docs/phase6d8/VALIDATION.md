# Phase 6D8 validation

## Automated evidence

- Final focused PostgreSQL 14 disposable + DOM/static gate: 21/21 passed.
- Final full suite after completed-mode reset, tracked Check License and `COLLATE "C"`: 1,110 tests in 81.295s; 1,097 passed, 13 skipped, 0 failures/errors.
- Coordinator independently reran the final full suite on a separate disposable PostgreSQL 14 instance: 1,110 tests in 86.645s; 1,097 passed, the same 13 skipped, zero failures/errors. Independent bounded re-review found no remaining blockers after all five P2 findings were addressed; actual-function DOM tests also passed. This does not replace browser/mobile staging UAT.
- Skips: three external `QUOTE_TEMPLATE_FIXTURE` cases and ten production-style `psql` runner cases; host `psql` was unavailable.
- Syntax gates: `node --check`, Python `py_compile`, and `git diff --check`.

Covered cases include filter off/absent, invalid/blank input, code/CAS/both/dedup, positive versus zero quantity, inactive and concurrently replaced snapshots, team/brand and field/CAS grants, no stock, stock-only name/code, direct cap, literal `%`/`_`, expiry warnings, export blocking and static versions.

Unicode code-only cases deliberately use different CAS values and omit CAS grants: composed/decomposed accents, `ß/SS`, `ﬀ/ff`, `İ/i` plus combining dot, control-tab trimming and dotless `ı`. Negative Unicode mismatch and empty normalized CAS are also covered.

DOM coverage executes the production functions for Product Search, Advanced Search, Find Code and Check License: abort/generation ordering, scheduled Find Code invalidation, completed alternate-mode reset, selection reset and blank-query toggle behavior.

## Synthetic benchmark

Run with `scripts/benchmark_stock_only_filter.py` against a disposable PostgreSQL 14 database. The dataset contains 1,000,000 products and 10,000 active plus 10,000 historical stock rows. These local warm-cache measurements are not a production SLO.

- Representative ASCII query: 1,000 catalog candidates and 90 positive matches. Product GIN narrows 1M rows to 1,000; hashed stock membership removes 910 before compliance resolution.
- Separate no-CAS query: 1,000 candidates, zero filtered products and zero resolver loops.
- Final local run: EXPLAIN off 15.330 ms and on 18.239 ms. Warm service medians were off 59.41 ms/1,000 rows and on 46.92 ms/90 rows.
- Unicode-heavy negative slow lane: 1,000 candidates deliberately reach the resolver, service 53.90 ms, exact matcher returns zero. This is the conservative-gate cost, not a claim that Unicode-heavy queries receive the ASCII fast-path reduction.
- Non-admin team with Brand A visibility: median 77.26 ms/90 rows over three warm samples.
- No-CAS negative: 1,000 candidates filtered to zero before resolver; service result count zero.

Evidence sources retained in the worktree: `scripts/benchmark_stock_only_filter.py`, `tests/test_search_stock_suggest.py`, `tests/search_stock_only_dom_test.js`, and this validation record. Raw command output remains in the local task transcript and is intentionally not committed as a large log.
