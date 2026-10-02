# Verification Result — staging-restore-rehearsal

Do verifier độc lập tạo ngày 2026-10-02 (lần chạy này). Chỉ nhận yêu cầu, hợp đồng khóa, phạm vi diff, lệnh test và Observed/Expected/Reproduction/Classification của lần fail trước (attempt4). Không dùng PROJECT_STATE.md, CHECKPOINT.md hay ghi chú sửa lỗi làm bằng chứng hành vi.

## Phạm vi và artifact

- Phạm vi lần này: V1 (script S0.4 chỉ đọc) và V2 phần S1.1 tạo DB **trống**. V2 phần restore/xóa, V3–V6 chưa chuẩn bị: NOT RUN / PENDING (không tính PASS).
- Branch `ops/staging-restore-rehearsal`, đối chiếu `git diff 207cd11` + file untracked đọc trực tiếp. `opencode.json` ngoài phạm vi.
- Hợp đồng SHA256 khớp giá trị khóa: `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79`.
- Script S0.4 SHA256: `d0a32e3e11e023da62ca37361fa9e34cebcaefde6e9583f34d7159fe2225ec5a`.
- Script S1.1 SHA256: `9e28d067ed6828f80b954bce153d037689833c6e1d556dc098c443653e0f9ee7`.
- SOURCE_CHECK.sql SHA256: `05a868759de8611451bd3aec060fc2b6d70bce31f5ed54943a41fe9b394f2951` (không đổi).
- Toàn bộ thực thi chỉ local: Python mock/fake subprocess và vài subprocess Python local. Không SSH, không sudo/systemctl/ss/psql/createdb thật, không kết nối DB, không production, không commit/push.
- Test độc lập MỚI: `tests/independent/test_staging_restore_verifier5_matrix_adversarial.py` (25 test; không sửa file cũ).

## Lệnh và kết quả quan sát

| Lệnh | Kết quả |
| --- | --- |
| `.venv/bin/python -B -m unittest discover -s tests -p 'test_staging_restore_*.py'` | Ran 63, OK |
| 10 file `tests/independent/test_staging_restore_*adversarial.py` có sẵn (từng file) | tất cả OK (4, 9, 11, 4, 7, 8, 9, 5, 8, 11 test) |
| `test_staging_restore_verifier5_matrix_adversarial.py` (mới) | Ran 25, **FAILED (failures=2)**: cả hai là cùng một lỗi pin SHA256 của archive (xem V2) |
| `git diff --check` | exit 0 |

Ma trận adversarial mới chạy toàn bộ `main()` thật của hai script với subprocess giả. Mỗi subcommand bị chèn lần lượt: warning stdout đầu/giữa/cuối/cùng dòng (đầu và cuối), dòng trống/dòng toàn khoảng trắng chen giữa, dòng NOTICE/warning chữ thường, dòng rác không phải warning, ESC/NUL/BEL/DEL/BOM/zero-width/ký tự điều khiển giữa chuỗi, `\r` ghi đè, dấu tách dòng `\x0b \x0c \x85  ` kèm warning, stdout rỗng, stdout lặp, stderr (warning, ANSI, NUL, rác, NOTICE) với exit 0, exit code 1/2/124/-9 kèm stdout hợp lệ, exception từ runner (Timeout, FileNotFound, PermissionError, UnicodeDecodeError, RuntimeError có chứa DSN giả), và với hai gate JSON: JSON+rác cùng dòng, hai JSON, rác trước JSON, khóa thừa/thiếu/trùng, mảng bọc, NaN, số dạng chuỗi/bool, null, và 21 giá trị semantic sai. Subcommand phủ: systemctl, git, ss (hai lần), locale, createdb --version, createdb, psql nguồn, psql ghi (REVOKE/COMMENT), psql đọc DB tạm, và toàn bộ 16 gate kể cả runtime/instance recheck sau CREATE.

## Claim V1

