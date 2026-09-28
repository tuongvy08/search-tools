# Kiến trúc và quyết định

## Phạm vi

Search-tools là ứng dụng Flask (Python) một tiến trình web + một worker nền,
dùng PostgreSQL làm kho dữ liệu duy nhất. Không có framework frontend/build
step: giao diện là HTML (Jinja2, `templates/`) + CSS/JS thuần
(`static/`). Đang chạy production cho nhân viên công ty (không phải SaaS đa
khách hàng).

## Thành phần chính

| File/khu vực | Vai trò |
|---|---|
| `search.py` | Entry point Flask (~6.500 dòng); đăng ký toàn bộ blueprint/module khác, chứa các route chính: `/login`, `/search`, `/check_cas*`, `/find_code_batch`, `/advanced_search*`, `/api/quote-assistant/*`, `/api/results/*`, các route admin còn lại chưa tách blueprint (`/admin/exchange-rates`, `/admin/network`, `/admin/users`, `/admin/brand-compliance`, `/admin/quote-templates`...). |
| `db.py` | Kết nối PostgreSQL qua `psycopg2`, đọc `DATABASE_URL` từ env; không dùng connection pool (mỗi request tự mở/đóng connection). |
| `team_permissions.py` | Registry quyền theo **team** (feature: SEARCH, CHECK_LICENSE...; field: VIEW_NAME, VIEW_PRICE...), enforce ở `before_request` + redact response JSON. |
| `admin_permissions.py` | Registry quyền **menu admin** theo user (`admin_menu_grants`), super_admin vs admin thường, enforce ở `before_request` cho mọi path `/admin/*` và `/api/admin/*`. |
| `session_security.py` | Kiểm tra tính hợp lệ của session mỗi request (account_status, auth_version), CSRF token, `/logout`. |
| `middleware_access.py` | Giới hạn IP văn phòng (global + theo policy từng team). |
| `auth_google.py` | Đăng nhập Google Workspace OIDC (Authlib), vòng đời tài khoản GOOGLE. |
| `admin_google_users.py`, `admin_lifecycle.py`, `admin_teams.py` | CRUD/­lifecycle cho user Google, user LOCAL, team (archive/restore, preview trước khi áp dụng). |
| `brand_gateway.py` | Chuẩn hóa brand (canonical brand + alias) cho import, khóa advisory dùng chung cho mọi thao tác ghi `products`. |
| `currency_rates.py` | Resolver tỷ giá brand → currency → rate_vnd, fail-closed khi thiếu dữ liệu. |
| `compliance_resolver.py`, `regulatory.py` | Hợp nhất tình trạng pháp chế (manual override vs rule tự động), SQL resolver dùng chung (LATERAL join) cho search/check-license/import. |
| `stock.py`, `stock_manual.py`, `stock_import_jobs.py`, `admin_stock.py` | Tồn kho: snapshot bất biến, sửa nhanh 1 dòng, import Excel nền, trang admin. |
| `import_engine.py`, `import_jobs.py`, `admin_import_center.py` | Engine đọc/áp dụng workbook sản phẩm dùng chung cho web + CLI; hàng đợi job trong PostgreSQL; route admin mỏng, không xử lý nặng trong HTTP request. |
| `product_import_manual.py` | Validate/parse 2 cột tùy chọn khi import: `Compliance`/`Compliance_Note` (override pháp chế thủ công) và `Preparation_Type`. |
| `regulatory_import_jobs.py`, `admin_regulatory.py` | Import quy tắc pháp chế nền + trang quản trị danh mục tình trạng/màu sắc. |
| `admin_products.py` | CRUD sản phẩm qua admin, xóa có preview/khôi phục (không xóa cứng ngay). |
| `quote_request_file.py` | Đọc file yêu cầu báo giá của khách (xlsx/csv), tự gợi ý mapping cột (Name/Code/CAS). |
| `quote_workbook_export.py` | Ghi kết quả vào file mẫu Excel (BG template) bằng thao tác XML/zip trực tiếp (không dùng openpyxl để ghi, để giữ nguyên style/công thức gốc); template mặc định cột B–P theo `BG_HEADERS`. |
| `search_suggestions.py` | Endpoint gợi ý tìm kiếm nhẹ, tách khỏi `/search` (không JOIN giá/pháp chế), giới hạn 500ms/10 kết quả. |
| `scripts/import_worker.py` | Vòng lặp worker độc lập: xử lý job import sản phẩm → job import quy tắc pháp chế → job import tồn kho, mỗi loại một hàm `run_once()`. |

