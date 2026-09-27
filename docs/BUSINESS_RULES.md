# Nghiệp vụ đã chốt

Quy tắc dưới đây được đọc trực tiếp từ code (`search.py`, `team_permissions.py`,
`regulatory.py`, `compliance_resolver.py`, `stock.py`, `currency_rates.py`,
`brand_gateway.py`, `admin_*.py`) và migration trong `sql/`. Mỗi mục ghi rõ
**[xác nhận từ code]** (có thể trỏ file:line khi cần tra lại) hoặc **[cần
người dùng xác nhận]** khi chỉ suy ra được ý định kỹ thuật, không chắc đúng ý
nghiệp vụ thật.

## 1. Tìm kiếm sản phẩm (Product Search)

- Tìm theo `name` (chứa, ILIKE), `code` (chứa), và `cas` (chứa, chỉ khi team
  có quyền `SEARCH_BY_CAS`) — không phân biệt hoa/thường, có dùng trigram
  index để tăng tốc tìm dạng chứa/tiền tố. **[xác nhận từ code]**
  (`search.py` route `/search`, `sql/migration_010_search_trgm_indexes.sql`).
- Kết quả chỉ gồm brand nằm trong `team_brands` của team người dùng (trừ
  admin thấy tất cả); vô hiệu hóa nếu team bị archive (`lifecycle_status`).
  **[xác nhận từ code]** (`team_permissions.py`, hàm `_visibility_sql` trong
  `search.py`).
- Gợi ý tìm kiếm (autocomplete) là endpoint riêng, nhẹ, không JOIN giá/pháp
  chế, tối thiểu 3 ký tự, tối đa 10 gợi ý, ngân sách 500ms; chỉ gợi ý các
  field người dùng có quyền xem (name/code/cas theo VIEW_NAME/VIEW_CODE/
  VIEW_CAS + SEARCH_BY_CAS cho cas). **[xác nhận từ code]**
  (`search_suggestions.py`).
- Lọc "chỉ hiện kết quả có tồn kho" (`in_stock_only=1`): chỉ trả sản phẩm có
  ít nhất 1 dòng tồn kho **đang active snapshot**, **số lượng > 0**, và
  **hiển thị được với team hiện tại** (brand nằm trong `team_brands`); nếu
  bật cờ này mà không nhập từ khóa thì báo lỗi 400. Sản phẩm chỉ có trong
  tồn kho (không có trong catalog `products`) vẫn hiển thị riêng nhưng
  **không được chọn/xuất báo giá**. Hạn sử dụng chỉ mang tính thông tin
  (hiển thị cảnh báo hết hạn/sắp hết hạn ≤30 ngày), không phải điều kiện đủ
  bán hàng. **[xác nhận từ code]** (`stock.py`, `docs/phase6d7/SCOPE.md`,
  `docs/phase6d8/SCOPE.md`).
- CAS đồng nhất trả về nhãn "Cùng CAS — cần đối chiếu" (không coi là khớp
  chính xác), chỉ hiện khi team có cả `SEARCH_BY_CAS` và `VIEW_CAS`.
  **[xác nhận từ code]**.

## 2. Pháp chế / quản lý xuất nhập khẩu (Compliance)

- Các trạng thái pháp chế là danh mục **hoàn toàn động và phải linh hoạt để
  người dùng đổi** (**[xác nhận từ người dùng]**, 2026-09-27) — admin được
  thêm, đổi tên, đổi thứ tự ưu tiên bất kỳ lúc nào; tên hiển thị chỉ là nhãn
  hiện tại, không phải enum cố định. Mỗi trạng thái dùng **định danh ổn định**
  tách biệt khỏi nhãn hiển thị — đổi tên/đổi màu **không** tự đổi
  `export_policy` (chặn/cho xuất) đang gắn với định danh đó. Chức năng
  tắt/xóa hẳn một trạng thái **chưa làm** (theo quyết định người dùng); cách
  cập nhật chính là import lại danh sách quy tắc.
