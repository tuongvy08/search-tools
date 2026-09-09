# Phase 6C3 — Quản lý sản phẩm

Chỉ triển khai local; chưa SSH, migrate, mutate UAT, deploy staging/production
hoặc merge `main`. Staging gate phải hoàn tất trước khi soạn cutover production.

## Thứ tự staging

1. Xác minh candidate release đúng commit được review. Trên host, lấy `MainPID`,
   working directory thật từ `/proc/<pid>/cwd`, và xem effective systemd unit;
   không suy ra release/database từ `/opt/search-tools-pg`, release cũ hoặc `.env`.
2. Backup database đích bằng quy trình hiện có. Migration 025 cần các cột
   `products.source_brand`, manual compliance và preparation type, cùng
   `app_users`/Brand Master đã được migrate.
3. Chạy `sql/migration_025_admin_product_management.sql` bằng plain
   `psql -X -v ON_ERROR_STOP=1 -f`. **Không** thêm `-1` hay
   `--single-transaction`: index `(brand,id)` dùng `CREATE INDEX CONCURRENTLY`
   và cố ý nằm ngoài transaction DDL của các bảng.
4. Deploy code, restart web service, rồi kiểm tra `/admin/products` bằng admin và
   staff. Import worker không cần service mới; mọi product mutation tiếp tục dùng
   advisory lock hiện có.
5. UAT trên staging: search/code/CAS, canonical-brand filter, next/previous page,
   create/update, stale form, single delete, brand delete, replay/TTL, restore,
   restore sau khi có import mới, revoke admin trong lúc chờ lock, và concurrent
   Import Center apply. Chụp 1440/390. Không production cutover trước khi gate này
   được duyệt.

Khối kiểm tra/migrate staging dưới đây không in DSN và đưa URI vào biến môi
trường `PGDATABASE`, không đặt credential trên argv. Operator phải đặt đúng tên
service và đường dẫn candidate đã duyệt:

```bash
set -euo pipefail
: "${SERVICE:?Đặt SERVICE, ví dụ search-tools-pg.service}"
: "${CANDIDATE_RELEASE:?Đặt đường dẫn candidate release đã duyệt}"
MAIN_PID="$(sudo systemctl show "$SERVICE" -p MainPID --value)"
case "$MAIN_PID" in ''|*[!0-9]*|0) echo "MainPID không hợp lệ" >&2; exit 1;; esac
LIVE_CWD="$(sudo readlink -f "/proc/$MAIN_PID/cwd")"
sudo systemctl cat "$SERVICE" >/dev/null
test -d "$LIVE_CWD" && test -f "$CANDIDATE_RELEASE/sql/migration_025_admin_product_management.sql"
LIVE_DATABASE_URL="$(sudo sh -c 'tr "\0" "\n" < "/proc/$1/environ"' sh "$MAIN_PID" | sed -n 's/^DATABASE_URL=//p')"
test -n "$LIVE_DATABASE_URL"
PGDATABASE="$LIVE_DATABASE_URL" psql -X -v ON_ERROR_STOP=1 -f "$CANDIDATE_RELEASE/sql/migration_025_admin_product_management.sql"
unset LIVE_DATABASE_URL
```

## Dung lượng và phục hồi

Xóa brand copy toàn bộ row sang `product_deleted_rows` rồi mới xóa exact IDs
trong cùng transaction. Không load catalog vào Gunicorn memory. Với brand lớn,
chuẩn bị dung lượng cho backup + index + WAL và chạy trong maintenance window;
transaction thất bại sẽ rollback cả backup lẫn delete.

Restore tự động chỉ chạy khi brand chưa có product mới (hoặc ID của single row
chưa bị dùng lại). Nếu có dữ liệu mới, hệ thống fail closed; giữ backup và xử lý
reconciliation thủ công. Backup không tự hết hạn ở Phase 6C3. Operator phải đặt
retention sau khi business owner xác nhận khả năng phục hồi; không xóa bảng/rows
backup chỉ để rollback code.

## Rollback

- Rollback ứng dụng: chuyển web service về release trước. Bảng/index 025 là
  additive nên để nguyên; Import Center/Search cũ không phụ thuộc chúng.
- Rollback dữ liệu: dùng nút **Khôi phục** của đúng batch khi điều kiện không
  chồng lấp đạt. Đối chiếu `row_count`, audit và product count sau restore.
- Không `DROP` Brand Master, aliases, grants, regulatory rules, import history,
  `product_delete_batches` hay `product_deleted_rows`. Nếu buộc gỡ schema sau khi
  hết retention, cần ticket/backup/duyệt riêng; không nằm trong staging block trên.
