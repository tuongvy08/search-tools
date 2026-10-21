# Kế hoạch: n8n/AI lập báo giá nháp từ Google Sheets

Trạng thái: **DỰ THẢO — chờ PO duyệt; chưa có code.** Mức HIGH (xác thực, cổng truy cập mới). Phạm vi: [SCOPE](SCOPE.md). Hợp đồng kiểm chứng: [VERIFICATION](PAUSED_quote_gateway_VERIFICATION.md). Đã qua một lượt phản biện độc lập bằng Codex (mục 3).

## 1. Luồng nghiệp vụ

1. Khách/nhân viên ghi yêu cầu báo giá vào một **Google Sheet** (mỗi dòng: mã hàng hoặc CAS, tên, số lượng).
2. **n8n** đọc các dòng mới (n8n tự đăng nhập Google; ứng dụng Search-tools **không** cần quyền vào Google).
3. n8n gửi các dòng sang ứng dụng qua **cổng máy** (mục 3). Ứng dụng trả về: sản phẩm khớp, tồn kho, giá bán — đúng với những gì team được cấp quyền thấy.
4. n8n ghi kết quả vào **tab "Báo giá nháp"** trong Google Sheet; dòng không khớp được đánh dấu **"cần người xử lý"**, không tự bịa mã hay giá.
5. Nhân viên có quyền báo giá xem nháp, rồi lập báo giá chính thức bằng chức năng báo giá **hiện có** của ứng dụng (đăng nhập như bình thường). Nháp không tự gửi cho khách.

## 2. Một điểm tôi đề nghị đổi so với thỏa thuận trước — cần PO quyết

Trước đây ghi "nháp lưu trong ứng dụng, tự xóa sau 30 ngày". Sau khi đọc code, tôi **khuyên lưu nháp trong Google Sheet** (như luồng ở mục 1):

| | A. Nháp trong Google Sheet (khuyến nghị) | B. Nháp trong ứng dụng |
| --- | --- | --- |
| Thay đổi database | **Không** | Có: thêm bảng mới + migration, kể cả trên production |
| Ứng dụng có ghi dữ liệu không | **Không, hoàn toàn chỉ đọc** | Có |
| Nếu có lỗi | Chỉ sai một tab nháp, xóa là xong | Dữ liệu sai nằm trong database thật |
| Nhân viên làm việc ở đâu | Quen thuộc: ngay trong Sheet | Phải mở thêm màn hình mới (chưa có) |
| Giá nằm ở đâu | Một phần giá bán nằm trong Google Sheet của công ty | Giá chỉ nằm trong ứng dụng |
| Tự xóa sau 30 ngày | Làm ở n8n/Sheet, không thuộc ứng dụng | Phải viết thêm |

Lưu ý: ở phương án A, chỉ **giá bán** theo quyền của team cấu hình cho n8n được gửi ra; **giá nhập/giá vốn không bao giờ** nằm trong phản hồi cho máy. Phương án B vẫn có thể làm sau (bước 3) khi A đã chạy ổn.

**Khuyến nghị: chọn A.** Bạn trả lời "đồng ý" nghĩa là chọn A và chấp nhận một phần giá bán nằm trong Google Sheet của công ty.

## 3. Thiết kế (tóm tắt kỹ thuật)

