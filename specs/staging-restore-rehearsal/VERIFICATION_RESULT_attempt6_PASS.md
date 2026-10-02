# Verification Result — staging-restore-rehearsal (verifier độc lập, lần chạy 6)

Do verifier độc lập tạo, context sạch. Chỉ local/mock; không SSH, không PostgreSQL/systemd thật, không production, không commit/push, không sửa scripts/ hay tests có sẵn.

Hợp đồng khóa: `specs/staging-restore-rehearsal/VERIFICATION.md`, SHA256 đã kiểm lại = `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79` (khớp).
Artifact được verify (SHA256 hiện tại): `scripts/staging_restore_identity_readonly.py` = `d0a32e3e11e023da62ca37361fa9e34cebcaefde6e9583f34d7159fe2225ec5a`; `scripts/staging_restore_create_temp.py` = `55f36ef6d8e15a435a7b6052d3c1bf4a4bf0c91bc6fda1267fa6b8ad2bb9bca2`.
Nguồn bằng chứng: code script, hợp đồng, hành vi thực thi. Không dùng PROJECT_STATE.md, CHECKPOINT.md, ghi chú giải thích sửa trong OPERATIONS.md, hay nội dung các `VERIFICATION_RESULT_attempt*.md` (ngoài Observed/Expected/Reproduction/Classification lần fail trước). Các dòng "output PO" trong OPERATIONS.md chỉ được dùng làm dữ liệu thật để đối chiếu hằng pin (hash/bytes/mtime/data dir/số đĩa), không dùng để kết luận PASS.

## Lệnh đã chạy (từ thư mục gốc, .venv Python 3.9.6)

| Lệnh | Kết quả |
| --- | --- |
| `.venv/bin/python -B -m unittest discover -s tests -p 'test_staging_restore_*.py'` | Ran 65, OK |
| từng `tests/independent/test_staging_restore_*adversarial.py` (bỏ `test_staging_restore_preflight.py`) | 12 file; additional_output 4, archive 9, create_temp 11, create_temp_output 4, db_disk 7, identity 8, initial_command 9, pg_metadata 5, source 8, toc 11, verifier5_matrix 25 đều OK; **file mới** `test_staging_restore_pinned_constants_adversarial.py` Ran 43, OK |
| `git diff --check` | exit 0, không cảnh báo |
| kiểm đột biến (bản sao trong scratchpad, không đụng repo) | 8 đột biến hằng pin/logic (SHA 63 ký tự, bytes+1, mtime đơn vị giây, mtime+1ns, buffer 2x thay 4x, bỏ so sánh archive sau create, bỏ so khớp PID, commit sai 1 ký tự) đều bị test mới bắt (FAIL) |

Test độc lập mới: `tests/independent/test_staging_restore_pinned_constants_adversarial.py` (43 test; không patch `archive_guard`; fixture file thật mang đúng kích thước 37333943 byte và mtime ns đã pin; /proc, systemctl, git, ss, psql, createdb, locale là fake).

## Kết luận tóm tắt

- **V1: PASS**
- **V2 (phần S1.1 tạo DB trống): PASS**
- V3, V4, V5, V6: NOT RUN / PENDING (chưa chuẩn bị lệnh/chưa thực thi; không tính PASS, không tính DONE toàn task)

Lưu ý quan trọng: PASS này là PASS local/mock của tầng an toàn S0.4 và S1.1. Không thay thế kết quả chạy thật trên staging; production luôn NOT RUN.

## Claim V1

