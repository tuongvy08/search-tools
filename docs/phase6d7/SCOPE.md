# Phase 6D7 — tìm tên hàng tồn và gợi ý Product Search

User approved implementation after observing that Product Search finds stock-only
HI70024P by code but not by its inventory name. Baseline: production merge
cd7ebb7e663c07ec47d9a12853ba23cce86982d9. Preserve dirty prior worktrees.

## Required behavior

1. Product Search searches the active stock snapshot by name as well as the
   existing code/CAS paths. Include stock-only matches even when catalog has
   matches. Never return historic snapshots. Avoid additional duplicate rows for
   stock items already attached to catalog results; deduplicate by stock item ID,
   not redacted field values. Correct the match badge for name/CAS-origin rows
   rather than labelling all of them exact-code. Preserve current stock quantity,
   expiry, stock pricing separation, export/copy policy and regulatory semantics.
2. Suggestions use a dedicated read-only endpoint; do not call the heavy /search
   endpoint on input. Minimum 3 trimmed characters, debounce ~300ms, maximum10
   lightweight suggestions, capped query length. Include catalog and active stock;
   useful exact/prefix matches before contains where practical. No total-count or
   pricing/compliance joins. Bound candidate work and SQL timeout; LIMIT alone is
   not an efficiency guarantee. Return graceful errors without SQL details.
3. Enforce authentication, SEARCH and current team/brand visibility before LIMIT.
   Only search/return allowed fields; CAS requires SEARCH_BY_CAS and VIEW_CAS for
   suggestions. Hidden fields may not leak through label/value/match metadata or
   selection behavior. No cross-user/team shared suggestion cache in this phase.
4. Suggestions select into the existing search input and trigger explicit search
   consistently, with safe visible values. Arrow keys, Enter, Escape, outside click,
   focus/blur and IME composition supported. Never double-submit existing Enter
   handlers. Abort obsolete requests AND reject late responses, including after
   clear, Escape, selection or blur. Render strings as text, not innerHTML. Add
   accessible combobox/listbox semantics and responsive layout, keep existing IDs.
5. Preserve existing Unicode/case-insensitive matching; escape LIKE wildcards for
   the new suggestion/stock text paths. Accent-insensitive/fuzzy typo matching is
   not promised here. Do not change legacy catalog query semantics gratuitously.

## Performance and safety

- Audit existing migration010 trigram indexes; do not assume production has them.
- Use PostgreSQL initially; no new external search service/package requirement.
- If new indexes are justified, provide safe documented migration/operations gates
  with fail-closed identity/definition checks, no automatic destructive cleanup.
- Inventory is874 active rows per last production deployment; catalog1,106,803.
  Benchmark with representative isolated synthetic catalog >=1million rows and
  active/historical inventory, exact/prefix/common/rare/missing queries plus team
  scopes. Capture EXPLAIN ANALYZE BUFFERS, latency and concurrent reads; distinguish
  local evidence from production SLO. No benchmark on real DB without new gate.
- Search-only additions must not silently truncate. If direct stock matches need
  a safety cap, report truncation explicitly and ask the user to refine the query.
- No data writes, snapshot changes, migrations or services on remote hosts during
  implementation. No deletion, business policy changes or production authorization
  inferred from prior Phase6D6 approval.

## Verification / handoff

Focused regression tests: stock-only name, mixed catalog/stock results, exact code,
CAS grants, active-only snapshots, duplicate attachment, Unicode/wildcard literals,
SEARCH denied, unauthenticated, team filtering, hidden fields/value leaks, query
bounds/timeouts, XSS, async races/IME/keyboard/accessibility and existing exports.
Run local PG-backed tests using disposable databases only, then full suite/DOM
gates and desktop/mobile QA. Report all skips and remaining rollout risks.

Stop READY FOR REVIEW. No commit/push/PR/merge/deploy by implementation task.
Coordinator reviews and prepares staging; user performs staging UAT before a
separate production rollout decision.
