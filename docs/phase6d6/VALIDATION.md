# Phase 6D6 — validation local

Date: 2026-09-26. Base: `f39ecc11539db7b66f72e8862c9a9da7b4148ee9`.
Worktree: `/private/tmp/search-tools-phase6d6-stock-quick-edit`.
Chưa commit/push/PR/deploy; không dùng database thật.

## Thay đổi

- Tồn hiện hành: tìm chứa tên/code/CAS (ILIKE, `%`/`_` literal), AND brand,
  phân trang server mặc định 25, tối đa 100; chín trường và nút Sửa/Thêm.
- Form nhập -> kiểm tra trước/sau -> lưu/hủy; ghi chú text nhiều dòng <=2.000 ký tự.
  Dùng chung chuẩn hóa CAS/ngày/giá/lượng/ghi chú với Excel. Giá 0 khác trống.
  Lượng là giá trị thay thế, không cộng/trừ. Trùng identity kể cả expiry NULL bị từ chối.
- Review ký server (15 phút) ràng buộc actor/auth_version/action/row/request ID,
  snapshot/revision/fingerprint, chín trường trước/sau và bộ lọc. POST save không
  tin field ẩn ngoài token; CSRF và menu grant vẫn bắt buộc.
- Snapshot MANUAL bằng SQL INSERT SELECT, digest/count thật, audit chín trường,
  ledger chống double-submit. No-op không snapshot/audit/brand mới.
- Migration032 chỉ thêm source kind và ledger, fail-closed/idempotent, không sửa
  029/030/031 hoặc hàng lịch sử. Xem `OPERATIONS.md` trước rollout/rollback.

## Kiểm chứng tự động

- Focused ban đầu: **118/118 pass, 0 skip**, 16,301s (`focused-tests.log`).
- Sau bổ sung bad-filter và lock timeout: **27/27 quick-edit pass, 0 skip**,
  3,213s (`quick-edit-tests.log`).
- Full suite chạy đúng một lần: **1.091 test, 1.088 pass, 3 skip, 0 lỗi**,
  80,614s (`full-tests.log`). Ba skip thuộc `RealTemplateExportTests` vì thiếu
  `QUOTE_TEMPLATE_FIXTURE` ngoài repo; không tuyên bố đã kiểm tra template production.
- DOM/syntax: `stock_quick_edit_dom_test.js`, `stock_notes_dom_test.js`,
  `license_results_dom_test.js`, `admin_ux_dom_test.js`,
  `admin_regulatory_dom_test.js`, `quick_quote_export_dom.js` pass;
  `node --check static/admin_stock.js` và `git diff --check` pass.
- PostgreSQL14 Docker riêng, chỉ localhost; mỗi fixture tạo/drop DB `pgtest_*`.
  Migration032 được chạy với psql thật, chạy lại và thử schema xung đột/thiếu PK.

## Đồng thời và quyền

- Lock order write: products-import advisory -> stock advisory -> actor SHARE /
  kiểm tra grant hiện hành -> stock_state FOR UPDATE. Restore chỉ stock -> actor
  -> state. Không giữ actor lock rồi chờ domain lock.
- Review read-only không lấy domain lock và không đăng ký brand.
- Review/save đặt SET LOCAL lock_timeout=5.000ms, statement_timeout=20.000ms;
  timeout theo từng lock/SQL, không phải tổng deadline HTTP. Không đổi importer.
  HTTP báo503 thân thiện khi DB bận, transaction rollback; người dùng tải lại/thử lại.
- Test giữ products lock, stock lock và actor lock bằng connection khác; dùng
  timeout100ms trong test, kiểm tra thoát <2s và không snapshot/ledger/brand/audit.
- Race edit/edit, edit/import, edit/restore chỉ một activation; stale bị từ chối.
  Grant/account bị revoke trong lúc chờ khóa và replay được revalidate.
- Chữ ký token tamper/cross-actor, CSRF, delegated stock/no grant/staff đều kiểm tra.
  Idempotency recheck quyền trước replay, replay trước stale; payload khác cùng key409.