- Danh mục mặc định/lịch sử gồm: **Được bán, Phụ lục II, Phụ lục III, Cần
  giấy phép, Cấm nhập, Chưa xác định** — phản ánh quy định quản lý tiền
  chất/hóa chất của Việt Nam tại thời điểm khởi tạo, **không phải danh sách
  bất biến**. Ý nghĩa nghiệp vụ hiện tại của các nhãn này (**[xác nhận từ
  người dùng]**, 2026-09-27): **Phụ lục II** = danh mục hóa chất kinh doanh
  có điều kiện; **Phụ lục III** = danh mục hóa chất kiểm soát đặc biệt; **Cần
  giấy phép** = hóa chất hạn chế nhập khẩu, muốn nhập phải xin giấy phép;
  **Cấm nhập** = không được nhập, tự động **chặn xuất báo giá**
  (`export_policy=BLOCK`); các trạng thái khác cho xuất bình thường. Tài
  liệu này không tự gắn các nhãn đó với số nghị định/thông tư cụ thể — cần
  tra văn bản pháp luật hiện hành nếu cần trích dẫn chính xác điều khoản, và
  nhớ rằng admin có thể đổi nhãn/ý nghĩa này bất cứ lúc nào.
- Mỗi trạng thái có `export_policy` chỉ 2 giá trị: `ALLOW` hoặc `BLOCK` —
  đây là cờ duy nhất quyết định "được xuất báo giá/bán hay không", tách biệt
  khỏi nhãn hiển thị và màu sắc (đổi tên/đổi màu không đổi chính sách).
  **[xác nhận từ code]** (`regulatory.py`).
- Một sản phẩm có thể khớp nhiều rule (theo CAS, theo Code, theo Name chứa
  chuỗi) — rule thắng là rule có `priority` nhỏ nhất (rồi tới `status_id`);
  ghi chú (note) của mọi rule cùng thắng trạng thái đó được gộp lại
  (distinct, sắp theo alphabet). **[xác nhận từ code]** (LATERAL resolver
  trong `regulatory.py`).
- **Override thủ công theo sản phẩm** (`products.manual_compliance*`) thắng
  tuyệt đối rule tự động, nhưng chỉ có hiệu lực khi brand đó đã bật
  `brand_compliance_settings.manual_compliance_priority=true`; nếu override
  trỏ tới một status đã bị xóa khỏi danh mục, hệ thống trả về trạng thái lỗi
  dữ liệu ("Lỗi dữ liệu quản lý", `BLOCK`) thay vì âm thầm bỏ qua.
  **[xác nhận từ code]**. Ý nghĩa nghiệp vụ của cơ chế này (**[xác nhận từ
  người dùng]**): override dùng để tạo **ngoại lệ Được bán tường minh cho
  từng sản phẩm cụ thể**, cho phép xuất báo giá kể cả khi rule CAS/Code/Tên
  tự động đang là Cấm nhập — đây là quyết định có chủ đích của người có
  thẩm quyền (không phải lỗi hệ thống), luôn được audit; xóa override đưa
  sản phẩm về đúng kết quả tự động.
- Thứ tự ưu tiên tổng quát: **override thủ công (nếu brand bật) → lỗi dữ liệu
  override (nếu override trỏ tới status đã xóa) → rule tự động theo
  priority → không có trạng thái nào (mặc định coi như ALLOW/không cảnh
  báo)**. **[xác nhận từ code]** (`resolve_compliance_precedence`,
  `compliance_resolver.py`).
- Check License/Check CAS dùng đúng resolver trên, độc lập với quyền xem
  brand của team (CAS-only lookup không cần biết sản phẩm nào).
  **[xác nhận từ code]**.
- Export (kết quả tìm kiếm, báo giá) phụ thuộc `VIEW_COMPLIANCE` — không cho
  export nếu team không có quyền xem tình trạng pháp chế, để tránh dùng
  export như một "oracle" dò trạng thái pháp chế qua thành công/thất bại.
  **[xác nhận từ code]** (`team_permissions.DEPENDENCIES['EXPORT']`).

## 3. Báo giá (Quote / Quick Quote)

