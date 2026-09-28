# Developer validation — regulatory-manual-edit v2

Ngày chạy: 2026-09-28. Hợp đồng khóa: `ee2ee5a`.
Đích: Docker local riêng `search-tools-regulatory-test-db-1`, PostgreSQL 16,
DB thử do helper tạo/drop, không `.env`, không server. Preflight và lệnh:
[OPERATIONS.md](OPERATIONS.md).

## Kết quả trước verifier

- `.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v`:
  **20 tests PASS, 0 skipped**, 7,921 giây ở lần chạy cuối trước verifier.
  Gồm migration/rollback/restore, mutation/audit/replay, HTTP/CSRF/quyền,
  concurrency có barrier PostgreSQL, tìm kiếm, import cả hai mode, cleanup
  và danh sách đầy đủ/biên 8 MiB thật (không chỉ mock giới hạn).
- `.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v`:
  **81 tests PASS, 0 skipped**, 7,860 giây. Lần đầu có một test legacy schema
  chờ redirect migration028; đã cập nhật đúng yêu cầu mới: app chưa có 033
  phải trả 503 trước ghi. Các assertion dữ liệu/migration legacy giữ nguyên.
- `node tests/regulatory_manual_dom_test.js`: PASS — preview không lưu,
  cảnh báo khác tình trạng, nội dung hiển thị text, retry giữ request ID,
  thay form hủy preview, payload action ngừng áp dụng không lén sửa field.
- `node tests/admin_regulatory_dom_test.js`: exit 0, hồi quy đổi màu.
- `git diff --check`: PASS. Không có diff `VERIFICATION.md` từ commit khóa.

## Giới hạn / NOT RUN tại checkpoint trước UAT

- Đây là developer evidence, **không thay verifier**; kết quả độc lập ở
  `specs/regulatory-manual-edit/VERIFICATION_RESULT.md` sau khi verifier chạy.
- Không chạy full suite toàn repo; đã chọn các suites liên quan nêu trên.
- Chưa UAT trình duyệt thật, chưa staging/production, chưa deploy.
- Có cảnh báo nền Python 3.9/LibreSSL của urllib3 và ResourceWarning
  openpyxl trong tests cũ; không đổi runtime/dependency trong task này.
- Chỉ commit tài liệu `ee2ee5a`. Không commit code/push/merge.

## Sau lượt verifier đầu tiên

- Verifier độc lập FAIL V13: retry đã queued/chưa có snapshot hoàn tất mới
  nhưng trang chi tiết mặc định vẫn hiện snapshot trước đó. Các claim còn
  lại PASS trong phạm vi local. Không có bằng chứng lỗi này ghi đè rule.
- Repair 1/2, thêm hồi quy cả hai mode: ngay sau retry mặc định báo chưa có
  snapshot; event cũ chọn tường minh vẫn xem được; sau worker hoàn tất,
  mặc định đúng event mới. Snapshot cũ giữ nội dung cũ.
- Chạy lại: **21 tests mới PASS, 0 skip (9,440 giây)**; **81 hồi quy PASS,
  0 skip (8,591 giây)**; hai bộ Node DOM exit 0. VERIFICATION.md không đổi.
- Verifier lượt mới (context sạch) **PASS 20/20 claim**, chạy lại 21+81
  tests và 2 DOM, thêm phản biện độc lập. V13: mặc định 404 sau retry,
  event cũ tường minh 200, sau chạy lại/default và cleanup đúng snapshot.
  Bộ byte Unicode độc lập cũng đạt 8 MiB ±1 ở cả hai mode. Xem evidence
  từng claim và residual risk trong VERIFICATION_RESULT.md.
- attempt=1/max_auto_repairs=2; same_claim_repeat_fail=none;
  next_action=HUMAN_REVIEW. Chưa human approval/UAT, không READY/DONE.

## UAT browser và polish hiển thị — 2026-09-28

- **PO xác nhận UAT local 8/8 đạt trên trình duyệt**, trước các chỉnh sửa
  hiển thị dưới đây. Đây là nghiệm thu của người dùng, không chỉ HTTP/DOM test.
