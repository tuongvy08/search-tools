# Verification Result — staging-restore-rehearsal (S1.2 restore script; V1/S1.1 regression)

Verifier độc lập, context sạch. Hợp đồng `VERIFICATION.md` SHA256 kiểm lại = `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79` (khớp). Script kiểm: `scripts/staging_restore_run_temp.py` `c622e597748a3af2b5d6185fec08588923ea2ffd560cc266544aba729d902f2b` (khớp hash pin trong hồ sơ vận hành); `staging_restore_create_temp.py` `a9fe5d3f…397c`; `staging_restore_identity_readonly.py` `d0a32e3e…ec5a`. Không SSH, không kết nối staging/production, không commit/push.

Nguồn đối chiếu công cụ: postgresql.org/docs/16/app-pgrestore (đã đọc: `--single-transaction` bọc BEGIN/COMMIT và ngầm `--exit-on-error`; `-d` nhận cả chuỗi kết nối; `-w` không hỏi mật khẩu; không có tên file thì đọc stdin; mặc định pg_restore tiếp tục khi lỗi và chỉ báo số lỗi cuối). Binary thật: container Docker dùng-một-lần `postgres:16` (PostgreSQL **16.15 Debian**, cùng bản 16.15 với server staging), `--network none`, dữ liệu giả dựng từ chính `sql/schema.sql` + `migration_002..032` (pre-033) và thêm `033` (nguồn). Container đã xóa sau khi chạy.

## Cách kiểm thật (test độc lập mới)

`tests/independent/test_staging_restore_run_temp_real_pg_adversarial.py` (23 test; tự dựng/xóa container, tự SKIP nếu không có Docker/image). Chạy THẬT: máy chủ PG16, pg_dump/pg_restore/psql/createdb, `main()` thật của script S1.1 (tạo DB tạm trống) rồi `main()` thật của S1.2 với các gate SQL thật và archive custom-format thật; stdin của pg_restore thử cả dạng file thường (có seek, như sudo trên staging) và pipe. Phần GIẢ (không thể có ở máy này, ghi rõ): tiền tố `sudo -n -u postgres env -i` thay bằng `docker exec -u postgres`; `runtime_guard` (systemctl/git/proc) vô hiệu; `ss` giả từ postmaster.pid fixture; thư mục data/đĩa là thư mục fixture; hằng pin archive (đường dẫn/size/mtime/SHA), OID và LABEL thay bằng giá trị fixture; chuỗi phiên bản `Debian`→`Ubuntu` để qua regex. Archive/dữ liệu là giả (5000 products, 30 stock_items, 60 rules, 6 statuses, 8 inactive) — KHÔNG phải archive/dữ liệu staging thật.

## Lệnh con thật của S1.2 (argv ghi lại khi chạy main thật)

Môi trường: `PATH=/usr/local/bin:/usr/bin:/bin LC_ALL=C LANG=C`. Lệnh ghi duy nhất:
`sudo -n -u postgres env -i LC_ALL=C LANG=C PGOPTIONS="-c default_transaction_read_only=off -c statement_timeout=0 -c lock_timeout=5000" PGCONNECT_TIMEOUT=10 /usr/lib/postgresql/16/bin/pg_restore --exit-on-error --single-transaction -w -h /var/run/postgresql -p 5432 -U postgres --dbname=<TEMP>` với archive trên stdin (mở bằng `open(ARCHIVE)` lần thứ hai, không phải fd đã hash). Mọi lệnh khác (psql nguồn/tạm, `ss`, `pg_restore --version`) đều `default_transaction_read_only=on`. Binary 16.15 thật chấp nhận argv; docs 16 khớp từng cờ. Không có DROP/dropdb/createdb/pg_dump/terminate/FORCE/start/stop trong script (kiểm tĩnh + kiểm argv thực thi).

## Claim V2 (S1.2: chỉ restore archive có sẵn vào DB tạm, không ghi DB đang dùng) — phần an toàn của lệnh restore

Test thực hiện: chạy S1.1 thật rồi S1.2 thật trên PG16 thật; kiểm argv; so `pg_stat_database` tup_inserted/updated/deleted của DB nguồn trước/sau; so md5 nội dung bảng products giữa DB nguồn dump và DB tạm; các gate trước ghi với DB tạm sai.
Kết quả: PASS
Evidence (Observed): happy path (stdin file và pipe): exit 0, marker `STAGING_TEMP_RESTORE_COMPLETED`, không HOLD; counts in ra 5000/30/60/6, inactive 8, đúng dữ liệu nguồn; md5 dữ liệu products DB tạm = DB dump nguồn; tup_* của DB "live" không đổi; đúng 1 lệnh pg_restore, `--dbname=<TEMP>`, không `--clean/--create`, không tên DB nguồn trong argv. Gate: chạy lần 2 sau thành công → `DUNG: TEMP_DATABASE_MUST_BE_EMPTY`, không gọi restore, không HOLD; DB tạm có 1 session mở → `TEMP_DATABASE_HAS_SESSIONS`; OID sai → `TEMP_DATABASE_IDENTITY`; DB tạm không tồn tại → `TEMP_DATABASE_MUST_EXIST` và không tạo DB; thiếu/sai cờ → `ROOT_EXPLICIT_RESTORE_ONLY`, không lệnh nào chạy.
Negative case đã thử: các gate trên; truy vấn mọi psql có `default_transaction_read_only=on` và chỉ nhắm TEMP hoặc nguồn.
Residual risk: sudo/systemd/git/proc, đường dẫn/đĩa/OID/hash thật chưa được chạy (chỉ gate đó đã chạy thật ở S1.1 trên staging). `open(ARCHIVE)` lần hai là TOCTOU nhỏ giữa hash và restore (có kiểm lại sau; chỉ ảnh hưởng DB tạm). Không chứng minh archive staging thật (376 mục, 1.2 GB) restore sạch — chỉ chứng minh cơ chế bằng archive giả cùng schema thật của repo.

