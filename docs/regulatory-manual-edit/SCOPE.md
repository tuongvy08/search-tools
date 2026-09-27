# Phase — Quản lý quy tắc pháp chế thủ công

Trạng thái: **SPEC v2 theo duyệt có điều chỉnh của PO ngày 2026-09-27**,
chưa triển khai; lượt hiện tại chỉ cập nhật tài liệu.
Risk: HIGH. Branch: `feature/regulatory-manual-edit`, base `main@db26d40`.

Hồ sơ phase theo quy ước `docs/<phase>/`; tài liệu chi tiết và hợp đồng
kiểm chứng đặt trong `specs/` theo yêu cầu của Product Owner:

- [Yêu cầu A–H hiện hành và spec v2](../../specs/regulatory-manual-edit/SPEC.md).
- [Migration/rollback chỉ local](../../specs/regulatory-manual-edit/MIGRATION.md).
- [Hợp đồng kiểm chứng trước code](../../specs/regulatory-manual-edit/VERIFICATION.md).

Lượt hiện tại chỉ tài liệu: không application code, không file migration,
không SQL, không deploy, không commit/push. Chưa có validation runtime hay
VERIFICATION_RESULT.md; không hiểu bản spec là tính năng đã triển khai.

V2 bỏ tùy chọn import ghi đè và bảng preview tay, dùng UI preview + server
revalidation/revision/chống gửi lặp; chi tiết import dùng JSONB sẵn có.
Hợp đồng còn 20 claim, giữ số cũ và bỏ V12; không có số đếm sản phẩm ảnh hưởng.

Bước tiếp theo: review tài liệu v2 và dừng chờ yêu cầu tiếp. Chỉ khi được
phép implement mới triển khai local theo mục 11 của
[quy trình](../DEVELOPMENT_WORKFLOW.md), qua verifier rồi human review.
