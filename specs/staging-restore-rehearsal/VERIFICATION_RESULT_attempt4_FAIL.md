# Verification Result — staging-restore-rehearsal

Do verifier độc lập tạo ngày 2026-10-01. Chỉ nhận yêu cầu, hợp đồng khóa, phạm vi diff, lệnh test và Observed/Expected/Reproduction/Classification của lần fail trước. Không dùng comment hoặc mô tả repair làm bằng chứng hành vi.

## Phạm vi và artifact

- Review S1.1 tạo DB **trống**, và phần chỉ đọc/evidence S0 liên quan; không coi đây là review lệnh restore hoặc drop chưa chuẩn bị.
- Branch `ops/staging-restore-rehearsal`, đối chiếu `git diff 207cd11` và đọc trực tiếp các file mới chưa tracked. Application code/artifact deploy cũ không được verifier sửa; `opencode.json` ngoài phạm vi.
- Hợp đồng SHA256 khớp giá trị khóa: `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79`.
- Script S1.1 SHA256: `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16`.
- Script S0.4 SHA256: `1d3ab0f853046fd3ecf546e11bc4a819bf2c9dddc256e4a353807b12faebc489`.
- SOURCE_CHECK.sql SHA256: `05a868759de8611451bd3aec060fc2b6d70bce31f5ed54943a41fe9b394f2951`.
- Toàn bộ thực thi verifier chỉ local/mock hoặc subprocess Python local. Không SSH, không chạy sudo/systemctl/ss/psql/createdb thật, không kết nối DB, không production. Fixture mới chỉ trong `tests/independent/**`.

## Lệnh và kết quả quan sát

1. `.venv/bin/python -B -m unittest discover -s tests -p test_staging_restore_create_temp.py -v`: **13/13 PASS**.
2. `.venv/bin/python -B -m unittest discover -s tests/independent -p 'test_staging_restore_create_temp*adversarial.py' -v`: **15/15 PASS**.
3. `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_identity_adversarial.py -v`: **8/8 PASS**. Bộ này không có oracle từ chối warning exit 0; kết quả không thay thế probes mới bên dưới.
4. `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_source_adversarial.py -v`: **8/8 PASS**. SQL catalog semantic execution trên PostgreSQL thật không chạy.
5. `.venv/bin/python -B -m unittest discover -s tests/independent -p test_staging_restore_additional_output_adversarial.py -v`: **4 test methods; 1 PASS, 3 methods FAIL, tổng 4 failure records** (một method có hai subtests FAIL).
6. `git diff --check`: exit 0. Hash tính lại khớp các giá trị trên.

Ba negative cases lần fail trước đã đạt trên hash S1.1 hiện tại:

- Warning stdout ở version: exit 1; trace `source → locale → version`; `DUNG: CREATEDB_VERSION_STDOUT_UNEXPECTED`; không CREATE/HOLD/success marker.
- Warning stdout ở CREATE: exit 1; trace kết thúc ở `create`; `DUNG: TEMP_CREATE_COMMAND_STDOUT_UNEXPECTED` và HOLD; không ACL hoặc success marker.
- Warning stdout ở metadata write: exit 1; trace kết thúc ở `acl`; `DUNG: TEMP_METADATA_WRITE_STDOUT_UNEXPECTED` và HOLD; không temp hậu kiểm hoặc success marker.
- Không canary giả trong output các probes này. Các PASS hồi quy trên không làm cho mọi output bất thường đã được chặn.

## Claim V1 — Bước đầu chỉ kiểm staging, không ghi hoặc lộ secret

Test thực hiện: review các lệnh S0 và SQL SELECT/SHOW, alias staging, unit staging, env sạch/read-only; chạy 8 tests identity và 8 tests source độc lập; thêm real local stderr-warning probe và full-flow stdout/stderr-warning probes cho script S0.4.

Kết quả: **FAIL — quy tắc dừng khi bất thường ở S0.4**. Không quan sát lệnh ghi/secret leak trong các probes S0; failure là warning bị che/bỏ qua và báo identity thành công.

Evidence / Observed:

