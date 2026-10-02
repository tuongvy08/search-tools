# Verification Result — staging-restore-rehearsal (S3 drop script, claim V6; quick regression V1–V5)

Verifier độc lập, context sạch. VERIFICATION.md SHA256 kiểm lại = `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79` (khớp). Script được kiểm: `scripts/staging_restore_drop_temp.py` SHA256 `88309119973333349a378cd6e91abf31eaf08b3ee69cb72acd022edfdd1139b6`. Không dùng PROJECT_STATE.md, CHECKPOINT.md, ghi chú giải thích sửa trong OPERATIONS.md, các `VERIFICATION_RESULT_attempt*.md`. Không SSH, không kết nối staging/production thật, không commit/push. Docker chỉ dùng cho một container `postgres:16` (16.15) dùng-một-lần, `--network none`, `log_statement=all`, dữ liệu giả từ `sql/` của repo, đã xóa sau khi chạy.

Test độc lập MỚI: `tests/independent/test_staging_restore_drop_temp_real_pg_adversarial.py` (34 test; chạy 3 lần liên tiếp đều OK, ~75s/lần).

Phần thật vs giả lập trong harness: thật = máy chủ PG 16.15, dropdb/psql 16.15 (argv, PGOPTIONS, hành vi), `main()` của script, toàn bộ SQL gate, `runtime_guard` (thư mục /proc giả + systemctl/git giả), `archive_guard` (file giả), `instance_guard` (postmaster.pid giả). Giả lập = `sudo -n -u postgres` thành `docker exec -u postgres` (giữ nguyên `env -i ... PGOPTIONS=...`), `ss`, đường dẫn data dir, OID/size/hash/mtime pin, `Debian` thành `Ubuntu` trong text version. Chỉ chạy được trên máy này: không chứng minh hành vi `sudo`, systemd, quyền root, filesystem thật của staging.

## Claim V6 — script xóa `scripts/staging_restore_drop_temp.py`

Test thực hiện: dựng cụm PG16 giả có DB nguồn `search_tools_staging` (schema + migration 001–033 của repo), DB tạm đúng tên pin, owner postgres, nhãn pin, PUBLIC CONNECT tắt, rồi chạy `main()` thật với positive control và các negative case ở bảng dưới. Mọi lần chạy đối chiếu: danh sách (tên:OID) DB trước/sau, số dòng bảng nguồn, thống kê ghi của DB khác, argv thật của mọi lệnh con, toàn bộ câu SQL máy chủ ghi log (`log_statement=all`, theo ứng dụng/DB), và canary secret trong stdout/stderr/DSN/argv.

Kết quả: **PASS** (phần script/gate/hậu kiểm; phần chạy thật trên staging vẫn PENDING, xem dưới).

Evidence (Observed):
- Positive control: exit 0, marker `STAGING_TEMP_DATABASE_DROPPED`, không HOLD. Danh sách DB sau = danh sách trước trừ đúng DB tạm, kể cả OID của mọi DB còn lại (có 2 DB anh em cùng tiền tố `search_tools_restore_test_20260930_010101` và `search_tools_restore_test_20261001_0645` — giữ nguyên); số dòng bảng nguồn không đổi; thống kê tup_inserted/updated/deleted của DB khác không đổi.
- dropdb thật: 1 lần gọi, exit 0, stdout/stderr rỗng. Log máy chủ: đúng 1 câu `DROP DATABASE search_tools_restore_test_20261001_064530;` do ứng dụng `dropdb` gửi, trên kết nối `search_tools_staging` (đúng `--maintenance-db`); ngoài ra chỉ có `SELECT pg_catalog.set_config('search_path','',false)` của chính dropdb và các `SELECT json_build_object(...)` của psql chỉ-đọc. Không có terminate/cancel/FORCE.
- Argv ghi đúng một: `sudo -n -u postgres env -i LC_ALL=C LANG=C PGOPTIONS="-c default_transaction_read_only=off -c statement_timeout=60000 -c lock_timeout=5000" PGCONNECT_TIMEOUT=10 /usr/lib/postgresql/16/bin/dropdb -w -h /var/run/postgresql -p 5432 -U postgres --maintenance-db=search_tools_staging <TEMP>`. Mọi tùy chọn nằm trong `dropdb --help` của PG16.15 thật (`-w`, `-h`, `-p`, `-U`, `--maintenance-db`; không `-f/--force`, `--if-exists`, `-i`, `-e`); psql đều `default_transaction_read_only=on`, `-w`. Không DSN/mật khẩu/DATABASE_URL/PGPASSWORD trong argv.
- Mutation sanity (kiểm test thật sự bắt lỗi, bản sao ở scratchpad, không sửa repo): bỏ kiểm session -> test session fail; bỏ hậu kiểm danh sách/OID -> 3 test hậu kiểm fail; thêm `--force` vào argv -> 14 test fail.

