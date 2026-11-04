# Triển khai và kiểm chứng staging — 2026-11-04

PO đã nghiệm thu local, cho commit/push/PR và triển khai/kiểm chứng staging.
PR: https://github.com/tuongvy08/search-tools/pull/41 (OPEN, chưa merge).
Trạng thái: **kiểm chứng kỹ thuật và smoke giao diện staging PASS; sẵn sàng PO nghiệm thu staging**.
Production chưa thay đổi; không chạy công cụ dọn nhãn trên staging/production.

## Phiên bản và phạm vi

- Baseline sạch: `f77cffb863018a568664c097a816f1b0ccd28f4e`.
- Bản code đã triển khai: `e33b0a22463366d5e19842307f31d80696bb1ecc`.
- Main lúc tạo PR: `17ef914`, cùng tree với baseline local đã kiểm trước đó
  (`79fca9a`); chỉ có ba merge commit tài liệu mới, không đổi nội dung code.
- Chỉ chuyển checkout staging và restart web/worker, không migration,
  cài dependency, đổi service/Nginx/DNS hay sửa dữ liệu giữ lại. Firewall được xử lý riêng sau deploy theo PO yêu cầu (bên dưới).
- `opencode.json` ngoài phạm vi, không commit.

## Gate và chuyển bản

Đã dùng preflight chỉ đọc hiện hữu trước và sau chuyển bản:
web/worker active, cùng checkout, cùng database staging, PostgreSQL 16.15;
dấu vết schema 030–033 đều có; ba hàng đợi product/regulatory/stock đều
queued=0/running=0. Checkout sạch, khoảng 56,2 GiB trống.

Bundle tạo từ baseline staging tới nhánh PR, verify cả local và remote;
fetch từ bundle không dùng credential GitHub trên server. Hash SHA256:

- Bundle `search-tools-cas-e33b0a2.bundle`:
  `dd32f81e1135c53bd99c5958d9ed0abc6591668ea2f817570273a4d630c4cdf2`.
- Script chuyển bản staging (artifact tạm, không phải pipeline tái sử dụng):
  `c3d40bc108a9ce88ad8638ad85bb25ef421fb7893e748ebd40c1eb33a56de87b`.
- Script smoke staging:
  `9a012e9e85cc214ef4fdf5f1d2269ee2b157c905fa468160435e88fae8b45fd3`.

Hai script được Claude review riêng chỉ đọc trước thực thi: không finding
chặn. Gate gồm đúng baseline/FETCH_HEAD/hash, tree sạch, đúng hai unit và
thư mục staging, không có unit production active. Trình tự dừng worker →
web → checkout detached SHA mới → start web → worker. Có nhánh phục hồi
code baseline nếu lệnh/health lỗi; nhánh này không phát sinh khi chạy thật.
Không coi review tĩnh là bằng chứng đã thử fault injection/rollback thật.

Output thực tế: `STAGING CODE SWITCH PASS: e33b0a2, web/worker active, login 200`.
Hậu kiểm xác nhận cả hai process ở đúng checkout/SHA, cùng database;
schema giữ nguyên, hàng đợi vẫn rỗng, checkout sạch.

## Smoke trên môi trường thật

Chạy Python 3.12.3 trong `.venv` staging; kết nối lấy trong bộ nhớ từ process
đang chạy, không đọc `.env` hoặc in secret. Smoke kiểm đúng database staging,
giới hạn truy vấn 15 giây và chờ khóa 3 giây.

- 7 trường hợp resolver bằng bảng TEMP: tên/mã giống nhưng CAS khác hoặc
  trống → không cảnh báo; CAS đúng (kể cả khoảng trắng ngoài) → BLOCK đúng;
  hãng bật ưu tiên + gán riêng ALLOW → manual ALLOW; hãng tắt → CAS BLOCK;
  liên kết riêng hỏng ở hãng ưu tiên → error/BLOCK.
