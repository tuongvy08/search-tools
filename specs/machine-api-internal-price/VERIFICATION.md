# Verification Contract — machine-api-internal-price — v1 (KHÓA khi bắt đầu implement 2026-11-21)

Kế hoạch: [docs/machine-api/INTERNAL_PRICE.md](../../docs/machine-api/INTERNAL_PRICE.md). Nền: hợp đồng đã PASS ở [specs/machine-api-readonly/VERIFICATION.md](../machine-api-readonly/VERIFICATION.md) — mọi claim cũ của thẻ **khách** phải còn đúng (claim I1).

## Claim I1 — Thẻ khách không đổi, không bao giờ có giá

Thẻ `profile=customer` (hoặc không khai `profile`) cho kết quả y như trước về quyền và trường (trừ thứ tự xếp hạng, xem I7). Không khóa giá/ship/ghi chú nào xuất hiện; tài khoản DB của thẻ khách vẫn **không** đọc được `products.price/ship/note`, `stock_items.stock_price_vnd/note`.

Oracle: toàn bộ 52 test hiện có của `tests/test_machine_api.py` + bộ độc lập `tests/independent/*machine*` còn PASS (số lượng không giảm); truy vấn giá bằng role khách → "permission denied"; quét JSON của ≥ 30 lời gọi thẻ khách không có khóa giá.

Failure case: thẻ khách gửi `?profile=internal`, `?include_price=1`, header `X-Profile: internal`, `price=1` → bị bỏ qua (không có giá); thẻ khách + DSN nội bộ đã cấu hình → vẫn dùng DSN khách.

## Claim I2 — Thẻ nội bộ chỉ hoạt động khi cấu hình đủ và an toàn (fail-closed)

Oracle: thẻ `profile=internal` bị **bỏ qua kèm cảnh báo** nếu thiếu `allowed_ips` hoặc `profile` không thuộc {customer, internal}; thiếu `MACHINE_INTERNAL_DATABASE_URL` → 503 (không lùi sang DSN khách, không 200); sai/hết hạn/bị thu hồi → 401; IP lạ → 403; vẫn chỉ `GET`.

Failure case: file thẻ có hai mục trùng id/băm; `profile` viết hoa/thừa khoảng trắng; `allowed_ips` rỗng; DSN nội bộ trỏ sai → 503 chung, không lộ DSN/SQL.

## Claim I3 — Thẻ nội bộ thấy tất cả hãng, tất cả trường cần cho báo giá

Oracle: với dữ liệu thử gồm ≥ 4 hãng (kể cả hãng KHÔNG thuộc team nào), thẻ nội bộ tìm thấy sản phẩm của mọi hãng và nhận: `name, code, cas, brand, size, note`, khối `price` (giá gốc, hệ số ship, tiền tệ, tỷ giá, `unit_price_vnd`, `price_status`), `compliance` đầy đủ (kể cả ghi chú), `stock` với tổng số lượng chính xác + từng lô (số lượng, hạn dùng, trạng thái hạn, giá tồn kho, ghi chú tồn kho). Không khóa id kỹ thuật (`id`, `product_id`, `stock_item_id`).

Failure case: team nào đó bị giới hạn hãng/trường vẫn không ảnh hưởng thẻ nội bộ; sản phẩm không có tồn kho → `status=OUT_OF_STOCK`, danh sách lô rỗng.

## Claim I4 — Giá VND khớp từng đồng với ứng dụng nhân viên

Oracle: ≥ 30 sản phẩm thử (nhiều hãng/tiền tệ, giá dạng số nguyên/thập phân/âm/rỗng/chữ, ship rỗng/0/lẻ) — `unit_price_vnd` của cổng **bằng** kết quả hàm `search._compute_unit_price_details` (cùng input, cùng bộ phân giải tỷ giá), kể cả làm tròn đến nghìn đồng. Trạng thái tỷ giá lỗi (hãng chưa gán tiền tệ, thiếu tỷ giá, lược đồ chưa đủ) → `unit_price_vnd = null` + `price_status` khớp mã trạng thái của ứng dụng; **không bao giờ** 0 hay tỷ giá 1 ngầm.

Failure case: đổi tỷ giá trong DB giữa hai lời gọi → lời gọi sau dùng tỷ giá mới (không cache sai); hãng viết khác hoa/thường/khoảng trắng.

## Claim I5 — Thẻ nội bộ không chạm được dữ liệu ngoài 3 loại cho phép

Oracle: role `machine_api_internal_ro` chỉ có SELECT theo cột trên đúng: `products`, `stock_items`, `stock_state`, `regulatory_rules`, `regulatory_statuses`, `brand_compliance_settings`, `brand_master`, `currency_rates`; thử `SELECT` từ `app_users`, `teams`, `login_audit_events`, `team_brands` và mọi INSERT/UPDATE/DELETE/DDL bằng role này → "permission denied"; kết nối luôn chỉ-đọc.

Failure case: query chèn/SQL lạ trong tham số `q` không thực thi được (tham số hóa); `statement_timeout` vẫn áp dụng.

## Claim I6 — Nhật ký và phản hồi lỗi không rò bí mật

Oracle: dòng truy cập không chứa thẻ, từ khóa tìm, giá hay mã sản phẩm (chỉ id thẻ, điểm vào, số kết quả, IP, ms, và nhãn profile); lỗi 4xx/5xx không chứa DSN, SQL, đường dẫn file, giá.

## Claim I7 — Xếp hạng đúng (lỗi gây tạm dừng 2026-11-15)

