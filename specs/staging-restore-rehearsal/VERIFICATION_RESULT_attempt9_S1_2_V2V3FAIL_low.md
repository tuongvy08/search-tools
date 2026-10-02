# Verification Result — staging-restore-rehearsal (S1.2 restore script; V1/S1.1 regression)

Verifier độc lập, context sạch. Hợp đồng `VERIFICATION.md` SHA256 kiểm lại = `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79` (khớp). Script kiểm: `scripts/staging_restore_run_temp.py` `3f697a20003da5c132d32e83a72349f02dd2bfe3f904790127534cb5f6b84f45` (khớp hash pin hồ sơ vận hành); `staging_restore_create_temp.py` `a9fe5d3f…397c`; `staging_restore_identity_readonly.py` `d0a32e3e…ec5a`. Không SSH, không kết nối staging/production, không commit/push. Chỉ chạy local + container Docker `postgres:16` (PostgreSQL 16 thật, `--network none`, dữ liệu giả; đã xóa container sau khi chạy). V6: NOT RUN / PENDING.

Test độc lập MỚI lần này: `tests/independent/test_staging_restore_run_temp_shape_leak_adversarial.py` (15 test, tự dựng/xóa container, tự SKIP nếu thiếu Docker). Dùng lại Harness của `test_staging_restore_run_temp_real_pg_adversarial.py` (phần thật: máy chủ PG16, pg_dump/pg_restore/psql, `main()` thật của S1.1 và S1.2 cùng các gate SQL thật; phần giả: `sudo`→`docker exec`, runtime_guard/ss/đường dẫn data/hằng pin archive+OID thay bằng giá trị fixture — như lần trước; không chứng minh archive staging thật).

## Kết luận tóm tắt

| Phần | Kết luận |
| --- | --- |
| S1.2 restore — V2 (chỉ ghi DB tạm, gate trước ghi) | **FAIL (thấp)** — gate "DB tạm trống" chỉ đếm quan hệ trong schema `public` |
| S1.2 restore — V3 (restore thật, exit code, HOLD) | **FAIL (thấp)** — dòng gợi ý lỗi còn lộ mảnh dữ liệu với lỗi nhập liệu ngoài COPY |
| V4 hậu kiểm (4 bảng chính có dữ liệu) | PASS |
| V5 hậu kiểm (trước 033) | PASS (lỗi lần trước đã sửa; còn SPEC_AMBIGUOUS cho vật thể 033 bị đổi tên/chuyển schema) |
| Hồi quy V1 (S0.x) và S1.1 | PASS |
| V6 | NOT RUN / PENDING |

## Claim V2 (S1.2: chỉ restore archive có sẵn vào DB tạm, không ghi DB đang dùng)

Test thực hiện: chạy lại bộ test thật cũ (một lệnh ghi duy nhất; argv; một giao dịch; tup_inserted/updated/deleted của DB nguồn; từ chối khi session/sai OID/DB tạm không tồn tại/sai cờ) và thêm test mới `test_nonempty_temp_variants_refused_before_write`: DB tạm (tạo bằng S1.1 thật) bị thêm một vật thể KHÔNG phải bảng trong `public` rồi chạy `main()` thật của S1.2.
Kết quả: **FAIL (thấp)**; phần an toàn đã PASS trước đó vẫn PASS (không hồi quy).
Evidence (Observed): với DB tạm có (a) bảng trong schema khác, (b) schema trống thêm, (c) function trong `public`, (d) sequence trong `public`, (e) type trong `public`, (f) extension `pg_trgm`, script đều qua gate, in `PRERESTORE_READONLY_GATES_OK … temp_user_tables=0`, CHẠY pg_restore (`restore_results` có 1 lần) và kết thúc exit 0. Đối chứng: một bảng trong `public` → `DUNG: TEMP_DATABASE_MUST_BE_EMPTY`, pg_restore không chạy. Log: `non-empty temp variants (code, pg_restore_ran, gate): {'other_schema_table': (0, True, []), 'empty_extra_schema': (0, True, []), 'public_function': (0, True, []), 'public_sequence': (0, True, []), 'public_type': (0, True, []), 'extension': (0, True, []), 'control_public_table_refused': (1, False, ['DUNG: TEMP_DATABASE_MUST_BE_EMPTY …'])}`. S1.1 tạo DB từ `template0` nên DB tạm hợp lệ chỉ có extension mặc định `plpgsql`; gate không phân biệt được DB đã bị sửa giữa S1.1 và S1.2.
Expected: yêu cầu của chủ dự án cho bước này: từ chối trước khi ghi nếu DB tạm không trống; chứng cứ "temp_user_tables=0" nên đồng nghĩa DB không có gì khác.
Negative case đã thử: danh sách trên; thêm các ca cũ (chạy lần 2 sau thành công → `TEMP_DATABASE_MUST_BE_EMPTY`; 1 session mở → `TEMP_DATABASE_HAS_SESSIONS`; OID sai → `TEMP_DATABASE_IDENTITY`; DB tạm không tồn tại → `TEMP_DATABASE_MUST_EXIST`, không tạo; thiếu/sai cờ → `ROOT_EXPLICIT_RESTORE_ONLY`, không lệnh nào chạy) — tất cả PASS.
Classification: IMPLEMENTATION_FAIL (thấp; chỉ xảy ra nếu ai đó sửa DB tạm giữa S1.1 và S1.2 — hợp đồng không viết chữ "trống" cho bước restore nên có thể coi là SPEC_AMBIGUOUS nếu chủ hợp đồng cho rằng gate hiện tại đủ).
Reproduction: `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_run_temp_shape_leak_adversarial.py -k nonempty` (cần Docker + image postgres:16).
Residual risk: sudo/systemd/git/proc, đường dẫn/đĩa/OID/hash thật chưa chạy trên máy này; TOCTOU nhỏ `open(ARCHIVE)` lần hai sau khi hash; DB nguồn "không đổi" chỉ chứng minh bằng thống kê tup_* và gate (không phải bằng hash từng dòng).

