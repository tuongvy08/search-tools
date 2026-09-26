# Phase 6D9 validation

## Automated

- Coordinator final independent PostgreSQL 14 run on the frozen UI: `1121` tests in `86.550s`, `OK (skipped=13)` — `1108` passed. Remaining skips require external workbook fixtures or the host psql/migration runner. Only a disposable local database was used; no staging/production connection.
- Coordinator final focused UI/static/search/stock/DOM suite: `32` tests, all passed, no skips, after the mobile touch-target and fixture corrections.
- Independent re-review: no remaining blocking findings. Earlier empty-filter helper, fixture wire-field, desktop density and CAS/price wrapping findings were corrected. Mobile actions were subsequently measured at 44px and covered by a scoped regression test.
- Local full Python suite: `1121` tests discovered, `OK`, `454` skipped because this checkout does not have the optional local PostgreSQL / external workbook fixtures. This is a local regression signal, not a final PG/release-quality claim.
- Focused UI/RBAC/search/quote suite: `343` tests, `OK`, `30` skipped for the same external-runtime gates.
- Eight existing JavaScript DOM harnesses completed successfully, including Phase 6D8 stock-only race/reset/Check License behavior and Phase 6D7 suggestions debounce/abort/IME/keyboard behavior.
- `git diff --check`: clean.

The focused suite includes the new Phase 6D9 gates for:

- page-only asset ordering and cache version;
- unique interaction IDs and preserved `.search-container` / `.filter-container` hooks;
- stock checkbox label/description relationship;
- result actions outside the hidden `selectionBar`;
- scoped CSS and no grant-dependent table `nth-child` assumptions;
- restricted staff, no-SEARCH, COPY-only, and EXPORT-only server-rendered variants.

## Real browser QA

The local-only fixture `tests/phase6d9_visual_fixture.py` renders the real `templates/index.html` and real static assets with synthetic permissions/results. It imports no production app, opens no database, and creates no authentication bypass in production code.

Verified in the Codex in-app browser:

- Desktop 1440 CSS px: initial, results-heavy, long identity/team/template/filter/product/note strings, selected row, and backend-supported `current`, `near_expiry`, and `missing` stock states.
- In the 1440×900 results-heavy selected state, table top is `695.36px`, header bottom `735.26px`, and the first data row is visible in the same viewport. CAS is `92px` wide and `75-21-8` remains intact; Unit_Price is `100px` wide and `496,000` remains intact.
- Mobile 390 CSS px: stacked cards/actions, long identity wrap, no page-level horizontal overflow, and table-only horizontal scrolling (`337px` client / `1705px` scroll width in the full-permission fixture).
- At mobile breakpoints, query box is `54px`, Search is `44px`, and every rendered utility/result action is `44px` high.
- Tablet/effective 200% layout gate at 720 CSS px: one-column filters/actions, internal table scroll, no page overflow, and 3px keyboard focus outline. The browser surface did not expose a persistent page-zoom API, so this uses the equivalent 1440-at-200%-CSS viewport rather than claiming a native zoom toggle.
- Autocomplete popup: visible inside viewport, `z-index: 120`, query card `overflow: visible`, Escape dismissal.
- Desktop admin disclosure: visible when opened; Escape closes it and returns focus to the trigger.
- Mobile hamburger: admin links render flat, desktop admin trigger is hidden, no horizontal overflow.
- Restricted profiles: no-SEARCH shows only Find Code and omits query/stock-only; no-COPY/EXPORT omits both result actions and admin quote context.
- Loading and error statuses render with the established live-region classes and visible semantic colors.
- The fixture uses numeric `product_id` for catalog rows, `product_id=None` for the stock-only row, supported `exact_code`/`same_cas`/`name` match values, `Compliance_Status`, and renderer-supported custom Bg/Fg fields.
- The custom compliance badge renders text `Kiểm soát đặc biệt nhóm 1` with `rgb(237, 233, 254)` background and `rgb(91, 33, 182)` foreground. The selected regulatory row retains the same background/text/badge colors while hovered.

## Evidence

- `docs/phase6d9/screenshots/search-workbench-1440-initial.png`
- `docs/phase6d9/screenshots/search-workbench-1440-results-selected.png`
- `docs/phase6d9/screenshots/search-workbench-390-results-selected.png`
- `docs/phase6d9/screenshots/search-workbench-390-results-table.png`

Browser screenshots are synthetic-data evidence only; they are not staging or production UAT.
