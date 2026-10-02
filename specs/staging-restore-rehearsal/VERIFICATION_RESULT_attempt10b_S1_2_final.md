# Verification Result — staging-restore-rehearsal, S1.2 (scripts/staging_restore_run_temp.py), vòng verifier độc lập mới

Verifier độc lập (context sạch). VERIFICATION.md SHA256 kiểm lại = 0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79 (khớp). scripts/staging_restore_run_temp.py SHA256 lúc kiểm = eaea62a8935e4a64150fdc976a97dca6b0faaf09861357b09a9e7d44ed6a9016. Không SSH, không kết nối staging/production, không commit/push. Chỉ dùng Docker PostgreSQL 16 dùng-một-lần (`--network none`, dữ liệu giả; đã xóa container sau khi chạy). Test độc lập mới: `tests/independent/test_staging_restore_run_temp_verifier10_adversarial.py`.

Lệnh đã chạy
- `.venv/bin/python -B -m unittest discover -s tests -p 'test_staging_restore_*.py'` -> Ran 79, OK.
- `git diff --check` -> sạch (exit 0).
- Từng file `tests/independent/test_staging_restore_*adversarial.py` (không chạy preflight): tất cả OK trừ 3 ghi nhận ở "Ghi nhận test cũ" bên dưới. `shape_leak` 15/15 OK; `verifier5_matrix` 25/25 OK; `pinned_constants` 43/43 OK; `create_temp*`, `identity`, `initial_command`, `archive`, `db_disk`, `pg_metadata`, `source`, `toc`, `additional_output` OK.
- Test mới (25 test, PG16 thật): 24 PASS, 1 FAIL (`test_a_nonempty_matrix_all_refused_before_write`).

## Claim V1 (hồi quy S0.4, chỉ đọc staging)

Test thực hiện: chạy lại `test_staging_restore_identity_adversarial`, `initial_command`, `source`, `toc`, `archive`, `pg_metadata`, `db_disk`, `additional_output`, `verifier5_matrix`, `pinned_constants` và bộ unit `tests/test_staging_restore_*` (79 test).
Kết quả: PASS (hồi quy)
Evidence (Observed): tất cả test trên OK, không test nào thất bại; hằng pin (OID 18535, tên DB tạm, SHA/size/mtime archive, COMMIT, tên unit) khớp bộ kiểm 43 test pin.
Negative case đã thử: bộ test có sẵn (web/worker khác DB, DSN sai, output lạ, tool trả stderr) vẫn dừng đúng; không phát sinh lệnh ghi.
Residual risk: chưa chạy được trên staging thật (ngoài phạm vi verifier); chỉ chứng minh trên mock/PG16 Docker.

## Claim V2 — S1.2 chỉ restore vào DB tạm trống (gate "DB tạm trống")

Test thực hiện: dựng DB tạm bằng chính script S1.1 (`createdb --template=template0` + REVOKE/COMMENT) trên PG16 thật, thêm TỪNG loại vật thể người dùng vào DB tạm rồi chạy S1.2 `--restore` thật; quan sát có `pg_restore` chạy hay không (`test_a_nonempty_matrix_all_refused_before_write`, 29 biến thể). Positive control: DB tạm đúng nghĩa trống (S1.1) -> phải qua gate. Bổ sung: sai OID, nhãn bị sửa, CONNECT cho PUBLIC (chặn trước khi ghi), có session, DB không tồn tại, restore lần 2 lên DB đã có dữ liệu.
Kết quả: FAIL

