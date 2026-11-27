# Hướng dẫn dùng API Search-tools để lập báo giá (thẻ NỘI BỘ có giá)

Dành cho phiên/nhóm xây hệ thống báo giá tự động (Telegram bot, quét email, n8n, AI Agent). Tài liệu này là **hợp đồng** giữa hệ thống báo giá và Search-tools: bên báo giá lo *luồng* (nhận yêu cầu, đọc ảnh/Excel/PDF, tạo file báo giá); Search-tools chỉ lo *dữ liệu* (hàng hóa, giá, pháp chế, tồn kho). Trạng thái: **production đã chạy từ 2026-11-22** (PR #42).

Tài liệu liên quan: [HUONG_DAN_KET_NOI](HUONG_DAN_KET_NOI.md) (thẻ khách, MCP), [INTERNAL_PRICE](INTERNAL_PRICE.md) (thiết kế), [OPERATIONS](OPERATIONS.md) (vận hành, hoàn tác), mẫu workflow chạy được: [integrations/n8n/bao-gia-tu-dong](../../integrations/n8n/bao-gia-tu-dong/README.md).

## 1. Nguyên tắc vàng (đọc trước)

1. **AI chỉ nhận diện sản phẩm; giá do node cố định điền.** AI Agent đọc yêu cầu (text/ảnh/Excel/PDF) và rút ra *mã hàng / CAS / tên / hãng / quy cách / số lượng*. Sau đó một node HTTP Request gọi API và một node Code điền giá. **Không để AI tự tính hoặc tự nhớ giá**; không đưa trường `price` vào ngữ cảnh của AI nếu không cần (giá không rời khỏi hệ thống của công ty).
2. **Chỉ tự điền khi xác định được đúng MỘT sản phẩm.** Còn lại ghi "cần người xử lý" kèm lý do và tối đa 5 ứng viên. Không bao giờ lấy ứng viên đầu tiên.
3. **Thẻ nội bộ chỉ dùng cho nhân viên/hệ thống nội bộ.** Không đưa kết quả có giá cho khách; không dùng thẻ này cho chatbot khách (chatbot khách dùng thẻ khách, không bao giờ có giá).
4. **Pháp chế chặn thì không tính tiền.** `compliance.blocked = true` (ví dụ Cấm nhập) → không điền thành tiền. `NO_RULE` nghĩa là *chưa có quy tắc*, **không** phải được phép nhập.
5. **Không ghi thẻ vào log, ô Code, tin nhắn, prompt của AI.** Thẻ chỉ nằm trong n8n Credentials.

## 2. Kết nối

| Mục | Giá trị |
| --- | --- |
| Địa chỉ gốc production | `https://search.standards.com.vn/machine/v1` |
| Địa chỉ gốc staging (thử) | `https://staging.search.standards.vn/machine/v1` (dữ liệu/tỷ giá/quy tắc khác production) |
| Xác thực | Header `Authorization: Bearer <thẻ nội bộ>` |
| Credential n8n | `Search_Tools_Machine_API_Noi_bo` (loại Header Auth) — đã tạo, đang trỏ thẻ production |
| Thẻ production | `n8n-bao-gia-prod`, hạn **2027-02-20**, tối đa 300 lần/phút |
| IP được phép | `172.18.0.3` (container n8n) và `139.180.215.28` (máy chủ). Gọi từ nơi khác → **403** |
| Mô tả máy đọc được | `GET /openapi.json` (không cần thẻ; không chứa giá) |

- n8n chạy cùng máy chủ nên API thấy nó là IP nội bộ docker. **Nếu container n8n khởi động lại và đổi IP → 403**: báo bên API cấp lại thẻ với IP mới (thu hồi + cấp mới mất vài phút).
- Code node của n8n **không có mạng**: việc gọi API phải đặt ở node **HTTP Request** (bật "Never Error" + "Full Response" để đọc được mã lỗi, "Batching" 1 yêu cầu/**1 giây** — Nginx chặn 429 khi gọi dày hơn; để "Response Format" = autodetect vì 429 của Nginx là HTML).
- Đổi thẻ khi gần hết hạn (nhắc trước 2 tuần). Thẻ chỉ hiện một lần lúc cấp.

## 3. Các điểm vào (chỉ đọc)

**Nhiều dòng có mã/CAS → dùng `POST /products/batch`** (tới 500 dòng/lần, khớp chính xác, đúng thứ tự, đánh dấu không tìm thấy/bị cắt). Chi tiết cách gọi, giới hạn, mã lỗi: [BATCH](BATCH.md). Hướng dẫn cho người dựng workflow kèm node n8n mẫu: `n8n-automation/docs/search-tools-api.md` mục 4.0 và 13.1. Dòng chỉ có tên vẫn dùng `/products` bên dưới.

### 3.1 `/products` — tìm hàng hóa kèm giá, pháp chế, tồn kho

Tham số: `q` (3–100 ký tự; mã, CAS hoặc tên), `limit` (mặc định 20, tối đa 50).

Thứ tự kết quả: trùng mã → trùng CAS → trùng tên → bắt đầu bằng → chứa; trong cùng mức xen đều các hãng. `more = true` nghĩa là còn kết quả chưa trả.

Ví dụ (kết quả thật trên production 2026-11-22, `q=T0050000&limit=1`, chuỗi pháp chế rút gọn):

```json
{"query":"T0050000","count":1,"more":true,"results":[
 {"name":"TERBUTALINE SULFATE CRS","code":"T0050000","cas":"23031-32-5","brand":"EDQM","size":"100mg","note":"A1A",
  "price":{"original_price":"90","ship_factor":"1.05","currency":"EUR","rate_vnd":31000.0,"unit_price_vnd":2930000,"price_status":"OK"},
  "compliance":{"state":"NO_RULE","message":"Chưa có quy tắc nào trong hệ thống cho mục này. Điều đó KHÔNG có nghĩa là được phép nhập; …","disclaimer":"…"},
  "stock":{"status":"IN_STOCK","expiry_warning":false,"quantity":1,"pack_confirmed":true,
           "lots":[{"brand":"EDQM","size":"100mg","quantity":1,"expiry_date":null,"expiry_state":"missing","available":true,"stock_price_vnd":2820000.0,"stock_note":""}],
           "lots_total":1,"lots_truncated":false,"same_cas":[],"same_cas_more":false}}]}
```

Lưu ý ví dụ: `more: true` vì còn mã khác *chứa* từ khóa (ví dụ `EPT0050000`) — luôn lọc mã trùng chính xác (mục 5). Có thể có `expiry_date: null` (lô chưa nhập hạn dùng).

### 3.2 `/compliance?cas=64-17-5` — pháp chế theo CAS

Trả `compliance`: `state` (`RULE`, `NO_RULE`, `DATA_ERROR`, `HIDDEN`), `label` (ví dụ "CẤM NHẬP"), `blocked` (true = chặn báo giá), `export_policy`, `regulatory_note`. Hệ thống **chưa lưu số nghị định/căn cứ pháp lý**: không bịa; luôn nhắc "cần pháp chế xác nhận".

### 3.3 `/stock?code=…` hoặc `/stock?cas=…` — tồn kho chi tiết

Đúng một trong hai tham số. Trả `items` (nhóm theo hãng + quy cách; theo CAS thì theo mã + hãng + quy cách), mỗi nhóm có tổng `quantity`, `status`, `lots` (từng lô), tối đa 30 nhóm; `more` báo còn nữa.

## 4. Cách đọc từng trường

| Trường | Ý nghĩa và cách dùng |
| --- | --- |
| `price.unit_price_vnd` | Đơn giá VND = `round(giá gốc × hệ số ship × tỷ giá, -3)` — đúng công thức ứng dụng nhân viên. Đây là số duy nhất được điền vào báo giá. **Chưa cộng lợi nhuận** (quyết định của PO) |
| `price.price_status` | `OK` dùng được. `NO_PRICE_DATA` = chưa có giá (giá liên hệ) → để người xử lý. `INVALID_PRICE_DATA`, `INVALID_RATE_DATA`, hoặc mã lỗi tỷ giá → **không quy đổi được**, đừng tự tính. Không bao giờ coi `0`/`null` là giá |
| `price.original_price`, `ship_factor`, `currency`, `rate_vnd` | Ghi kèm vào báo giá nháp để người duyệt đối chiếu |
| `note` | Ghi chú sản phẩm (ví dụ "Stock expected") |
| `compliance` | Xem 3.2. Với `/products`, pháp chế tính theo CAS của sản phẩm |
| `stock.status` | `IN_STOCK` / `OUT_OF_STOCK` |
| `stock.quantity` | Tổng số lượng còn dùng được (chính xác, theo mã) |
| `stock.pack_confirmed` | `false` = có lô cùng mã nhưng chưa chắc đúng hãng/quy cách → nói rõ "cần xác nhận" |
| `stock.expiry_warning`, `lots[].expiry_date` | Hàng sắp/đã hết hạn; báo trong ghi chú |
| `stock.lots[].stock_price_vnd` | Giá ghi trên dòng tồn kho (có thể khác `unit_price_vnd`). **Không dùng làm đơn giá báo giá**; chỉ để người duyệt tham khảo. Đơn giá báo là `price.unit_price_vnd` |
| `stock.lots` | Tối đa 200 lô/sản phẩm; `lots_total`, `lots_truncated` cho biết có bị cắt (tổng tồn vẫn đúng) |
| `stock.same_cas` | Hàng **cùng CAS khác hãng/quy cách** — chỉ để *gợi ý thay thế*, không phải đúng mã khách hỏi |

## 5. Quy trình tra cứu khuyến nghị (đã chạy thật 28 ca)

Với mỗi dòng yêu cầu đã chuẩn hóa (mã / CAS / tên / hãng / quy cách / số lượng):

1. Chọn khóa tra: **mã** nếu có, nếu không thì **CAS** (đúng định dạng `64-17-5`), nếu không thì **tên**. Dưới 3 ký tự hoặc quá dài → không gọi API, để người xử lý.
2. Gọi `/products?q=<khóa>&limit=50`.
3. **Lọc chính xác phía n8n**: mã → `code` trùng (không phân biệt hoa thường); CAS → `cas` trùng; tên → chỉ coi là khớp khi `name` trùng chính xác. API trả cả các mã *chứa* từ khóa nên bước này bắt buộc.
4. Có hãng/quy cách trong yêu cầu → lọc tiếp; nếu lọc ra rỗng thì **không tự điền** (ghi "hãng/quy cách không khớp").
5. Còn đúng **1** ứng viên (và với tên: khớp chính xác) → tự điền mã, tên, hãng, quy cách, `unit_price_vnd`, tồn kho; ngược lại → "cần người xử lý" + lý do + tối đa 5 ứng viên (mã | hãng | quy cách | giá).
6. Số lượng thiếu/sai (`-3`, `abc`, trống) → vẫn tra nhưng **không tính thành tiền**, ghi lý do.
7. `compliance.blocked = true` → không tính thành tiền, ghi nhãn pháp chế. `compliance.state = DATA_ERROR` → cần kiểm tra.
8. Đủ hàng? `quantity ≥ số lượng yêu cầu` → ĐỦ; ngược lại THIẾU (ghi số còn lại) / HẾT HÀNG.
9. Thành tiền = `số lượng × unit_price_vnd` chỉ khi không có lý do nào ở trên.

Mã triển khai sẵn: `integrations/n8n/bao-gia-tu-dong/{chuan_hoa_yeu_cau.js, lap_bao_gia_nhap.js}` — dùng lại thay vì viết mới.

## 6. Lỗi thường gặp

| Mã | Nghĩa | Xử lý |
| --- | --- | --- |
| 400 | Tham số sai (CAS sai định dạng, `q` quá ngắn...) | Sửa dữ liệu đầu vào, không thử lại nguyên xi |
| 401 | Thiếu/sai/hết hạn thẻ | Kiểm tra credential, hạn thẻ (2027-02-20) |
| 403 | IP không được phép hoặc thẻ bị chặn | IP n8n đã đổi → báo bên API cấp lại thẻ |
| 429 | Gọi quá nhiều (300/phút/thẻ) | Giãn ra, tôn trọng header `Retry-After` |
| 503 `service_busy` | Truy vấn quá 3 giây (thường là từ khóa tên 1 từ quá phổ biến như "methyl") | Thử lại sau 5 giây một lần; vẫn lỗi → để người xử lý, đề nghị người gõ thêm mã/CAS |
| Lỗi mạng | Không tới được | Ghi "không gọi được API", không bỏ mất dòng |

## 7. Giới hạn đã biết

- Tìm theo **tên một từ phổ biến** có thể quá chậm hoặc quá nhiều ứng viên → luôn ưu tiên mã/CAS; yêu cầu chỉ có tên thì mặc định để người chọn.
- Tồn kho khớp theo **mã**; hãng/quy cách trong kho có thể lệch tên so với danh mục (xem `pack_confirmed`).
- Chưa có số nghị định/căn cứ pháp lý; chưa có giá bán (chưa cộng lợi nhuận).
- Sản phẩm chưa có giá (giá liên hệ), hoặc hãng thiếu tỷ giá → không có `unit_price_vnd`.
- API chỉ **đọc**; không cập nhật được sản phẩm, tồn kho, giá.
- Dữ liệu có giá nằm trong luồng n8n: nếu đưa kết quả vào AI của bên thứ ba thì giá rời khỏi công ty — nên tránh (nguyên tắc 1).

## 8. Đoạn hướng dẫn mẫu cho AI Agent (bước nhận diện yêu cầu, KHÔNG gọi API giá)

```text
Bạn đọc yêu cầu báo giá (văn bản, ảnh, Excel, PDF) từ nhân viên kinh doanh và chỉ TRÍCH XUẤT danh sách dòng hàng.
Mỗi dòng gồm: ma_hang, cas, ten_san_pham, hang, quy_cach, so_luong (số). Trường nào không có thì để trống, tuyệt đối không đoán hoặc bịa mã/CAS.
CAS phải có dạng 64-17-5; sai định dạng thì giữ nguyên chuỗi gốc và đừng sửa.
Không nêu giá, không tính tiền, không kết luận pháp chế. Dòng nào không chắc, đưa nguyên văn vào "ghi_chu_goc".
Đầu ra: JSON mảng các dòng.
```

(Bước tra giá/pháp chế/tồn kho do node HTTP Request + Code thực hiện theo mục 5.)

## 9. Danh sách kiểm trước khi bật tự động với email/Zalo

- [ ] Chạy thử bằng Google Sheet/Telegram nội bộ với 10–20 yêu cầu thật; người duyệt đối chiếu giá với ô tìm kiếm của ứng dụng.
- [ ] Mọi dòng "cần người xử lý" có lý do rõ; không dòng nào bị mất.
- [ ] Quét email: chỉ đọc hộp thư đã được nhân viên đồng ý, lọc đúng email báo giá, **không gửi báo giá ra ngoài tự động** — luôn qua người duyệt trước (báo giá sai là sai cam kết với khách).
- [ ] Kết quả có giá chỉ gửi lại cho nhân viên (Telegram/email nội bộ), không gửi thẳng cho khách.
- [ ] Credential `Search_Tools_Machine_API_Noi_bo` đã gắn; thẻ không xuất hiện trong log/ô Code/prompt.
- [ ] Có người nhận cảnh báo khi API trả 401/403/503 liên tục (thẻ hết hạn, IP đổi).
- [ ] Nhớ ngày hết hạn thẻ: **2027-02-20**.
