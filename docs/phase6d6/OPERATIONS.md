# Phase 6D6 — release gates và UAT

Chỉ hướng dẫn; chưa được phép chạy production. Coordinator review trước đóng gói;
staging UAT và production approval riêng. Không tự commit/push/deploy.

## Migration / rollback

1. Xác nhận đang ở baseline notes031 (f39ecc1 hoặc descendant đã review), backup
   DB và kiểm tra restore theo quy trình vận hành. Không áp dụng SQL030 ghi chú
   từ WIP cũ; SQL030 main là admin_menu_permissions, ghi chú dùng031.
2. Dừng web/worker writers theo cửa sổ triển khai; chạy032 bằng psql ON_ERROR_STOP=1.
   SQL có transaction và khóa ACCESS EXCLUSIVE stock_snapshots; không chạy khi
   importer đang hoạt động. Schema không khớp phải dừng điều tra, không sửa tay bỏ check.
3. Deploy application code đồng bộ và khởi động web/worker. Xác nhận history ghi
   "Thủ công", nhập Excel/restore vẫn hoạt động, menu stock được cấp đúng.
4. Rollback code về f39ecc1: **giữ032 và tất cả MANUAL snapshots/ledger**. Bản cũ
   đọc chín trường và restore được snapshot, nhưng history cũ gắn MANUAL thành
   "Import" do nhánh label mặc định. Ghi rõ hạn chế audit UI hoặc dùng hotfix label
   đã review trước rollback. Không thu hẹp source_kind check khi MANUAL đã tồn tại.
   Không drop ledger/snapshot để rollback. Snapshot đang dùng vẫn là dữ liệu thật.

Lock timeout5s và statement timeout20s chỉ áp dụng transaction HTTP review/save;
không thay background import600s. Timeout rollback và báo thử lại; nếu response
mất sau commit, replay cùng token/request ID không tạo snapshot lần2 nhưng vẫn
kiểm tra actor/grant. Nếu token quá15phút, mở lại danh sách để xác nhận trạng thái.

## UAT staging, cần người dùng xác nhận

- Admin chỉ có menu stock: tìm theo tên/code/CAS, kết hợp brand, wildcard literal,
  đổi trang; user không có quyền không đọc/sửa được.
- Thêm đủ9trường; trường tùy chọn trống, giá0, note nhiều dòng/Unicode; brand mới
  hiển thị xác nhận và không được tự gán tiền tệ/quyền team.
- Sửa ghi chú/lượng/identity; số tồn thay thế, không cộng. Review before/after đúng;
  hủy/back giữ dữ liệu; lưu xong giữ filter, cảnh báo nếu dòng không còn khớp.
- Trùng identity expiry trống/có ngày, CAS lỗi, lượng âm/tràn, giá quá2số lẻ,
  note2.001ký tự báo lỗi, không tạo snapshot.
- Double click/reload/back-save; no-op không thêm history. Hai tab sửa cùng bản
  hoặc đang import/restore: bản cũ bị conflict, không ghi đè.
- History thủ công đúng actor/source/count; restore phục hồi cả note; import8/9cột
  và Search/Find Code/Quick Quote vẫn như trước, quote price không nhận stock price.
- Desktop/mobile, keyboard focus, table cuộn ngang, nhập/lưu khi mạng chậm;
  thông báo bận/503 không lộ SQL, refresh rồi kiểm tra trước khi thử lại.
- Đo thời gian/dung lượng với dữ liệu staging đại diện và tần suất sửa dự kiến;
  kiến trúc snapshot hiện tại nhân bản toàn bộ tồn mỗi lần sửa, không có retention tự động.

Evidence local: `VALIDATION.md`, logs, benchmark JSON và screenshots cùng thư mục.
