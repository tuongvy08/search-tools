# Phase ui-system — đưa toàn bộ giao diện về một phong cách

PO yêu cầu 2026-10-02 sau khi xem giao diện quản trị quy tắc mới trên staging (“quá ổn, rất đẹp”): thiết kế lại toàn bộ các menu khác và trang chủ theo phong cách đó (hoặc Apple). **Quyết định agent:** mở rộng đúng phong cách quy tắc (đã được PO duyệt, riêng cho nghiệp vụ hóa chất), không trộn phong cách Apple, để cả ứng dụng một ngôn ngữ thiết kế.

## Nguyên tắc

- Chỉ giao diện: giữ route, tên trường form, CSRF, quyền, truy vấn, `id`/`data-*` mà JavaScript dùng, và các đoạn HTML bị test khóa.
- Hệ thiết kế chung `static/ui_system.css` (màu, chữ, nút, form, bảng, khung, chip) bật theo từng trang bằng class `ui-v2` trên `<body>` — trang chưa chuyển không bị ảnh hưởng.
- Mỗi đợt: một branch/PR, test liên quan đạt, tự review ảnh chụp desktop + mobile, triển khai staging để PO xem. Production gom theo cụm đợt, mỗi lần cần PO “đồng ý bắt đầu”.

## Các đợt

1. **Nền tảng:** `ui_system.css` (tách từ trang quy tắc) + thanh điều hướng chung theo phong cách mới.
2. **Quản trị nhỏ/vừa:** sản phẩm (danh sách, form), tồn kho (danh sách, form), trung tâm nhập, mạng/IP, lịch sử đăng nhập, bảo vệ quy tắc.
3. **Quản trị lớn:** người dùng, nhóm/quyền, mẫu báo giá, tỷ giá, brand compliance, nhập dữ liệu.
4. **Đăng nhập** và **chờ duyệt**.
5. **Tra cứu (trang chủ)** và **Quick Quote** — nhân viên dùng hằng ngày, nhiều JavaScript nhất: làm cuối, cẩn thận nhất, PO dùng thử trên staging trước khi lên production.

## Theo dõi

| Đợt | Branch | Trạng thái |
| --- | --- | --- |
| Quy tắc (mẫu) | `feat/admin-regulatory-ui` (PR #30) | Staging, PO khen |
| 1 + 2 | `feat/ui-system` | Xong local: `ui_system.css`, thanh điều hướng mới; 8 trang (sản phẩm, form sản phẩm, tồn kho, form tồn, trung tâm nhập, mạng/IP, lịch sử đăng nhập, bảo vệ quy tắc). 1233 test + 8 DOM OK; tự review ảnh chụp. **Đã lên staging** (`a8cdae2`). |
| 3 | — | Chưa làm |
| 4 | — | Chưa làm |
| 5 | — | Chưa làm |

## Ghi chú kỹ thuật đợt 1–2

- Nhiều trang cũ giới hạn `max-width` ngay trên `<body>` (làm thanh điều hướng bị thụt); `ui-v2` bỏ giới hạn đó và các trang này được bọc nội dung trong `<main class="admin-shell">`.
- Bỏ các nhãn in hoa kiểu “QUẢN TRỊ · …” chỉ lặp tiêu đề; giữ nhãn có thông tin (mã tác vụ, bước 1/2).
- Đổi khóa cache `admin_nav.css` thành `20261002ui1` ở mọi template để trình duyệt tải thanh điều hướng mới.
- Test `test_phase6c4_admin_ux` nới cách so `class="admin-page"` để cho phép thêm class (`ui-v2`), giữ nguyên ý “trang quản trị phải dùng lớp chung”; bổ sung `v=` còn thiếu cho `styles.css` ở 3 trang quy tắc (lỗi có sẵn từ PR #22).