Test thực hiện: chạy full-flow `main()` của S0.4 với 4 subcommand (systemctl web, git web, systemctl worker, git worker) x ~27 biến thể stdout x 5 biến thể stderr x 4 exit code x 6 loại exception; thêm `checked()` với subprocess Python local thật (stderr warning, ESC trong stderr, UTF-8 hỏng, nonzero kèm output hợp lệ, warning sau output, rỗng); kiểm 23 biến thể DSN (`?host=`, `?hostaddr=`, `?dbname=`, `?service=`, `?options=`, `?user=`, host/DB/cổng sai, fragment, scheme sai, thiếu user, path thừa, khác hoa/thường), web/worker khác DSN; canary mật khẩu giả `V5_FAKE_SECRET_CANARY` nhúng vào DSN và vào output giả.
Kết quả: PASS
Evidence (Observed): lần fail trước (warning ở stdout/stderr của lần systemctl đầu làm main trả 0 và in `STAGING_RUNTIME_IDENTITY_OK`) nay không còn tái hiện: tests `test_staging_restore_additional_output_adversarial.py` 4/4 OK, và ma trận mới: với mọi (gate, biến thể bất thường) main trả 1, trace dừng đúng tại subprocess bất thường (không subprocess kế, không retry), không in `STAGING_RUNTIME_IDENTITY_OK`, canary không xuất hiện trong output (kể cả khi exception chứa DSN). Control: lần chạy sạch trả 0, trace đúng 4 lệnh `systemctl, git, systemctl, git`, không in canary, có marker. Giá trị systemctl sai (inactive, User=root, MainPID 0/rỗng/chữ số Unicode, khóa trùng) đều dừng. Tất cả DSN nguy hiểm bị từ chối, thông báo lỗi không chứa canary; web/worker DSN khác nhau in `STAGING_WEB_WORKER_SAME_DSN`.
Expected: bất thường ở bước chỉ đọc dừng, không chạy subprocess kế, không marker đạt, không in credential, không retry.
Negative case đã thử: xem Test thực hiện (tổng ~1.200 trường hợp subTest cho hai script, trong đó phần S0.4 gồm warning cùng dòng/đầu/cuối stdout, stderr exit 0, exception mang DSN, exit code lạ, DSN override qua query).
Residual risk (không phải FAIL): (1) `.strip()` và `splitlines()` làm ký tự điều khiển/khoảng trắng đặc biệt (`\x1f`, `\x1c-\x1e`, `\x85`, `\xa0`, ` /9`, `\x0b`, `\x0c`, `\r`) ở đầu/cuối hoặc làm dấu tách dòng bị chấp nhận im lặng (12/12 biến thể thử đều được nhận, in dạng thông tin); các ký tự này không mang chữ chẩn đoán và mọi giá trị vẫn bị whitelist, nên không che được warning/secret, nhưng "output lạ" dạng này không dừng — ghi nhận SPEC_AMBIGUOUS (hợp đồng không định nghĩa chuẩn hóa khoảng trắng). (2) DSN có tab/newline nhúng giữa (urlsplit tự bỏ) được chấp nhận. (3) Script không in hostname máy; việc alias `staging` đúng máy dựa vào tên unit/cwd/commit. (4) Chỉ chứng minh hành vi với mock; output thật của PO ở server là evidence riêng (S0.4 đã có output cũ, script đã đổi sau đó, chưa có output mới của bản hash hiện tại).

## Claim V2 (chỉ phần S1.1 tạo DB trống; restore/xóa PENDING)

