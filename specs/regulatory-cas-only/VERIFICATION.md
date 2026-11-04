# Verification Contract v2 — regulatory-cas-only

2026-11-02. PO duyệt triển khai/kiểm chứng local và làm rõ nghiệp vụ:
quy tắc tự động chỉ đối chiếu CAS; tình trạng gán trực tiếp cho sản phẩm
chỉ ghi đè CAS khi hãng bật ưu tiên thủ công. HIGH do ảnh hưởng phân loại.
Hợp đồng khóa trước implementation. Không cần migration cho resolver.
Phát hành hoặc ghi dữ liệu production cần PO duyệt riêng.

## Claim V1 — Tự động chỉ đối chiếu CAS chính xác

Tên hoặc mã trùng quy tắc cũ không tạo tình trạng tự động. CAS khác, trống,
chỉ chứa một phần CAS không được nhận tình trạng từ tên/mã. CAS khớp vẫn
chọn theo ưu tiên tình trạng và gộp ghi chú đúng như trước.

Oracle: fixture PostgreSQL có tên dài chứa tên chất khác, mã trùng quy tắc,
CAS không trùng/thiếu, và hai tình trạng trên cùng CAS. Kiểm status/source/
policy/note trên Search, Quick Quote, export và Check CAS theo quyền sẵn có.

Test type: PostgreSQL integration, HTTP regression.

Failure case: quy tắc tên/mã BLOCK ưu tiên cao không được thắng quy tắc CAS
ALLOW; quy tắc CAS BLOCK thật không được mất tác dụng vì bỏ quy tắc tên.

## Claim V2 — Gán riêng chỉ ưu tiên theo hãng

Hãng bật ưu tiên thủ công + sản phẩm có tình trạng gán riêng hợp lệ: tình
trạng đó ghi đè CAS. Hãng tắt hoặc sản phẩm không có gán riêng: dùng CAS.
Liên kết gán riêng hỏng ở hãng bật ưu tiên vẫn là lỗi dữ liệu BLOCK.

Oracle: cùng CAS có sản phẩm ở hai hãng bật/tắt, trạng thái riêng ALLOW và
CAS BLOCK; đối chiếu dữ liệu trả về, khả năng xuất, note và source. Không
thay đổi cài đặt hãng hay trường tình trạng sản phẩm trong migration/code.

Test type: PostgreSQL integration, HTTP/export regression.

Failure case: hãng không ưu tiên có gán riêng ALLOW vẫn phải bị CAS BLOCK;
gán riêng lỗi không được âm thầm rơi về CAS/ALLOW.

## Claim V3 — Không tiếp nhận quy tắc tự động tên/mã mới

Form thêm/kiểm tra/lưu, mẫu Excel, parser và build/apply plan chỉ nhận CAS.
File tên/mã hoặc CAS sai phải từ chối toàn bộ; không đoán CAS từ tên/mã.
Không thể dùng request giả hoặc preview cũ để bỏ qua kiểm tra.
CAS chỉ dùng chữ số thông thường `0-9` và dấu `-`; CAS chứa chữ số Unicode
(ví dụ `６４-１７-５`, `٦٤-١٧-٥`, hoặc trộn hai dạng) phải bị từ chối với
hướng dẫn nhập lại, không tự chuyển đổi. Áp dụng khi thêm/sửa/khôi phục qua
API và nhập file/áp dụng preview. Không tự sửa dữ liệu cũ trong database.

Oracle: response HTTP/lỗi dòng import và snapshot DB (rules/statuses/audit/
jobs) trước/sau. CAS đúng vẫn thêm/sửa, preview/apply và retry được.

Test type: unit, HTTP, PostgreSQL integration.

Failure case: gửi JSON match_field=name/code dù UI không có; worker xử lý
file cũ đã upload, hoặc apply plan tạo trước đổi policy; không ghi rule mới.

## Claim V4 — Giữ dữ liệu cũ và bảo vệ khi nhập

Không xóa/quy đổi ngầm quy tắc tên/mã cũ; giao diện chỉ rõ chúng không dùng
đối chiếu. Có thể xem lịch sử/ngừng nhưng không khôi phục hoặc sửa thành
quy tắc đang dùng. Import CAS chỉ tác động phạm vi CAS tương ứng, bảo toàn
quy tắc/khóa được bảo vệ, revision, audit và cơ chế chống gửi lặp.

Oracle: snapshot DB trước/sau đọc UI và import CAS upsert/replace_scoped;
test concurrency/replay/audit failure hiện hành chuyển fixture sang CAS.

Test type: integration PostgreSQL, DOM/HTTP.

Failure case: file replace_scoped CAS vắng quy tắc tên/mã hoặc quy tắc CAS
được bảo vệ; không được xóa chúng. Restore quy tắc tên/mã cũ bị từ chối.

## Claim V5 — Dọn nhãn sai không làm mất dữ liệu liên kết

Công cụ dọn chỉ cho phép đúng định danh/nhãn đã duyệt, mặc định chỉ kiểm
tra. Từ chối xóa khi còn tham chiếu trực tiếp hoặc lịch sử qua khóa ngoại;
không đổi nhãn/policy để né kiểm tra. Giữ bản trước và thông tin audit.

Oracle: PostgreSQL fixture có/không có references từ rules/products/
product_deleted_rows/manual_keys/status_events, fingerprint trước/sau và
snapshot mọi bảng không thuộc phạm vi. Chưa chạy ghi trên production.

Test type: integration PostgreSQL, negative/concurrency tests.

Failure case: không có rule nhưng có product/history reference; hoặc nhãn
đã đổi sau dry-run; hoặc thêm reference cạnh tranh; không được mất dữ liệu.

## Claim V6 — Menu, nhãn tình trạng và quyền giữ đúng

Menu nhất quán trên trang con. CAS đơn lẻ không thể trở thành tên tình trạng
mới qua admin/import; tên tình trạng tự đặt vẫn được chấp nhận. Quyền,
CSRF, kiểm revision và scope team/brand không bị nới lỏng.

Oracle: test menu/status validation hiện hành, HTTP negative và browser UAT.

Test type: unit, HTTP, browser.

Failure case: staff gửi mutation, thiếu CSRF, CAS sai ô tình trạng, đường
dẫn gần giống /admin/regulatory-other; bị từ chối/không đánh dấu nhầm.

## Ngoài phạm vi

Không xác định phân loại pháp luật cho từng hóa chất; không bổ sung danh
sách CAS loại trừ theo rule; không đổi quy tắc tìm kiếm sản phẩm thông
thường (vẫn tìm theo tên/mã/CAS), không bật ưu tiên hãng hoặc gán riêng mới.
Không tự commit/push/deploy; verifier không được truy cập production/secret.

## Thay đổi hợp đồng

Sau khi bắt đầu implementation, thay đổi claim/oracle cần lý do, phê duyệt,
tăng version và chạy lại toàn bộ verification theo workflow.

### Change request log

- v1 (2026-11-02): thay đề xuất loại trừ chưa triển khai, theo làm rõ nghiệp vụ PO.
- v2 (2026-11-04): PO đồng ý bổ sung từ chối CAS dùng ký tự số đặc biệt và
  hướng dẫn nhập lại. Làm rõ V3 sau phát hiện SPEC_AMBIGUOUS của verifier;
  các claim khác giữ nguyên. Chạy lại toàn bộ verification với context sạch.