## Mô hình dữ liệu (theo `sql/schema.sql` + `sql/migration_*.sql`)

Bảng cốt lõi:

- **`products`** — danh mục sản phẩm: `name, code, cas, brand, size, ship,
  price, note`, cộng dồn qua các migration: `source_brand` (nguồn brand gốc,
  brand master), `manual_compliance`/`manual_compliance_note`/
  `manual_compliance_status_id` (override pháp chế thủ công theo sản phẩm),
  `preparation_type` (NEAT/SOLUTION/MIXTURE/OTHER). Index: btree trên
  `cas`/`code` (+ bản upper/trim), trigram (`pg_trgm`) trên
  `name`/`code`/`cas` để hỗ trợ tìm kiếm dạng chứa (contains)/tiền tố.
- **`teams`** / **`team_brands`** / **`app_users`** (migration 002, mở rộng
  qua 014/015/020/021) — team sở hữu tập brand được xem (`team_brands`), tập
  quyền tính năng/field (`teams.permission_keys`, mảng text), chính sách IP
  (`teams.ip_policy`), vòng đời (`lifecycle_status`: ACTIVE/ARCHIVED).
  `app_users` có `auth_provider` (LOCAL/GOOGLE), `account_status`
  (ACTIVE/PENDING/INVITED/SUSPENDED), `is_admin`, `is_super_admin`,
  `auth_version` (tăng để thu hồi mọi session cũ ngay lập tức),
  `ip_bypass_allowlist`.
- **`admin_menu_grants`** / **`admin_rbac_events`** (migration 030) — quyền
  menu admin theo từng user (khác với `teams.permission_keys` — đây là
  quyền của admin, không phải quyền của team/nhân viên thường).
- **`regulatory_statuses`** / **`regulatory_rules`** (migration 003, đại tu ở
  026/027/028) — danh mục tình trạng pháp chế (label, priority, export_policy
  ALLOW/BLOCK, màu) và các rule khớp theo `cas`/`code`/`name`.
  `brand_compliance_settings` (migration 011) bật/tắt ưu tiên override thủ
  công theo brand.
- **`brand_master`** / **`brand_aliases`** (migration 017) — brand chuẩn hóa
  (canonical) + currency mặc định, danh sách alias quy về 1 canonical brand.
- **`currency_rates`** / **`currency_rate_history`** /
  **`brand_currency_history`** (migration 018) — tỷ giá VND theo currency
  (VND cố định =1), lịch sử thay đổi tỷ giá và thay đổi currency của brand.
- **`stock_snapshots`** / **`stock_items`** / **`stock_state`** (migration
  029, thêm `stock_manual_requests` ở 032, `stock_note` ở 031) — mô hình
  snapshot bất biến: `stock_state` (singleton) trỏ tới `active_snapshot_id` +
  `revision`; mỗi lần import hoặc sửa tay tạo **snapshot mới** (copy toàn bộ
  dòng + áp thay đổi) rồi chuyển con trỏ, snapshot cũ vẫn còn trong
  `stock_snapshots`/`stock_items` (không xóa) để giữ lịch sử/rollback.
- **`product_import_jobs`** / **`product_import_rows`** /
  **`product_import_events`** (migration 024), **`regulatory_import_jobs`**
  (migration 026), **`stock_import_jobs`** (migration 029) — hàng đợi import
  nền, mỗi loại dữ liệu một bảng job riêng nhưng cùng một worker process xử
  lý tuần tự.
- **`quote_templates`** / **`team_quote_templates`** (migration 013/022/023)
  — file mẫu Excel báo giá theo ngữ cảnh (brand/nguồn), có thể archive.
- **`office_ip_allowlist`** (migration 006), **`login_audit_events`**
  (migration 014) — CIDR/IP văn phòng được whitelist, log đăng nhập/hành vi
  phiên (không chứa secret).
- **`team_permission_previews`** / **`team_lifecycle_previews`** /
  **`product_delete_previews`** / **`admin_rule_import_previews`** — mọi
  thao tác admin có rủi ro cao (đổi quyền team, archive team, xóa sản phẩm
  hàng loạt, xóa quy tắc) đều đi qua bước **preview lưu trong PostgreSQL
  (token dùng một lần, có TTL) rồi mới confirm** — không áp dụng ngay, không
  giữ state trong bộ nhớ process (an toàn khi chạy nhiều worker Gunicorn).

