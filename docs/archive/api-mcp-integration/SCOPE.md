# Phase API/MCP — phạm vi

Trạng thái: **ĐÃ ĐÓNG, KHÔNG LÀM (PO 2026-10-21) — xem [README](README.md).**

## Quyết định của PO

1. **Tạm dừng cổng báo giá tự động** (Google Sheets → n8n → báo giá nháp) cho tới khi PO tìm được cách xử lý trước dữ liệu thô sao cho yêu cầu chỉ còn mã sản phẩm. Lý do: yêu cầu thực tế thường thiếu mã/CAS nên tự động hóa khớp được ít dòng, làm tay bằng search còn nhanh hơn. Tài liệu giữ lại để làm tiếp: [PAUSED_quote_gateway_PLAN](PAUSED_quote_gateway_PLAN.md), [PAUSED_quote_gateway_VERIFICATION](PAUSED_quote_gateway_VERIFICATION.md). **Điều kiện mở lại:** có quy trình/nguồn dữ liệu khiến phần lớn dòng yêu cầu có mã hoặc CAS (khuyến nghị đo bằng 1–2 file yêu cầu thật trước: tỷ lệ dòng có mã/CAS).
2. **Chuyển sang trợ lý AI tra cứu, chỉ đọc và trả lời** (PO 2026-10-21): hỏi về một sản phẩm — có thuộc loại cấm nhập không, nghị định nào, có tồn kho không, cần giấy phép không, sản phẩm tương đương là gì.

## Mục tiêu nghiệp vụ

Nhân viên hỏi AI bằng lời, AI tra dữ liệu thật của Search-tools (pháp chế, tồn kho, sản phẩm cùng CAS) và trả lời có dẫn nguồn từ dữ liệu. AI không ghi dữ liệu, không lập báo giá, không tự kết luận pháp lý.

## Phạm vi đợt này

- **Cổng máy chỉ đọc** + thẻ truy cập gắn team + giới hạn IP (nền an toàn dùng chung với hướng báo giá).
- **4 công cụ đọc** cho AI qua MCP: `tra_san_pham`, `kiem_tra_phap_che`, `xem_ton_kho`, `tim_tuong_duong`.
- Chạy thử với PO và vài nhân viên qua Claude Desktop có gắn MCP, trên staging.

## Ngoài phạm vi (làm sau, cần PO duyệt riêng)

- Giá bán/giá nhập/báo giá qua máy; n8n/Google Sheets; máy ghi dữ liệu (bước 3).
- Thêm trường **"căn cứ pháp lý" (số nghị định)** vào danh mục quy tắc. Hệ thống hiện **chưa lưu** số nghị định, nên AI chỉ trả lời căn cứ khi ghi chú quy tắc có ghi; không được bịa.
- Ô chat trong web ứng dụng, bot Zalo.
- Mọi thao tác production. Staging được thử tự do theo quyết định PO 2026-10-02; production cần PO nói "đồng ý bắt đầu".

## Phân loại và quy trình

**HIGH** (cổng truy cập mới, xác thực, đọc dữ liệu pháp chế qua máy). Trước khi viết code: [PLAN](PLAN.md) + [VERIFICATION](VERIFICATION.md) khóa, PO duyệt. Sau khi làm: verifier độc lập + lượt Codex chỉ-đọc (mục 11–12 của [quy trình](../../DEVELOPMENT_WORKFLOW.md)).

## Quyết định nghiệp vụ đã có từ trước (còn hiệu lực)

- Quyền AI thấy = quyền của team gắn vào thẻ; người dùng thử đầu tiên là nhân viên có quyền hiện có.
- (Hướng báo giá, đã tạm dừng) nháp tự xóa sau 30 ngày; nguồn yêu cầu là Google Sheets.

## Bước tiếp theo

PO trả lời 3 câu ở [PLAN](PLAN.md) mục 7 (ai dùng ở đâu; có làm căn cứ pháp lý không; chấp nhận dữ liệu đi qua dịch vụ AI) → khóa VERIFICATION → S0 → P1 trên local/staging.