## Claim V3 (restore thật hoàn tất, exit code, HOLD, không lộ dữ liệu)

Test thực hiện: chạy lại bộ cũ (archive tham chiếu role không tồn tại, cắt nửa, lật 4000 byte, 033 có, timeout, stdout hỏng) và thêm 8 ca lỗi mới trên archive không nén (`pg_dump -Fc -Z0`), sửa tại chỗ một giá trị `SECRETMARK`/`1234567890`: (1) chỉ mục biểu thức `((t)::integer)`; (2) materialized view `t::integer` (REFRESH khi restore); (3) COPY số nguyên sai; (4) CHECK vi phạm; (5) UNIQUE trùng; (6) FK vi phạm; (7) domain CHECK vi phạm; (8) byte UTF-8 sai trong COPY. Kiểm: exit, HOLD, không marker, một lần pg_restore, DB tạm còn và 0 bảng (rollback một giao dịch), DB nguồn không đổi, và output KHÔNG chứa mảnh dữ liệu.
Kết quả: **FAIL (thấp)** ở tiêu chí "không lộ dữ liệu"; mọi tiêu chí còn lại PASS (cả 8 ca: exit 1, HOLD, không marker, 1 lần pg_restore, 0 bảng trong DB tạm, DB nguồn không đổi, DB tạm không bị xóa).
Evidence (Observed): ca (1) và (2) in nguyên văn: `restore_error_first_line=pg_restore: error: could not execute query: ERROR:  invalid input syntax for type integer: "SECRETMARK"` — mảnh dữ liệu trong bảng lọt ra output. Các ca (3)(4)(6)(7)(8): không có dòng gợi ý (bị lọc, đúng). Ca (5): chỉ in `... could not create unique index "lk_e_u_key"` (tên ràng buộc, không có dữ liệu).
Expected: HOLD/dòng gợi ý không bao giờ chứa dữ liệu (hợp đồng V3: không xuất nội dung dữ liệu; yêu cầu chủ dự án: không in secret/dữ liệu). Bộ lọc hiện loại theo các từ `copy/line/key/value/DETAIL/CONTEXT` nên bỏ sót lỗi cú pháp nhập có trích giá trị mà không đi qua COPY (chỉ mục biểu thức, materialized view…).
Negative case đã thử: danh sách 8 ca trên + các ca cũ (archive hỏng, cắt, role thiếu, timeout).
Classification: IMPLEMENTATION_FAIL (thấp: trên staging thật cần lỗi dữ liệu-phụ-thuộc ở bước post-data; chưa tái hiện với dữ liệu thật).
Reproduction: `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_run_temp_shape_leak_adversarial.py -k leak_expression_index` và `-k leak_materialized_view`.
Residual risk (đã biết, không phải FAIL mới): sau timeout/mất SSH tiến trình pg_restore có thể vẫn chạy và commit sau khi script in HOLD (tái hiện ở test cũ `test_timeout_then_orphan_pgrestore_keeps_running_and_commits_after_hold`: bảng lúc HOLD=0, sau đó 43); chỉ dòng `HOLD` in "trạng thái không rõ". Test cũ `test_sigkill_of_direct_child_leaves_grandchild_running_like_sudo` (stand-in POSIX, không phải claim của script) thất bại 1 lần khi chạy cả file nhưng PASS 3/3 khi chạy riêng → flaky phụ thuộc môi trường, không tính lỗi. Chạy trên sudo thật chưa quan sát được.

## Claim V4 (hậu kiểm: bốn bảng chính có dữ liệu)

