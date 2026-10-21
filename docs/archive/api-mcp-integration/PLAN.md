# Kế hoạch: Trợ lý AI tra cứu (chỉ đọc) cho nhân viên

Trạng thái: **DỰ THẢO — chờ PO duyệt; chưa có code.** Mức HIGH (cổng truy cập mới + dữ liệu pháp chế). Phạm vi: [SCOPE](SCOPE.md). Hợp đồng kiểm chứng: [VERIFICATION](VERIFICATION.md). Hướng cổng báo giá tự động đã **tạm dừng**: [PAUSED_quote_gateway_PLAN](PAUSED_quote_gateway_PLAN.md).

## 1. Mục tiêu nghiệp vụ

Nhân viên hỏi AI bằng lời, AI tra dữ liệu thật của Search-tools rồi trả lời, ví dụ:

- "Sản phẩm này (mã/CAS/tên) có thuộc loại **cấm nhập** không? Có cần **giấy phép** không?"
- "**Còn tồn kho** không? Kho nào, hạn dùng bao lâu?"
- "**Sản phẩm tương đương** là gì?" (cùng CAS, khác hãng/quy cách)
- "Nó thuộc Phụ lục II hay III?"

Người quyết định cuối cùng vẫn là nhân viên. AI **không** sửa dữ liệu, **không** lập báo giá, **không** tự kết luận pháp lý.

## 2. Bốn công cụ đọc AI được dùng

| Công cụ | Trả lời câu hỏi | Dùng lại logic có sẵn |
| --- | --- | --- |
| `tra_san_pham` | Tìm theo mã/CAS/tên → danh sách ứng viên (tên, mã, CAS, hãng, quy cách) | tìm kiếm `/search`, gợi ý tìm |
| `kiem_tra_phap_che` | Trạng thái quản lý, chặn/cho xuất, ghi chú, nguồn (quy tắc tự động hay ngoại lệ thủ công) | `check_cas` + bộ phân giải `regulatory.py` |
| `xem_ton_kho` | Tồn theo kho, quy cách, hạn dùng (còn hạn/sắp hết/hết hạn) | `stock.py` |
| `tim_tuong_duong` | Các sản phẩm cùng CAS (hãng, quy cách khác) | tìm theo CAS |

Phiên bản đầu **không** có giá bán, giá nhập, báo giá: bớt hẳn rủi ro lộ giá.

## 3. Hai giới hạn dữ liệu phải nói thẳng với người dùng

1. **Hệ thống chưa lưu số nghị định / căn cứ pháp lý.** Dữ liệu chỉ có nhãn trạng thái (Cấm nhập, Cần giấy phép, Phụ lục II/III, Được bán...) và ghi chú tự do. Vì vậy câu "nghị định nào?" chỉ trả lời được **khi ghi chú của quy tắc có ghi**. AI **không được tự bịa** số nghị định từ kiến thức của nó. Nếu không có: trả lời "dữ liệu hiện chưa lưu căn cứ pháp lý", kèm nhãn và ghi chú đang có.
   - Muốn trả lời đầy đủ phải bổ sung dữ liệu "căn cứ pháp lý" vào danh mục quy tắc (thêm cột/import). Đó là **thay đổi dữ liệu riêng, chưa nằm trong đợt này**, cần PO quyết (xem mục 7).
2. **"Không có quy tắc" không có nghĩa "được phép nhập".** Hệ thống hiện coi sản phẩm không khớp quy tắc nào là "không cảnh báo" (cho xuất báo giá). AI phải nói: "**chưa có quy tắc nào trong hệ thống**; cần xác nhận với pháp chế", không được kết luận "được phép". Bản quy tắc còn phụ thuộc vào lần nhập danh mục gần nhất; câu trả lời nên ghi mốc cập nhật nếu hệ thống có.

Quy tắc trả lời bắt buộc cho AI (đặt trong chỉ dẫn của MCP): chỉ nói điều có trong kết quả công cụ; trích nguyên nhãn và ghi chú; nêu rõ khi thiếu dữ liệu; không tư vấn pháp lý; mọi kết luận nhạy cảm kèm "cần xác nhận với pháp chế".

## 4. Thiết kế (dùng lại nền đã phản biện ở bản báo giá)