- **Quick Quote**: người dùng upload file yêu cầu báo giá của khách hàng
  (.xlsx/.csv, tối đa 10MB), hệ thống tự đoán sheet/hàng tiêu đề/cột
  Name-Code-CAS (theo alias tên cột tiếng Anh/Việt phổ biến), người dùng xác
  nhận mapping rồi hệ thống đối chiếu (match) từng dòng với `products` theo
  Code/CAS trước, Name sau. **[xác nhận từ code]** (`quote_request_file.py`).
- Yêu cầu quyền `QUICK_QUOTE` phụ thuộc `VIEW_PRICE` — không thể dùng Quick
  Quote nếu không được xem giá. **[xác nhận từ code]**.
- Kết quả match được xuất vào **file mẫu Excel (BG template)** của công ty,
  ghi trực tiếp vào XML của file gốc (không dựng lại workbook bằng
  openpyxl) để giữ nguyên định dạng, công thức, dòng tổng — tự thêm dòng nếu
  số sản phẩm vượt sức chứa mặc định của template. **[xác nhận từ code]**
  (`quote_workbook_export.py`).
- Nếu người xuất **không có đủ quyền xem một số cột** (VD: thiếu
  `VIEW_CAS`), export tạo **workbook mới chỉ gồm cột được phép** thay vì file
  mẫu gốc (không mang theo nội dung/style cũ có thể hé lộ cấu trúc cột ẩn).
  Người có đủ quyền vẫn dùng đúng exporter/template gốc. **[xác nhận từ
  code]** (ghi trong `PHASE6C01_LOCAL_REPORT.md`, khớp cơ chế redact chung).
- `quote_templates`/`team_quote_templates`: mỗi ngữ cảnh (brand/nguồn) có thể
  gán một file mẫu khác nhau, chỉ có **một** template active tại một thời
  điểm cho mỗi ngữ cảnh (unique index), có thể archive template cũ.
  **[xác nhận từ code]** (`sql/migration_013...`, `022`, `023`).
- `products.preparation_type` (NEAT/SOLUTION/MIXTURE/OTHER, tùy chọn) **chỉ
  dùng làm điều kiện lọc trong Quick Quote**, không phải cột hiển thị trong
  Search/Quick Quote/copy/export, và không có quyền field riêng
  (`VIEW_PREPARATION_TYPE`) — quyền `QUICK_QUOTE` sẵn có đã kiểm soát việc
  dùng bộ lọc này. **[xác nhận từ người dùng]**.
- Copy/Export chỉ chứa cột người dùng **đang có quyền xem**, không tự cấp
  thêm quyền xem giá dù đang copy/export. **[xác nhận từ code]**.

## 4. Tồn kho (Stock)

- Tồn kho là một **mô hình snapshot riêng** (`stock_items`/`stock_snapshots`/
  `stock_state`), không phải cột trong `products` — một sản phẩm có thể xuất
  hiện trong catalog nhưng không có tồn kho, hoặc có tồn kho nhưng không có
  trong catalog (hiển thị riêng, không thể chọn để báo giá).
  **[xác nhận từ code]**.
- Nạp tồn kho là **thay thế toàn bộ** (full snapshot) qua import Excel nền
  (worker), không phải cộng dồn dòng — mỗi lần nạp tạo snapshot mới, snapshot
  cũ vẫn lưu lại (lịch sử/khôi phục), con trỏ `active_snapshot_id` đổi atomic.
  **[xác nhận từ code]** (`stock_import_jobs.py`, migration 029).
- **Sửa nhanh 1 dòng tồn kho** (`stock_manual.py`, migration 032) cũng tạo
  snapshot mới (clone toàn bộ + áp 1 thay đổi) thay vì UPDATE tại chỗ, để giữ
  cùng mô hình bất biến; có bước xem trước (preview, review) rồi mới lưu,
  chặn trùng Brand+Code+Size+Hạn sử dụng. **[xác nhận từ code]**.
- Hạn sử dụng có 3-4 trạng thái hiển thị: còn hạn, sắp hết hạn (≤30 ngày),
  đã hết hạn, không có hạn sử dụng — chỉ mang tính cảnh báo, **không** tự
  động ẩn hay chặn bán hàng theo hạn. **[xác nhận từ code]** (`stock.py`
  hàm `expiry_state`). Ngưỡng 30 ngày là **quyết định nghiệp vụ đã chốt**,
  không phải giá trị kỹ thuật tạm thời. **[xác nhận từ người dùng]**.