- Rollback và kiểm bảng TEMP đã mất; không INSERT/UPDATE/DELETE bảng giữ lại.
- CAS fullwidth/Ả Rập/trộn/dấu gạch đặc biệt bị từ chối, có hướng dẫn `0-9`.
- Hai nhãn CAS sai bị validation từ chối.
- Workbook giả: CAS đúng được đọc; CAS Unicode, header Tên hoặc Code bị từ chối.
- Kết quả cuối: `STAGING FUNCTIONAL SMOKE PASS; no permanent database writes`.

HTTP loopback `/login` = 200. Nginx HTTPS kiểm qua hostname chứng chỉ thật
với loopback = 200 (không dùng bỏ qua kiểm chứng TLS).
Static `regulatory_manual.js` đang phục vụ có SHA256 trùng file đã commit:
`e2c9ae67fff5f9cddfd81062a2b7ad1bbb2360754bacb70830a59f1685fff161`.

## Khôi phục HTTPS staging theo PO yêu cầu

PO khởi động lại server nhưng chưa truy cập được; đã yêu cầu agent dùng `ssh staging` tìm và sửa. Sau reboot, web/worker/Nginx đều active; backend và Nginx TLS loopback 200. DNS A đúng địa chỉ staging, không có AAAA. UFW active/default deny incoming, HTTPS chỉ cho một IPv4 quản trị cũ; nguồn kết nối SSH hiện tại khác. Tunnel cũ ngắt khi reboot.

Chỉ bổ sung đúng IPv4 quản trị hiện tại (địa chỉ host /32) vào TCP 443. Giữ nguyên quyền cũ, SSH/HTTP, default deny và xác thực ứng dụng; không restart, không sửa DNS/Nginx/database/production. Không công bố địa chỉ mạng quản trị trong hồ sơ public. Script khóa theo code staging `e33b0a2`, yêu cầu đúng SSH peer, unit staging active, unit production inactive, checkout sạch và UFW active/default deny. Không tự dùng lại script nếu điều kiện đã đổi.

- Hợp đồng lập trước mutation: [VERIFICATION](../../specs/staging-https-access/VERIFICATION.md).
- Claude context sạch: 19 case mock (CIDR/rỗng/sai peer/private/IPv6/sai SHA/tree bẩn/unit sai/UFW sai…), không finding chặn; [báo cáo gốc](../../specs/staging-https-access/VERIFICATION_RESULT.md).
- Script [allow-current-ip.sh](../../specs/staging-https-access/allow-current-ip.sh), SHA256 `d05ab44af23073bd726ad21c8cec1e4dabc29c7f58e396149d48de41ffa231bb`; hash bản review và bản chạy khớp.
- Dry-run trên server thật qua `ssh staging 'bash -s -- <observed-peer> dry-run'` PASS, đúng một nguồn/cổng; apply chỉ thêm một rule. Trạng thái trước/sau 5 → 6 rule; các rule cũ và default giữ nguyên.
- Hậu kiểm từ Mac: `https://staging.search.standards.vn/login` = **200**, `ssl_verify_result=0` (không `-k`); `/admin/regulatory/rules` ẩn danh = **302** về HTTPS login.
- Web/worker/Nginx đều active, HEAD vẫn `e33b0a2`, checkout sạch. Browser URL HTTPS thật hiện form đăng nhập.
- Hoàn tác nếu cần: đối chiếu rule rồi chỉ `ufw delete allow from <same-observed-peer> to any port 443 proto tcp`; chưa cần thực hiện. IP mạng động đổi lần nữa có thể cần cập nhật /32; không mở HTTPS mọi nguồn để tránh việc đó.

V1/V2: review/mock + hậu kiểm thật đạt. [Lượt review độc lập riêng](../../specs/staging-https-access/LIVE_REVIEW.md) không finding chặn và tự chạy curl xác nhận login 200/TLS=0/admin ẩn danh 302; không kiểm SSH/firewall/service/browser. Các phần đó do agent chính kiểm trực tiếp như evidence trên, không gán cho verifier. Đây là khôi phục kết nối, không thay thế UAT sau đăng nhập.