Evidence (Observed), 29 biến thể trong `NONEMPTY`:
- Bị chặn đúng trước khi ghi (DUNG: TEMP_DATABASE_MUST_BE_EMPTY, `pg_restore` không chạy): schema trống, bảng ở schema khác, sequence, type enum, domain, function, aggregate, extension (pg_trgm), event trigger, large object, cast, view, matview, policy, trigger, statistics object, bảng public.
- KHÔNG bị chặn, `pg_restore` đã chạy vào DB tạm "không trống" và script in `STAGING_TEMP_RESTORE_COMPLETED` (exit 0): operator (`CREATE OPERATOR public.=== ... FUNCTION=int4eq`), collation, text search configuration, text search dictionary, foreign data wrapper, foreign server (qua FDW), publication, conversion, default privileges (`ALTER DEFAULT PRIVILEGES`), `COMMENT ON SCHEMA public`.
- `DROP SCHEMA public` trong DB tạm: gate cũng không chặn, `pg_restore` chạy (rồi tự lỗi, HOLD) -> đã ghi vào DB không còn đúng dạng S1.1.
- Quan sát thêm (không khẳng định FAIL): thay đổi cấu hình không phải vật thể (`ALTER DATABASE ... SET work_mem`, đổi owner schema public, `GRANT CREATE ON SCHEMA public TO PUBLIC`) cũng qua gate, restore chạy xong.
Expected: chỉ restore vào DB tạm thật sự trống; mọi vật thể người dùng sẵn có (đề bài nêu rõ operator, publication...) phải bị chặn trước lệnh ghi duy nhất.
Negative case đã thử: bảng trên + (đã qua) sai OID (TEMP_DATABASE_IDENTITY), nhãn bị đổi (TEMP_DATABASE_IDENTITY), GRANT CONNECT TO PUBLIC (TEMP_DATABASE_IDENTITY), session mở (TEMP_DATABASE_HAS_SESSIONS), DB không tồn tại (TEMP_DATABASE_MUST_EXIST), chạy lần 2 sau thành công (TEMP_DATABASE_MUST_BE_EMPTY); đều không có `pg_restore`.
Positive control: PASS. DB S1.1 mới tạo qua gate (`temp_user_tables=0`), restore xong, một `pg_restore`, không HOLD (`test_a_positive_control_*`, `_happy` của real_pg test); không chặn nhầm.
Classification: IMPLEMENTATION_FAIL (mức thấp: chỉ xảy ra nếu có người tự thêm các vật thể đó vào DB tạm; không mất dữ liệu staging đang dùng)
Reproduction: `.venv/bin/python -B -m unittest tests.independent.test_staging_restore_run_temp_verifier10_adversarial.Verifier10.test_a_nonempty_matrix_all_refused_before_write` (cần Docker + image postgres:16; ~100 giây; in danh sách `A. non-empty variants seen` và `A. BYPASSED the gate`).
Residual risk: các vật thể ngoài danh sách đã thử (access method, language, transform, rule có bảng...) chưa thử riêng; thao tác song song sau lúc kiểm (race) không được chứng minh.

## Claim V3 — restore thật, lỗi -> HOLD, không lộ dữ liệu/secret

Test thực hiện: restore thật bằng `pg_restore` 16 trên archive giả có chuỗi canary `CANARYDATA`, tạo lỗi ở nhiều ngữ cảnh; quét TOÀN BỘ stdout+stderr của script tìm canary, `CANARY`, tên role/extension, `pg_restore:`, `ERROR:`, `DETAIL:`, `CONTEXT:`, `restore_error_first_line`, `invalid input`, `bad value`, `violates`, `psql:`; xác nhận một `pg_restore`, DB tạm giữ nguyên và rollback (0 bảng), DB staging không đổi (`pg_stat_database`).
Kết quả: PASS
Evidence (Observed): 15 ngữ cảnh lỗi đều exit 1, in `restore_error_category=<nhãn cố định>` + `DUNG: TEMP_RESTORE_COMMAND` + `HOLD`, không có canary/tool text: cột sinh (generated column), hàm raise trong chỉ mục biểu thức, hàm raise trong materialized view, enum, jsonb, date, lệch partition, numeric tràn, vi phạm unique/exclusion, FK hoãn, UTF-8 sai (`\xff`), archive cắt ngang dòng dữ liệu, header hỏng, role chủ sở hữu thiếu (nhãn ROLE_MISSING, không lộ tên role), extension thiếu (nhãn EXTENSION_PROBLEM, không lộ tên). Ngoài ra bộ có sẵn `shape_leak` (chỉ mục biểu thức, matview, COPY int, check, unique, FK, domain, UTF-8) 15/15 OK. Nhãn lỗi ở đa số trường hợp là `UNCLASSIFIED`/nhãn cố định, không chứa dữ liệu.
Expected: mọi output khi lỗi không chứa dữ liệu/secret từ công cụ.
Negative case đã thử: danh sách trên; thêm `test_owner_role_missing...` cũ (xem Ghi nhận).
Residual risk: sau timeout/mất SSH, `pg_restore` mồ côi có thể vẫn chạy và commit sau khi in HOLD (đã ghi nhận trước đó; `test_timeout_then_orphan_pgrestore_keeps_running_and_commits_after_hold` vẫn quan sát được hành vi này, PASS dạng "ghi nhận"). Lỗi event trigger lúc restore không dựng được trường hợp lỗi thật (restore hoàn tất) nên không kết luận. Chưa thử secret thật trong DSN (không có trong output theo mã: chỉ in nhãn cố định).