## Claim V3 (restore thật hoàn tất; exit code; lỗi không báo đạt; không lấy TOC thay restore)

Test thực hiện: pg_restore thật với archive hợp lệ và các archive xấu; đối chiếu stdout/stderr thực; timeout; mất stdout.
Kết quả: PASS (có residual risk ghi bên dưới, không phải FAIL theo hợp đồng)
Evidence (Observed): trường hợp thành công: pg_restore thật trả `(exit 0, stdout "", stderr "")` — khớp yêu cầu "rỗng khi đạt" (cả stdin file lẫn pipe). Thất bại → exit 1, `DUNG`, `HOLD`, KHÔNG marker, đúng 1 lần gọi pg_restore (không retry), DB tạm vẫn tồn tại (không drop), DB nguồn không đổi: (a) archive tham chiếu role chủ sở hữu không còn tồn tại → `TEMP_RESTORE_COMMAND`, in `restore_error_first_line=pg_restore: error: ...vrf_ghost...`, DB tạm 0 bảng (rollback hoàn toàn nhờ --single-transaction); (b) archive cắt nửa → HOLD, 0 bảng; (c) 4000 byte giữa bị lật → HOLD, 0 bảng; (d) restore xong nhưng archive chứa 033 → `TEMP_MUST_BE_PRE_033`, DB tạm giữ nguyên đã restore, không drop. Timeout (ép 1 giây): `DUNG: TEMP_RESTORE_COMMAND_TIMEOUT` + HOLD, không báo hoàn tất.
Negative case đã thử: danh sách (a)–(d) và timeout.
Residual risk (quan sát được, không vi phạm chữ trong hợp đồng): (1) Sau timeout, `subprocess.run` chỉ SIGKILL tiến trình con trực tiếp (trên staging là `sudo`); trong thử nghiệm tương đương (docker exec) pg_restore thật KHÔNG bị dừng: lúc HOLD có 0 bảng, vài giây sau DB tạm có 43 bảng (transaction đã COMMIT) trong khi script đã in `DUNG/HOLD`. Stand-in POSIX fork-wait cho thấy cùng cơ chế (con cháu sống sót). Sudo thật chưa được quan sát (không có ở máy này); thông báo HOLD không dặn kiểm tra pg_restore còn chạy. (2) Mất kết nối SSH không tty: tiến trình không bị hủy; khi chỉ print đầu ra hỏng, restore vẫn commit (tái hiện: DB tạm 42 bảng và script trả mã 1 + DUNG/HOLD sai nghĩa; thực tế dòng print lỗi BrokenPipe). Mô tả vận hành "SSH đứt thì restore bị hủy" không được chứng minh. (3) Dòng lỗi đầu của pg_restore được in (tối đa 140 ký tự, lọc ký tự) có thể chứa mảnh dữ liệu với một số loại lỗi kiểu nhập liệu; chưa tái hiện với dữ liệu thật. (4) Fail-closed khi pg_restore thành công nhưng có bất kỳ stderr/NOTICE → HOLD dù DB tạm đã đầy; không quan sát trong ca thật, lưu ý có thể gây DUNG sai trên archive thật.

## Claim V4 (phần script tự hậu kiểm: bốn bảng chính tồn tại và có dữ liệu)

Test thực hiện: restore thật rồi để script đọc; archive có products rỗng; archive thiếu regulatory_statuses; so truy vấn footprint/đếm với `SOURCE_CHECK.sql` trên PG thật.
Kết quả: PASS
Evidence (Observed): products rỗng → `DUNG: TEMP_MAIN_TABLES_PRESENT_NONEMPTY` + HOLD, giữ DB; thiếu bảng → psql lỗi → `TEMP_RESTORED_READ` + HOLD; ca đạt in đủ 4 count + inactive. Biểu thức 9 cờ 033 và 3 cờ 030–032 của script cho kết quả BẰNG HỆT câu trong `SOURCE_CHECK.sql` (thực thi cả hai trên 8 DB: pre-033, 033, nullable-revision, function-only, keys-as-view, trigger-sai, thiếu 030, thiếu 032).
Negative case: các ca trên.
Residual risk: script tự nó không đối chiếu counts với snapshot (1262173/2295/6003/6/3) — chỉ >0; việc so sánh vẫn do người/agent sau khi nhận output. Count (>0) không chứng minh toàn bộ dòng.

