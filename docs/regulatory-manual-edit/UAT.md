# UAT local — Quy tắc pháp chế thủ công

**PO xác nhận UAT local 8/8 đạt trên trình duyệt — 2026-09-28.** Đây là kết
quả bản chức năng trước polish. Bốn chỉnh sửa hiển thị yêu cầu sau UAT đã
qua tests/verifier phạm vi ảnh hưởng; **PO đã kiểm tra lại 4/4 hiển thị,
xác nhận UAT đạt**. Chưa phát hành/merge/deploy. Checklist bên dưới giữ làm
hồ sơ kịch bản đã chạy.

## 1. Mở app và đăng nhập

1. Mở địa chỉ đăng nhập ứng dụng UAT local do người chuẩn bị môi trường cung
   cấp (chỉ truy cập trên máy đang chạy UAT).
2. Đăng nhập bằng username **`regulatory_uat`**, dùng form username/password,
   không dùng Google hoặc đăng nhập chỉ-mật-khẩu.
3. Thông tin đăng nhập UAT nằm trong thư mục tạm riêng của agent **ngoài
   repository, không commit**; nhận qua kênh nội bộ của phiên UAT. Không
   chụp/gửi mật khẩu khi báo lỗi.
4. Sau đăng nhập, mở menu **Quản trị → Quy tắc quản lý**.
5. Chọn **Tìm theo tên / CAS / mã, sửa và xem lịch sử** để vào danh sách.

Đây là DB **`search_tools_uat`**, dữ liệu giả hoàn toàn, không copy database
local gốc hay production. Tài khoản chỉ có menu Quy tắc quản lý, không phải
Super Admin. Web và worker riêng đều trỏ cùng DB UAT; không dùng worker chung.

## 2. Hai file thử import

Hai file mẫu có sẵn trong thư mục `uat-samples/` cùng tài liệu này; chọn
chúng trong màn hình upload:

- [01-upsert-conflicts.xlsx](uat-samples/01-upsert-conflicts.xlsx)
  — chọn **Thêm / cập nhật, giữ các dòng ngoài tệp**.
- [02-replace-scoped-conflicts.xlsx](uat-samples/02-replace-scoped-conflicts.xlsx)
  — chọn **Thay đúng nhóm trường + tình trạng có trong file**.

Dùng nguyên file cho lần thử đầu. Chỉ ba cột, một sheet, sáu dòng mỗi file;
không công thức, macro, comment VML hoặc dữ liệu thật. Nếu sửa để thử thêm,
lưu bản sao, chỉ sửa A2:C7; không thêm sheet/cột. Nguồn giả được ghi trong
thuộc tính workbook/footer và README đi kèm.

### Dữ liệu đã chuẩn bị

- `UAT-DUP`, `UAT-EDIT`, `UAT-STOP`, `UAT-RACE`: bốn mục riêng để thử các
  thao tác bên dưới; tình trạng ban đầu **Được bán**.
- `50-00-0`, `64-17-5`: quy tắc CAS mẫu; không phải hồ sơ sản phẩm.
- `UAT-PAGE 001` tới `UAT-PAGE 056`: 56 quy tắc tên để thử phân trang.
- Nhóm `UAT-UP-…`: code / **CẤM NHẬP**, dành cho upsert.
- Nhóm `UAT-RP-…`: code / **Phụ lục II**, dành cho replace_scoped.
  Hai nhóm import có scope khác nhau, không xóa chéo dòng thuần import của nhau.
- Trong mỗi nhóm: `EDIT` đã sửa ghi chú tay; `STOP` đã ngừng tay; `OLD` đã
  đổi thành `NEW` (khóa OLD được giữ); `ABSENT` thêm tay nhưng không có trong
  file; `PURE` là mục thuần import. File có thêm `INSERT` chưa tồn tại.
- Riêng `UAT-RP-REMOVE`: mục thuần import vắng file, **được phép bị xóa**.

Các sửa/ngừng tay của fixture đi qua service thật và có lịch sử với lý do
chuẩn bị UAT. Không bịa lịch sử bằng chèn event trực tiếp.

## 3. Checklist — làm theo thứ tự, đánh dấu một ô mỗi mục

Trước khi thử import, không đổi tên/thứ tự danh mục tình trạng hoặc sửa
nhóm `UAT-UP-…`/`UAT-RP-…`, để số liệu lần đầu khớp hướng dẫn. Những thao tác
sửa riêng dùng `UAT-EDIT`, `UAT-STOP`, `UAT-RACE`.