- `test_s04_real_local_stderr_warning_is_not_success` chạy Python local thực, exit 0, stdout `valid`, stderr `WARNING: unexpected diagnostic <canary giả>`. `IDENTITY.checked(..., WEB_SERVICE_READ)` không raise GateFailure; test FAIL `GateFailure not raised`.
- `test_s04_warning_in_either_channel_stops_identity_flow`: hai fixtures cùng runtime hợp lệ; thêm warning vào lần systemctl đầu tiên. Với stdout, warning là dòng riêng sau bốn properties. Với stderr, fixture mô phỏng đúng DEVNULL trả `stderr=None`.
- **Cả hai** main trả **0**, trace `systemctl → git → systemctl → git`, output có `STAGING_RUNTIME_IDENTITY_OK (no DB connection, no changes)`, không `DUNG`, không warning/canary. Do đó PO không thấy bất thường để dừng; thành công không chứng minh subprocess không cảnh báo.
- Các ca sai worker DB/port, PID thiếu, lỗi quyền/process/query, option DSN ghi đè vẫn dừng trong các tests hiện có. Không có DB write từ script S0.4.
- Evidence PO được ghi trong OPERATIONS: S0.1 hai unit active; S0.3 hash archive/TOC; S0.4 runtime staging 127.0.0.1:5432/commit đúng; S0.5 PG16.15/DB size; S0.6 available `60691943424`; S0.7 PID `3993835` khớp peer/listener, 030–033 đủ. Đây là bản ghi evidence PO, không phải verifier tự quan sát server. Không rút kết luận “không warning” của các subprocess S0.4 từ marker.

Expected: warning/bất thường ở bước chỉ đọc phải dừng, không tiếp tục subprocess kế hoặc phát marker đạt; thông báo lỗi an toàn, không in credential và không retry.

Negative case đã thử: stderr-warning exit 0 bằng subprocess local thực; stdout/stderr-warning trong full-flow identity mock. Cả ba tình huống bị implementation chấp nhận, không phải test chỉ đọc comment.

Classification: **IMPLEMENTATION_FAIL**.

Reproduction: trên script S0.4 hash ở trên, chạy lệnh test số 5; methods `test_s04_real_local_stderr_warning_is_not_success` và `test_s04_warning_in_either_channel_stops_identity_flow`. Test chỉ dùng Python local và filesystem/subprocess doubles.

Residual risk: không khẳng định systemctl/git thật đã phát warning trên staging. Runtime/alias snapshot cũ không chứng minh trạng thái hiện tại; dữ liệu server chỉ do PO cung cấp. Việc không ghi/không lộ secret ở ca đã thử không thay thế yêu cầu dừng khi có bất thường.

## Claim V2 — Chỉ tạo DB tạm mới và chỉ restore archive staging có sẵn vào đó

Test thực hiện: review tên/target, guard source/temp, CREATE argv, SQL ghi metadata, disk buffer/archive fingerprint; chạy developer tests, hai bộ adversarial S1.1 cũ và thêm full-flow fixture có warning cùng dòng listener. Positive control của fixture mới dùng cùng môi trường mô phỏng nhưng không warning.

Kết quả: **FAIL — safety review S1.1**. Phần tạo DB thật và phần restore của V2: **NOT RUN/PENDING**.

Evidence / Observed:

- `test_s11_inline_listener_warning_stops_before_any_write` cung cấp exit 0/stderr rỗng/stdout một dòng:

  `LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7)) WARNING: unexpected diagnostic <canary giả>`

- Dùng PID file fixture thật, chạy instance_guard/checked/output validator thật; chỉ runtime/archive guards và external commands được mock. Warning xuất hiện ngay trong precheck trước source query; không phải lỗi phát sinh sau CREATE.
- main trả **0**, trace **`listener → source → locale → version → create → acl → temp → listener`**. Có một CREATE attempt và metadata write trong mock; không `DUNG` hoặc HOLD; output cuối **`STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)`**. Không lộ canary giả. Test FAIL `0 != 1`.
- Positive control `test_s11_normal_listener_control_completes_empty_create` PASS, exit 0, đúng một CREATE; chứng minh fixture không bị fail do thiếu setup.
- Các ca cũ PASS: DB tạm đã có/sai tên/sai server/port/data directory/schema; archive mất/symlink/hash đổi/quyền thiếu; disk `required−1` chặn WRITE và `required` cho qua. Buffer thử cả nguồn `1228176407` bytes và nguồn 4 GiB, dùng `max(5 GiB, 4×source)`, không dùng dump nén.
- Timeout CREATE/ACL/hậu kiểm giữ HOLD, không DROP/cleanup/rerun; query write vào SOURCE/DB khác bị chặn trước subprocess. CREATE explicit tên `search_tools_restore_test_20261001_064530`, template0, không --clean/--create restore, không dump/service restart. Các điều kiện này đã được kiểm local/mock, không phải tạo DB staging thật.