## Claim V5 (phần script tự hậu kiểm: DB tạm trước 033; 030–032 có)

Test thực hiện: 033 đầy đủ; function-only; thiếu 030/032; và các ca cùng tên khác hình dạng.
Kết quả: **FAIL** (độ nghiêm trọng thấp, chỉ với dữ liệu tổng hợp; xem ghi chú)
Evidence (Observed): archive đủ 033 → HOLD `TEMP_MUST_BE_PRE_033`; function-only → HOLD cùng gate; thiếu 030/032 → `TEMP_MISSING_030_032`. NHƯNG ba DB tạm sau restore có vật thể mang đúng tên footprint 033 nhưng hình dạng khác cờ chặt của script: (i) cột `regulatory_rules.revision` bigint NULLABLE; (ii) `regulatory_rule_manual_keys` là VIEW; (iii) trigger `zz_regulatory_rule_revision` AFTER INSERT dùng hàm khác → cả ba cho exit 0, in `footprints_033=...False` và marker `STAGING_TEMP_RESTORE_COMPLETED`. Oracle hợp đồng V5 là các footprint riêng 033 "đều vắng"; script thực tế kiểm "có với hình dạng chính xác", nên False không chứng minh vắng. Test lỗi: `test_v5_same_name_different_shape_033_residue_must_be_held` → FAIL (`{'v_nullable_rev': (0, True), 'v_keys_view': (0, True), 'v_wrong_trigger': (0, True)}`, mong đợi `(1, False)`).
Negative case đã thử: xem trên.
Classification: IMPLEMENTATION_FAIL (thấp)
Reproduction: `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_run_temp_real_pg_adversarial.py -k same_name_different_shape` (cần Docker + image postgres:16).
Ghi chú ngữ cảnh (không phải giải pháp): migration 033 nguyên tử và đúng hình dạng nên trạng thái này không thể phát sinh tự nhiên từ việc chạy 033; archive dự kiến đặt tên prechange và TOC S0.3 không có bảng/function/trigger 033. Mức rủi ro thực tế thấp, nhưng claim như viết chưa được chứng minh bằng thực thi. Còn lại 030–032 (`footprint` chỉ kiểm "có") đạt.
Residual risk: các cờ 033 chưa phân biệt "vắng" với "có nhưng sai hình"; cũng không kiểm index 033 theo tên bất kể bảng.

## Hồi quy V1 / S1.1 (nhanh)

Test thực hiện: chạy lại bộ test developer và bộ independent cũ; dùng S1.1 thật (main thật) làm bước dựng DB tạm cho mọi ca restore; đối chiếu hash.
Kết quả: PASS
Evidence (Observed): `tests/test_staging_restore_*.py` 76 test OK; independent: additional_output 4, archive 9, create_temp 11, create_temp_output 4, db_disk 7, identity 8, initial_command 9, pg_metadata 5, pinned_constants 43, source 8, toc 11, verifier5_matrix 25 — đều OK; `test_staging_restore_real_tool_argv_adversarial.py` 11 test: 1 FAIL đã biết (`4 not greater than or equal to 8`, đếm sai ngưỡng) + 1 skip — chỉ ghi nhận, không tính mới; `git diff --check` sạch. S1.1 thật tạo DB tạm trống trên PG16 thật ở mọi ca (marker `STAGING_TEMP_DATABASE_CREATED`, owner postgres, PUBLIC CONNECT tắt, nhãn khớp) và S1.2 chấp nhận đúng DB đó (nhãn/owner/ACL khớp).
Negative case: S1.2 từ chối DB tạm có OID/phiên/bảng khác (xem V2).
Residual risk: script S0.4 chưa chạy lại ở máy này với tool thật ngoài mock (đã có evidence server sạch ở lần trước).

## Claim V6

NOT RUN / PENDING (chưa chuẩn bị lệnh xóa, chưa có PO duyệt xóa; production NOT RUN). Không có lệnh xóa nào trong S1.2 và script không có đường xóa/cleanup.

## Phần chưa chứng minh được (tổng hợp)

Restore archive staging thật (376 mục, ~1.2 GB, extension/role/ACL thật, thời gian vs timeout 3600 s); hành vi `sudo` thật (stdin file, SIGKILL/orphan); runtime_guard/systemctl/git/proc thật; stderr/NOTICE do archive thật sinh ra; chênh lệch counts với snapshot nguồn. PASS ở đây là PASS cơ chế trên PG16.15 thật với dữ liệu giả, không thay evidence restore staging thực.

## Task status

attempt: <placeholder> / max_auto_repairs: 2
same_claim_repeat_fail: <placeholder>
next_action: <placeholder>
