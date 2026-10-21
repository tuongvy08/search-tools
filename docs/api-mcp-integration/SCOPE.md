# Phase API/MCP cho n8n và AI — phạm vi

Trạng thái: **đã chốt phạm vi (PO "đồng ý" 2026-10-21); thiết kế chi tiết chưa làm; chưa có code.**

## Mục tiêu nghiệp vụ

Cho n8n và trợ lý AI dùng dữ liệu Search-tools để tự động hóa việc **lập báo giá**, thay vì nhân viên làm tay: hệ thống ngoài gửi yêu cầu → ứng dụng tra sản phẩm/tồn kho/giá → ra **báo giá nháp** → nhân viên duyệt.

## Phạm vi (bước 1 + 2 theo thỏa thuận với PO)

1. **Bước 1 — chỉ đọc.** Một "thẻ ra vào" riêng cho máy (n8n/AI), tách khỏi tài khoản nhân viên, chỉ cho tra cứu sản phẩm, tồn kho, giá, quy định. Không sửa được gì.
2. **Bước 2 — ghi vào vùng nháp.** Máy chỉ tạo **báo giá nháp** hoặc đề xuất cập nhật. Dữ liệu thật chỉ đổi khi nhân viên có quyền bấm duyệt.
3. **MCP:** lớp mỏng bọc các chức năng bước 1–2 để AI gọi bằng ngôn ngữ tự nhiên. MCP không có quyền riêng; mọi thứ đi qua cùng cổng và thẻ ra vào như n8n.

## Ngoài phạm vi (làm sau, cần PO duyệt riêng)

- Máy ghi **trực tiếp** vào dữ liệu thật (bước 3): sản phẩm, giá, tồn kho, quy định.
- Mọi thao tác trên production. Staging được thử tự do theo quyết định của PO 2026-10-02; production cần PO nói "đồng ý bắt đầu".
- Nối MCP vào database trực tiếp.

## Phân loại và quy trình

**HIGH** (auth, quyền, ghi dữ liệu qua API, có thể có migration cho bảng nháp). Trước khi viết code: kế hoạch chi tiết, `specs/api-mcp-integration/VERIFICATION.md` khóa, PO duyệt kế hoạch. Sau khi làm: verifier độc lập + lượt Codex chỉ-đọc (mục 11–12 của [quy trình](../DEVELOPMENT_WORKFLOW.md)).

## Nguyên tắc thiết kế cần chốt ở bước Plan (đề xuất, chưa phải quyết định)

- Thẻ ra vào lưu dạng băm, có phạm vi quyền (đọc / tạo nháp), có thể thu hồi, có hạn dùng; không dùng chung với phiên đăng nhập Google.
- Mỗi thao tác ghi nhật ký với tên người thao tác dạng `service:<tên>` để biết máy nào đã làm gì.
- Giới hạn số yêu cầu và số dòng mỗi lần; mọi dữ liệu máy tạo ra mang trạng thái "nháp" cho tới khi người duyệt.
- Dùng lại logic báo giá hiện có (`/api/quote-assistant/*`, `quote_to_order.py`) thay vì viết lại.

## Quyết định nghiệp vụ của PO (2026-10-21, "đồng ý" theo khuyến nghị)

1. **Nguồn yêu cầu báo giá:** bắt đầu với **một** nguồn duy nhất. *Nguồn cụ thể (email, form, Zalo hay Google Sheets) còn phải chốt ở bước Plan — chưa chọn.*
2. **Người duyệt báo giá nháp:** nhân viên đang có quyền báo giá hiện có (`QUICK_QUOTE`).
3. **Lưu báo giá nháp:** lưu trong ứng dụng; tự xóa sau 30 ngày nếu không được duyệt.

## Bước tiếp theo

PO chọn nguồn yêu cầu báo giá cụ thể → viết kế hoạch chi tiết + VERIFICATION.md → PO duyệt kế hoạch → triển khai trên local/staging.