Danh sách migration đầy đủ: xem `sql/migration_*.sql` (đánh số thứ tự,
002–032 tại thời điểm viết tài liệu này); chạy tuần tự theo số.

## Đăng nhập và phân quyền

Ba cơ chế đăng nhập cùng tồn tại, không loại trừ nhau:

1. **LOCAL** (`app_users.auth_provider='LOCAL'`) — username + mật khẩu
   (`werkzeug.security` bcrypt-style hash), tài khoản tạo qua
   `scripts/bootstrap_admin.py`/`scripts/add_user.py` hoặc trang admin.
2. **Legacy break-glass** (`ENABLE_LEGACY_PASSWORD_LOGIN=true`, mặc định
   `false` trong code) — chỉ nhập mật khẩu (`APP_PASSWORD_MANAGER`/
   `APP_PASSWORD_STAFF` trong `.env`), không có username, không có row
   `app_users` riêng; staff kiểu này gắn cứng vào `LEGACY_STAFF_TEAM_ID`
   (mặc định `1`). **[xác nhận từ người dùng, 2026-09-27]**: hiện đang **bật**
   trên production, hiển thị dưới nút "Đăng nhập bằng Google" trên form đăng
   nhập; trên thực tế chỉ admin dùng cách này, và có thể tắt nếu cần.
3. **Google Workspace OIDC** (`GOOGLE_AUTH_ENABLED=true`) — qua Authlib,
   giới hạn theo `GOOGLE_WORKSPACE_ALLOWED_DOMAINS` (so khớp claim `hd`, không
   dùng đuôi email). Tài khoản Google mới đăng nhập lần đầu vào trạng thái
   `PENDING` (chờ admin duyệt ở `/admin/users`) trừ khi email đã được admin
   **invite** trước (`account_status='INVITED'` → tự kích hoạt khi đăng nhập
   đúng email). `PENDING`/`SUSPENDED` hiển thị `templates/pending_approval.html`
   hoặc lỗi, không vào được app.

Phân quyền có **hai lớp độc lập**:

- **Quyền nghiệp vụ theo team** (`team_permissions.py`) — áp dụng cho user
  thường (không phải admin): feature grant (SEARCH, CHECK_LICENSE, FIND_CODE,
  ADVANCED_SEARCH, SEARCH_BY_CAS, QUICK_QUOTE, COPY, EXPORT) và field grant
  (VIEW_NAME/CODE/CAS/BRAND/SIZE/PRICE/NOTE/COMPLIANCE/COMPLIANCE_NOTE), lưu
  trong `teams.permission_keys`, có phụ thuộc (VD: CHECK_LICENSE cần
  SEARCH_BY_CAS + VIEW_COMPLIANCE). Field bị từ chối sẽ bị **redact** khỏi
  JSON response (kể cả alias lồng nhau), không chỉ ẩn ở UI. `is_admin=True`
  luôn có toàn bộ quyền này.
- **Quyền menu admin** (`admin_permissions.py`) — áp dụng cho tài khoản
  admin, theo từng menu (`products, imports, teams, users, network,
  quote_templates, exchange_rates, regulatory, stock, manual_priority,
  login_history`), lưu trong `admin_menu_grants`. `is_super_admin=True` có
  toàn bộ menu; admin thường chỉ có menu được cấp tường minh. Chỉ super_admin
  được đổi quyền admin của người khác (`/admin/users/admin-access`).

Cơ chế bảo vệ chung: mọi mutation admin nhạy cảm (đổi quyền, archive, xóa)
đều `SELECT ... FOR UPDATE` + khóa advisory (`pg_advisory_xact_lock`) +
revalidate lại actor (session có thể đã bị thu hồi trong lúc chờ khóa) trước
khi ghi, và bump `auth_version` của tài khoản bị ảnh hưởng để buộc đăng nhập
lại. Team hiển thị brand mới ngay (đọc `team_brands` sống mỗi request),
nhưng đổi **team_id của một user** cần bump `auth_version` vì thông tin đó
cache trong session.

Giới hạn IP văn phòng (`middleware_access.py`, migration 006/015): 3 chế độ
theo từng team — `INHERIT` (theo rule global + ngoại lệ cá nhân),
`ALLOWLIST_ONLY` (chặn hết nếu không có rule), `ANY_AUTHENTICATED` (mọi IP,
cần đăng nhập). Admin luôn được miễn IP sau khi session hợp lệ. Đọc rule
thất bại → 503 (không suy diễn thành cho phép/từ chối); tắt hẳn bằng
`DISABLE_IP_ALLOWLIST=1` (khuyến nghị khi dev local).