- Giá tồn kho (`Giá tồn kho`) là **VND, chưa gồm VAT, tùy chọn, chỉ để tham
  khảo khi xem tồn kho** — không bao giờ thay thế giá catalog hoặc giá dùng
  để tính báo giá; thiếu giá không đồng nghĩa hết tồn. **[xác nhận từ người
  dùng]**.
- Quyền sửa tồn kho thủ công / xem trang quản lý tồn kho nằm trong menu admin
  `stock` (`admin_menu_grants`), không phải quyền team thông thường.
  **[xác nhận từ code]**.
- Tần suất cập nhật tồn kho (**[xác nhận từ người dùng]**, 2026-09-27): nạp
  lại tùy thời điểm biến động thực tế, thường khoảng **1 tuần/lần**, không
  theo lịch cố định cứng.

## 5. Team và phân quyền

- Mỗi team có: tập brand được xem (`team_brands`), tập quyền tính năng/field
  (`teams.permission_keys`), chính sách IP riêng (`teams.ip_policy`).
  **[xác nhận từ code]**.
- Danh sách quyền tính năng: `SEARCH, CHECK_LICENSE, FIND_CODE,
  ADVANCED_SEARCH, SEARCH_BY_CAS, QUICK_QUOTE, COPY, EXPORT`; quyền field:
  `VIEW_NAME, VIEW_CODE, VIEW_CAS, VIEW_BRAND, VIEW_SIZE, VIEW_PRICE,
  VIEW_NOTE, VIEW_COMPLIANCE, VIEW_COMPLIANCE_NOTE`. Phụ thuộc bắt buộc:
  QUICK_QUOTE→VIEW_PRICE; CHECK_LICENSE→SEARCH_BY_CAS+VIEW_COMPLIANCE;
  ADVANCED_SEARCH→SEARCH_BY_CAS; EXPORT→VIEW_COMPLIANCE. **[xác nhận từ
  code]** (`team_permissions.py`).
- Mọi thay đổi brand/IP-policy/quyền của team đi qua **preview rồi confirm**
  (token PostgreSQL, TTL 30 phút, gắn đúng admin đã tạo preview); brand đưa
  vào phải tồn tại thật trong `products` tại thời điểm submit, brand lạ làm
  **từ chối toàn bộ request** (không âm thầm bỏ qua phần không hợp lệ).
  **[xác nhận từ code]** (`admin_teams.py`).
- Archive team có thành viên bắt buộc chọn team thay thế để chuyển toàn bộ
  thành viên sang trước khi archive; không tự phục hồi thành viên khi restore
  team. **[xác nhận từ code]** (`admin_lifecycle.py`).
- Quyền **menu admin** (`products/imports/teams/users/network/
  quote_templates/exchange_rates/regulatory/stock/manual_priority/
  login_history`) là lớp riêng, chỉ áp dụng cho tài khoản admin, cấp qua
  `admin_menu_grants`; chỉ `is_super_admin=true` được đổi quyền admin của
  người khác và được toàn bộ menu mặc định. Hệ thống luôn giữ ít nhất một
  super_admin đang hoạt động (không cho khóa/hạ quyền người cuối cùng).
  **[xác nhận từ code]**.
- Đổi `team_id` hoặc vai trò của **một user** bump `auth_version` (buộc đăng
  nhập lại phiên cũ); đổi brand/IP-policy của **cả team** thì không cần,
  effective ngay request tiếp theo vì đọc `team_brands` sống. **[xác nhận từ
  code]**.

## 6. Tỷ giá và giá bán

- Giá gốc `products.price` + `products.ship` theo **currency của brand**
  (`brand_master.currency_code`), quy đổi sang VND qua `currency_rates`.
  VND luôn cố định = 1, không thể sửa. **[xác nhận từ code]**
  (`currency_rates.py`).
- Nếu brand chưa gán currency, hoặc currency chưa có tỷ giá dương hợp lệ,
  hoặc dữ liệu brand/tỷ giá đang trong trạng thái nửa migrate — hệ thống
  **luôn từ chối hiển thị giá** ("Giá không khả dụng"), không bao giờ dùng
  tỷ giá mặc định 1.0 để "cho có giá hiển thị". **[xác nhận từ code]**.
