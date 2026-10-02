# Bảo vệ dữ liệu và production

Áp dụng cùng [AGENTS.md](AGENTS.md) và [quy trình phát triển](docs/DEVELOPMENT_WORKFLOW.md).

## Môi trường

Local/development dùng database development hoặc test tách biệt. Trước thao tác ghi dữ liệu, xác minh đích kết nối bằng thông tin không chứa secret; không mặc định file cấu hình hoặc container đang trỏ tới local.

Chỉ tạo/thử/rollback migration trong môi trường development được giao sau khi xác nhận dữ liệu và phạm vi. Chỉ reset dữ liệu test khi đã xác nhận đó là dữ liệu có thể bỏ và thao tác được cho phép. Dữ liệu local của người dùng cũng cần được bảo vệ.

## Production mặc định được bảo vệ

Quyền sửa code local không bao gồm quyền deploy hoặc truy cập production. Khi chưa được phê duyệt rõ, không SSH, restart service/container, deploy, chạy migration/script, sửa dữ liệu hoặc `.env`, đổi DNS/Nginx, xóa file hay thay secret/API key trên production.

Không tự DROP, TRUNCATE, DELETE hàng loạt, ALTER dữ liệu quan trọng hoặc sửa trực tiếp database production. Trước bước có rủi ro, trình bày mục tiêu, môi trường, phạm vi ảnh hưởng, migration/thao tác dự kiến, backup có thể phục hồi, kiểm tra trước/sau và rollback. Chỉ thực hiện phần được phê duyệt, trong đúng môi trường và thời điểm được phép.

Git là cơ chế quay lại phiên bản mã nguồn, không phải backup database. Nếu rollback không thể hoàn tác hoàn toàn, phải nói rõ trước khi thực hiện.

## Auth và dependency

Không bỏ validation, authentication, authorization hoặc security check chỉ để làm hết lỗi. Thay đổi auth/permissions/payment có rủi ro cao cần kế hoạch, kiểm tra tương xứng và phê duyệt trước bước rủi ro theo quy trình.

Trước dependency mới, đánh giá maintenance, security, compatibility và giải pháp sẵn có. Không tự nâng major framework trong task sửa bug thông thường.

## Secret

Không commit `.env`, password, token, API key, private key hoặc dữ liệu nhạy cảm. Không đưa secret vào code, log, báo cáo, ảnh chụp hay issue. `.env.example`, nếu cần, chỉ chứa tên biến và giá trị giả.

`.gitignore` không bảo vệ file đã được theo dõi và không thay thế review diff. Khi phát hiện secret trong Git, báo người dùng mà không lặp lại giá trị; xác định phạm vi lộ và đề xuất thu hồi/thay secret. Thao tác trên secret production vẫn cần quyền phù hợp. Xóa khỏi bản hiện tại không xóa khỏi lịch sử; sửa lịch sử Git là thao tác riêng cần được phép.

## Báo cáo bảo mật

Search-tools chưa có kênh báo cáo bảo mật riêng. Nếu chia sẻ project cho người khác, chủ project cần bổ sung kênh riêng tư trước. Không đưa secret, dữ liệu nhạy cảm hoặc chi tiết khai thác vào issue công khai.

## Riêng của Search-tools

Search-tools là ứng dụng Flask **đang chạy production cho nhân viên công
ty**. Các quy tắc chung ở trên áp dụng đầy đủ; phần dưới đây bổ sung chi
tiết riêng của project này, không thay thế phần chung.

### Máy chủ và alias SSH — KHÔNG nhầm hai máy

| Môi trường | Lệnh SSH (alias trên Mac của PO) | Dịch vụ | Database | Ai dùng |
| --- | --- | --- | --- | --- |
| **Staging** | `ssh staging` | `search-tools-staging.service`, `search-tools-import-worker.service`; mã tại `/srv/search-tools` | `search_tools_staging` (PostgreSQL 16) | Chỉ để thử, không có người dùng thật |
| **Production** | `ssh python` | `search-tools-pg.service`, `search-tools-import-worker.service`; release tại `/opt/search-tools-pg-release-<timestamp>-<commit>-clean` | `searchtools_pg_r1_rollback_20260906_153842` (PostgreSQL 14) | **Nhân viên đang dùng** |

- Hai máy có worker **cùng tên unit** `search-tools-import-worker.service`; luôn phân biệt bằng alias SSH và tên unit web/database, không chỉ bằng tên worker.
- Lệnh cho staging không bao giờ dùng `ssh python`; lệnh cho production không bao giờ dùng `ssh staging`. Script/runbook phải ghi rõ alias đích và tự kiểm đúng máy (unit, thư mục, database) trước bước ghi.
- Quyết định PO 2026-10-02: staging được tự do triển khai/thử; production vẫn cần PO nói rõ “đồng ý bắt đầu” cho từng lần (xem `PROJECT_STATE.md`).