Test thực hiện: chạy full-flow `main()` của S1.1 với runtime_guard thật (fake `/proc`, fake LIVE) nên phủ cả 4 lệnh systemctl/git ở đầu và ở hậu kiểm; ma trận như trên trên 16 gate; kiểm trình tự CREATE đúng một lần, không DROP/terminate/pg_restore/force/stop/restart trong mọi argv, không DSN/mật khẩu/`DATABASE_URL` trong argv/env của subprocess nào; kiểm lệnh ghi chỉ nhắm DB tạm; kiểm pin archive bằng hàm `archive_guard` thật với file fixture.
Kết quả: FAIL
Evidence (Observed):
- Phần an toàn đầu ra: lần fail trước (dòng listener hợp lệ + ` WARNING: ...` cùng dòng làm main trả 0, CREATE chạy, in `STAGING_TEMP_DATABASE_CREATED`) không còn tái hiện. Với mọi bất thường ở gate trước CREATE (systemctl, git, ss, source psql, locale, createdb --version): main trả 1, không có `create#1`/`acl#1` trong trace, không in HOLD, không marker thành công, canary không bị in. Với bất thường ở CREATE hoặc sau (create, acl, temp metadata, recheck runtime/ss): main trả 1, in `HOLD`, không marker thành công, không lệnh cleanup/retry/drop (không thấy `create#2`). Control sạch: trả 0, `create#1` đúng một lần. `test_no_secret_in_any_argv_or_env` PASS.
- **Lỗi mới phát hiện:** hằng `ARCHIVE_SHA` trong `scripts/staging_restore_create_temp.py` dài 63 ký tự hex (`c021ca2848d75b98ec103726d9d42e1cabb3e39df36aa6d03e04b9789b7473f`). SHA-256 hexdigest luôn đúng 64 ký tự nên `archive_guard()` không bao giờ khớp bất kỳ file thật nào: kiểm chứng bằng hàm thật với fixture — khi pin = hexdigest thật của fixture thì `archive_guard` qua, khi pin bị cắt 1 ký tự thì raise `ARCHIVE_HASH_UNCHANGED`. Hệ quả: S1.1 với pin hiện tại luôn dừng ở `ARCHIVE_HASH_UNCHANGED` trước bước ghi (dừng an toàn, không tạo DB), nhưng không bao giờ đạt `STAGING_TEMP_DATABASE_CREATED` và không thể đáp ứng oracle "metadata/hash archive được ghi trước và giữ nguyên sau". Các test developer/độc lập cũ đều `patch` `ARCHIVE_SHA` bằng digest fixture nên không bắt được. Chuỗi 63 ký tự cũng xuất hiện nguyên văn trong phần evidence S0.3 của OPERATIONS.md (nguồn gốc: có thể do mất ký tự khi chép output PO; verifier không tự truy cập server nên không xác định được hash 64 ký tự thật).
Expected: pin archive là giá trị SHA-256 hợp lệ (64 hex) khớp archive staging thật, để precheck có thể đạt khi archive đúng và dừng khi sai.
Negative case đã thử: toàn bộ ma trận phía trên; archive_guard với pin hợp lệ (qua) và pin bị cắt (dừng); biến thể JSON (13 loại) và 21 giá trị semantic sai ở metadata nguồn/DB tạm (đều dừng đúng gate, không CREATE nếu ở precreate). Kết quả đều đạt trừ lỗi pin ở trên.
Classification: IMPLEMENTATION_FAIL (hệ quả quan sát: lệnh S1.1 không thể hoàn thành; nguồn gốc giá trị 63 ký tự trong evidence có thể là SPEC_AMBIGUOUS về provenance nhưng script đang mang giá trị không hợp lệ)
Reproduction: `.venv/bin/python -B -m unittest discover -s tests/independent -p 'test_staging_restore_verifier5_matrix_adversarial.py'` — hai test `ArchiveHashShape` FAIL (`len=63`, `63 != 64`); hoặc `python -c "import importlib.util as u; s=u.spec_from_file_location('c','scripts/staging_restore_create_temp.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print(len(m.ARCHIVE_SHA))"` in 63.
Suggested area (không phải giải pháp): hằng pin hash archive và nguồn evidence của nó.
Residual risk: (1) Số float bằng giá trị (ví dụ `version_num: 160015.0`) được chấp nhận ở metadata nguồn (chỉ khả dĩ với mock, psql thật trả số nguyên). (2) Phiên ghi mở bằng `--maintenance-db=search_tools_staging` (đã ghi trong OPERATIONS là chỉ tạo object catalog mức cluster); không phải ghi dữ liệu DB ứng dụng nhưng là kết nối ghi qua DB đang dùng, PO nên biết. (3) Race giữa precheck và CREATE chỉ được DB chặn "đã tồn tại" (stderr khác rỗng -> HOLD). (4) Ký tự điều khiển/khoảng trắng đặc biệt ở rìa/dấu tách dòng được nhận im lặng như V1. (5) Chỉ mock: chưa có evidence server cho S1.1, restore, đối chiếu, xóa.

## Claim V3 — NOT RUN / PENDING

Chưa chuẩn bị lệnh restore; không kiểm chứng, không tính PASS.

## Claim V4 — NOT RUN / PENDING

Chưa có dữ liệu/lệnh đối chiếu counts; không kiểm chứng, không tính PASS.

## Claim V5 — NOT RUN / PENDING

Chưa có lệnh/evidence schema 033 trên DB tạm và staging; không kiểm chứng, không tính PASS.

## Claim V6 — NOT RUN / PENDING

Chưa có phê duyệt/lệnh xóa; production NOT RUN, ngoài phạm vi. Không tính PASS.

## Task status

attempt: 5 / max_auto_repairs: 2 (repair5 do PO chỉ đạo 2026-10-02; ngân sách tự động vẫn cạn, không reset)
same_claim_repeat_fail: V2 (lỗi mới khác nguyên nhân: hằng ARCHIVE_SHA 63 ký tự; lỗi output-guard cũ của V2 và V1 đã hết)
next_action: ESCALATE_USER_NEED_PO_ARCHIVE_SHA256_READONLY_RECHECK_NO_WRITE