Expected: diagnostic bất thường ở listener precheck phải chặn bước tiếp/WRITE và không báo tạo DB thành công, bất kể exit 0 và vị trí warning trên stdout. Không tự cleanup/retry/fallback. Hợp đồng “bất kỳ bước nào” không giới hạn warning vào đầu dòng hoặc stderr.

Negative case đã thử: warning nối cùng dòng listener hợp lệ, ngoài các ca version/CREATE/ACL stdout-warning trước đây; bị chấp nhận và tiếp tục WRITE trong mock.

Classification: **IMPLEMENTATION_FAIL**.

Reproduction: trên script S1.1 hash ở trên, chạy lệnh test số 5; method `test_s11_inline_listener_warning_stops_before_any_write`. Fixture đầy đủ nằm trong `tests/independent/test_staging_restore_additional_output_adversarial.py`.

Residual risk: không khẳng định ss/PostgreSQL thật thường tạo output này hoặc sự cố đã xảy ra trên staging. Đây là adversarial full-flow output, cùng loại kiểm chứng mock như các stdout-warning cases lần trước. Restore command chưa chuẩn bị nên chưa review target/options/exit handling của restore. Race từ actor có quyền thay file/config đồng thời không được chứng minh loại bỏ. Snapshot disk cũ không chứng minh free space hiện tại.

## Claim V3 — Restore hoàn tất thật, không lấy đọc TOC thay cho restore

Test thực hiện: review phân biệt S0.3 TOC/S1.1 empty create/restore; tests trace không chứa pg_restore, marker S1.1 ghi rõ `empty, no restore performed`. Timeout/nonzero sau CREATE được thử nhưng **không phải** negative execution của restore.

Kết quả: **NOT RUN/PENDING**.

Evidence: OPERATIONS không có lệnh S1-restore đã chuẩn bị hoặc output/exit status/mốc bắt đầu-kết thúc restore của PO. S0.3 chỉ hash/TOC; không có evidence DB phục hồi.

Negative case đã thử: timeout CREATE/ACL/temp postcheck dừng HOLD không cleanup/rerun; không nâng evidence này thành PASS V3. Archive hỏng/restore nonzero/SSH mất/restore một phần ở bước restore: **NOT RUN** vì chưa có implementation của bước đó để thử.

Residual risk: chưa chứng minh restore thực/no errors/đích đúng hoặc elapsed restore. Mốc thời gian của test/create không được ghi thành thời gian restore.

## Claim V4 — Dữ liệu chính hiện diện; đối chiếu đúng thời điểm

Test thực hiện: review SOURCE_CHECK counts và ghi nhận snapshot nguồn hiện tại; adversarial source fixtures bảng rỗng/query thiếu bảng được thử, và kiểm chúng không kích hoạt lệnh ghi tự động.

Kết quả: **NOT RUN/PENDING** cho dữ liệu DB phục hồi và đối chiếu.

Evidence: PO snapshot nguồn UTC `2026-10-01 06:45:30.598206+00`: products `1262173`, stock_items `2295`, regulatory_rules `6003`, statuses `6`, inactive `3`. Không có counts DB tạm hoặc baseline row counts trước dump trong evidence nhận được. OPERATIONS phân biệt rõ source hiện tại với snapshot backup.

Negative case đã thử: source fixture `products=0` vẫn hiển thị nguyên số 0 (marker chỉ exit status, không claim dữ liệu đạt); query lỗi/missing table chặn marker. Không có command CREATE/restore/drop nối theo output. DB phục hồi thiếu/rỗng/chênh lớn/không baseline: **NOT RUN**, không bịa mốc so sánh hoặc coi counts nguồn là counts backup.

Residual risk: chưa có dữ liệu để phân loại mức chênh lệch hoặc PO xác nhận giải thích; không chứng minh bốn bảng của backup nonempty hay toàn bộ từng dòng giống hệt.