Negative cases đã thử (tất cả: không gọi dropdb ghi, DB tạm còn nguyên, không HOLD, không lộ canary, trừ khi ghi khác):
| Tình huống | Kết quả quan sát |
| --- | --- |
| DB tạm đang có session (psql pg_sleep) | `DUNG: TEMP_DATABASE_HAS_SESSIONS`, không dropdb, session vẫn sống (không bị terminate) |
| Session xuất hiện SAU gate, TRƯỚC dropdb (đua) | dropdb thật exit != 0 ("being accessed by other users"), script in HOLD, DB còn, session còn, không retry/force, text thô của dropdb không bị in |
| Sai nhãn / owner khác postgres / PUBLIC CONNECT bật / encoding SQL_ASCII+C | `DUNG: TEMP_DATABASE_IDENTITY` |
| DB tạo lại cùng tên (OID khác OID pin) | `DUNG: TEMP_DATABASE_IDENTITY`, không xóa |
| DB tạm không tồn tại; chạy lại sau lần thành công | `DUNG: TEMP_DATABASE_PRESENCE`, không dropdb, danh sách DB không đổi |
| Tên khác pattern (`search_tools_staging`, `postgres`, thiếu phần giờ, 64 ký tự, hậu tố `_x`, chữ hoa, tên chèn `"; drop database postgres; --`, xuống dòng) | `DUNG: TEMP_NAME_ONLY`, KHÔNG chạy bất kỳ lệnh con nào |
| Hai DB `restore_test` cùng lúc | chỉ DB đúng tên pin bị xóa, DB kia nguyên; khi DB pin vắng, DB kia không bị đụng |
| Không root / thiếu `--drop` / thừa `--force` / `drop` không gạch | `DUNG: ROOT_EXPLICIT_DROP_ONLY`, không lệnh con. Dạng gọi `python -I -B - --drop < script` (như S3) chạy đúng cổng này khi không root |
| Web DSN hoặc worker DSN trỏ TEMP; `?dbname=`/`?host=` override; DSN dạng key=value có dbname TEMP; unit inactive/failed; sai user; web/worker khác DSN; sai commit | lần lượt `*_STAGING_DB_ONLY`, `*_DSN_OPTIONS`, `*_STAGING_RUNTIME`, `STAGING_WEB_WORKER_SAME_DSN`, `*_STAGING_COMMIT`, không drop |
| Archive: sai SHA / size / mtime / symlink / mất file | `ARCHIVE_HASH_UNCHANGED` / `ARCHIVE_METADATA_MATCH` / `ARCHIVE_CANONICAL_PATH` / `DROP_CHECK_FAILED`, không drop |
| Instance khác: pid khác, IP khác, ss rỗng, ss có output lạ, thiếu postmaster.pid | `STAGING_TCP_SOCKET_INSTANCE`/`..._STDOUT_UNEXPECTED`/exception, không drop |
| dropdb client 15/17/có chữ WARNING | `DUNG: DROPDB_VERSION...` |
| Output lạ (WARNING/NOTICE/canary) chèn vào từng gate (listener, version, source, temp) | dừng trước drop, DB còn, không lộ canary |
| Tên DB lạ có dấu gạch ngang ở cụm (`legacy-app-db`) | `DUNG: STAGING_METADATA_READ_STDOUT_UNEXPECTED` trước drop (fail-closed; xem Residual) |
| Static: không có `DROP DATABASE` SQL thô, `pg_terminate`, `--force`, createdb/pg_restore/pg_dump, shell=True; chỉ 1 điểm gọi subprocess | PASS |

Sau/giữa DROP (mọi trường hợp: exit 1, có `HOLD:`, KHÔNG marker thành công, không retry — đúng 1 lần dropdb, không canary, không FORCE):
| Tình huống | Quan sát |
| --- | --- |
| DB khác biến mất ngay sau drop | `DUNG: POSTDROP_ONLY_TEMP_REMOVED` + HOLD (DB tạm đã xóa thật; script không tuyên bố thành công) |
| DB lạ xuất hiện sau drop | `POSTDROP_ONLY_TEMP_REMOVED` + HOLD |
| DB nguồn bị thay bằng bản copy cùng tên nhưng OID mới | `POSTDROP_ONLY_TEMP_REMOVED` + HOLD |
| DB nguồn mất trigger 033 (disable) | `STAGING_SOURCE_METADATA_MATCH` + HOLD |
| Worker thành inactive sau drop | `WORKER_STAGING_RUNTIME` + HOLD |
| postmaster pid đổi sau drop | HOLD |
| Archive sửa 1 byte (cùng size, đặt lại mtime) hoặc chỉ đổi mtime sau drop | `ARCHIVE_HASH_UNCHANGED`/metadata + HOLD, không in `temp_database_exists_after=NO` |
| dropdb thật có output NOTICE (dù thành công) | `TEMP_DROP_COMMAND_STDOUT_UNEXPECTED` + HOLD (DB đã xóa nhưng script nói "trạng thái không rõ", không báo OK) |
| dropdb timeout (DROP_TIMEOUT=1s, dropdb thật đang chờ session) | `DUNG: TEMP_DROP_COMMAND` + HOLD, 1 lần gọi, DB còn, session còn |
| KeyboardInterrupt trong lúc drop | in HOLD, exit 1 |
| SIGKILL / SIGHUP / SIGTERM tiến trình script khi dropdb đang chờ (tiến trình con chạy riêng, quan sát stdout) | tiến trình chết (rc -9/-1/-15), stdout dừng ở `drop_started_at_utc=...`, KHÔNG in HOLD (không thể), KHÔNG in marker thành công. DB tạm còn (do session chặn, dropdb mồ côi thất bại sau ~5s) |