Oracle: trên bộ dữ liệu có ≥ 5 hãng cùng khớp một từ khóa: (a) trùng mã đứng trước trùng CAS, trước trùng tên, trước "bắt đầu bằng", trước "chứa"; (b) trong cùng mức, `min(limit, số hãng)` kết quả đầu thuộc các hãng **khác nhau** (không dồn một hãng như CATO); (c) thứ tự ổn định giữa các lần gọi; (d) `more` đúng khi còn kết quả; (e) thẻ khách ≤ 10, thẻ nội bộ ≤ 50 (mặc định 20); `limit` vượt trần bị cắt, `limit` sai kiểu → 400.

Failure case: từ khóa là một đoạn của nhiều tên; tên chứa ký tự đặc biệt `% _ \`; code trùng nhưng khác hãng.

## Claim I8 — Pháp chế và tồn kho không đổi nghĩa so với bản đã duyệt

Oracle: `NO_RULE` vẫn là trạng thái tường minh (không phải "được phép"); ngoại lệ thủ công và trạng thái lỗi dữ liệu xử lý như cũ; tồn kho `pack_confirmed`/`same_cas` hoạt động cho thẻ khách y như trước và có thêm số lượng chính xác cho thẻ nội bộ.

## Claim I9 — Chạy thật trên staging: n8n → cổng → Google Sheet (UAT)

Oracle: workflow n8n trên staging đọc ≥ 25 ca yêu cầu trong tab **Yêu cầu** của một Google Sheet thử (mã đúng, CAS nhiều hãng, chỉ có tên, mã sai, dòng trống, số lượng lỗi, trùng...), gọi cổng bằng thẻ nội bộ, ghi vào tab **Báo giá nháp**: đủ số dòng ra; dòng khớp có giá VND **bằng** giá ứng dụng nhân viên hiển thị cho cùng sản phẩm (đối chiếu ≥ 10 dòng bằng truy vấn độc lập); dòng không khớp được đánh dấu "cần người xử lý" và **để trống** mã/giá; không dòng nào bị mất; thẻ chatbot khách cùng lúc vẫn **không** thấy giá. Xác nhận từ nhật ký thực thi n8n + nhật ký cổng.

Failure case: cổng tạm dừng giữa chừng; thẻ hết hạn; yêu cầu quá 50 kết quả (`more=true` được ghi nhận, không cắt thầm lặng).

## Ngoài phạm vi

Production (cần PO "đồng ý bắt đầu"); ghi/sửa dữ liệu; cộng lợi nhuận/công thức bán; đổi agent chatbot khách; giao diện quản lý thẻ; tìm "gần giống".

---

## Thay đổi hợp đồng

Không tự sửa file này sau khi implement đã bắt đầu để làm test dễ pass hơn. Cần đổi claim/oracle: ghi lý do vào "Change request log" dưới đây → xin phê duyệt (PO cho thay đổi yêu cầu; verifier độc lập đủ nếu chỉ làm rõ oracle) → tăng version → chạy lại toàn bộ verification.

### Change request log

- 2026-11-22 (làm rõ oracle I4, không đổi yêu cầu; verifier độc lập đồng ý cách hiểu khi chạy lại): giá dữ liệu hỏng/không hữu hạn (`nan`, `inf`, giá×ship tràn) → `unit_price_vnd=null` + `price_status="INVALID_PRICE_DATA"`, KHÔNG làm hỏng cả phản hồi; một sản phẩm bẩn không được làm mất các sản phẩm sạch cùng truy vấn.
- 2026-11-22 (làm rõ oracle I3): "từng lô" = trả tối đa 200 lô kèm `lots_total` và cờ `lots_truncated`; không cắt thầm lặng.
- 2026-11-22 (làm rõ oracle I7, theo Codex chỉ-đọc): "bắt đầu bằng" là MỘT mức chung cho mã và tên (không tách mã-trước-tên); mức: 0 trùng mã, 1 trùng CAS, 2 trùng tên, 3 bắt đầu bằng, 4 chứa; hãng xen đều trong từng mức.
- 2026-11-22 (sửa lần 2, I3): lô của ĐÚNG các mã đang hỏi được đọc đầy đủ (trần 50.000 dòng) nên `quantity`/`status` luôn chính xác; lô cùng CAS (chỉ để gợi ý tương đương) giữ trần 5.000; danh sách `lots` trả tối đa 200 lô kèm `lots_total`/`lots_truncated` (PO xác nhận 200 là đủ cho báo giá — PO đã chốt giữ 200 ngày 2026-11-22; dữ liệu staging: tối đa 1 lô/mã). Giá/tỷ giá/giá tồn kho hỏng (`NaN`, `inf`) → trường `null` + `INVALID_*`, không 500, không `NaN` trong JSON.
- 2026-11-22 (siết thêm theo khuyến nghị verifier, I2): thẻ nội bộ bị bỏ qua nếu `allowed_ips` có dải quá rộng (IPv4 /<16, IPv6 /<48) hoặc hạn dùng quá 90 ngày; lô tồn kho của đúng mã đang hỏi luôn được đọc trước khi cắt 5000 dòng.
- 2026-11-22 (sửa lần 3, PO duyệt thêm 1 lượt sửa vì hết ngân sách 2/2; mục tiêu I1): mỗi nhánh của truy vấn lô tồn kho (đúng mã / cùng CAS) nay có ORDER BY brand_norm, code_norm, size_norm, expiry_date NULLS LAST TRƯỚC khi cắt trần, để tập lô giữ lại không phụ thuộc thứ tự vật lý của bảng và nhóm `same_cas` của thẻ khách khớp bản cũ `351cf1a`. Khác biệt có chủ ý còn lại so với `351cf1a`: bản cũ đọc sai `status`/`quantity` khi mã đang hỏi nằm sau >5000 lô cùng CAS (kết luận HẾT HÀNG sai); bản mới đúng (I3).