## Claim V4 (hậu kiểm bảng chính do script tự làm)

Test thực hiện: restore archive tiền-033 hợp lệ, kiểm số đếm bốn bảng chính và các trường hợp bảng rỗng/thiếu; positive control restore pristine.
Kết quả: PASS
Evidence (Observed): `temp_count_products=5000`, `stock_items=30`, `regulatory_rules=60`, `regulatory_statuses=6`, `rules_inactive=8` khớp dữ liệu giả; test có sẵn `main_table_empty` -> DUNG TEMP_MAIN_TABLES_PRESENT_NONEMPTY, `main_table_missing` -> TEMP_RESTORED_READ, thiếu footprint 030/032 -> TEMP_MISSING_030_032; MD5 bảng products tạm == nguồn.
Negative case đã thử: bảng trống, thiếu bảng, thiếu 030/032 (đều HOLD, không in PASS).
Residual risk: không đối chiếu với snapshot trước dump thật (ngoài phạm vi; PO review số liệu).

## Claim V5 (hậu kiểm "tiền-033")

Test thực hiện: archive 033 đầy đủ, 033 từng phần, và 8 biến thể "vật thể mang tên 033" ở schema/dạng khác; positive control DB tiền-033 hợp lệ có thêm vật thể không liên quan.
Kết quả: PASS (theo footprint nêu trong hợp đồng); còn 2 trường hợp mơ hồ ghi nhận
Evidence (Observed): bị HOLD (`TEMP_MUST_BE_PRE_033`) đúng: 033 đầy đủ; từng phần (thiếu trigger+function, chỉ cột, chỉ bảng, thiếu index); view khác schema có cột `revision`; matview khác schema có cột `manual_protected`; trigger `zz_regulatory_rule_revision` trên bảng ở schema khác; view/sequence trùng tên index 033; hàm `update_regulatory_rule_revision(a text,b text)` ở schema khác; bộ `shape_leak` 17 biến thể RESIDUE/partial. Positive control: DB tiền-033 + schema `reporting`, view, sequence (`p_extras`) và các tên "gần giống" (`p_lookalikes_unrelated`) qua đúng, `footprints_033` toàn False.
Negative case đã thử: xem trên.
Residual risk (SPEC_AMBIGUOUS, ghi nhận, không phải FAIL): (1) bảng 033 bị đổi tên sang `regulatory_rule_manual_keys_old` nhưng index khóa chính vẫn mang tên `regulatory_rule_manual_keys_pkey` -> qua gate (hợp đồng không liệt kê index pkey như footprint riêng); (2) type enum đứng riêng tên `regulatory_rule_manual_keys` -> qua gate (033 không tạo type đứng riêng). Hợp đồng không nói rõ các biến thể này có phải footprint 033 hay không.
Reproduction (ghi nhận): `-k test_c_033_name_variants_observed_and_asserted` (in dòng `C. 033-named object variants`).

## Claim hồi quy S1.1 (script tạo DB tạm)

Test thực hiện: `test_staging_restore_create_temp_adversarial`, `create_temp_output_adversarial` và unit `test_staging_restore_create_temp.py`; DB tạm tạo thật bằng `main()` S1.1 trên PG16 trong mọi test restore (hàng chục lần).
Kết quả: PASS
Evidence (Observed): S1.1 tạo DB trống (`user_tables=0`, 0 vật thể ngoài catalog), S1.2 positive control chạy trọn.
Negative case đã thử: bộ test S1.1 có sẵn (DB tồn tại, sai tên, output lạ) vẫn đúng.
Residual risk: như V1.