Expected (hợp đồng V6): chỉ xóa đúng DB tạm; kiểm lại identity trước xóa; không FORCE/terminate tự động; sau xóa xác nhận DB tạm không còn, dịch vụ staging active, archive không đổi; chưa duyệt / tên lệch / có kết nối thì không xóa. Observed khớp Expected cho mọi tình huống ở trên.

Classification: không có IMPLEMENTATION_FAIL / REGRESSION_FAIL cho V6.
Reproduction (toàn bộ): `.venv/bin/python -B -m unittest discover -s tests/independent -p 'test_staging_restore_drop_temp_real_pg_adversarial.py'` (cần Docker + image postgres:16; tự bỏ qua nếu thiếu; `VRF_PG_KEEP=1` để giữ container; `VRF_DROP_SCRIPT=<path>` để chạy với bản sao script khi làm mutation).

Residual risk (chưa được chứng minh / biết rõ còn thiếu):
1. Quan sát, không phải lỗi hợp đồng — SPEC_AMBIGUOUS mức thấp: nếu tiến trình script bị giết (mất SSH -> SIGHUP, kill) giữa lúc DROP, KHÔNG có dòng HOLD; PO chỉ thấy output dừng sau `drop_started_at_utc=...` mà không có marker. Trạng thái DB tạm khi đó chỉ biết bằng kiểm tra chỉ-đọc sau đó (dropdb mồ côi có thể vẫn hoàn tất). Marker thành công không bao giờ in trong trường hợp này.
2. Hậu kiểm "danh sách sau = trước trừ DB tạm" chỉ so tên DB và OID của DB nguồn; một DB KHÁC bị drop rồi tạo lại cùng tên (OID mới) không bị phát hiện (test `known_gap` ghi nhận: script vẫn in marker). Thay đổi dữ liệu bên trong DB nguồn đang chạy không thể và không được kiểm; dòng in `live_database_untouched=YES` là chuỗi cố định, chỉ được hậu thuẫn bởi OID + schema033 + tên-còn-đó, không chứng minh dữ liệu nguyên vẹn. Vì cụm staging có hoạt động ghi bình thường nên đây là giới hạn tự nhiên.
3. Availability, fail-closed: mọi DB trên cụm phải có tên `[A-Za-z0-9_]{1,63}` (regex mới chỉ có ở script xóa; các script trước không liệt kê DB). Nếu staging có DB tên có dấu gạch ngang/chấm thì script dừng ở cổng nguồn trước khi drop. Không an toàn bị ảnh hưởng.
4. Cổng session đếm cả background worker (ví dụ autovacuum đang xử lý DB tạm) như một session; khi trùng thời điểm script sẽ dừng (an toàn, PO phải chạy lại thủ công). Ngoài ra `public_connect` chỉ kiểm quyền CONNECT cho PUBLIC, không kiểm GRANT CONNECT trực tiếp cho role ứng dụng; chặn thực tế dựa vào DSN dịch vụ + 0 session + từ chối của dropdb khi còn session.
5. Không kiểm được trên máy này: hành vi `sudo` thật, đường dẫn/quyền root thật, `systemctl`/`/proc` thật, timeout qua SSH, PG 16.15 bản Ubuntu (đã dùng bản Debian cùng 16.15). Đặc biệt cổng `DROPDB_VERSION` và tên DB/locale của staging thật chỉ được kiểm bằng giả lập.
6. Chưa có: xác nhận câu hỏi/đồng ý xóa riêng của PO được ghi vào evidence, kết quả/thời gian thực tế trên staging, cập nhật phiếu/PROJECT_STATE, PR evidence. Đây là phần runtime/tài liệu của V6, trạng thái NOT RUN/PENDING (xem Task status). Production: NOT RUN.

Kết luận V6 (script xóa): **PASS** — đủ điều kiện để PO chạy trên staging, với các residual ở trên.

