# Phase 6D8 operations

This is a code-only phase: no schema migration or new index is currently required.

Before staging packaging:

- Review the complete diff from the Phase 6D7 baseline and rerun focused/full tests on disposable PostgreSQL.
- Run the synthetic benchmark and retain its JSON/log path; inspect `EXPLAIN (ANALYZE, BUFFERS)` for ASCII, no-CAS, Unicode-heavy slow-lane and non-admin team cases.
- Verify existing product GIN indexes and stock snapshot code/CAS indexes are valid on the target environment. Do not create indexes from synthetic evidence alone.
- Bump and verify `script.js`/`styles.css` cache versions at every load site.

Deployment remains gated by staging UAT and explicit production authorization. Production rollout must revalidate the live web/worker commit, database identity, active stock revision and existing indexes. This document does not authorize SSH, service restart, database writes, cleanup or deployment.