### 1) Tìm theo tên, CAS, mã; lọc và phân trang

- Trong danh sách, tìm `UAT-PAGE`, chọn loại **Tên sản phẩm**: có **56 mục**,
  trang 1 có 50, trang 2 có 6; bộ lọc vẫn giữ khi chuyển trang.
- Tìm `50-00-0`, chọn loại **CAS**: đúng một mục.
- Tìm `UAT-RP-STOP`, chọn **Đang áp dụng**: không thấy; chọn **Ngừng áp dụng**
  hoặc **Tất cả**: thấy mục. Lọc **Phụ lục II** vẫn thấy đúng mục đó.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 2) Thêm mới và xử lý trùng

- Thêm loại **Mã sản phẩm**, giá trị `UAT-ADD-01`, tình trạng **Được bán**,
  ghi chú tùy chọn → **Xem trước → Xác nhận lưu**: có đúng một mục và lịch sử thêm.
- Thêm lại `UAT-DUP` / **Được bán**, ghi chú khác: báo trùng, có liên kết mở
  mục có sẵn, không ghi đè ghi chú cũ.
- Thêm `UAT-DUP` / **Cần giấy phép**: có cảnh báo cùng mã khác tình trạng,
  vẫn cho xác nhận thêm. Không chọn Phụ lục II để tránh thay scope mẫu RP.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 3) Sửa tình trạng và bắt buộc lý do

- Mở `UAT-EDIT`, đổi **Được bán → CẤM NHẬP**, bỏ trống lý do → xem trước:
  bị yêu cầu nhập lý do.
- Nhập `UAT: kiểm tra đổi tình trạng`, xem trước/sau, xác nhận: giữ cùng ID,
  đổi tình trạng, có dấu bảo vệ import. Loại đối chiếu không sửa được.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 4) Ngừng áp dụng / khôi phục

- Mở `UAT-STOP` → chọn **Ngừng áp dụng**. Thiếu lý do bị chặn; nhập lý do
  `UAT: thử ngừng` rồi xem trước/xác nhận: mục vẫn tồn tại nhưng ngừng áp dụng.
- Tìm bằng bộ lọc **Ngừng áp dụng**, mở lại → chọn **Khôi phục**, xác nhận:
  active trở lại, cùng ID, dấu bảo vệ vẫn còn. Không có nút xóa hẳn.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 5) Import upsert giữ sửa tay

- Về **Quy tắc quản lý**, upload file 01, chọn chế độ **Thêm / cập nhật**.
- Chờ xem trước hoàn tất. Lần đầu phải có: **1 dòng thêm / 1 dòng cập nhật /
  0 quy tắc xóa / 4 dòng giữ-bỏ qua**, **3 quy tắc được bảo vệ**, **4 chi tiết**.
- Mở toàn bộ chi tiết: thấy `EDIT`, `STOP`, khóa `OLD` và khóa `NEW`;
  OLD/NEW cùng dẫn về một quy tắc.
- **Xác nhận áp dụng**, chờ hoàn tất. Kiểm tra:
  - `UAT-UP-EDIT`: ghi chú tay còn nguyên.
  - `UAT-UP-STOP`: vẫn ngừng áp dụng (chọn bộ lọc Tất cả để tìm).
  - `UAT-UP-NEW`: còn; tìm `UAT-UP-OLD` trong danh sách không có rule cũ.
  - `UAT-UP-ABSENT`: vẫn còn dù vắng file.
  - `UAT-UP-PURE`: ghi chú `ĐÃ CẬP NHẬT TỪ FILE UP`.
  - `UAT-UP-INSERT`: được thêm.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 6) Import replace_scoped giữ mục tay vắng file

- Upload file 02, chọn **Thay đúng nhóm trường + tình trạng có trong file**.
- Lần đầu: **1 dòng thêm / 1 dòng cập nhật / 1 quy tắc xóa / 4 dòng giữ-bỏ qua**,
  **4 quy tắc được bảo vệ**, **5 chi tiết**. Phạm vi phải là **Mã / Phụ lục II**.