- Theo yêu cầu tiếp theo, chỉ sửa nhãn/format: cas/code/name hiện bằng
  CAS/Mã sản phẩm/Tên sản phẩm; lịch sử bỏ hàng ID tình trạng, bool Có/Không
  (thiếu khóa vẫn “—”); thời gian `%d/%m/%Y %H:%M` theo UTC+7, giữ ISO có
  timezone trong thuộc tính `<time datetime>`; actor import tra username
  từng event bằng LEFT JOIN chỉ đọc; worker là “Hệ thống”, actor không tìm
  thấy có fallback tường minh. Không cập nhật actor/timestamp/snapshot DB.
- Ô xác nhận dùng **số động** từ preview: “Nhập N để xác nhận số quy tắc sẽ
  xóa”; N=1 cho ví dụ PO, không hardcode 1 hoặc thay kiểm tra số phía server.
- Username import là username hiện tại của ID trong event (log cũ không lưu
  snapshot tên). Lịch sử tay vẫn dùng actor_label snapshot, không tra đè tên
  cũ. Không đổi quyền, payload match_field, revision, idempotency hay planner.

### Claim bị ảnh hưởng / hợp đồng

- **V3/V4/V15:** nhãn trên form/xem trước, nhưng payload và loại đối chiếu
  bất biến phải giữ nguyên; kiểm tra cả create/edit, hủy preview và retry.
- **V7:** hiển thị before/after, actor và thời gian lịch sử; ID/boolean gốc
  vẫn có trong snapshot DB, chỉ không hiển thị hàng ID tình trạng.
- **V9/V13:** hiển thị số quy tắc xóa và nhật ký/snapshot import, phân biệt
  đơn vị dòng file với rule; kiểm tra số 0/1/3 và từ chối xác nhận sai số.
- Không đổi yêu cầu/oracle nào trong VERIFICATION.md đã khóa ở ee2ee5a;
  chạy lại verifier độc lập cho **V3, V4, V7, V9, V13, V15**. Các claim khác
  không bị sửa logic; hồi quy liên quan vẫn chạy lại, không suy PASS chỉ từ UAT cũ.

### Developer tests sau polish

- `.venv/bin/python scripts/test_regulatory_local.py test_regulatory_manual_display -v`:
  **3 PASS**, gồm timezone qua ngày/năm/năm nhuận; format cả ba loại;
  bool true/false/khóa thiếu; actor khác chủ job, worker, ID không tồn tại,
  username HTML được escape. So snapshot DB trước/sau GET bằng nhau.
- Sau gián đoạn do ổ DATA mất kết nối, PO kết nối lại; đã xác minh working
  tree/contract và chạy lại toàn bộ nhóm kiểm tra dưới đây trên ổ đã phục hồi.
- `.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v`:
  **24 PASS, 0 skip** (10,939 giây).
- `.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v`:
  **81 PASS, 0 skip** (8,397 giây).
- `node tests/regulatory_manual_dom_test.js`: PASS với cả CAS/code/name,
  nhãn trước/sau đúng, payload raw/revision không đổi; preview/retry/invalidation
  vẫn đạt. `node tests/admin_regulatory_dom_test.js`: exit 0.
- Chỉ DB test tạm sau preflight Docker; không ghi database UAT đã nghiệm thu.
  Không migration mới. `git diff --check` đạt, hợp đồng không có diff.
- Verifier phạm vi hiển thị: **PASS V3, V4, V7, V9, V13, V15**, không FAIL
  mới. Verifier chạy 24/24 manual + 81/81 hồi quy, 2 bộ DOM, thêm **4/4 test
  độc lập** và **3 nhóm DOM độc lập** trong bộ nhớ; không skip. Evidence
  bổ sung được giữ riêng trong VERIFICATION_RESULT.md, không xóa báo cáo cũ.
- Các claim không thuộc scope polish giữ evidence của lượt kiểm chứng toàn
  bộ trước đó; không tuyên bố verifier vừa chạy mới toàn bộ 20 claim.