- **Cổng máy riêng** đặt dưới `/machine/v1/...`, tách hoàn toàn khỏi cổng nhân viên hiện có (đăng nhập Google + danh sách IP văn phòng). Cổng cũ không đổi hành vi.
- **Xác thực bằng thẻ (token)** gửi qua header `Authorization: Bearer`. Ứng dụng chỉ lưu **mã băm** của thẻ; thẻ thật chỉ n8n giữ.
- **Danh tính máy = một team riêng ("Máy – n8n")** do admin cấu hình quyền như mọi team. Bắt buộc team này có `QUICK_QUOTE` (kéo theo `VIEW_PRICE`); thiếu thì thẻ bị từ chối khi nạp, và nhân viên thiếu quyền này vốn cũng bị 403. Quyền và team được **kiểm tra lại ở mỗi lời gọi** (team bị lưu trữ/xóa → từ chối ngay; lỗi đọc database → từ chối 503, không bao giờ mặc định cho qua).
- **Không dùng "phiên giả".** Cổng máy không tạo, không sửa phiên đăng nhập thật, không bao giờ trả `Set-Cookie`, không thừa hưởng `is_admin`. Quyền được truyền tường minh vào logic khớp (S0 chọn cách: tách hàm dùng chung hoặc truyền "tập quyền" vào). Phản hồi qua bộ lọc `redact` hiện có, **gồm cả danh sách ứng viên**, và còn lọc thêm theo danh sách trường cho phép riêng của cổng máy.
- **Chỉ nhận từ IP đã khai báo** của máy chạy n8n (bắt buộc, không có thẻ "mở mọi nơi"). Vì ứng dụng chỉ tin một lớp proxy, phải **chặn truy cập thẳng vào tiến trình Flask/Gunicorn** (đang chỉ lắng nghe loopback sau Nginx) và kiểm chứng trên staging qua Nginx thật.
- **Cấu hình thẻ (bản đầu)** trong biến môi trường (băm + team + danh sách IP + hạn dùng), **không thêm bảng database** → không migration. Thu hồi = xóa khỏi cấu hình và khởi động lại dịch vụ. Giao diện quản lý thẻ để sau.
- **Điểm vào:** `POST /machine/v1/quote/match` (khớp mã/CAS, bọc logic của `/api/quote-assistant/match`) và `GET /machine/v1/health`. Tồn kho **không** nằm trong kết quả khớp hiện có; lấy bằng hàm tồn kho riêng, và chỉ đưa vào bản đầu nếu S0 chứng minh dùng lại an toàn (nếu không, bỏ tồn kho khỏi bản đầu và ghi rõ).
- **Giới hạn bảo vệ dịch vụ nhân viên** (chặt hơn cổng nhân viên, vì giới hạn chung là hơn 128 MiB): body tối đa 256 KiB và kiểm tra **trước khi** đọc JSON, tối đa 200 dòng, tối đa 20 ứng viên mỗi dòng, giới hạn số lời gọi đồng thời và tần suất, thời gian chạy truy vấn tối đa (`statement_timeout`), kết nối database ở chế độ **chỉ-đọc bắt buộc**.
- **Số lượng do n8n xử lý**, không phải ứng dụng: ứng dụng hiện không đọc số lượng. n8n kiểm tra số lượng (âm, chữ, trống → "cần người xử lý"), giữ nguyên số lượng khi chia lô/chạy lại.
- **Trạng thái khớp:** n8n chỉ tự điền khi kết quả là **một** sản phẩm đã được chọn rõ ràng. Các trạng thái cần xem xét (`REVIEW`), bị chặn, thiếu giá hợp lệ hoặc nhiều ứng viên → "cần người xử lý", không lấy ứng viên đầu tiên.
- **Nhật ký:** mỗi lần gọi ghi `service:<tên>`, điểm vào, số dòng, kết quả, IP — **không ghi thẻ**, không ghi giá. Nhật ký ở log dịch vụ trong bản đầu. Cổng máy không được kích hoạt các đường ghi nhật ký đăng nhập hiện có.
- **MCP** (bước sau): một chương trình nhỏ **riêng**, chạy ở máy dùng AI (không đặt trên server production), chỉ gọi cổng máy bằng thẻ trên và chỉ có công cụ **đọc**.

### Rủi ro kỹ thuật chính và cách xử lý