Test thực hiện: chạy lại bộ cũ (products rỗng, thiếu regulatory_statuses, đủ dữ liệu) và test mới `positive_control_pristine…`, `test_positive_controls_not_blocked`.
Kết quả: PASS
Evidence (Observed): ca đủ: 5000/30/60/6, inactive 8, footprints 030–032 = True, 9 cờ 033 và 9 cờ theo-tên = False; products rỗng → `DUNG: TEMP_MAIN_TABLES_PRESENT_NONEMPTY` + HOLD; thiếu bảng → `TEMP_RESTORED_READ` + HOLD; DB giả đúng 030–032 với mọi luật active (inactive = 0) và với schema phụ → exit 0, marker. Biểu thức footprint trùng `SOURCE_CHECK.sql` (test cũ, 8 DB).
Negative case đã thử: bảng rỗng/thiếu; thiếu footprint 030 hoặc 032 → `TEMP_MISSING_030_032`.
Residual risk: script chỉ kiểm count > 0, không đối chiếu snapshot (1262173/2295/6003/6/3); chưa thử "bảng chính là view" (không thực tế với archive staging).

## Claim V5 (hậu kiểm: DB tạm trước 033; 030–032 có)

Test thực hiện: 17 biến thể "có 033 một phần/ngụy trang/khác hình dạng" (mỗi biến thể một archive PG16 thật, restore qua `main()` thật), 4 biến thể phần-033 dựng từ schema 033 thật (bỏ trigger+function; chỉ cột; chỉ bảng; bỏ ba index), 5 biến thể di chuyển/đổi tên, 3 đối chứng dương.
Kết quả: PASS (với phạm vi tên gọi trong hợp đồng)
Evidence (Observed): tất cả 17 biến thể residue → exit 1, `DUNG: TEMP_MUST_BE_PRE_033`, HOLD, không marker: function trùng tên khác chữ ký (`(integer)`), PROCEDURE trùng tên, trigger `zz_regulatory_rule_revision` trên bảng khác / bị disable / constraint trigger, index 033 trùng tên trên bảng khác (3 index), `regulatory_rule_manual_keys` là materialized view / sequence / composite type / bảng phân vùng, `regulatory_rule_manual_events` là view / bảng thường, cột `manual_protected` kiểu text hoặc bool nullable, cột `revision` kiểu text. Ba ca lần trước FAIL (revision nullable, manual_keys là view, trigger dùng hàm khác) nay HOLD. Bốn ca phần-033 từ schema thật đều HOLD; archive 033 đầy đủ → HOLD, DB tạm giữ nguyên. Đối chứng dương không bị chặn: tất cả luật active; có column `revision` trên `products`, `manual_protected` trên `stock_items`, function `update_regulatory_rule_revision_x`, trigger `zz_regulatory_rule_revision_old`, index `…_identity_old`, bảng `…_manual_keys_archive_note`; schema/bảng/index phụ → exit 0 + marker (một lần đối chứng này FAIL do session đang đóng của S1.1 — race của harness, đã thêm chờ trong test, 3/3 PASS khi chạy riêng và PASS khi chạy cả file).
Negative case đã thử: danh sách trên.
Classification: SPEC_AMBIGUOUS (không đổi kết luận PASS) — khi vật thể 033 bị chuyển schema hoặc đổi tên, script KHÔNG chặn (exit 0 + marker): `a_keys_other_schema`, `a_keys_renamed`, `a_func_other_schema`, `a_idx_other_schema` đều (0, completed). Hợp đồng liệt kê footprint theo tên, không nói schema khác/đổi tên; migration 033 chỉ tạo trong `public`.
Reproduction: `-k ambiguous` trong file mới (in kết quả; không assert).
Residual risk: không kiểm sequence/CHECK/PK riêng 033 (không nằm trong danh sách hợp đồng); vật thể 033 đổi tên/chuyển schema không bị phát hiện.

## Hồi quy V1 (S0.x) và S1.1

Test thực hiện: `.venv/bin/python -B -m unittest discover -s tests -p 'test_staging_restore_*.py'` (79 test) và từng file `tests/independent/test_staging_restore_*adversarial.py` (trừ `…_preflight.py`) gồm identity, initial_command, source, archive, toc, pg_metadata, db_disk, create_temp, create_temp_output, additional_output, pinned_constants, verifier5_matrix; kiểm hằng pin chéo giữa 2 script (TEMP `search_tools_restore_test_20261001_064530`, OID 18535 chỉ trong S1.2, size 37333943, SHA `cc021ca2…473f`, LABEL, commit `2ec9b6c9…`) và `git diff --check`.
Kết quả: PASS
Evidence (Observed): 79/79 OK; mỗi file độc lập OK (4, 9, 11, 4, 7, 8, 9, 5, 43, 8, 11, … test) ngoại trừ `test_staging_restore_real_tool_argv_adversarial.py`: 1 fail đã biết (`4 not greater than or equal to 8`, ngưỡng đếm) — chỉ ghi nhận, không tính lỗi mới. `git diff --check` exit 0. Hằng pin nhất quán; hash script khớp hồ sơ vận hành.
Negative case: các file adversarial ở trên (mất PID, DB khác, stdout bẩn, secret giả…).
Residual risk: S1.1 đã chạy thật trên staging (evidence PO); S1.2 chưa chạy thật.

## V6

NOT RUN / PENDING (chưa có duyệt xóa; không đánh giá).

## Task status

attempt: <n> / max_auto_repairs: <m>
same_claim_repeat_fail: <placeholder>
next_action: <placeholder>