## Kiểm thử giao diện sau đăng nhập — 2026-11-04

PO đã đăng nhập HTTPS staging. Agent kiểm bằng tab riêng cùng phiên, không nhập credential, không thay cấu hình quyền. Đây là smoke browser do agent thực hiện, không tự coi là PO đã nghiệm thu staging.

- Menu trên danh sách/thêm/sửa đồng bộ: Quản trị active, mục Quy tắc quản lý có `aria-current=page`, biểu tượng Font Awesome có glyph thật. Mở menu rồi Escape đóng đúng. Đã kiểm ảnh form desktop và menu thu gọn trong tab cạnh chat.
- Form thêm chỉ có loại CAS. Sáu lựa chọn tình trạng staging không có nhãn CAS. **Staging vốn không có hai nhãn sai của production**, nên quan sát này không chứng minh đã dọn dữ liệu production; khả năng loại nhãn sai khỏi lựa chọn đã được kiểm bằng fixture local.
- Nhập `６２９-１４-１` → báo “CAS chỉ dùng chữ số thông thường 0-9 và dấu -. Hãy nhập lại, ví dụ: 64-17-5.” Không có nút xác nhận lưu.
- Đổi thành `629-14-1` → preview hiện trước/sau đúng giá trị/tình trạng, có nút xác nhận. **Không bấm lưu**; rời form rồi tìm tất cả quy tắc CAS này vẫn là 0 hàng.
- Quy tắc tên Diethyl ether `#6010` vẫn tồn tại, hiển thị “Không dùng đối chiếu — quy tắc tên/mã cũ”. Chi tiết chỉ có thao tác “Ngừng quy tắc cũ”; loại/giá trị/ghi chú readonly, tình trạng disabled. Không ngừng hoặc sửa hàng này.
- Search `629-14-1` trả 15 sản phẩm, đều đúng CAS; gồm 5 hàng CATO. Cả 15 cột tình trạng và ghi chú quản lý trống: không còn cảnh báo lấy từ tên Diethyl ether. Đây là hành vi ứng dụng theo dữ liệu staging, không phải kết luận pháp lý hóa chất.
- Đối chứng `60-29-7`: 61 hàng đúng CAS có “Kiểm soát đặc biệt nhóm 1”, khớp quy tắc CAS đang áp dụng `#5825`. Search có 10 hàng CAS khác chứa chuỗi tìm kiếm (`18960-29-7`, `5460-29-7`, `92960-29-7`), tất cả không nhận cảnh báo này. Việc tìm kiếm chuỗi rộng vẫn giữ; phân loại khớp CAS chính xác.
- Trang sửa CAS `#5825` cho sửa nội dung/ngừng áp dụng, loại CAS readonly và CAS editable, đúng chính sách; không sửa hoặc xác nhận lưu.

Không xuất/copy giá, không lưu dữ liệu quy tắc/sản phẩm trong lượt browser. Phần lưu/transaction, ưu tiên hãng bật/tắt và liên kết gán riêng hỏng dựa trên 211 test độc lập/local cùng 7 trường hợp TEMP rollback đã chạy tại staging; lượt browser không thay cờ hãng hoặc tạo dữ liệu thử giữ lại. Không cần chạy lại toàn bộ test vì không đổi application code.

## Phần cần PO duyệt tiếp

Smoke kỹ thuật và browser đã đạt. PO chưa nghiệm thu staging/cho merge PR hoặc phát hành production. Hai nhãn sai và quy tắc dữ liệu production vẫn giữ nguyên; kiểm tham chiếu và dọn là thao tác riêng sau phê duyệt. Không coi kết quả staging là bằng chứng đã sửa production.

CI GitHub: không có workflow/check được cấu hình, PR báo không có checks;
không gọi đó là CI PASS. Chưa duyệt merge hoặc production. Dọn nhãn cần
phê duyệt riêng, kiểm FK + text reference và timeout theo OPERATIONS.