## Import và worker

Mọi import "nặng" (Excel sản phẩm, quy tắc pháp chế, tồn kho) đi qua một
**hàng đợi job trong PostgreSQL** (`product_import_jobs` /
`regulatory_import_jobs` / `stock_import_jobs`), **không xử lý trong tiến
trình HTTP**:

1. Web app (`admin_import_center.py`...) chỉ nhận upload, lưu file vào
   `IMPORT_UPLOAD_DIR` (mặc định `/var/lib/search-tools/imports`, quyền
   `0700`), ghi 1 dòng job `status='queued'`, trả về ngay.
2. `scripts/import_worker.py` (chạy như systemd service riêng
   `search-tools-import-worker`, xem `deploy/search-tools-import-worker.service`)
   poll tuần tự 3 loại job (`import_jobs.run_once()` →
   `regulatory_import_jobs.run_once()` → `stock_import_jobs.run_once()`),
   dùng advisory lock để một job chỉ một worker xử lý, heartbeat để phát hiện
   worker chết giữa chừng.
3. Mỗi job có 2 phase: **preview** (đọc/validate/tính fingerprint, không ghi
   `products`) rồi **apply** (chỉ chạy khi fingerprint client gửi khớp
   fingerprint đã preview — chống đổi dữ liệu giữa preview và apply).
   Chế độ ghi: `append`, `upsert` (theo `brand+code(+source_brand+size)` khi
   mơ hồ), `replace_by_brand` (xóa toàn bộ brand đó rồi chèn lại — canonical
   brand-wide, xem `import_engine.py`).
4. An toàn file XLSX: `import_engine.inspect_workbook()` chặn macro, external
   link, zip-bomb (tỷ lệ nén, tổng dung lượng giải nén), XML không an toàn
   (DOCTYPE/ENTITY) trước khi cho `openpyxl` đọc.
5. Web và worker **phải dùng chung một `DATABASE_URL`** — tài liệu triển khai
   cảnh báo rõ: không suy luận `DATABASE_URL` đang chạy thật từ file `.env`
   trên server vì từng có sai lệch giữa hai nguồn (xem
   `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` mục Phase 6C2).

Brand trong workbook được chuẩn hóa qua `brand_gateway.py` (canonical +
alias, bảng `brand_master`/`brand_aliases`); brand chưa tồn tại được đăng ký
mới khi apply (không tự ý sinh brand ngoài luồng import).

## Deploy

- Production: VPS Vultr, service systemd cho web (Gunicorn, `Procfile`:
  `gunicorn search:app`) và một service riêng cho import worker (xem
  `deploy/search-tools-import-worker.service`), Nginx reverse proxy (mẫu
  cấu hình import: `deploy/nginx-imports.conf.example`).
- **Quy trình release thực tế (khác với mô tả đơn giản "git pull tại chỗ"
  trong `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` — tài liệu đó mô tả một cách làm
  cũ/đơn giản hơn, **[cần cập nhật lại `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` ở
  một task riêng]**): mỗi release là một **thư mục bất biến riêng**
  `/opt/search-tools-pg-release-<timestamp>-<commit ngắn>-clean`, không sửa
  đè lên thư mục đang chạy. Mỗi lần deploy production gồm 3 bước, mỗi bước
  là một script Python đã được review, **luôn do người dùng tự chạy qua
  SSH** (AI không tự SSH vào production/staging):
  1. **Preflight** (chỉ đọc): xác minh đúng tiến trình web/worker đang chạy
     thật (đọc từ `/proc`, không suy từ file `.env` cũ), đúng database, đúng
     schema đã áp dụng, không có job import đang chạy.
  2. **Prepare**: clone release mới (qua HTTPS từ GitHub) vào thư mục riêng,
     tạo checkpoint/backup, chưa dừng dịch vụ.
  3. **Cutover**: dừng cả web + worker, backup đầy đủ (`pg_dump`), chạy
     migration (nếu có), chuyển cả hai sang release mới, khởi động lại, kiểm
     tra (hash asset tĩnh, HTTP, số dòng dữ liệu không đổi ngoài phạm vi
     migration). Nếu thất bại sau khi đã dừng dịch vụ: tự động khôi phục lại
     **code/config** của release cũ (không tự động rollback dữ liệu/migration
     đã áp dụng thành công).
  - Web và worker **có thể tạm thời chạy khác release nhau** khi một phase
    chỉ đổi giao diện (không cần restart worker) — đây là chủ đích, không
    phải lỗi lệch phiên bản.
