# Phase 6C2 — Admin Import Center

Local implementation only. No VPS, application, staging or production database
was used during implementation. Staging review is required before deployment.

## Deployment order

1. Review and back up the intended environment through the existing operator
   procedure. Identify the web service's authoritative environment source.
   Production historically ran `25ca3d94d3222f08f9373df09a32e78129b7284c`; its
   live systemd DATABASE_URL and `/opt/search-tools-pg/.env` referred to different
   databases. Never infer the target from that `.env`, and do not print secrets.
2. Apply `sql/migration_024_admin_import_center.sql` with `psql -X -v
   ON_ERROR_STOP=1 -f ...` only to an explicitly approved target. Requires existing
   migrations including 019, users, products, manual compliance and preparation.
   Migration is additive and transactional. Its product identity index may block
   writes during creation; schedule a maintenance window on a large catalog.
3. Deploy code, create `/var/lib/search-tools/imports` owned by the same service
   account as web and worker, mode 0700 (`install -d -m 0700 -o searchtools -g
   searchtools ...`). Use local disk shared by all Gunicorn workers and import
   workers on that host. Multi-host deployment requires one shared persistent
   filesystem mounted at the identical path. Do not expose it through Nginx.
4. Configure web and `deploy/search-tools-import-worker.service` to use the same
   reviewed environment file. Enable the worker with systemd; adapt user/path
   names first. Worker needs product/brand write and job table privileges; do not
   grant database creation privileges in deployment. It adds no Redis/Celery.
5. Review `deploy/nginx-imports.conf.example` alongside existing TLS, trusted proxy,
   IP allowlist and session settings. Match 128 MiB file / 129 MiB request limits
   and 180-second request timeouts. Size proxy body-temp disk for simultaneous
   uploads. Flask's body cap also applies to other application uploads.

## Operation and semantics

`/admin/imports` uploads XLSX only. Exactly one sheet; recognized product headers,
no formulas/errors/macros/external references/images/embedded content. Replace
formulas with values and save a simple workbook. The optional compliance pair
must be supplied together. Blank supplied controls clear values; omitted controls
are preserved on updates. Replacements preserve controls only for an unambiguous
brand + code + source + size identity, avoiding cross-catalog copying.

Jobs first queue a read-only product preview. Staging writes only job tables.
Review counts, first ten sample rows and exact brand/source deletion scopes.
Replace requires a separate disclosure and typing the exact deletion count.
Apply always rechecks affected products (including PostgreSQL row versions) and
Brand Gateway mappings under the shared product import advisory lock. Any catalog
change requires another preview. Append never deletes; upsert follows gateway
code/brand candidate disambiguation by source and size. Duplicate incoming
identities/ambiguous targets fail the whole file. Missing codes append rows.

New brands are registered through Brand Gateway only during apply, with NULL
currency, no default rate and no team grant. Admin assigns any valid extensible
currency in Tỷ giá and separately grants team visibility. Existing alias/source
provenance remains intact. CLI uses the same parser and SQL engine; `--append`,
`--replace-brands-from-file`, `--dry-run` and historical full-replace default
remain, with an added `--upsert`. Full-replace is CLI-only. Workbook validation is
intentionally stricter than the former parser; errors explain required cleanup.

Rules/single-product tools remain at `/admin/imports/tools`. Rules uploads remain
small (2 MiB, 10k rows) and now have durable actor-bound previews and CSRF. Bulk
rule deletion is not offered by this product import center. Quick product/rule
deletes require a signed actor-bound, five-minute preview of the exact scope and
count, then a separate confirmation; changed row versions invalidate it.

## Limits, retention and recovery

All `IMPORT_*` defaults appear in `.env.example`. File: 128 MiB; expanded ZIP:
768 MiB; combined non-sheet metadata: 64 MiB; compression ratio: 250; ZIP entries:
2048; cells: 4000 chars; rows: 1 million; source scopes: 2000; chunks: 2000 rows.
Raw uploads have 48-hour retention and are private 0600 UUID files. Upload admission
is serialized and bounds retained files to 100 / 8 GiB. Orphans older than 24 hours
are removed. Job rows, previews and samples are purged on expiry; audit summaries
and events expire after 90 days. Worker cleanup runs each loop. No permanent
workbook backups or public downloads are created.

Each failed job stores one controlled Vietnamese error (`IMPORT_ERROR_CHARS`, default 400, hard cap 4000 characters) and
stops at the first invalid row, so bad files cannot grow logs. Samples cap at ten
rows. No SQL, DSN, filesystem path or raw parser exception is logged. systemd log
rate limiting bounds repeated connection errors; configure journald disk retention
with the host's existing policy. Provision PostgreSQL space for durable staged JSON,
SQL temp tables, indexes and transaction WAL (several times the workbook's expanded
size); monitor database/temp-disk usage independently of upload-disk admission.

One worker is sufficient. Multiple workers claim jobs with session advisory locks;
all product mutations and final status commit in one transaction under the existing
product lock. There is no partially successful import. Cancellation checks during
chunks and an independent watchdog cancel long SQL. A two-hour job / ten-minute
statement default bounds processing. Graceful shutdown finishes current work;
systemd kills it after 30 seconds if necessary, rolling back the transaction.
A replacement worker detects a missing job lock, marks interruption as failed and
requires a new preview/confirmation. It never guesses whether to replay a completed
apply. Failed/cancelled previews can retry from the same file before expiry, with
six total worker attempts; completed applies cannot retry. Duplicate form submission
keys return the existing job, and double apply clicks cannot enqueue duplicate work.

Monitor queued age, heartbeat age, failures, free upload/temp/WAL disk and worker
service health. A worker outage leaves uploads queued and visible. Start/restart
worker to recover. Never repair jobs by modifying product tables manually.

## Staging UAT

Use representative real-world XLSX exports (including shared strings) to confirm
that documented metadata limits match catalog shape; do not increase limits without
memory/disk measurement. Test Google admin and staff sessions, role revocation,
new-brand currency assignment with a currency outside the original five, alias
replacement, stale previews, cancellation during apply, process kill/restart,
worker outage/recovery, disk quota, retention, and desktop/mobile accessibility.
Measure 200k+ row workloads on staging hardware while search traffic continues;
preview and apply briefly serialize imports and can consume database I/O. Approve
operational limits and backup/restore UAT before any production rollout.