### Production

- Production chạy trên VPS Vultr, mỗi release nằm trong một thư mục bất
  biến riêng (`/opt/search-tools-pg-release-<timestamp>-<commit>-clean`) —
  xem mô hình deploy thực tế ở ARCHITECTURE.md mục Deploy. Mặc định
  **không** SSH, restart service, deploy, chạy migration hay sửa `.env`
  trên server này trừ khi người dùng yêu cầu rõ trong task hiện tại.
- Thực tế lịch sử vận hành: **mọi lệnh trên production/staging đều do người
  dùng tự chạy** qua script preflight (chỉ đọc) → prepare → cutover đã được
  review trước, AI chưa từng tự SSH vào production/staging của project này —
  giữ đúng nguyên tắc này khi tiếp tục làm việc trên project.
- Trước migration/import lớn trên production: yêu cầu backup đầy đủ
  (`pg_dump`, nằm trong bước cutover) và xác nhận rõ ràng của người dùng,
  theo đúng mục "Production mặc định được bảo vệ" ở trên. Luôn có staging
  thật (`search-tools-staging.service`, `/srv/search-tools`, database
  `search_tools_staging`) để UAT trước khi động tới production.
- Worker import và web **phải dùng chung một `DATABASE_URL` đã được
  review** — không suy luận DATABASE_URL đang chạy thật từ file `.env` cũ
  trên server hay từ đường dẫn release cũ, vì đã từng ghi nhận các nguồn
  trỏ tới database khác nhau; luôn đọc từ tiến trình đang chạy thật
  (`/proc`), không suy diễn.

### Secret và `.env`

- Không đọc, in hay chép nội dung `.env` (kể cả để "kiểm tra cấu hình") —
  cần biết tên biến thì đọc `.env.example`.
- Secret liên quan trực tiếp đến project này gồm: `DATABASE_URL`,
  `FLASK_SECRET_KEY`, `APP_PASSWORD_MANAGER`/`APP_PASSWORD_STAFF` (nếu bật
  legacy login), `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`,
  `ADMIN_PASSWORD` (dùng một lần cho `bootstrap_admin.py`). Không đưa các
  giá trị này vào commit, log, báo cáo hay ảnh chụp màn hình.
- `ENABLE_LEGACY_PASSWORD_LOGIN=true` bật lối đăng nhập chỉ-mật-khẩu
  (không có username, không audit theo từng người). **[xác nhận từ người
  dùng, 2026-09-27]**: hiện đang **bật** trên production, hiển thị dưới nút
  đăng nhập Google; thực tế chỉ admin dùng. Người dùng nghiêng về hướng
  **tắt nếu xác nhận không còn cần thiết** (xem PROJECT_STATE.md) — nhưng
  đây vẫn là thay đổi cấu hình production, chỉ thực hiện khi được yêu cầu rõ
  trong một task riêng, không tự tắt/bật khi chưa được giao việc đó.

### Database

- Database local là PostgreSQL qua Docker Compose (service `db`, xem
  `docker-compose.yml`), độc lập hoàn toàn với production. Không giả định
  schema local giống production — luôn kiểm tra `sql/migration_*.sql` đã áp
  dụng bản nào trước khi viết migration mới hoặc suy luận cấu trúc bảng.
  `sql/seed_test.sql` xóa sạch bảng `products` trước khi seed — chỉ chạy
  trên DB test/local, không bao giờ chạy trên production.
- Không chạy SQL phá hủy dữ liệu (`DELETE`/`TRUNCATE`/`DROP` không có
  `WHERE` rõ ràng, hoặc theo phạm vi rộng) khi chưa được duyệt, kể cả trên
  local nếu người dùng đã có dữ liệu thật ở đó.
- Nhiều thao tác admin nhạy cảm (đổi quyền team, archive team/user, xóa sản
  phẩm hàng loạt) đã có sẵn bước preview/confirm và audit log
  (`login_audit_events`, `product_admin_events`, `team_capability_history`,
  `admin_rbac_events`...) trong code — khi cần thao tác dữ liệu tương tự
  bằng tay (SQL trực tiếp), ưu tiên dùng đúng luồng admin đã có thay vì bỏ
  qua các bước kiểm tra đó.

### Deploy

- Deploy/cập nhật production theo đúng trình tự trong
  `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md`: commit trên Mac → push → SSH Vultr →
  `git pull` → cài dependency → chạy migration mới (nếu có) → restart
  service → kiểm tra sau deploy. Không rút gọn trình tự hoặc bỏ bước kiểm
  tra khi chưa được yêu cầu.
- Thay đổi cấu hình Nginx/systemd (timeout, đường dẫn upload, giới hạn kích
  thước) là thay đổi hạ tầng chia sẻ — áp dụng mức HIGH theo
  `docs/DEVELOPMENT_WORKFLOW.md`, cần phê duyệt trước khi thực hiện trên
  server thật.
