# Phase 6D9 UAT checklist

Use a staging build based on the frozen Phase 6D8 candidate. Do not infer Phase 6D8 or production acceptance from local evidence.

## Desktop

- At 1280, 1440, and 1920 widths, confirm Product Search is the dominant first card and Search is the only saturated primary action.
- At 1440×900 with results, confirm the table header and start of the first data row are visible without scrolling the page vertically.
- Confirm Check license, Find Code, Advanced Search, and Quick Quote retain their existing behavior.
- Search for a term returning multiple brands, sizes, long notes, regulatory rows, and stock states.
- Confirm Brand/Quy cách controls wrap within their groups and never change the exact option values.
- Select a catalog row. Confirm Copy/Xuất enable, the selected count appears, and regulatory color plus selected outline both remain visible.
- Confirm stock-only rows cannot be selected and near-expiry/expired/same-CAS messaging keeps its established colors and wording.
- For admin, confirm Team/template controls are clearly export context and changing them does not filter search results.
- Open the admin disclosure and autocomplete popup near the top of the page; neither should be clipped. Verify Escape closes each appropriately.

## Permissions

- Staff without SEARCH: query and stock-only controls are absent; granted lookup tools still render and work.
- Staff without COPY and/or EXPORT: the corresponding actions are absent, not merely visually disabled.
- Verify representative missing VIEW_NAME, VIEW_CODE, VIEW_CAS, VIEW_PRICE, and compliance grants; table headings/body stay aligned without positional CSS assumptions.
- Confirm `Cas` and `Unit_Price` headers/values remain intact, while long Name, Code, notes, and compliance text wrap without causing page-level overflow.

## Responsive and accessibility

- At 768px and 390/375px, confirm cards stack, action buttons remain at least 44px high, long identity/filter/team/note strings wrap, and the page itself never scrolls horizontally.
- Confirm the table scrolls horizontally inside its own region and keyboard focus can reach that region.
- At 200% browser zoom, confirm the same no-page-overflow behavior.
- Tab through search, secondary tools, filters, result actions, export context, and table controls; focus indicators must remain visible.
- Confirm disabled buttons and disabled stock-only state remain legible.

## State coverage

- Initial/empty page.
- Search loading, success, no-results, truncation warning, and error.
- Zero, one, and many selected rows.
- Check License mode hides the entire filter card and disables the stock-only control through the existing JS lifecycle; returning restores it.
- Find Code and Advanced Search panels open/cancel without moving or duplicating controls.