- Inject lỗi sau clone rollback cả brand/snapshot/audit/receipt. Lịch sử cũ bất biến;
  restore giữ đủ ghi chú. Không đổi catalog, currency, aliases hoặc team grants.

## Desktop/mobile QA

Skill Browser dùng để thao tác app Flask thật trên DB giả lập, đăng nhập tài khoản
local fixture. Desktop1440x1000, mobile390x844. Không dùng template file:// làm bằng
chứng runtime. Ảnh viewport (full-page capture có lỗi ghép nên không dùng):

- `desktop-list.png`: đủ chín trường; số0/giá trống, cảnh báo hạn, note text/XSS literal.
- `desktop-review.png`: before/after ghi chú, đánh dấu thay đổi, nút lưu/quay lại/hủy.
- `mobile-form.png`, `mobile-review.png`: nhập/so sánh ghi chú nhiều dòng; focus rõ.
- `mobile-list.png`: bộ lọc xếp dọc; table cuộn riêng (257px viewport/1150px nội dung),
  document375px trong viewport390px: không tràn ngang toàn trang.

Đã thực hiện: tìm ETH -> sửa code/lượng/note -> review -> lưu, revision3->4,
giữ bộ lọc; mobile thêm brand mới có cảnh báo -> lưu revision5; dòng ngoài bộ lọc
báo rõ, không đổi dữ liệu để khớp lọc. Quay lại chỉnh sửa giữ nội dung và hủy không
lưu. Search là native GET navigation, không có fetch response cũ ghi đè DOM;
submit guard/reset khi browser history được test DOM. Form/note render autoescape,
không innerHTML. QA dùng server development, chưa xác nhận reverse proxy/staging.

## Chi phí 10.000 dòng

Chạy `PYTHONPATH=.:tests python scripts/benchmark_stock_quick_edit.py` với
DATABASE_URL localhost Docker tạm. Python3.12/PostgreSQL14, dữ liệu ngắn giả lập;
7 mẫu đọc, 3 mẫu lưu. Raw: `benchmark-10000.json`.

- List25: median12,80ms; contains+brand25:17,37ms (1.000 kết quả); trang400:28,72ms.
- Lưu clone10k+digest+commit:126,78ms median, 126,34–131,77ms.
- Đo riêng lần lưu thứ4 bằng tracemalloc: peak7.654.832bytes (~7,30MiB), chỉ Python
  allocations; không phải RSS, không gồm libpq/PostgreSQL.
- `stock_items`+indexes:3.473.408bytes trước ->16.187.392bytes sau3 lần lưu;
  tăng trung bình4.237.994bytes/lần (~4,04MiB) trong phép đo này.
- Đây là latency service local, không phải SLA production hay thử tải concurrent.
  Không đo payload ghi chú tối đa2.000ký tự trên mọi dòng. SQL clone O(n), digest
  vẫn giữ sorted repr và joined buffer O(n) trong RAM dù dùng cursor từng1.000dòng.
  Mỗi edit giữ thêm toàn snapshot; lịch sử tăng không giới hạn, không tự dọn dữ liệu.
  Cần cân nhắc dung lượng/tần suất sửa và đo staging trước rollout lớn.

## Giới hạn

Không full-text/fuzzy/bỏ dấu; không xóa dòng; không delta nhập-xuất. Bộ lọc brand
lấy trong active snapshot; danh sách gợi ý brand form lấy active brand master.
Phần feedback ngoài bộ lọc dùng Python casefold còn query dùng PostgreSQL ILIKE,
có thể khác cho một số Unicode đặc biệt; không ảnh hưởng dữ liệu/quyền/lọc SQL.
Review ký không cho lưu sau15phút: cần mở lại form và kiểm tra dữ liệu mới.

## Phạm vi đóng gói

Log `.log`, ảnh `.png`, JSON benchmark và `COORDINATOR_HANDOFF.md` chỉ là evidence
local, không đưa vào release. Có thể giữ docs hướng dẫn gọn (`OPERATIONS.md`,
`VALIDATION.md`, spec) sau review. Source migration032, application/templates/static,
tests và benchmark script là diff kỹ thuật cần review. Không sửa hai worktree WIP cũ.