## Claim V1 — hồi quy nhanh (S0.x chỉ đọc)

Test thực hiện: `.venv/bin/python -B -m unittest discover -s tests -p 'test_staging_restore_*.py'` (toàn bộ test của dự án) và các file độc lập `*_adversarial.py` liên quan (identity, initial_command, db_disk, archive, source, pg_metadata, toc, additional_output, pinned_constants, verifier5_matrix).
Kết quả: **PASS** (không hồi quy).
Evidence: 88 test OK; identity 8/8, initial_command 9/9, archive 9/9, db_disk 7/7, additional_output 4/4, pinned_constants 43/43, verifier5_matrix 25/25, source 8/8, pg_metadata 5/5, toc 11/11 đều OK. `git diff --check` sạch.
Negative case đã thử: bộ test độc lập cũ (web/worker khác DB, mất PID, secret giả) vẫn chạy và OK; hằng pin của script xóa nhất quán với run_temp/create_temp/identity (SOURCE, TEMP, TEMP_OID=18535, LIVE, DATA, BASE, ARCHIVE*, COMMIT, WEB, WORKER, LABEL, ENV) bằng test mới.
Residual risk: như đã ghi ở phần V6; V1 chạy thật trên staging do PO đã thực hiện, không được verifier tự kiểm lại.

## Claim V2 — hồi quy nhanh (S1.1 tạo DB tạm)

Test thực hiện: `test_staging_restore_create_temp.py` (đơn vị) + `test_staging_restore_create_temp_adversarial.py` (11) + `..._create_temp_output_adversarial.py` (4); hằng pin chéo script.
Kết quả: **PASS**.
Evidence: tất cả OK; hằng pin create/drop khớp. `OPERATIONS.md hash(es) attached to create script that differ from actual file: []`.
Negative case: DB tạm đã có / output lạ / ... theo bộ test cũ vẫn dừng; không phát hiện thay đổi hành vi.
Residual risk: chỉ hồi quy tĩnh/mock; kết quả create thật trên staging do PO cung cấp (không kiểm lại).

## Claim V3 — hồi quy nhanh (S1.2 restore)

Test thực hiện: `test_staging_restore_run_temp.py`, `..._run_temp_real_pg_adversarial.py` (PG16 thật), `..._run_temp_shape_leak_adversarial.py`, `..._run_temp_verifier10_adversarial.py`, `..._real_tool_argv_adversarial.py`.
Kết quả: **PASS có lưu ý** (chỉ các lỗi đã biết, không có lỗi mới).
Evidence: shape_leak OK; verifier10 OK; real_pg: 21/23 OK, 2 fail đúng hai mục đã biết là không tính (`test_sigkill_of_direct_child_leaves_grandchild_running_like_sudo`, `test_owner_role_missing_fails_closed_and_rolls_back`); real_tool_argv: 1 fail đã biết (`test_real_pg16_clients_accept_every_argv_we_build`, đếm ngưỡng `4 >= 8` sai), 1 skip.
Negative case: archive hỏng/cắt, thiếu role owner, bảng trống/thiếu, kết quả thiếu 030–032, 033 có sẵn, timeout restore: vẫn HOLD, không DROP/retry (theo bộ test cũ).
Residual risk: các test "đã biết" không được xem xét lại ở lần này.

## Claim V4 — hồi quy nhanh

Test thực hiện: các test counts/main-table trong `test_staging_restore_run_temp*.py` và bộ real_pg (bảng chính trống/thiếu).
Kết quả: **PASS** (không đổi). Evidence: `test_main_table_empty_is_held`, `test_main_table_missing_is_held` và biến thể thuộc nhóm test trên không fail mới. Negative case: bảng trống/thiếu bị HOLD. Residual risk: không chứng minh dữ liệu từng dòng.

## Claim V5 — hồi quy nhanh

Test thực hiện: nhóm `verifier5_matrix_adversarial` (25) + biến thể partial/full 033 trong `run_temp_real_pg`/`verifier10`.
Kết quả: **PASS** (không đổi). Evidence: 25/25 OK; các biến thể `v_nullable_rev`, `v_keys_view`, `v_wrong_trigger` bị giữ lại (không completed). Negative case: archive có 033, partial 033 dừng và không chạy migration. Residual risk: nhận diện 033 đổi tên/đổi schema đã biết là nằm ngoài mục tiêu, kết quả quan sát trong log cũ không thay đổi.

## Task status

attempt: 11 / max_auto_repairs: 2 (script xóa S3 mới; PO chỉ đạo tiếp tục; ngân sách tự động không reset)
same_claim_repeat_fail: none (V6 PASS local + PG16 thật Docker)
next_action: PO_RUNS_S3_DROP_TEMP_DB (PO đã đồng ý xóa riêng; production NOT RUN)
