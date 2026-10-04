# Phase quote-to-order — menu "Báo giá → Đơn hàng"

Branch `feat/quote-to-order` (từ `ops/production-ui-release`), mức MEDIUM, **chưa commit**. Nguồn tham chiếu: bản nháp `quote_to_order.zip` của PO (viết lại cho khớp dự án).

## Nghiệp vụ đã chốt (PO, 2026-10-04)

- Upload **một hoặc nhiều** file báo giá `.xlsx` → gộp **một danh sách** (có cột "Báo giá nguồn") → một file đơn hàng. Trùng Code giữa các báo giá: giữ hai dòng riêng, cảnh báo "Trùng Code".
- Quyền dùng: team có `QUICK_QUOTE` (đã kèm `VIEW_PRICE`). Không thêm quyền mới, không sửa `team_permissions.py`.
- Loại hàng chỉ có `Nhập khẩu` | `Mua trong nước`.
- Giá: `Đơn giá (*)` ← **"Đơn giá có VAT"**; `Thành tiền` = Số lượng × Đơn giá (ghi giá trị, không ghi công thức); `Đơn giá mua dự kiến` ← "Giá nhập chưa VAT" giữ nguyên số; `Giá bán tối thiểu`: nếu báo giá không có cột này, hoặc ô trống/0 thì **bằng Đơn giá** (đã gồm VAT; PO chốt 2026-10-04), có giá trị riêng thì giữ nguyên; ghép tay được và người dùng sửa được trên bảng.
- "Ghi chú hàng hóa" và "Ghi chú khác" được đưa sang, người dùng sửa/xóa/gõ thêm trước khi xuất. "Ghi chú nội bộ" không bao giờ đưa sang.
- Bảng **sửa từng ô như Excel**, thêm dòng trống, xóa dòng. Số lượng đặt có thể khác báo giá (cho thập phân, cho lớn hơn).
- **Chặn nút Tải về** khi còn dòng đã tick thiếu trường (*) hoặc số lượng ≤ 0. Dòng không tick không bị kiểm tra.
- **Dòng tiêu đề mặc định là dòng 16** (PO chốt 2026-10-04). File có tiêu đề ở dòng khác (ví dụ 11, 13) vẫn đọc được nhưng hiện **cảnh báo** và mở sẵn phần ghép cột để người dùng kiểm tra/sửa; mỗi file có số dòng tiêu đề riêng. Người dùng nhập/áp dụng số dòng thì coi là đã xác nhận, không cảnh báo lại.
- Ghép cột linh hoạt: tự nhận diện theo tên cột; trường chưa nhận ra thì chọn từ danh sách cột thật của file hoặc "Không có, tôi nhập tay"; không thấy dòng tiêu đề thì nhập số dòng. Nhớ cách ghép trên **trình duyệt từng người** (không lưu server/DB).

## Thiết kế

- `quote_to_order.py` (blueprint `/quote-to-order/`: `GET /`, `POST /parse`, `POST /export`); `templates/quote_to_order.html`; `static/quote_to_order.{js,css}`; mẫu đơn hàng `assets/quote_to_order/bang-hang-hoa_template.xlsx` (nguyên bản).
- **Stateless:** server không lưu file/dữ liệu. Trình duyệt giữ file; đổi ghép cột thì gửi lại file kèm bảng ghép. Khi tải về, trình duyệt gửi các dòng đã chọn và server **kiểm tra lại toàn bộ** (bắt buộc, số hợp lệ, SL > 0, Loại hàng, độ dài, tối đa 1.000 dòng) — không tin client. Lý do bỏ `payload` ẩn của bản nháp: gửi lại toàn bộ danh sách, theo chỉ số dòng, không dùng được khi người dùng sửa/thêm dòng; có 3 worker gunicorn nên không giữ trạng thái trong tiến trình.
- CSRF bằng `session_security.verify_csrf_token` (header `X-CSRF-Token`); đăng nhập + quyền kiểm tra trong route; phản hồi `Cache-Control: no-store`.
- Đọc file: kiểm tra container (zip bomb, macro) rồi `openpyxl` `data_only=True`, chế độ chỉ đọc; không ghi ra đĩa. Dòng tiêu đề tìm theo nội dung; vùng dữ liệu kết thúc khi cột TT gặp chữ không phải số. Chưa có giá trị tính sẵn thì tự tính: `ROUNDUP(giá nhập/(1−%LN), −4)` (Decimal, không lỗi float) rồi × (1 + thuế VAT đọc từ dòng "Thuế VAT x%", mặc định 8%), làm tròn đồng.
- Ghi file: chuỗi bắt đầu bằng `=` được ghi dạng chuỗi (không thành công thức); Code/Cas định dạng văn bản.

## Kiểm chứng

- `tests/test_quote_to_order.py` (46 test). Toàn bộ bộ test: 1.275 test, 0 lỗi sau khi sửa icon menu (477 skip vì cần Postgres).
- Chạy tay trong trình duyệt với app local + dữ liệu mẫu: tải 2 file, ghép cột thủ công, nhớ ghép cột, sửa ô, Enter/mũi tên, tự tick khi sửa SL, lọc, chặn tải, tải thành công (2 dòng, tổng tạm tính đúng).

## Điểm còn mở cho PO

1. Công thức thật của ô "Đơn giá có VAT" (hiện tạm: giá chưa VAT × (1 + thuế), làm tròn đồng) — cần file báo giá thật đã điền để đối chiếu.
2. "Đơn giá mua dự kiến" đang giữ nguyên số chưa VAT — nên thử import một file vào phần mềm để xác nhận.
3. Nhớ ghép cột chung cho cả team (lưu DB) chưa làm; nếu cần là việc riêng, cần duyệt (đổi cơ sở dữ liệu).
4. **Staging (`ssh staging`) — 2026-10-04:** commit `ee56710` từ `900a5f9`, gói bundle `search-tools-q2o-900a5f9-to-ee56710.bundle` SHA256 `8dc76cfb7fb80be9212e6fea0a3aa9d24421d37d852b6941dadca029f3dc1995`. Gate: HEAD cũ `900a5f9`, working tree sạch, bundle verify OK, FETCH_HEAD đúng; checkout detach `/srv/search-tools`; chỉ restart `search-tools-staging.service`. Smoke: web/worker active, `/login` 200, `quote_to_order.css/js` 200, log 0 lỗi; trang `/quote-to-order/` khi chưa đăng nhập từ IP ngoài văn phòng trả 403 giống Quick Quote (IP allowlist). Quay lại: `checkout --detach 900a5f9… && restart`. Chưa production.
