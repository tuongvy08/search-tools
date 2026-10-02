# Phase admin-regulatory-ui — thiết kế lại giao diện quản trị quy tắc

PO yêu cầu 2026-10-02 sau UAT release 033: “giao diện cực xấu”, thiết kế lại. Branch `feat/admin-regulatory-ui`. Rủi ro **MEDIUM** (giao diện trang quản trị; không đổi dữ liệu/quyền).

## Phạm vi

- Trang **Quy tắc quản lý** (`/admin/regulatory`, `templates/admin_regulatory.html`): thứ tự tình trạng, nhập Excel, tác vụ gần đây; phần chi tiết tác vụ chỉ đổi theo style chung.
- **Tìm và sửa quy tắc** (`/admin/regulatory/rules`, `admin_regulatory_rules.html`).
- **Quy tắc #id** và **Thêm quy tắc thủ công** (`admin_regulatory_rule.html`): form, xem trước, lịch sử.

## Không đổi (tiêu chí nghiệm thu kỹ thuật)

- Route, method, tên trường form, CSRF, quyền, truy vấn dữ liệu, JavaScript hành vi (`regulatory_manual.js`, `admin_regulatory.js`) và mọi `id`/`data-*` mà JS dùng.
- Các đoạn HTML được test khóa nguyên văn (bảng lịch sử `<th>…</th><td>…</td>`, ô loại đối chiếu chỉ đọc `<input value="…" readonly>`, `>…</time>`, chuỗi xác nhận xóa…).
- Chỉ thêm ở Python: gắn màu tình trạng (đã có sẵn ở trang tổng quan) cho danh sách/chi tiết để hiện chip đúng màu.

## Phương án thiết kế

Chủ đề: sổ đăng ký quản lý hóa chất của nhà phân phối hóa chất phòng thí nghiệm. Người dùng: nhân viên quản trị/sales nội bộ. Việc chính: tìm nhanh quy tắc theo CAS/mã/tên, thấy ngay tình trạng, sửa an toàn có xem trước.

- **Màu:** Mực `#13202E` (cùng họ với thanh điều hướng tối), Giấy `#FFFFFF`, Nền phòng thí nghiệm `#EEF1F4`, Đường kẻ `#D5DCE3`, Xanh ống nghiệm `#0B6E73` cho hành động chính/liên kết, Đỏ cảnh báo `#B42318`. Màu từng tình trạng giữ theo cấu hình của người dùng.
- **Chữ:** *Be Vietnam Pro* (thiết kế cho tiếng Việt) cho toàn bộ giao diện; *IBM Plex Mono* chỉ cho giá trị định danh (CAS, mã) — vì là mã máy cần đọc đúng từng chữ số, không dùng làm nhãn trang trí.
- **Điểm nhấn duy nhất:** “nhãn tình trạng” kiểu nhãn hóa chất — chip có dải màu bên trái theo màu tình trạng; ở trang chi tiết, khối nhận diện quy tắc phóng to giá trị CAS/mã như nhãn chai thuốc thử. Mọi thứ khác phẳng, yên tĩnh, căn trái.
- **Bố cục:** tiêu đề căn trái, hành động chính ở góc phải tiêu đề; thứ tự tình trạng là danh sách có số (vì đúng là thứ tự ưu tiên); nhập Excel là 3 bước có số (đúng là quy trình); bảng danh sách: giá trị (mono, đậm) + loại, chip tình trạng, ghi chú cắt 2 dòng, trạng thái áp dụng; form chi tiết dạng lưới 2 cột; lịch sử dạng dòng thời gian.
- **Tránh:** nhãn chữ in hoa nhỏ phía trên tiêu đề, chuỗi “A · B · C”, mũi tên “→” gắn sau nút, mọi khối là thẻ bo góc giống hệt có bóng đổ.

## Kiểm chứng

Test hiện có (Python + DOM JS) phải đạt; chụp màn hình local (desktop + mobile) để tự review; triển khai staging (PO cho phép tự do) để PO xem; production cần “đồng ý bắt đầu” riêng.