## Hồi quy an toàn restore (V2/V3 phần an toàn)

Test thực hiện: `test_d_*` + test có sẵn `test_restore_argv_is_exactly_one_pgrestore_to_temp_only`, `test_static_no_drop...`, `test_second_run_after_success_refuses...`, `test_wrong_pinned_oid_refuses`, `test_temp_with_open_session_refuses`, `test_missing_temp_refuses_and_never_creates`.
Kết quả: PASS
Evidence (Observed): đúng một `pg_restore`, argv = `sudo -n -u postgres env -i LC_ALL=C LANG=C PGOPTIONS=... pg_restore --exit-on-error --single-transaction -w -h /var/run/postgresql -p 5432 -U postgres --dbname=<tạm>`; không `--clean/--create`, không DSN/password/PGPASSWORD trong argv; mọi psql khác `default_transaction_read_only=on`; DB staging không đổi (`pg_stat_database` trước = sau); sai OID, nhãn bị sửa, CONNECT cho PUBLIC, session mở, DB thiếu, chạy lại -> chặn trước khi ghi (không có `pg_restore`); lỗi sau khi bắt đầu restore -> HOLD, DB tạm còn (không xóa, không retry), giao dịch đơn rollback về 0 bảng; hằng pin 43/43 OK.
Negative case đã thử: xem trên.
Residual risk: orphan `pg_restore` sau timeout (đã ghi).

## Claim V6

Kết quả: NOT RUN / PENDING (ngoài phạm vi vòng này; chưa có PO duyệt xóa, không phát lệnh xóa).

## Ghi nhận test cũ (không do implementation hiện tại)

- `test_staging_restore_run_temp_real_pg_adversarial.test_owner_role_missing_fails_closed_and_rolls_back`: FAIL vì còn khẳng định định dạng cũ `restore_error_first_line=...`. Output hiện tại: `restore_error_category=ROLE_MISSING` + DUNG + HOLD, giao dịch rollback đúng. Theo Expected (không lộ dữ liệu) hành vi hiện tại đúng; test lỗi thời (VERIFICATION_CONTRACT_PROBLEM mức test cũ, không phải lỗi script).
- `test_staging_restore_real_tool_argv_adversarial.test_real_pg16_clients_accept_every_argv_we_build`: FAIL đã biết (`checked=4 < 8`, đếm ngưỡng sai); 8 test còn lại OK, 1 skipped.
- `test_sigkill_of_direct_child_leaves_grandchild_running_like_sudo`: fail khi chạy trong cùng tiến trình với test khác (flaky, test hành vi hệ điều hành chứ không phải script); chạy riêng 3 lần: 3/3 PASS.

## Tổng kết

- S1.2 restore V2 (gate DB tạm trống): FAIL (IMPLEMENTATION_FAIL, thấp) — gate không chặn operator, collation, text search config/dictionary, FDW, foreign server, publication, conversion, default privileges, comment/drop schema public, ... trong DB tạm.
- S1.2 restore V3 (HOLD không lộ dữ liệu): PASS.
- V4 hậu kiểm: PASS. V5 hậu kiểm: PASS (2 biến thể mơ hồ ghi nhận, SPEC_AMBIGUOUS).
- Hồi quy V1 (S0.4): PASS. Hồi quy S1.1: PASS. Hồi quy an toàn restore: PASS.
- V6: NOT RUN/PENDING.

## Task status

attempt: 10 / max_auto_repairs: 2 (PO chỉ đạo tiếp tục; ngân sách tự động không reset)
same_claim_repeat_fail: V2 (gate DB tạm trống, S1.2: FAIL attempt 9 và 10; V3/V4/V5 PASS)
next_action: ESCALATE_USER_V2_EMPTY_GATE_REPEAT_FAIL (bản đã sửa lại chưa được verifier context sạch chứng nhận; chưa phát lệnh restore)