- **Cổng máy chỉ đọc** dưới `/machine/v1/...`, tách khỏi cổng nhân viên. Xác thực bằng **thẻ** (chỉ lưu mã băm), gắn một **team** có sẵn nên AI chỉ thấy đúng phần team đó được thấy (CAS, tuân thủ, tồn kho...). Bắt buộc IP đã khai báo, kiểm tra lại team mỗi lời gọi, không "phiên giả", không `Set-Cookie`, không thừa hưởng quyền admin.
- **Quyền cần có ở team của thẻ:** quyền xem tuân thủ (`VIEW_COMPLIANCE`) và tìm theo CAS (`SEARCH_BY_CAS`) cho tra pháp chế; quyền xem tồn kho; thiếu quyền nào thì công cụ tương ứng bị từ chối y như nhân viên cùng team.
- **Chặt hơn cổng nhân viên:** body tối đa 256 KiB, tối đa 20 kết quả mỗi truy vấn, giới hạn tần suất và đồng thời, thời gian truy vấn tối đa, kết nối database **chỉ-đọc bắt buộc**. Mỗi lời gọi ghi nhật ký `service:<tên>`, không ghi thẻ.
- **Cấu hình thẻ bản đầu** trong biến môi trường (băm + team + IP + hạn dùng) → **không thêm bảng, không migration**. Thu hồi = xóa khỏi cấu hình + khởi động lại.
- **MCP**: một chương trình nhỏ riêng, chỉ có 4 công cụ đọc ở trên, gọi cổng máy bằng thẻ. Không đặt trên server production.
- **Cần PO chọn: ai dùng trợ lý và dùng ở đâu** (mục 7) — quyết định thẻ cấp cho ai.

### Rủi ro kỹ thuật chính

1. Logic tìm/pháp chế hiện đọc quyền từ phiên đăng nhập → bước **S0** quyết định cách dùng lại mà kết quả **giống hệt** nhân viên cùng team; không đạt thì dừng báo PO.
2. Miễn middleware đúng tiền tố `/machine/v1/`, không hơn (claim V2, V3, V8).
3. Chống giả IP phụ thuộc hạ tầng: kiểm chứng qua Nginx thật trên staging.
4. **Rủi ro mới của hướng này: câu trả lời sai về pháp lý.** Biện pháp: dữ liệu trả về mang cấu trúc (nhãn, chính sách chặn/cho, ghi chú, nguồn), quy tắc trả lời ở mục 3, và claim V10–V12 kiểm tra nhiều tình huống (ngoại lệ thủ công, không có quy tắc, nhiều quy tắc khớp).

## 5. Các bước

| Bước | Nội dung | Môi trường | Cổng duyệt |
| --- | --- | --- | --- |
| S0 | Spike: dùng lại logic tìm + pháp chế + tồn kho với danh tính máy, kết quả giống nhân viên | local | báo PO |
| P1 | Cổng máy + thẻ + IP + 4 điểm vào chỉ đọc + test | local → staging | verifier độc lập + Codex chỉ-đọc |
| P2 | MCP 4 công cụ + chỉ dẫn trả lời; thử với vài nhân viên trên staging | staging | PO thử hỏi thật (UAT) |
| P3 | Lên production | production | **PO nói "đồng ý bắt đầu"**, theo quy trình release nhẹ prepare/switch |

Không bước nào ghi dữ liệu vào database.

## 6. Hoàn tác

Ứng dụng chỉ đọc nên không có dữ liệu cần hoàn tác. Tắt ngay bằng cách xóa thẻ khỏi cấu hình và khởi động lại.

## 7. Cần PO quyết

1. **Ai dùng, dùng ở đâu?** Khuyến nghị: bắt đầu **thử với bạn và 2–3 nhân viên** qua Claude Desktop có gắn MCP (không cần làm giao diện), mỗi người một thẻ gắn đúng team của họ. Cách khác (ô chat trong web ứng dụng, bot Zalo) tốn công hơn nhiều, để sau.
2. **Căn cứ pháp lý:** bạn có danh sách nghị định tương ứng với từng nhãn/quy tắc không? Khuyến nghị: **chưa làm trong đợt này**; chạy bản đầu trả lời nhãn + ghi chú, và ghi rõ khi chưa có căn cứ. Nếu sau thấy cần, mở phase riêng thêm trường "căn cứ pháp lý" vào danh mục quy tắc.
3. **Dữ liệu ra ngoài:** khi nhân viên hỏi, mã/CAS/tên sản phẩm và kết quả tra cứu đi qua dịch vụ AI (Anthropic). Không có giá, không có thông tin khách hàng. Bạn có chấp nhận không? Khuyến nghị: chấp nhận cho bản thử, vì không chứa dữ liệu giá hay khách.