Test thực hiện:
- Chạy lại toàn bộ test developer + 11 file adversarial cũ (ma trận stdout/stderr/exit-code/exception cho từng gate S0.4, biến thể DSN, web≠worker).
- Test mới `IdentityScriptS04`: đi qua `main()` với DSN thật-dạng (ký tự đặc biệt, port khác, `localhost`, `[::1]`, `?sslmode=require`), ghi lại mọi `subprocess.run`: đúng 4 lệnh (systemctl show ×2, git rev-parse ×2), argv chính xác, env = `READ_ENV`, không shell; mọi dòng stdout khớp whitelist cố định (không DSN/password/username). Quét tĩnh code (bỏ docstring): không có psql/createdb/pg_*/sudo/Popen/os.system/open(/write/unlink/start/stop/restart/reload/.env/dotenv; đúng 1 điểm `subprocess.run(`.
- Test mới `StdinWiringWithRealPython`: chạy script bằng `python -I -B -` thật qua stdin: không root -> `ROOT_NO_EXTRA_ARGUMENTS`; có tiền tố giả root + đối số thừa -> cùng gate; có tiền tố giả root, không đối số -> dừng ở `STAGING_LIVE_DIRECTORY` (máy local không có `/srv/search-tools`) trước mọi lệnh con, stderr rỗng. Lệnh `ssh staging 'sudo -n env LC_ALL=C LANG=C python3 -I -B -'` trong OPERATIONS.md khớp đúng cách gọi này.

Kết quả: PASS

Evidence (Observed): 65/65 developer OK; adversarial cũ OK; test mới `IdentityScriptS04` 3/3 và wiring 5/5 OK. Với DSN mang canary `V6_FAKE_SECRET_CANARY`, stdout thành công có đúng 8 dòng cố định; ca lỗi in đúng `DUNG: <GATE> (no secret output, no retry)`, canary không xuất hiện ở bất kỳ ca nào. Port 0 / 70000, host 10.0.0.9, DB `search_tools_prod`, `?host=evil`, DSN dạng key=value đều dừng, exit 1.
Expected: chỉ đọc; lệnh con chỉ systemctl show/git rev-parse; sai DB/host/port/commit/web≠worker/warning/stderr -> dừng, không in secret.

Negative case đã thử: 27 biến thể stdout x 4 gate, stderr x 5 x 4 gate, exit code 1/2/124/-9, exception runner (Timeout/FileNotFound/Permission/UnicodeDecode/RuntimeError mang DSN), DSN sai đích (13+ biến thể), DSN web≠worker, argv thừa, không phải root. Tất cả dừng fail-closed.

Classification: không áp dụng (PASS).
Residual risk: chưa chạy trên server thật (PO tự chạy); `/proc/<pid>/environ` và systemd thật không được mô phỏng đầy đủ; S0.4 chấp nhận host `localhost`/`::1` rộng hơn S1.1 (S1.1 chỉ `127.0.0.1`, nên an toàn hơn chứ không hở); stdout whitespace/CRLF lành tính vẫn được chấp nhận (chỉ thông tin, không phải bất thường).

## Claim V2 (phạm vi S1.1: tạo DB tạm trống; không restore)

Lần fail trước (Observed): `ARCHIVE_SHA` dài 63 ký tự nên `archive_guard()` không bao giờ khớp. Kiểm lại lần này: `len(ARCHIVE_SHA)==64`, khớp `[0-9a-f]{64}`, bằng giá trị hash PO đã in ở S0.3; lớp `ArchiveHashShape` cũ PASS; test mới chứng minh với dữ liệu thật dạng (không patch) rằng cổng không còn kẹt ở bước hash do hình dạng hằng số.

Test thực hiện (mới, mỗi mục là adversarial riêng, không chỉ patch):
1. **Hằng pin vs dữ liệu thật** (`PinnedConstantsAgainstRealData`): SHA 64 hex thường và bằng output PO; `ARCHIVE_BYTES` = `backup_bytes` PO (int, đơn vị byte); `ARCHIVE_MTIME_NS` tính lại độc lập bằng `calendar.timegm` từ chuỗi `2026-09-29 08:03:10.692114220 +0000` = đúng; độ lớn ns (1e18..1e19), vòng khứ hồi `os.utime`/`st_mtime_ns` trên filesystem thật đúng; `COMMIT` 40 hex = `ID.TARGET` = tiền tố `2ec9b6c` trong tên archive = output PO; `TEMP` đúng regex, <=63 byte, ngày/giờ hợp lệ thật (`20261001_064530`) khớp mốc snapshot S0.7 và nhãn `snapshot=20261001T064530Z`; `LABEL`/`TEMP` không chứa ký tự phá chuỗi SQL; `DATA`, `BASE=DATA/base`, `ARCHIVE`, `LIVE`, `WEB`, `WORKER`, `SOURCE` nhất quán giữa hai script, tuyệt đối/chuẩn hóa, và khớp lệnh S0.1/S0.2/S0.3/S0.5/S0.6; `160015` đúng số PostgreSQL báo cho 16.15; công thức đĩa tái hiện số PO: với DB 1.228.176.407 byte và free 60.691.943.424 -> `(free, required)=(60691943424, 5368709120)`.
2. **archive_guard không patch, fixture thật** (`ArchiveGuardWithRealPins`): file sparse đúng 37333943 byte, mtime ép đúng `ARCHIVE_MTIME_NS`; `archive_guard` với hằng pin thật chỉ dừng ở `ARCHIVE_HASH_UNCHANGED` (tức bytes/mtime pin dùng được, chỉ hash khác vì fixture); khi digest = giá trị pin (hexdigest được stub trả đúng hằng) cổng PASS và trả bytes/mtime pin. Hoán vị từng pin: SHA đổi 1 ký tự / hoa / thừa khoảng trắng / 63 / 65 ký tự; bytes ±1; mtime ±1 ns, ±1 s, đơn vị giây -> đều bị chặn. Can thiệp file thật: lật 1 byte giữ mtime (`ARCHIVE_HASH_UNCHANGED`), thêm 1 byte, touch +1ns, thay bằng symlink (`ARCHIVE_CANONICAL_PATH`), thay bằng thư mục, file mất.
3. **Full `main()` trên filesystem thật** (`RealFilesystemFullFlow`, `archive_guard` thật; chỉ `ARCHIVE_SHA` được gán digest fixture vì không thể tạo preimage): luồng sạch với số của PO -> thành công đúng 1 lần CREATE, `data_base_free_bytes=60691943424 required_free_bytes=5368709120`; archive lỗi TRƯỚC create (size±1, mtime±1ns, lật byte, symlink, mất, thư mục) -> dừng tại `ss#1`, không có lệnh PG nào, không `create`, không HOLD; archive bị can thiệp SAU create (tại create#1/acl#1/temp#1/systemctl#3 x lật byte/touch/symlink) -> exit 1, không marker thành công, in `HOLD: CREATE attempted` và `temp_database=`, đúng 1 lệnh create, không retry; đĩa thiếu 1 byte / 0 / 1 -> `DEFAULT_TABLESPACE_FREE_SPACE`, dừng trước create.
4. **Whitelist lệnh toàn luồng + SQL**: mọi argv thuộc tập cố định (systemctl show, git rev-parse, ss, locale -a, createdb --version, và sudo -n -u postgres env -i ... psql|createdb); đúng 1 `createdb` tạo (đích cuối là TEMP, `--maintenance-db=SOURCE`, `--template=template0`, owner postgres, pg_default, không `--clean/--create/--force`), đúng 1 psql ghi (database = TEMP, đúng 2 câu `REVOKE CONNECT ... FROM PUBLIC` và `COMMENT ON DATABASE ... IS '<LABEL>'`), 2 psql đọc chỉ `SELECT` không từ khóa ghi/terminate/pg_read_file; không `postgres://`, không secret, không `shell=True`, `env=CR.ENV`, `check=False`. SQL schema033 nội tuyến = chính xác chín cờ trong `SOURCE_CHECK.sql` (đã được PO chạy và thấy true) nối bằng AND (so sánh sau chuẩn hóa khoảng trắng), ngoặc/dấu nháy cân bằng.
5. **Runtime guard S1.1 với DSN** (`CreateRuntimeGuardDsn`): đúng chấp nhận (có/không port, `?sslmode`, mật khẩu percent-encode); từ chối localhost, `[::1]`, 127.0.0.2, host lạ, port 5433, DB prod/temp/viết hoa, thiếu user, `?host=`/`hostaddr`/`dbname`, option rỗng/trùng, fragment, scheme lạ, dạng key=value, thiếu/rỗng/trùng/non-UTF8 `DATABASE_URL`, web≠worker, commit sai 1 ký tự hoặc rút gọn 7 ký tự; không lần nào lộ canary, gate in ra theo dạng `[A-Z0-9_]+`.
6. **Instance/ss/định dạng tool thật** (`RealisticToolOutputs`): `systemctl show` thứ tự bất kỳ; `createdb --version` biến thể Ubuntu; `locale -a` dạng thật; `git rev-parse HEAD` thật của repo; `ss` IPv4+IPv6 cùng postmaster và wildcard 0.0.0.0 được chấp nhận; từ chối chỉ-IPv6, PID khác, PID là tiền tố/hậu tố, sai địa chỉ/cổng, không LISTEN, rỗng, `postmaster.pid` dạng sai (số 0 đầu, khoảng trắng, rỗng, symlink), `DATA` là symlink.
7. **Cách PO gọi** (`StdinWiringWithRealPython`): `python -I -B - --create` qua stdin; không root -> `ROOT_EXPLICIT_CREATE_ONLY`; thiếu `--create`, thừa đối số, `--CREATE`, `--create=1`, `-`, `--create --create` -> cùng gate, không in `temp_database`; đúng `--create` -> chỉ tới `STAGING_LIVE_PATH` (local không có staging), không `PRECREATE_READONLY_GATES_OK`, không HOLD, stderr rỗng.

Kết quả: PASS

Evidence (Observed): test mới 43/43 OK; toàn bộ test developer 65/65 và 11 file adversarial cũ OK. Cụ thể: `len(ARCHIVE_SHA)=64`; mtime pin = 1790668990692114220 ns = 2026-09-29 08:03:10.692114220 UTC; luồng thật trên fixture đạt `STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)` với trace đúng 16 bước, đúng 1 lệnh create; mọi ca archive lỗi trước create không phát lệnh PG nào; mọi ca archive đổi sau create đều in HOLD và không in marker thành công. Kiểm đột biến: 8/8 bị phát hiện.
Expected: oracle "hash archive ghi trước và giữ nguyên sau" phải thỏa được với file đúng; mọi hằng pin phải dùng được với dữ liệu thật; chỉ tạo DB tạm trống mới trên đúng staging, không ghi đè DB đang dùng, không dùng DSN/password trong argv, dừng không retry/cleanup khi bất thường.

Negative case đã thử: xem các mục 1–7 ở trên, cộng ma trận stdout/stderr/exit-code/exception x 16 gate của verifier cũ (đều PASS), JSON metadata biến thể (trùng key, thêm/thiếu key, NaN, số dạng chuỗi, bool thay int), sai ngữ nghĩa (version, port, read_only, encoding, locale, provider, tablespace, temp_exists, schema033, oid=source, owner, label, public_connect, user_tables).

Classification: không áp dụng (PASS). Lỗi IMPLEMENTATION_FAIL của lần trước (SHA 63 ký tự) đã hết: không tái hiện được, test `ArchiveHashShape` cũ và test mới đều PASS.

Quan sát ngoài phạm vi kết luận (không đổi PASS/FAIL):
- SPEC_AMBIGUOUS (tài liệu): `OPERATIONS.md` mục S1.1 vẫn gắn hash `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16` cho `scripts/staging_restore_create_temp.py` ở đoạn mô tả lệnh, trong khi hash thực tế là `55f36ef6d8e15a435a7b6052d3c1bf4a4bf0c91bc6fda1267fa6b8ad2bb9bca2` (hash đúng có xuất hiện ở đoạn cập nhật đầu mục). Hai hash khác nhau cho cùng một artifact trong tài liệu gửi PO. Lệnh S1.1 không tự kiểm hash nên không ảnh hưởng hành vi script. Reproduction: `grep -n "56e0575" specs/staging-restore-rehearsal/OPERATIONS.md` và so với `shasum -a 256 scripts/staging_restore_create_temp.py`; test mới `test_pinned_script_hashes_in_operations_equal_actual_files` in danh sách hash lệch.
- Thay file archive bằng bản sao giống hệt nhưng inode khác sau create sẽ bị coi là "đổi" (fingerprint gồm st_ino): bảo thủ, dừng an toàn, không phải lỗi.

Residual risk:
- Không thể xác minh cục bộ rằng giá trị SHA256 pin đúng bằng hash của file thật trên server (không có file đó; không preimage). Chỉ chứng minh được: đúng hình dạng 64 hex, bằng giá trị PO báo, và logic so sánh chấp nhận hằng đúng hình dạng. Nếu chép sai nhưng vẫn đủ 64 ký tự thì lỗi chỉ là dừng an toàn tại `ARCHIVE_HASH_UNCHANGED` trước mọi lệnh ghi (đã chứng minh dừng trước create), không phải ghi nhầm.
- Hành vi thật của `psql`/`createdb`/`systemctl`/`ss`/`locale` 16.15 trên Ubuntu chưa chạy (cấm kết nối): đầu ra multi-statement `REVOKE`+`COMMENT`, cờ `createdb --locale-provider/--lc-collate`, JSON `json_build_object` chỉ được suy ra từ tài liệu PostgreSQL và dạng đầu ra đã quan sát gián tiếp qua evidence PO; bất kỳ lệch dạng nào sẽ dừng an toàn (trước create hoặc HOLD sau create) chứ không im lặng.
- Python local 3.9.6, server có thể khác; script chỉ dùng stdlib cơ bản nhưng chưa chạy trên phiên bản server.
- Race giữa các cổng và createdb (TOCTOU) không loại trừ được; createdb tự chặn DB trùng tên (không ghi đè).
- Chỉ phạm vi S1.1 tạo DB trống; restore/đối chiếu/xóa không thuộc kết luận này.

## Claim V3

NOT RUN / PENDING — lệnh restore chưa được chuẩn bị, chưa thực thi; không tính PASS.

## Claim V4

NOT RUN / PENDING — chưa có DB tạm đã restore để đối chiếu counts; không tính PASS.

## Claim V5

NOT RUN / PENDING — chưa có kiểm schema DB tạm / staging cho 033 ở bước sau create; không tính PASS (chỉ lưu ý cổng schema033 của DB nguồn trong S1.1 chạy qua fake, không phải evidence V5).

## Claim V6

NOT RUN / PENDING — chưa có phê duyệt xóa riêng, chưa chuẩn bị lệnh xóa; không tính PASS. Production: NOT RUN.

## Task status

attempt: 6 / max_auto_repairs: 2 (repair5 PO chỉ đạo 2026-10-02; verifier lần 6 sau sửa hash archive; ngân sách tự động không reset)
same_claim_repeat_fail: none (V1 PASS, V2 PASS local/mock)
next_action: PROCEED_TO_S1_1_CREATE_EMPTY_TEMP_DB_PO_RUNS_ONE_COMMAND (không phải quyền restore/xóa; production NOT RUN)
