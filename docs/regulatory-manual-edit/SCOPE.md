# Phase — Quản lý quy tắc pháp chế thủ công

Trạng thái: **UAT local 8/8 + polish hiển thị 4/4 đạt; chuẩn bị PR** (2026-09-28).
PO đã review verifier PASS 20/20 sau 1 repair V13 và xác nhận UAT browser
8/8. Polish sau UAT đã được verifier kiểm chứng lại V3/V4/V7/V9/V13/V15,
PASS 6/6; PO đã kiểm tra lại hiển thị 4/4 đạt. Hợp đồng không đổi; lần cuối
105 Python tests và 2 bộ DOM đều đạt. Chưa merge hoặc deploy.
Risk: HIGH. Branch: `feature/regulatory-manual-edit`, base `main@db26d40`.

Hồ sơ phase theo quy ước `docs/<phase>/`; tài liệu chi tiết và hợp đồng
kiểm chứng đặt trong `specs/` theo yêu cầu của Product Owner:

- [Yêu cầu A–H hiện hành và spec v2](../../specs/regulatory-manual-edit/SPEC.md).
- [Migration/rollback chỉ local](../../specs/regulatory-manual-edit/MIGRATION.md).
- [Hợp đồng kiểm chứng trước code](../../specs/regulatory-manual-edit/VERIFICATION.md).

Commit riêng spec: `ee2ee5a`; VERIFICATION.md khóa và không thay đổi.
Đã triển khai code/migration/tests local. Không đọc `.env` hoặc thao tác
staging/production; chưa merge/deploy.
[Developer validation](VALIDATION.md), [local operations/rollback](OPERATIONS.md),
[verifier result](../../specs/regulatory-manual-edit/VERIFICATION_RESULT.md).

V2 bỏ tùy chọn import ghi đè và bảng preview tay, dùng UI preview + server
revalidation/revision/chống gửi lặp; chi tiết import dùng JSONB sẵn có.
Hợp đồng còn 20 claim, giữ số cũ và bỏ V12; không có số đếm sản phẩm ảnh hưởng.

Bước tiếp theo: review PR vào `main`; merge và triển khai chỉ khi được cấp
quyền riêng theo quy trình. [UAT local](UAT.md) đã đạt; chưa full repo suite.