1. **Logic khớp hiện nay đọc quyền từ phiên đăng nhập, và danh sách quyền theo endpoint chỉ bảo vệ các route đã đăng ký.** Một endpoint máy mới không tự được bảo vệ. Bước S0 quyết định cách dùng lại và phải chứng minh: kết quả **giống hệt** nhân viên cùng team (V5), quyền chức năng được kiểm tra tường minh, không rò trường bị cấm kể cả trong danh sách ứng viên, và không phát sinh cookie. Không đạt thì dừng, quay lại xin PO.
2. **Middleware hiện có chặn mọi thứ ngoài IP văn phòng / chưa đăng nhập.** Cổng máy phải được miễn **đúng** tiền tố `/machine/v1/` và tự kiểm tra thẻ + IP nghiêm hơn. Sai chỗ này là mở cửa nhầm, nên có V2, V3, V8. Cổng máy cũng không được làm kích hoạt đường ghi nhật ký của phiên cũ.
3. **Chống giả IP phụ thuộc hạ tầng, không chỉ code.** Test trong Flask không chứng minh được; cần kiểm chứng qua Nginx thật trên staging, và kiểm tra trước khi lên production rằng không có đường vào thẳng tiến trình ứng dụng.
4. **Một thẻ bị lộ vẫn có thể làm nặng dịch vụ.** Biện pháp là các giới hạn ở mục 3 (kích thước, ứng viên, đồng thời, thời gian chạy), vì giới hạn tần suất theo từng tiến trình chỉ là lớp phụ.
5. **Yêu cầu chỉ có tên (không mã, không CAS)** hiện chưa khớp được. Bản đầu đánh dấu "cần người xử lý"; tìm theo tên là việc riêng.

### Phản biện độc lập đã nhận (Codex chỉ-đọc, 2026-10-21)

Codex đọc kế hoạch + code và nêu 11 điểm; tôi đối chiếu code và đưa vào bản này: bọc hàm khớp không tự kế thừa kiểm tra quyền; "phiên giả" có nguy cơ thành cookie đăng nhập; `ProxyFix` không đủ để tin IP nếu truy cập được thẳng; kiểm tra lại team mỗi lời gọi; giới hạn chung hơn 128 MiB là quá rộng; `QUICK_QUOTE` kéo theo `VIEW_PRICE` (claim V5 cũ sai); tồn kho không có trong kết quả khớp; `MATCHED`/`REVIEW` chưa đồng nghĩa "đã chọn hàng"; số lượng không do ứng dụng xử lý; V4 chưa chứng minh "không ghi"; V3 phải so với phản hồi ẩn danh vì có route công khai chủ đích. Nhật ký đầy đủ nằm ở bản VERIFICATION v1 đã sửa.

## 4. Các bước thực hiện

| Bước | Nội dung | Môi trường | Cổng duyệt |
| --- | --- | --- | --- |
| S0 | Spike: chứng minh dùng lại được logic khớp với danh tính máy, kết quả giống nhân viên | local | báo kết quả cho PO |
| P1 | Cổng máy chỉ đọc + thẻ + IP + nhật ký + test | local → staging | verifier độc lập + Codex chỉ-đọc |
| P2 | Workflow n8n đọc Google Sheet thử, ghi tab nháp | staging, Sheet thử | PO thử với Sheet thật (UAT) |
| P3 | MCP chỉ đọc cho AI | máy PO / staging | verifier + UAT |
| P4 | Lên production | production | **PO nói "đồng ý bắt đầu"**, theo quy trình release nhẹ prepare/switch như các lần trước |

Staging được làm tự do theo quyết định 2026-10-02. Không bước nào ghi dữ liệu vào database.

## 5. Hoàn tác

Không có dữ liệu để hoàn tác (ứng dụng chỉ đọc). Cổng máy tắt ngay bằng cách xóa thẻ khỏi cấu hình và khởi động lại; nếu phải gỡ cả code, bản trước nằm sẵn ở release cũ theo quy trình hiện tại.

## 6. Việc chưa làm trong phase này

Bước 3 (máy ghi trực tiếp), bảng nháp/migration, giao diện quản lý thẻ, tìm theo tên, tải lớn, mọi thao tác production.

## 7. Cần PO

1. Chọn phương án nháp: **A (khuyến nghị)** hay B.
2. Cho phép tôi tạo một Google Sheet **thử** (dữ liệu giả) để chạy P2 trên staging — hay bạn tự tạo và chia sẻ cho n8n? Khuyến nghị: dùng Sheet thử dữ liệu giả.
3. Duyệt kế hoạch này (và VERIFICATION) để bắt đầu S0/P1 trên local.
