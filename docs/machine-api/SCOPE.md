# Cổng API cho bot/AI (chỉ đọc) — Phạm vi

Trạng thái: PO chốt 2026-11-15 (mở lại phase đã đóng 2026-10-21, lý do: có nhu cầu cụ thể — AI trả lời cho sale/khách, chatbot tự động). Mức **HIGH** (cổng truy cập mới ra Internet, xác thực, dữ liệu pháp chế).

## Mục tiêu nghiệp vụ

Cho phép hệ thống bên ngoài (n8n, chatbot, AI agent như Claude) hỏi dữ liệu của Search-tools để trả lời về: thông tin hàng hóa, tình trạng pháp chế (cấm nhập, giấy phép...), tồn kho. Người hỏi cuối có thể là **khách hàng bên ngoài**. Bot **không được trả lời giá**.

## Quyết định của PO (2026-11-15)

1. Người hỏi: khách hàng (qua chatbot/AI agent do PO dựng). PO đề xuất "bot chỉ trả lời hàng hóa, pháp lý, và không trả lời giá" — chốt.
2. Dữ liệu cho bot: thông tin hàng hóa + tình trạng pháp chế + tồn kho. **Không giá bán.** (Giá nhập/giá vốn: không bao giờ — quyết định kỹ thuật của AI, PO không phản đối.)
3. Chấp nhận dữ liệu đi qua dịch vụ AI bên ngoài (Claude, ChatGPT, n8n...).
4. Tự động cập nhật dữ liệu vào database: **không làm đợt này.**

## Quyết định kỹ thuật của AI (theo ủy quyền của PO)

- Không mở trực tiếp database ra Internet; làm "cổng máy" riêng, chỉ đọc, chạy thành **dịch vụ tách biệt** với ứng dụng nhân viên.
- Giá **bị loại cứng ở cổng** (không phụ thuộc quyền team hay lời dặn bot): mọi trường giá, phí vận chuyển, ghi chú nội bộ không bao giờ có trong phản hồi.
- Tồn kho cho khách: mặc định chỉ báo **Còn hàng / Hết hàng** (+ cảnh báo hạn dùng), không lộ số lượng chính xác; từng thẻ có thể bật hiển thị số lượng cho nội bộ.
- Mỗi hệ thống bên ngoài có **một thẻ (token) riêng**, gắn một **team** có sẵn (team quyết định xem được hãng nào, trường nào) — cấu hình bằng chính trang admin hiện có.

## Ngoài phạm vi (đợt này)

Máy **ghi** dữ liệu (sửa/nhập/xóa); giá/báo giá qua máy; thêm trường "căn cứ pháp lý/số nghị định"; tìm "gần giống"; đăng nhập theo từng khách; giao diện quản lý thẻ; bot Zalo/Facebook cụ thể (PO dựng trên n8n/nền tảng chat — tài liệu hướng dẫn kết nối có kèm); mọi thao tác **production** (cần PO nói "đồng ý bắt đầu").

## Giới hạn dữ liệu phải nói với người dùng bot

- Hệ thống chưa lưu số nghị định/căn cứ pháp lý: bot chỉ trích nhãn + ghi chú đang có.
- "Chưa có quy tắc" ≠ "được phép nhập": cổng trả trạng thái tường minh `NO_RULE` kèm câu cần xác nhận với pháp chế.