- Hạn chế: git range từ ee2ee5a bao gồm cả feature trước polish (chưa có
  commit code riêng); không dùng diff đó để khẳng định toàn feature chỉ là
  hiển thị. Tests runtime đối chiếu sáu bảng không đổi qua GET, payload mã
  field/revision nguyên vẹn, kiểm tra sai số xóa và replay vẫn bị chặn đúng.
- Lịch sử username import là lookup hiện tại, không phục hồi tên quá khứ
  chưa được lưu; timestamp naive ngoài dữ liệu DB aware được xem là UTC.
  Không đổi audit lưu sẵn. Chưa có PO nghiệm thu bốn chỉnh sửa hiển thị mới;
  UAT 8/8 đã xác nhận là của bản chức năng trước polish.
- Khôi phục web/worker UAT sau lần ngắt ổ: tiến trình cũ gặp SIGBUS/lỗi
  đường dẫn và đã dừng. Xác minh đích local lại, pending_jobs=0 và
  pending_cleanup=0 trước khi start cùng cấu hình; **không setup/reseed**.
  HTTP đăng nhập thật + GET form/job/snapshot đã PASS tại 127.0.0.1:5002;
  so toàn bộ sáu bảng regulatory trước/sau smoke **không đổi**. Không chạy
  smoke import cũ vì nó dùng dữ liệu trước UAT.
- attempt vẫn 1/max_auto_repairs=2, same_claim_repeat_fail=none,
  next_action=HUMAN_REVIEW. VERIFICATION.md giữ nguyên từ ee2ee5a.
  Không commit/push; dừng chờ PO xem lại hiển thị.

## Nghiệm thu và lượt test cuối trước trình duyệt commit — 2026-09-28

- **PO xác nhận hiển thị 4/4 đã sửa, UAT đạt**, bổ sung cho nghiệm thu
  chức năng browser 8/8 trước đó. Không còn mục hiển thị chờ PO kiểm tra.
- Chỉ chuẩn bị danh sách/message để duyệt: **CHƯA stage, CHƯA commit,
  không push/merge/deploy**. Không sửa application code trong lượt này.
- Chạy lại toàn bộ nhóm test phase và hồi quy liên quan:
  - `.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v`
    → **24 PASS, 0 FAIL, 0 SKIP**, 10,358 giây.
  - `.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v`
    → **81 PASS, 0 FAIL, 0 SKIP**, 8,155 giây.
  - `node tests/regulatory_manual_dom_test.js` → PASS cả cas/code/name.
  - `node tests/admin_regulatory_dom_test.js` → exit 0.
- Tổng **105 Python tests + 2 bộ DOM đạt**. Preflight Docker trước kết nối,
  chỉ database test tạm, không ghi database UAT hoặc production. Đây không
  phải lần chạy toàn bộ test suite của repository. Warning nền LibreSSL,
  invalid escape và openpyxl như đã ghi ở trên không làm fail/skip.
- Verifier trước đó PASS 20/20, và lượt polish PASS lại 6/6 claim liên quan;
  không có code mới sau kiểm chứng đó, không suy thêm một lượt verifier mới.
- Rà soát toàn bộ **36 file ứng viên (12 sửa, 24 mới)**, bao gồm source,
  migrations/tests, tài liệu, verifier result và hai workbook mẫu được PO
  cho phép. Không phát hiện secret thật/file ngoài phạm vi trong tập này.
  `LOGIN.txt`, `private.json`, `.env`, database dump, runtime launcher/PID,
  upload/cache và log **không nằm trong danh sách commit**; không đọc chúng
  trong lượt rà soát này. Credential trong runner/tests là fixture local-only.
- Đọc lại hai workbook bằng openpyxl/ZIP: mỗi file một sheet, ba cột,
  sáu dòng tổng hợp đúng mã UAT, không công thức/error/macro/liên kết ngoài.
  Chỉ đọc, không sửa workbook; không đưa script kiểm tra tạm vào repository.
- `git diff --check` đạt; VERIFICATION.md không khác commit khóa ee2ee5a.
  Tiếp theo: trình danh sách 36 file và message, chờ PO cho phép commit.
