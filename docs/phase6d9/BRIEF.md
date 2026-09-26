# Phase 6D9 — Product Search workbench polish

## Baseline and boundary

- Baseline commit: `5ccc6ac6c18885be64ccaf6b6e38a835ff1ed954`
- Baseline tree: `27c6e9d3b6cbd79c884a3f86de2fe69be2f2e8ff`
- Phase 6D8 remains an unmerged staging candidate. This phase does not imply 6D8 UAT acceptance or production readiness.
- Product Search presentation only. No backend route, API, permission, query, match, stock, copy, export, regulatory, or pricing behavior changed.
- No new package, UI framework, font dependency, migration or application database write. Implementation and synthetic browser QA were local-only; coordinator-owned packaging and staging release checks are separate from this UI change.

## Findings addressed

1. Search, lookup utilities, copy, and export competed in one undifferentiated flex row.
2. The stock-only checkbox hung beneath the query and made the primary search row visually unbalanced.
3. Dynamic Brand/Size filters had no labels or bounded wrapping behavior.
4. Admin Team/template export context looked like a full-width search filter.
5. Result actions were far from selection status and table context.
6. Long user identity strings could overflow the shared mobile header on Product Search.

## Implemented hierarchy

- A single compact page title and white workbench cards on a light neutral background; repeated hero/subtitle/instruction layers were removed.
- Search is the only saturated indigo primary action and uses the full available width.
- Check license, Find Code, Advanced Search, and Quick Quote remain separate neutral secondary tools with their original IDs, names, links, and permission gates.
- Stock-only, Brand, and Quy cách now share one labeled filter card. Dynamic values retain their original exact values and event wiring.
- Copy Selected and Xuất báo giá sit in a persistent result toolbar; only the selection summary remains conditionally hidden.
- Admin Team/template controls are labeled explicitly as “Ngữ cảnh xuất báo giá” and state that they are not search filters.
- The table is contained by an accessible horizontally scrollable region. A permission-gated semantic `colgroup` gives Name/Code/CAS/price/notes/stock content-driven room without positional selectors; headers stay intact while Name/notes wrap naturally.
- A Product Search-only mobile override lets long user identities wrap without changing the shared navigation asset.

## Presentation system

`static/search_workspace.css` is loaded after `styles.css`, `admin_nav.css`, and `search_suggestions.css`, by `templates/index.html` only. It defines scoped color, spacing, radius, typography, and focus tokens under `.search-page`.

Regulatory row colors, custom inline foreground/background variables, compliance badges, selected-row outlines, stock available/near-expiry/expired states, same-CAS warnings, and stock-only cues remain owned by the existing shared styles and runtime classes.
