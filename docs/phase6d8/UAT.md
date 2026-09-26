# Phase 6D8 staging UAT

Do not use production for this checklist. Phase 6D7 production cutover/UAT must be verified independently; it is not implied by this phase.

1. Confirm the checkbox is off on first load and ordinary Product Search matches the accepted Phase 6D7 behavior.
2. Search a catalog product with positive exact-code stock; enable the checkbox and confirm `Khớp code`, quantity and expiry warning details.
3. With both CAS grants, verify a same-CAS result shows `Cùng CAS — cần đối chiếu` and the non-equivalence disclaimer. Remove either grant and verify CAS stock does not qualify the catalog result.
4. Verify zero-quantity, inactive-snapshot and unauthorized-brand stock cannot qualify results or affect counts.
5. Verify positive stock-only name/code results remain visible and cannot be selected/exported as catalog products.
6. Toggle rapidly and choose a suggestion while requests are in flight; only the latest checkbox/query state may render.
7. Complete Advanced Search and Find Code, then Cancel. Product Search must return empty with no stale rows under the checkbox. Run a new Product Search explicitly.
8. Start Check License/Advanced/Find Code requests, switch modes and repeat; aborted/late responses must not replace newer results or unlock/overwrite newer batch state.
9. On mobile, verify label focus/tap, suggestion touch scrolling and responsive wrapping.

Record candidate commit/tree, browser/device, team/grants, tested stock revision, screenshots and explicit UAT acceptance.