- **Staging thật, tách biệt production**: hạ tầng riêng (ghi nhận qua lịch
  sử vận hành: web `search-tools-staging.service`, worker dùng chung tên
  service `search-tools-import-worker.service`, thư mục `/srv/search-tools`,
  database `search_tools_staging`). Quy trình chuẩn: xong 1 phase → deploy
  staging (cùng mô hình preflight/prepare/cutover) → UAT trên staging → chỉ
  sau khi đạt mới lặp lại quy trình cho production.
- Backup: `pg_dump` đầy đủ trước mỗi migration/thao tác lớn, nằm trong bước
  cutover ở trên (không có script backup độc lập trong repo này).
- Quy mô dữ liệu production: **~1,1 triệu dòng `products`** (xác nhận đúng
  bởi người dùng, 2026-09-27; khớp lịch sử vận hành cho thấy tăng dần từ
  ~1,08 triệu quanh 2026-09-09 lên ~1,1 triệu quanh 2026-09-16 trở đi), dự
  kiến tiếp tục tăng tới khoảng 1–1,5 triệu; `stock_items` đang active hiện
  khoảng 800-900 dòng, brand khoảng 130-140 — các số tồn kho/brand đổi khá
  nhanh theo từng lần nạp, không dùng số cụ thể để quyết định kỹ thuật mới
  mà không đo lại.

## Quyết định kỹ thuật đã thấy trong code

- **Fail-closed khi thiếu dữ liệu giá/tỷ giá/brand**: không có brand hoặc
  brand chưa gán currency hoặc currency chưa có tỷ giá → giá hiển thị "không
  khả dụng", không bao giờ ngầm định tỷ giá = 1 (xem `currency_rates.py`).
- **Snapshot bất biến cho tồn kho** thay vì UPDATE tại chỗ, để giữ lịch sử và
  tránh đọc/ghi xung đột giữa các luồng (search đọc, import/quick-edit ghi).
- **Preview-then-confirm token trong PostgreSQL** (không phải session/biến
  process) cho mọi thao tác rủi ro cao, để đúng cả khi Gunicorn chạy nhiều
  worker process.
- **Redact ở tầng response, không chỉ ở UI**: quyền field bị từ chối bị xóa
  khỏi JSON trả về (kể cả alias lồng nhau) trước khi rời server.
- **Advisory lock (`pg_advisory_xact_lock`)** dùng làm khóa "miền" cho từng
  nhóm thao tác cạnh tranh (products import, regulatory, stock, tài khoản
  admin cuối cùng) thay vì khóa dòng đơn lẻ, để tránh race giữa nhiều luồng
  ghi đồng thời.
- **Không dùng ORM**: toàn bộ truy vấn là SQL thuần qua `psycopg2`.
- **Không có frontend build step**: JS/CSS phục vụ trực tiếp từ `static/`,
  không bundler/framework.

## Khi sửa kiến trúc

### Bổ sung local — regulatory-manual-edit (2026-09-28)

Trên branch `feature/regulatory-manual-edit`, chưa phát hành: service
`regulatory_manual.py` dùng advisory lock regulatory hiện có, kiểm tra quyền
sống, expected revision và audit request_id chống gửi lặp. Migration 033
thêm metadata protection/revision, bảng khóa lịch sử và bảng audit tay;
không bảng preview tay. UI preview trước/sau, server xác thực lại khi confirm.
Import giữ current/historical keys được bảo vệ, fingerprint v2; snapshot chi
tiết phân trang nằm trong JSONB job/event sẵn có. Không thay resolver hoặc
đếm products. Verifier PASS local; vận hành/rollback xem
`docs/regulatory-manual-edit/OPERATIONS.md`. PO đã xác nhận UAT browser local
8/8 đạt và hiển thị 4/4 đạt; polish qua verifier scope riêng, chưa production.

### Nguyên tắc chung

Giữ nguyên các quyết định trên khi sửa project trừ khi có yêu cầu thay đổi rõ
ràng. Đề xuất thay đổi lớn (đổi cơ chế phân quyền, đổi mô hình tồn kho, thêm
dependency mới...) theo quy trình phê duyệt trong
`docs/DEVELOPMENT_WORKFLOW.md`, kèm rủi ro và cách rollback.
