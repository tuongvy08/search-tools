# Rà soát chéo (Codex, chỉ-đọc) — machine-api-readonly — 2026-11-15

Phạm vi đã gửi: bản xuất sạch gồm `machine_api.py` + 7 module dùng chung, `scripts/machine_token.py`, `integrations/mcp/search_tools_mcp.py`, `deploy/*`, `sql/machine_readonly_role.sql`, `tests/test_machine_api.py`, `docs/machine-api/{SCOPE,PLAN,HUONG_DAN_KET_NOI}.md`, `specs/machine-api-readonly/VERIFICATION.md`. Không có `.env`, secret, dữ liệu database hay thông tin hạ tầng. Chạy `codex exec --sandbox read-only --ephemeral` (Codex CLI 0.163.0). Chạy trên bản đã sửa đợt 1 (V9), trước đợt sửa 2.

Kết luận của Codex: **không có phát hiện mức chặn; 3 cao, 3 trung bình.** Xử lý:

| # | Mức | Phát hiện | Xử lý |
| --- | --- | --- | --- |
| 1 | Cao | `/compliance` tra thẳng danh mục quy tắc, không lọc theo hãng của team | **Cố ý, giữ nguyên** — giống `/check_cas` (Check License) của nhân viên; quy định về một chất không phụ thuộc hãng, không lộ sản phẩm nào. Ghi rõ trong Change request log V4 và HUONG_DAN_KET_NOI; nhờ verifier đánh giá độc lập cách hiểu này |
| 2 | Cao | MCP: httpx ghi URL (từ khóa khách) vào stderr ở mức INFO — Codex đã tái hiện | **Đã sửa**: tắt log INFO của httpx/httpcore/mcp; selftest kiểm stderr không chứa từ khóa/URL |
| 3 | Cao | `machine_token.py`: đọc-sửa-ghi không khóa, hai lệnh thu hồi đồng thời có thể khôi phục thẻ đã thu hồi | **Đã sửa**: `flock` trên thư mục chứa file thẻ; test chạy 8 tiến trình `new` rồi 8 `revoke` song song, không mất cập nhật, không để lại tệp phụ |
| 4 | Trung bình | `/stock?code=` chỉ cần `SEARCH`, team bị ẩn mã vẫn dò được mã | **Đã sửa**: yêu cầu thêm `VIEW_CODE` (403 nếu thiếu); có test |
| 5 | Trung bình | Giới hạn tần suất theo thẻ nằm trong bộ nhớ từng worker; unit chạy 2 worker ⇒ ~2× | **Đã sửa**: unit chạy 1 worker × 8 luồng (giới hạn chính xác); đã triển khai và kiểm trên staging |
| 6 | Trung bình | Tồn kho khớp theo mã, bỏ hãng/quy cách: có thể báo `IN_STOCK` cho hãng/quy cách khác cùng mã | **Giảm thiểu, không ép khớp**: đo trên staging chỉ 212/233 dòng kho khớp cả hãng (180 khớp cả quy cách) với danh mục; ép khớp hãng sẽ báo "hết hàng" sai ~9%. Thay vào đó thêm cờ `stock.pack_confirmed` (lô còn hàng có đúng hãng + quy cách hay không) và chỉ dẫn cho bot nói "cần nhân viên xác nhận" khi `false`. Hành vi theo mã giống ô tìm kiếm nhân viên |

Phát hiện bổ sung của verifier độc lập đã xử lý cùng đợt: regex id/sha dùng `fullmatch` (id có xuống dòng cuối từng lọt), công cụ cấp thẻ chịu được mục rác có sẵn trong file.