## Claim V5 — DB tạm trước 033; staging đang dùng có 033

Test thực hiện: review SOURCE_CHECK catalog SELECT đối với cả chín footprint033 và đối chiếu tên ba index với migration033; source tests partial033; S1 source_metadata `schema033=False` chặn CREATE.

Kết quả: **NOT RUN/PENDING** toàn claim. Phần nguồn đã có evidence PO và local review, nhưng DB phục hồi trước033 chưa có.

Evidence: bản ghi S0.7 của PO có chín cờ033 true và 030–032 true; SOURCE_CHECK chỉ SELECT. TOC thiếu một số object033 không chứng minh mọi cột/index/trigger/function vắng sau restore. S1.1 chỉ tạo DB trống, không khôi phục snapshot.

Negative case đã thử: source fixtures lần lượt một trong chín cờ false vẫn hiển thị false, không tự migration/write; SOURCE_CHECK query failure không marker. S1 fixture `schema033=False` dừng trước WRITE. Partial/full033 trên DB phục hồi: **NOT RUN**.

Residual risk: chưa kiểm catalog thật của DB phục hồi; footprint schema không chứng minh toàn bộ nội dung function hoặc nghiệp vụ033. Không suy staging hiện tại từ Git hoặc TOC.

## Claim V6 — Chỉ xóa đúng DB tạm sau PO đồng ý riêng; lưu kết quả đúng phạm vi

Test thực hiện: negative trace khi ACL/CREATE/postcheck lỗi và chưa có xác nhận xóa; static review script không có DROP/FORCE/terminate backend và hồ sơ chưa phát drop command. Review diff tài liệu, không chỉnh trạng thái của agent chính.

Kết quả: **NOT RUN/PENDING** toàn cleanup/hậu kiểm. Ranh giới “không tự xóa khi chưa được duyệt” đạt ở các trace đã thử, không thay cho PASS V6.

Evidence: trace các fault không có dropdb/DROP DATABASE/FORCE/pg_terminate_backend, không retry; HOLD sau CREATE attempted. Hồ sơ ghi create/restore/delete server NOT RUN và thời gian restore chưa có. Không có xác nhận PO xóa riêng/tên DB/OID thật hoặc hậu kiểm dịch vụ/archive sau xóa.

Negative case đã thử: ACL failure sau CREATE attempted, chưa được PO duyệt xóa: giữ HOLD và không cleanup. Tên sai trước create bị chặn. Lệnh xóa với tên lệch hoặc DB có app connection: **NOT RUN**, chưa có lệnh xóa để review/execute.

Residual risk: chưa chứng minh identity/connection checks và trạng thái sau drop; chưa có kết quả/thời gian restore thực để ghi phiếu/PR. OPERATIONS và PROJECT_STATE vẫn có đoạn gọi hash/lượt FAIL cũ là “hiện hành”; không dùng các đoạn này thay kết quả thực thi của lượt verifier này. Không sửa hồi tố evidence PO hoặc tự biến lịch sử thành thành công.

## Task status

Giữ nguyên các trường điều phối đã có; verifier không cập nhật attempt/max_auto_repairs, same_claim_repeat_fail hoặc next_action. Giá trị `next_action` bên dưới **không** phải quyền chạy server. Agent chính xử lý điều phối từ kết quả lần chạy này.

attempt: 4 / max_auto_repairs: 2
same_claim_repeat_fail: V2
next_action: PAUSED_BY_PO_CHECKPOINT_SAVED_WAIT_NEXT_DIRECTION_V1_V2_NO_WRITE
manual_repair_authorization: PO approved comprehensive output-stop repair after repeat V2 escalation; auto budget remains exhausted

Trạng thái kiểm chứng hiện hành: **FAIL V1 và V2 (IMPLEMENTATION_FAIL)**. Ba stdout-warning cases lần trước PASS, nhưng probes mới vẫn tái hiện bỏ qua diagnostic. **Không qua safety gate để phát WRITE S1.1 từ kết quả này.** S1.1 server, restore, counts/schema DB phục hồi, cleanup: **NOT RUN/PENDING**. Production: **NOT RUN, ngoài phạm vi**. Không DONE toàn task, không tự truy cập server/retry/cleanup, không tự tăng/reset ngân sách repair.
