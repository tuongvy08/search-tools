-- Read-only. Counts and timestamps only (no row data). Backup was taken 2026-09-29 08:03:02 UTC.
SELECT current_database() AS db,
       count(*) AS rules_total,
       count(*) FILTER (WHERE NOT is_active) AS rules_inactive,
       count(*) FILTER (WHERE created_at > TIMESTAMPTZ '2026-09-29 08:03:02+00') AS created_after_backup,
       count(*) FILTER (WHERE NOT is_active AND created_at > TIMESTAMPTZ '2026-09-29 08:03:02+00') AS inactive_created_after_backup,
       count(*) FILTER (WHERE updated_at > TIMESTAMPTZ '2026-09-29 08:03:02+00') AS updated_after_backup,
       min(created_at) FILTER (WHERE created_at > TIMESTAMPTZ '2026-09-29 08:03:02+00') AS first_created_after_backup,
       max(created_at) AS latest_created
FROM public.regulatory_rules;
