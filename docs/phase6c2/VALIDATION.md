# Phase 6C2 validation — 2026-09-08

READY FOR STAGING REVIEW. No deployment or migration to an application database.

## Isolation

Base: `25ca3d94d3222f08f9373df09a32e78129b7284c` (`origin/main`). Feature branch:
`codex/phase6c2-admin-import-center`, isolated worktree. A new PostgreSQL 16 Alpine
container, `search-tools-phase6c2-test`, bound only to loopback port 55462, held all
temporary databases. Existing containers, `products_local`, staging, production,
VPS files and secrets were not accessed. The local psql wrapper pointed exclusively
to that disposable container and accepted only helper-generated test database names.

## Test results

- Final focused new import suite: **19 passed**. Covers admin/CSRF and live role
  recheck, durable preview, duplicate admission, new brand currency NULL/no team
  grant, exact deletion count confirmation, stale same-count catalogs, alias/source
  replacement, preservation of optional controls, upsert ambiguity/duplicate
  rejection, queued/running cancellation and rollback, exclusive worker claims,
  session-lock crash recovery, shared product-lock concurrency, rollback of new
  brands, bounded safe errors, expiry cleanup, signed quick-delete scope checks,
  rules preview/apply with numeric/boolean Excel cells, formulas, traversal, XML
  entities, ZIP expansion and adversarial content-type/metadata layouts.
- CLI focused regression: **11 passed**, including historical full replacement,
  append/replace/dry-run and canonical migration guards. Additional manual-control,
  Brand Gateway/currency and admin shell focused suites passed.
- **Full Python suite executed exactly once: 939 tests in 59.2 seconds.** It had
  35 environment-dependent failures and 3 pre-existing optional fixture skips.
  All 35 failures were HTTP 503 responses in three older mocked admin/export
  modules whose fixtures do not set `DISABLE_IP_ALLOWLIST=1`. Running those same
  three modules from the unchanged base commit reproduced the identical 35
  failures. With that documented local test setting, a focused rerun of the three
  affected modules passed **83 tests (3 optional fixture skips)**. No second full
  run was performed and no production code was changed after the full run.
- The three skips require an external `QUOTE_TEMPLATE_FIXTURE`, unrelated to
  product imports. Real representative workbook/template fixtures remain staging
  UAT requirements; no private fixture was read to fill that gap.
- JavaScript syntax checks passed once for the new polling module and modified
  legacy import inline script. Live browser: local admin login, actual XLSX upload,
  queued-to-preview polling, new-brand notice, apply/completion, mobile separate
  deletion confirmation, keyboard disclosure and no captured console errors.
- DOM checks: `scrollWidth === innerWidth` at **1440** and **390** CSS pixels;
  no nested cards. Native browser captures are in this directory. The in-app
  browser's high-DPI capture has extra blank canvas space; DOM geometry, not
  screenshot scaling, establishes the tested viewport dimensions.

## Performance and migration rehearsal

`rehearsal.json` records a synthetic single-sheet workbook with 200,000 distinct
product codes, 5,062,266 compressed bytes. Preview: 16.42s; initial apply: 3.47s;
second populated-catalog upsert preview: 19.67s; update apply: 6.59s. Exactly
200,000 inserted, then exactly 200,000 updated, final count 200,000. Peak Python
RSS: 74.4 MiB on the local Mac; this is not a staging throughput guarantee and
uses inline strings. Shared-string-heavy vendor files need staging measurement.

Migration 024 was applied **twice through real `psql -X -v ON_ERROR_STOP=1 -f`**
with ordinary psql transaction semantics. After rehearsal: zero staged rows,
zero retained UUID uploads, and database absence verified in `pg_database`.
The separate browser fixture also verified its temporary database removal.
Final maintenance query found zero non-system databases. The dedicated test
container and its disposable volume, browser server, synthetic upload and baseline
comparison checkout were removed.

## Independent review

A separate review agent examined security and correctness without editing files
or accessing databases. All findings were fixed and independently rechecked:
undefined rules-preview variable; native numeric/boolean rule cell normalization;
bounded legacy workbook parsing; renamed/mislabeled XML metadata expansion bypass.
Signed delete confirmation, locking, cancellation, atomic completion, recovery,
and cleanup passed review. Final reviewer reported no outstanding actionable
findings. Regression tests cover each reported parser/route issue.

## Operator handoff

Read `OPERATIONS.md`, `.env.example`, migration 024 and `deploy/` examples.
Review shared web/worker DATABASE_URL source explicitly; the running production
systemd environment historically differed from `/opt/search-tools-pg/.env`.
Provision private shared upload disk, PostgreSQL temp/WAL capacity, worker resource
limits and matching proxy limits. Staging UAT must include representative large
workbooks, Google admin/staff authorization, non-default currency assignment,
kill/restart, cancellation, outage/retention and concurrent search load.
