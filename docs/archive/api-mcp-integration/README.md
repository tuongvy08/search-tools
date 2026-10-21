# Phase API/MCP — ĐÃ ĐÓNG, KHÔNG LÀM (PO 2026-10-21)

Không có code nào được viết. Thư mục này lưu hồ sơ để khỏi phải làm lại nếu mở lại.

## Quyết định và lý do

1. **Cổng báo giá tự động** (Google Sheets → n8n → báo giá nháp): tạm dừng vì yêu cầu thực tế thường thiếu mã/CAS nên tự động hóa khớp được ít dòng. Hồ sơ: [PAUSED_quote_gateway_PLAN](PAUSED_quote_gateway_PLAN.md), [PAUSED_quote_gateway_VERIFICATION](PAUSED_quote_gateway_VERIFICATION.md).
2. **Trợ lý AI tra cứu chỉ đọc** (cấm nhập, giấy phép, tồn kho, tương đương): không làm vào lúc này vì ô tìm kiếm hiện có đã cho cùng thông tin (tình trạng quản lý, tồn kho, tìm theo CAS, kiểm tra hàng loạt), lợi ích thêm hẹp so với chi phí và rủi ro (cổng truy cập mới, rủi ro trả lời pháp lý sai, dữ liệu qua dịch vụ AI). Hồ sơ: [SCOPE](SCOPE.md), [PLAN](PLAN.md), [VERIFICATION](VERIFICATION.md).

## Điều kiện mở lại

Có một việc cụ thể nhân viên mất nhiều thời gian mà tìm kiếm không giải quyết được (ví dụ dữ liệu nhà cung cấp đã có mã, cần cập nhật hàng loạt), hoặc nguồn yêu cầu báo giá đã được chuẩn hóa để phần lớn dòng có mã/CAS. Khi đó đo trước (tỷ lệ dòng có mã/CAS; số yêu cầu mỗi ngày và số phút mỗi yêu cầu) rồi mới quyết.

## Phát hiện còn giá trị

- Hệ thống **chưa lưu số nghị định / căn cứ pháp lý** cho quy tắc pháp chế, chỉ có nhãn và ghi chú tự do.
- Hệ thống coi sản phẩm không khớp quy tắc là "không cảnh báo"; không được diễn giải thành "được phép nhập".
- Hướng thiết kế cổng máy chỉ đọc đã qua phản biện độc lập của Codex (11 điểm), nằm trong [PAUSED_quote_gateway_PLAN](PAUSED_quote_gateway_PLAN.md) mục "Phản biện độc lập" và các claim V1–V9.
