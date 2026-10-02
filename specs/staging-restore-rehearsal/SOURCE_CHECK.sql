-- Read-only source metadata/counts. Run only on explicit search_tools_staging.
-- This is not a migration and contains no data-changing statements.
SELECT current_timestamp AS observed_at,
       current_database() AS database_name,
       current_setting('server_version') AS server_version,
       current_setting('port') AS server_port,
       current_setting('data_directory') AS data_directory,
       current_setting('transaction_read_only') AS transaction_read_only,
       pg_database_size(current_database()) AS database_bytes,
       (SELECT CASE WHEN first_line ~ '^[0-9]{1,10}$' THEN first_line::bigint ELSE NULL END
          FROM (SELECT split_part(pg_read_file('postmaster.pid', 0, 64), E'\n', 1) AS first_line) pid)
         AS postmaster_pid,
       pg_encoding_to_char(d.encoding) AS encoding,
       d.datcollate AS lc_collate,
       d.datctype AS lc_ctype,
       d.datlocprovider AS locale_provider,
       d.daticulocale AS icu_locale,
       t.spcname AS database_tablespace
  FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace
 WHERE d.datname=current_database();

SELECT 'products' AS table_name, count(*) AS row_count FROM public.products
UNION ALL SELECT 'stock_items', count(*) FROM public.stock_items
UNION ALL SELECT 'regulatory_rules', count(*) FROM public.regulatory_rules
UNION ALL SELECT 'regulatory_statuses', count(*) FROM public.regulatory_statuses
UNION ALL SELECT 'regulatory_rules_inactive', count(*) FROM public.regulatory_rules WHERE NOT is_active;

SELECT (to_regclass('public.admin_menu_grants') IS NOT NULL
        AND to_regclass('public.admin_rbac_events') IS NOT NULL
        AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
          AND table_name='app_users' AND column_name='is_super_admin')) AS footprint_030,
       EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
          AND table_name='stock_items' AND column_name='stock_note') AS footprint_031,
       (to_regclass('public.stock_manual_requests') IS NOT NULL
        AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('public.stock_snapshots')
          AND conname='stock_snapshots_source_kind_check'
          AND pg_get_constraintdef(oid) LIKE '%MANUAL%')) AS footprint_032;

SELECT EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_keys')
          AND relkind='r') AS manual_keys_table,
       EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('public.regulatory_rule_manual_events')
          AND relkind='r') AS manual_events_table,
       EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
          AND table_name='regulatory_rules' AND column_name='manual_protected'
          AND data_type='boolean' AND is_nullable='NO') AS manual_protected_column,
       EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
          AND table_name='regulatory_rules' AND column_name='revision'
          AND data_type='bigint' AND is_nullable='NO') AS revision_column,
       EXISTS (SELECT 1 FROM pg_proc WHERE oid=to_regprocedure('public.update_regulatory_rule_revision()')
          AND prorettype='trigger'::regtype AND prokind='f') AS revision_function,
       EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.regulatory_rules')
          AND tgname='zz_regulatory_rule_revision' AND NOT tgisinternal
          AND tgenabled IN ('O','A') AND tgtype=19
          AND tgfoid=to_regprocedure('public.update_regulatory_rule_revision()')) AS revision_trigger,
       EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_identity')
          AND indrelid=to_regclass('public.regulatory_rule_manual_keys')
          AND indisvalid AND indisready AND indisunique) AS key_identity_index,
       EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_key_owner')
          AND indrelid=to_regclass('public.regulatory_rule_manual_keys')
          AND indisvalid AND indisready) AS key_owner_index,
       EXISTS (SELECT 1 FROM pg_index WHERE indexrelid=to_regclass('public.regulatory_manual_event_history')
          AND indrelid=to_regclass('public.regulatory_rule_manual_events')
          AND indisvalid AND indisready) AS event_history_index;
