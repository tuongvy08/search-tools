# Checkpoint để PO xem xét — 2026-11-04

Branch: `codex/fix-regulatory-menu-status`. Đã commit `e33b0a2`, push và tạo
PR #41; code đã lên staging, chưa merge/phát hành production.
PO đã nghiệm thu local và cho commit/push/tạo PR, triển khai/kiểm chứng staging
ngày 2026-11-04. Chưa duyệt merge hoặc production.
Kiểm chứng kỹ thuật staging PASS; UAT giao diện còn chờ đăng nhập, xem [STAGING](STAGING.md).
Verifier v2 PASS V1–V6; review chéo chỉ đọc đã xong, không có finding chặn.
Nghiệp vụ đã chốt: tự động đối chiếu CAS; tình trạng gán riêng chỉ thắng khi
hãng bật ưu tiên; không tự chuyển CAS chữ số đặc biệt sang số thông thường.

## V1 — Tự động chỉ nhận đúng CAS

- Cam kết: tên/mã gần giống không tạo tình trạng quản lý.
- Thử: tên và mã trùng quy tắc cũ nhưng CAS khác/trống; CAS đúng có nhiều
  tình trạng; Search, Quick Quote, dữ liệu export và Check CAS.
- Kết quả: CAS khác không nhận cảnh báo tên/mã; CAS đúng giữ ưu tiên và ghi chú.
- Verifier độc lập: PASS.
- Giới hạn: chưa đo hiệu năng production; thiếu CAS sẽ không được bù bằng tên.

## V2 — Tình trạng riêng theo hãng ưu tiên

- Cam kết: chỉ hãng bật ưu tiên mới dùng tình trạng gán riêng để ghi đè CAS.
- Thử: cùng CAS, hãng bật/tắt, gán riêng cho phép/chặn, liên kết bị hỏng.
- Kết quả: hãng tắt dùng CAS; hãng bật dùng gán riêng; liên kết hỏng vẫn chặn.
- Verifier độc lập: PASS; đọc kết quả không thay sản phẩm hay cài đặt hãng.
- Giới hạn: không thay hay nghiệm thu cài đặt của từng hãng trên production.

## V3 — Đầu vào và bản xem trước cũ

- Cam kết: API/file chỉ nhận CAS hợp lệ; chữ số đặc biệt bị từ chối và có
  hướng dẫn nhập lại; không lưu một phần file có dòng sai.
- Thử: request tên/mã giả, file cũ, CAS sai checksum, số toàn-rộng/Ả Rập/
  Devanagari/trộn, dấu gạch giống nhau, ký tự ẩn, edit/restore, plan cũ.
- Kết quả: bị từ chối, snapshot dữ liệu không đổi; CAS thông thường vẫn dùng được.
- Verifier độc lập: PASS; phát hiện v1 đã được PO chốt và kiểm lại theo v2.
- Giới hạn: không tự sửa CAS sai đã có trong sản phẩm/quy tắc cũ.

## V4 — Giữ lịch sử và mục đã bảo vệ

- Cam kết: không xóa/chuyển đổi ngầm quy tắc tên/mã cũ; chỉ xem/ngừng.
- Thử: import CAS thay theo phạm vi có quy tắc cũ và mục bảo vệ; gửi lặp;
  phiên bản cũ; gây lỗi ghi nhật ký.
- Kết quả: mục cũ/bảo vệ nguyên vẹn, restore tên/mã bị chặn; lỗi audit rollback.
- Verifier độc lập: PASS.
- Giới hạn: chưa thử tải đồng thời ở quy mô production.

## V5 — Dọn nhãn sai

- Cam kết: mặc định chỉ kiểm tra; không xóa nhãn còn tham chiếu hiện tại hoặc
  lịch sử qua khóa ngoại; xóa và audit cùng một transaction.
- Thử: liên kết từ quy tắc/sản phẩm/bản phục hồi/lịch sử/khóa bảo vệ; nhãn đổi;
  ID/nhãn sai; có liên kết mới đang ghi; một nhãn trong lô không đủ điều kiện.
- Kết quả: các trường hợp này đều không xóa; trường hợp đủ điều kiện chỉ xóa
  đúng ID và giữ bản trước trong audit.
- Verifier độc lập: PASS.
- Giới hạn: chưa chứng minh hai nhãn production không có liên kết; chưa xóa.
  Công cụ dựa trên khóa ngoại, chưa đánh giá các liên kết nghiệp vụ dạng text.

## V6 — Menu, nhãn và quyền

- Cam kết: menu trang con nhất quán; CAS không thành tên tình trạng mới;
  quyền và chống giả mạo yêu cầu giữ nguyên.
- Thử: trang con và đường dẫn gần giống, admin/staff, thiếu quyền/CSRF,
  thêm/đổi tên/import nhãn; browser local với template/static thật.
- Kết quả: menu và biểu tượng đúng, Escape đóng menu, form chỉ CAS và không
  chọn nhãn sai; request không đủ quyền bị từ chối.
- Verifier độc lập: PASS về HTTP/HTML/DOM; browser local do agent chính kiểm.
- Giới hạn: chưa nghiệm thu trình duyệt trên staging/production với bản mới.

## Bước kế tiếp

PO đã nghiệm thu local, cho commit/push/tạo PR và triển khai staging.
PR #41 đã mở, staging technical smoke PASS; hoàn tất UAT giao diện sau đăng nhập. Production/dọn nhãn
vẫn là bước duyệt riêng theo [OPERATIONS](OPERATIONS.md), có kiểm tra trước/sau.

Ảnh hưởng cần chấp nhận trước phát hành: quy tắc tên/mã chung ngừng tạo cảnh
báo trên toàn danh mục; tình trạng gán riêng của hãng ưu tiên vẫn có hiệu lực.
Không có cảnh báo CAS không phải kết luận hàng được phép nhập khẩu.

Evidence: [verifier v2](../../specs/regulatory-cas-only/VERIFICATION_RESULT.md),
[validation](VALIDATION.md). Tổng verifier: 211 test PASS, hai DOM script exit 0.
Review chéo: [kết quả gốc](../../specs/regulatory-cas-only/CROSS_VENDOR_REVIEW.md).
