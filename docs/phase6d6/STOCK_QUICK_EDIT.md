# Phase 6D6 — Thêm/sửa nhanh tồn kho

Trạng thái: được người dùng yêu cầu triển khai local; chưa duyệt production.
Base: `f39ecc11539db7b66f72e8862c9a9da7b4148ee9` (stock notes 031 đã UAT production).

## Mục tiêu và phạm vi

Trong Quản lý tồn kho, tìm một dòng tồn đang dùng rồi sửa, hoặc thêm một dòng mới,
không cần upload lại Excel. Giữ luồng import thay toàn bộ, lịch sử và khôi phục.
Không thêm xóa dòng/xóa hàng loạt, sổ nhập-xuất, vị trí kho hoặc tự cộng số lượng.

## Trải nghiệm

- Đặt danh sách tồn hiện hành và thanh thao tác trước khu vực import/lịch sử.
- Ô tìm kiếm chung: tên, code hoặc CAS chứa từ khóa, không phân biệt hoa/thường;
  khớp văn bản thường, không regex/fuzzy. Không hứa bỏ dấu. Trim từ khóa; `%` và `_`
  người dùng nhập phải được coi là ký tự thường. Lọc brand theo danh mục động.
- Kết hợp từ khóa AND brand; trạng thái lọc, tổng kết quả, phân trang phía server
  (mặc định 25 dòng, giới hạn tối đa 100), sắp xếp ổn định. Không tải toàn bộ tồn
  vào trình duyệt. Brand lấy trong tồn hiện hành cho bộ lọc; form thêm dùng Brand Gateway.
- Bảng đủ 9 trường hiện tại, số lượng/giá dễ đọc, giữ cảnh báo hạn sử dụng,
  có nút Sửa mỗi dòng, nút Thêm tồn mới và xóa bộ lọc. Giá 0 khác giá trống.
- Form thêm/sửa có Nhập -> kiểm tra dữ liệu/hiển thị thay đổi -> Lưu/Hủy;
  phần xem thay đổi có thể nằm ngay trong form, không bắt upload hay gõ số dòng
  như thao tác thay toàn bộ. Có loading, ngăn submit lặp, thông báo rõ thành công/lỗi.
- Khi lưu thành công làm mới danh sách và revision; giữ bộ lọc, thông báo nếu dòng
  vừa sửa không còn khớp bộ lọc. Không tự đổi trường dữ liệu để giữ dòng trong bảng.
- Desktop/mobile, label bàn phím, focus, empty/error state; không dùng innerHTML
  với dữ liệu người dùng. Giữ các luồng import/job detail hiện có.

## Quy tắc dữ liệu giữ nguyên

- 9 trường: Name, Code, Cas, Brand, Size, Giá tồn kho, Số lượng tồn, Hạn sử dụng, Ghi chú.
- Name/Code/Brand/Size bắt buộc; CAS, giá, hạn dùng, ghi chú được để trống.
- Dùng cùng chuẩn hóa/validation với import: CAS checksum, lượng số nguyên >=0,
  giá VND không âm tối đa 2 chữ số lẻ, DATE hợp lệ, ghi chú <=2000 ký tự.
  Kiểm tra giới hạn INTEGER và NUMERIC phía server, không để tràn thành 500.
- Số lượng form là số tồn mới thay thế giá trị cũ, không phải số cộng/trừ.
- Brand + Code + Size + Hạn dùng (kể cả hạn trống) là định danh; báo trùng,
  không cộng số lượng/merge âm thầm. Sửa được các trường định danh nếu không trùng.
- Cho thêm khi chưa có snapshot hiện hành. Nếu dữ liệu sửa không đổi, không tạo
  lịch sử/snapshot vô ích. Double-submit không tạo thêm dòng/snapshot thứ hai.
- Brand mới giữ quy tắc Brand Gateway của import: hiện rõ tạo brand mới trong
  xác nhận; không tự gán tiền tệ, cấp team hoặc đổi alias của brand cũ.
- Không sửa catalog/products, giá báo giá, regulatory, quote/copy/export.

## Lịch sử, đồng thời, phân quyền

- Giữ snapshot lịch sử bất biến. Mỗi lưu hợp lệ tạo snapshot mới từ snapshot active,
  chỉ thay/thêm dòng được chọn; dùng INSERT ... SELECT và transaction để giữ
  nguyên các dòng khác, kích hoạt nguyên khối và tăng stock_state.revision.
- Audit phân biệt thêm/sửa thủ công, có người/thời gian/before-after đúng 9 trường,
  snapshot nguồn/đích, ID dòng cũ và request ID. UI lịch sử không gắn nhãn Import
  cho thao tác thủ công. Restore tiếp tục hoạt động, gồm ghi chú.
- Dùng đúng lock order hiện có: actor/grants, Brand Gateway/products import lock,
  stock advisory lock và stock_state row lock theo code hiện tại; audit reviewer
  phải xác minh thứ tự thực tế, không tạo deadlock với import/restore/grant revoke.
- Server kiểm tra stock revision/fingerprint và row thuộc snapshot hiện hành
  trong cùng transaction; stale edit/import/restore trả conflict, không ghi đè.
- Giữ menu grant `stock`, CSRF, active account/auth_version, revalidate quyền
  trong transaction. Không yêu cầu quyền `products` chỉ để sửa dữ liệu tồn,
  nhưng không cho dùng API tồn để đổi catalog/brand aliases/team grants.
- Tính lại content_sha256/row_count theo contract hiện có để preview và restore
  vẫn tin cậy. Không dùng hash bịa hoặc lấy hash snapshot cũ cho dữ liệu mới.
- Cần migration additive tiếp theo (dự kiến 032, phải kiểm tra chưa dùng) để cho
  nguồn snapshot thủ công và idempotency nếu cần; không sửa SQL 029/030/031.
  Đánh giá rollback compatibility với bản f39ecc1.

## Kiểm chứng và bàn giao

- Tests DB cách ly: thêm từ kho trống/có dữ liệu; sửa từng trường; trùng identity
  cả expiry NULL/non-NULL; input không hợp lệ; no-op; submit lặp; audit; lịch sử
  bất biến; restore; lỗi giữa transaction rollback nguyên khối.
- Race/stale: edit-edit, edit-import, edit-restore; actor/grant bị thu hồi;
  CSRF và delegated admin `stock` được/không được cấp.
- Query/UX: chứa từ khóa tên/code/CAS, brand AND query, unicode, ký tự wildcard,
  tổng kết quả và pagination, ghi chú XSS, request cũ về muộn, trạng thái filter
  sau lưu. Kiểm tra API không lộ dữ liệu cho người không có quyền.
- Regression import 8/9 cột, note-aware digest, Search/Find Code/Quick Quote, quyền
  giá/ghi chú/brand và quote price không đổi.
- Đo thao tác tìm/lưu trên DB tạm đủ lớn (ví dụ 10k dòng) để ghi rõ chi phí copy
  snapshot. Không tự thêm engine/lưu delta/refactor rộng; báo nếu kiến trúc này
  không đáp ứng thực tế. Không tự xóa lịch sử để tiết kiệm dung lượng.
- Focused tests trong khi sửa, full suite một lần sau cùng; visual QA dữ liệu
  thật mô phỏng trên desktop/mobile. Bàn giao diff, migrations, test evidence,
  giới hạn và checklist UAT tại READY FOR REVIEW.
- Chưa commit/push/PR/deploy hoặc truy cập DB thật trong task triển khai.
  Coordinator review trước khi đóng gói; staging UAT và production approval riêng.