- Xem chi tiết: có `UAT-RP-ABSENT` được giữ vì là mục tay vắng trong file.
- Nhập **1** để xác nhận số mục xóa rồi áp dụng. Kiểm tra:
  - `UAT-RP-EDIT`, `UAT-RP-NEW`, `UAT-RP-ABSENT`: giữ nguyên nội dung tay.
  - `UAT-RP-STOP`: vẫn ngừng, không được bật lại.
  - `UAT-RP-OLD`: không tái xuất hiện; `UAT-RP-REMOVE`: đã bị xóa đúng dự kiến.
  - `UAT-RP-PURE`: ghi chú `ĐÃ CẬP NHẬT TỪ FILE RP`; `UAT-RP-INSERT`: được thêm.
  - Nhóm `UAT-UP-…` và mục `UAT-EDIT` không bị thay bởi file RP.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 7) Xem lịch sử và snapshot import

- Mở `UAT-EDIT` và `UAT-STOP`: thấy người `regulatory_uat`, thời gian, lý do,
  dữ liệu trước/sau; chuỗi ngừng/khôi phục giữ cùng ID.
- Mở tác vụ import đã áp dụng, chọn **Chi tiết snapshot** của sự kiện hoàn tất:
  xem được nội dung giữ/xung đột. Import không thêm sự kiện giả “sửa tay”.
- Tạo một preview mới từ file 01 nếu cần thử lại. Số liệu lúc này khác lần đầu
  vì dữ liệu thuần import đã cập nhật; không coi đó là lỗi. Trước khi confirm,
  sửa note `UAT-UP-EDIT` ở tab khác rồi confirm preview cũ: job phải từ chối
  vì danh mục đã đổi, yêu cầu xem trước lại.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

### 8) Hai cửa sổ cùng sửa / bấm xác nhận hai lần

- Mở `UAT-RACE` trong **hai tab/cửa sổ cùng trình duyệt** trước khi lưu.
- Tab A sửa note thành `UAT: cửa sổ A` và lưu.
- Tab B (chưa tải lại) sửa thành `UAT: cửa sổ B` rồi xác nhận: bị báo dữ liệu
  đã thay đổi, phải tải lại; note A không mất.
- Thử bấm nhanh nút xác nhận hai lần ở một thao tác mới hợp lệ: không tạo
  hai mục/hai sự kiện. Sau tải lại, kiểm tra số mục và lịch sử.
- Kết quả: [ ] Đạt  [ ] Không đạt — Ghi chú: __________

## 4. Kết quả chuẩn bị kỹ thuật (không thay nghiệm thu của người dùng)

- Đăng nhập username/password thật và HTTP trang admin/list/new: đã đạt.
- Worker regulatory-only đã preview cả hai workbook; số liệu đúng mục 5/6.
  **Chưa apply** hai job smoke, dữ liệu giữ trạng thái gốc cho lần thử đầu.
  Sau khi đã thử thêm/sửa quy tắc, không dùng lại preview smoke cũ để confirm;
  upload mới theo mục 5/6 để preview phản ánh dữ liệu hiện tại.
- Có thể thấy một job lỗi thử file ban đầu có comment Excel/VML; file cuối
  đã bỏ comment để đúng chính sách workbook XML-only, đã preview thành công.
  Không sửa bộ kiểm tra upload/application code để chấp nhận file đó.
- Excel cuối: một sheet, ba cột, sáu dòng, không công thức/error cell; nội dung
  đọc lại bằng openpyxl và đi qua parser/worker thực. Không cần recalculation.
- PO đã xác nhận **cả tám mục đạt** trên trình duyệt. Các ô trong kịch bản
  được giữ làm mẫu ghi chú; nguồn nghiệm thu là xác nhận của PO ngày 2026-09-28.

## 5. Nếu đóng máy hoặc gặp lỗi

- App/worker local sẽ dừng khi tắt máy; DB UAT không tự reset khi bật lại.
  Thông tin đăng nhập ở thư mục tạm ngoài Git có thể bị hệ điều hành dọn:
  báo AI để kiểm tra, không tự chạy lại setup hoặc xóa DB.
- Để báo lỗi: ghi số checklist, thao tác, kết quả mong đợi/thực tế, ảnh lỗi
  nếu có. Không gửi file chứa mật khẩu UAT hoặc cookie/CSRF.
- Không thử hai workbook này ở production. Không đổi scope để “sửa số lượng
  xóa”; nếu preview khác dự kiến ở lần đầu thì dừng và báo lại.
