# Thẻ nội bộ có giá (cho n8n lập báo giá) — kế hoạch

Trạng thái: PO duyệt 2026-11-21 ("đồng ý"; PO ngủ, AI làm xuyên đêm **chỉ trên staging**). Mức **HIGH** (mở giá qua Internet, xác thực). Production: CHƯA — cần PO nói "đồng ý bắt đầu". Mở lại phase đã tạm dừng 2026-11-15; hồ sơ nền: [SCOPE](SCOPE.md), [PLAN](PLAN.md), [OPERATIONS](OPERATIONS.md). Hợp đồng kiểm chứng: [specs/machine-api-internal-price/VERIFICATION.md](../../specs/machine-api-internal-price/VERIFICATION.md).

## Nhu cầu

n8n của PO nhận yêu cầu báo giá (email, Zalo...), tự tra cứu trên ứng dụng bằng mã hàng/CAS/tên để lấy **hàng hóa + giá + pháp lý + tồn kho** rồi điền vào file báo giá. Khác chatbot khách: **cần giá**, không bị giới hạn theo hãng/trường.

## Quyết định của PO (2026-11-21)

1. Thẻ nội bộ xem **tất cả hãng, tất cả trường** của 3 loại dữ liệu (hàng hóa, pháp chế, tồn kho) kể cả giá; **không chịu phân quyền team**.
2. Vẫn **không** mở: tài khoản/mật khẩu/lịch sử đăng nhập/cấu hình/mọi bảng ngoài 3 loại trên.
3. Thẻ chỉ chạy từ IP máy n8n, hạn ngắn, có nhật ký (không ghi giá).
4. Giá ghi đủ 3 cột (giá gốc, hệ số ship, giá VND quy đổi), **chưa cộng lợi nhuận**.
5. Thẻ thử đặt trong cấu hình workflow thử trên staging (không dùng cho production; đổi sang credential n8n trước khi lên production).
6. Thẻ chatbot khách giữ nguyên, **không bao giờ có giá**; agent chatbot khách đang chạy không bị đụng.

## Thiết kế (AI quyết)

- Mỗi thẻ có `profile`: `customer` (mặc định, hành vi cũ) hoặc `internal`. Profile chỉ lấy từ file thẻ, **không có tham số nào trong lời gọi** để tự nâng cấp.
- **Hai tài khoản database tách biệt** (phòng thủ nhiều lớp): `machine_api_ro` (khách; vẫn không đọc được cột giá) và `machine_api_internal_ro` (nội bộ; chỉ SELECT các cột cần của 3 loại dữ liệu + bảng tỷ giá). Thẻ khách **không bao giờ** dùng kết nối nội bộ; thiếu `MACHINE_INTERNAL_DATABASE_URL` thì thẻ nội bộ bị từ chối (503), không lùi về kết nối khách.
- Thẻ nội bộ **bắt buộc** có `allowed_ips`; không cần `team_id`; luôn trả số lượng tồn chính xác.
- Giá VND = đúng công thức ứng dụng nhân viên: `round(giá × hệ số ship × tỷ giá, -3)`, tỷ giá qua cùng bộ phân giải `currency_rates`; không phân giải được tỷ giá → trả `unit_price_vnd = null` + trạng thái, **không bao giờ** tính bằng tỷ giá 1.
- Sửa **xếp hạng** (lỗi gây tạm dừng 2026-11-15): trùng mã → trùng CAS → trùng tên → bắt đầu bằng → chứa; trong cùng mức, **xen đều các hãng**. Áp dụng cho cả thẻ khách. Thẻ nội bộ được trả tới 50 kết quả (mặc định 20).
- Bộ lọc cuối `_assert_clean` vẫn chặn khóa id/nội bộ kỹ thuật; với thẻ khách vẫn chặn mọi khóa giá/ghi chú.

## Kiểm chứng

Hợp đồng ở VERIFICATION (khóa trước khi viết code). Sau khi làm: test tự động + **verifier độc lập** (subagent ngữ cảnh sạch) + **Codex chỉ-đọc** (bản xuất sạch từ Git, không dữ liệu thật) + chạy thật trên staging qua n8n → Google Sheet.

## Hoàn tác

Thu hồi thẻ nội bộ (vài giây) hoặc `systemctl stop search-tools-machine-api`. Role nội bộ gỡ bằng phần "Gỡ bỏ" trong `sql/machine_internal_role.sql`. Ứng dụng nhân viên không bị ảnh hưởng.
