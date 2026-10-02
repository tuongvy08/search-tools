-- Read-only: does any restore-test database exist, and with what attributes? No data rows.
SELECT datname, oid::bigint AS oid, pg_encoding_to_char(encoding) AS encoding,
       datcollate, datctype, datconnlimit,
       shobj_description(oid, 'pg_database') AS label
FROM pg_database
WHERE datname LIKE 'search_tools_restore_test%'
ORDER BY datname;
