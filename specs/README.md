# specs/

Thư mục chứa hồ sơ đặc tả cho các task đủ lớn hoặc đủ rủi ro để cần ghi lại yêu cầu và cách kiểm chứng trước khi code. Áp dụng nguyên tắc: tài liệu tương xứng với rủi ro, không tạo hồ sơ cho thay đổi nhỏ.

## Khi nào tạo specs/<task>/

- Task LOW: không cần, sửa trực tiếp.
- Task MEDIUM: không bắt buộc; tạo khi thay đổi ảnh hưởng nhiều phần hoặc cần ghi lại quyết định.
- Task HIGH (theo AGENTS.md và SECURITY.md): bắt buộc tạo `specs/<task>/VERIFICATION.md` trước khi implement — xem quy trình đầy đủ tại [docs/DEVELOPMENT_WORKFLOW.md](../docs/DEVELOPMENT_WORKFLOW.md) mục 11.

## Cấu trúc một task HIGH

    specs/<task>/
    ├── VERIFICATION.md          # hợp đồng kiểm chứng, khóa trước khi code
    └── VERIFICATION_RESULT.md   # báo cáo của verifier độc lập, cập nhật mỗi lần chạy

Dùng `specs/_TEMPLATE/VERIFICATION.md` và `specs/_TEMPLATE/VERIFICATION_RESULT.md` làm khung khi tạo task mới.

Tên task theo định dạng `<MÃ-DỰ-ÁN>-<mô-tả-ngắn>`, ví dụ `ERP-027-order-unlock`, khớp với tên branch tương ứng.