- Sửa tỷ giá / gán currency cho brand chỉ qua trang admin (`exchange_rates`
  menu), có khóa dòng (`FOR UPDATE`) + ghi lịch sử
  (`currency_rate_history`/`brand_currency_history`) mỗi lần đổi.
  **[xác nhận từ code]**.
- Tỷ giá cập nhật thủ công qua admin, không có luồng tự động lấy tỷ giá từ
  nguồn ngoài trong code hiện tại. **[xác nhận từ code]**. Tần suất cập nhật
  (**[xác nhận từ người dùng]**, 2026-09-27): tùy thời điểm biến động, thường
  khoảng **1 tuần/lần**, cùng nhịp với cập nhật tồn kho.

## 7. Import dữ liệu (sản phẩm / pháp chế / tồn kho)

- Import qua Excel luôn có bước **preview (không ghi DB) rồi apply (ghi DB)**;
  apply chỉ chạy nếu "fingerprint" (chữ ký dữ liệu) tại thời điểm apply khớp
  với lúc preview — nếu có ai đổi dữ liệu ở giữa, phải preview lại.
  **[xác nhận từ code]**.
- Chế độ import sản phẩm: `append` (chỉ thêm), `upsert` (thêm/cập nhật theo
  brand+code, cần chỉ rõ nguồn brand/quy cách nếu mã bị trùng ở nhiều dòng),
  `replace_by_brand` (xóa toàn bộ sản phẩm thuộc (các) canonical brand có
  trong file rồi chèn lại — xóa theo **toàn bộ nguồn brand lịch sử** đã quy
  về canonical brand đó, không chỉ nguồn brand có trong file hiện tại).
  **[xác nhận từ code]** (`import_engine.py`).
- Import quy tắc pháp chế: `upsert` (chỉ thêm/cập nhật quy tắc có trong file,
  giữ nguyên quy tắc không có trong file) hoặc `replace_scoped`. Phạm vi
  `replace_scoped` (**[xác nhận từ người dùng]**, khớp code
  `regulatory_import_jobs.py`): thay thế theo **đúng cặp (trường đối chiếu,
  trạng thái)** xuất hiện trong file, không thay toàn bộ danh mục. Ví dụ file
  chỉ chứa quy tắc **Code/Cấm nhập** thì chỉ nhóm quy tắc Code+Cấm nhập cũ bị
  xóa/thay; quy tắc CAS/Cấm nhập, Tên/Cấm nhập và mọi trạng thái khác được
  giữ nguyên. File rỗng không suy diễn thành xóa mọi quy tắc — luôn xem
  preview trước khi xác nhận.
- Cột tùy chọn khi import sản phẩm: `Compliance`/`Compliance_Note` (override
  pháp chế thủ công — phải có cả hai cột hoặc không có cột nào, không được
  có Note mà thiếu Compliance) và `Preparation_Type` (chỉ nhận
  NEAT/SOLUTION/MIXTURE/OTHER, có alias tiếng Việt). **[xác nhận từ code]**.
- Giới hạn an toàn file: tối đa 128MB, 1.000.000 dòng/workbook (cấu hình qua
  biến môi trường `IMPORT_MAX_BYTES`/`IMPORT_MAX_ROWS`...), chặn macro/
  external link/zip-bomb. **[xác nhận từ code]**.

## Câu hỏi cần người dùng xác nhận (tổng hợp)

Đã xác nhận 2026-09-27 (xem chi tiết ở từng mục trên): ý nghĩa và tính linh
hoạt của nhãn pháp chế, ý nghĩa override thủ công, phạm vi `replace_scoped`
khi import quy tắc pháp chế, giá tồn kho/ngưỡng sắp hết hạn, cách dùng
`preparation_type`, trạng thái/đối tượng dùng `ENABLE_LEGACY_PASSWORD_LOGIN`,
tần suất cập nhật tồn kho/tỷ giá, quy mô dữ liệu production. Không còn câu
hỏi nghiệp vụ nào mở tại thời điểm viết tài liệu này.
