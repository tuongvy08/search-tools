# Phase 6C3 — Quản lý sản phẩm

**PRODUCTION HOLD.** Staging đã deploy application/SQL candidate
`c3feee313c2ba1bc1ea50fe74819ee5af95ca97e`; chưa merge `main` và chưa thực
hiện backup/stop/migrate/deploy/delete nào trên production.

## Staging execution record — 2026-09-09

- Backup trước migration: `/srv/backups/search-tools/phase6c3-pre.mrnAam`.
- Migration 025 hoàn tất: bốn bảng Phase 6C3 hiện diện và index
  `idx_products_admin_brand_id` valid.
- `search-tools-staging.service` và `search-tools-import-worker.service` active;
  worker có hai process.
- UAT do operator xác nhận: search, filter, pagination, detail, form và mobile
  access đạt.
- Không ghi nhận như đã chạy: single/brand delete/restore trên dữ liệu staging
  thật hoặc kiểm tra bằng staff account. Các case này chỉ có automated coverage
  trên PostgreSQL database tạm; không được diễn giải thành staging UAT.

## Read-only staging preflight reference

Chạy nguyên khối dưới đây trên staging. Khối nằm trong subshell nên lỗi không
đóng SSH hiện tại. Nó không in DSN, không bật tracing, tắt pager, lấy database từ
môi trường của **cả web lẫn worker đang chạy**, và không dùng `.env` hay một
release cũ làm nguồn sự thật.

Live staging đã được xác nhận là repository `/srv/search-tools` (chính xác, không
phải `/srv/search-tools/releases/*`), web `search-tools-staging.service`, worker
`search-tools-import-worker.service`, user `deploy` và database
`search_tools_staging`. `CANDIDATE_RELEASE` là checkout candidate được review;
không đồng nhất nó với live repository hoặc thư mục immutable production chỉ vì
tên đường dẫn trông giống nhau.

```bash
(
  set -euo pipefail
  set +x
  export SYSTEMD_PAGER=cat GIT_PAGER=cat PAGER=cat
  WEB_SERVICE='search-tools-staging.service'
  WORKER_SERVICE='search-tools-import-worker.service'
  LIVE_REPO='/srv/search-tools'
  EXPECTED_USER='deploy'
  EXPECTED_DB='search_tools_staging'
  : "${CANDIDATE_RELEASE:?Đặt đường dẫn checkout candidate đã review}"
  : "${EXPECTED_COMMIT:?Đặt full commit SHA đã review}"

  case "$CANDIDATE_RELEASE" in
    /*) ;;
    *) echo "Candidate phải là đường dẫn tuyệt đối" >&2; exit 1 ;;
  esac
  CANDIDATE_REAL="$(sudo readlink -f "$CANDIDATE_RELEASE")"
  test "$CANDIDATE_REAL" != "$LIVE_REPO"
  test "$CANDIDATE_REAL" != '/opt/search-tools-pg'
  test -f "$CANDIDATE_REAL/sql/migration_025_admin_product_management.sql"

  WEB_PID="$(sudo systemctl show "$WEB_SERVICE" -p MainPID --value)"
  WORKER_PID="$(sudo systemctl show "$WORKER_SERVICE" -p MainPID --value)"
  case "$WEB_PID" in ''|*[!0-9]*|0) echo "Web MainPID không hợp lệ" >&2; exit 1;; esac
  case "$WORKER_PID" in ''|*[!0-9]*|0) echo "Worker MainPID không hợp lệ" >&2; exit 1;; esac

  WEB_CWD="$(sudo readlink -f "/proc/$WEB_PID/cwd")"
  WORKER_CWD="$(sudo readlink -f "/proc/$WORKER_PID/cwd")"
  test "$WEB_CWD" = "$LIVE_REPO"
  test "$WORKER_CWD" = "$LIVE_REPO"

  WEB_USER="$(sudo systemctl show "$WEB_SERVICE" -p User --value)"
  WORKER_USER="$(sudo systemctl show "$WORKER_SERVICE" -p User --value)"
  test "$WEB_USER" = "$EXPECTED_USER" && test "$WORKER_USER" = "$EXPECTED_USER"
  test "$(stat -c %U "$CANDIDATE_REAL")" = "$EXPECTED_USER"
  CANDIDATE_COMMIT="$(sudo -u "$WEB_USER" env GIT_PAGER=cat git -C "$CANDIDATE_REAL" rev-parse HEAD)"
  test "$CANDIDATE_COMMIT" = "$EXPECTED_COMMIT"
  sudo systemctl cat "$WEB_SERVICE" "$WORKER_SERVICE" >/dev/null

  WEB_DATABASE_URL="$(sudo sh -c 'tr "\0" "\n" < "/proc/$1/environ"' sh "$WEB_PID" | sed -n 's/^DATABASE_URL=//p')"
  WORKER_DATABASE_URL="$(sudo sh -c 'tr "\0" "\n" < "/proc/$1/environ"' sh "$WORKER_PID" | sed -n 's/^DATABASE_URL=//p')"
  test -n "$WEB_DATABASE_URL" && test -n "$WORKER_DATABASE_URL"
  test "$WEB_DATABASE_URL" = "$WORKER_DATABASE_URL"

  WEB_DB="$(psql --dbname="$WEB_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc 'SELECT current_database()')"
  WORKER_DB="$(psql --dbname="$WORKER_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc 'SELECT current_database()')"
  test "$WEB_DB" = "$EXPECTED_DB" && test "$WORKER_DB" = "$EXPECTED_DB"
  READY="$(psql --dbname="$WEB_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('products') IS NOT NULL AND to_regclass('app_users') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='products' AND column_name='source_brand')")"
  test "$READY" = t

  unset WEB_DATABASE_URL WORKER_DATABASE_URL
  printf 'Read-only preflight passed: commit %s, database %s\n' "$EXPECTED_COMMIT" "$EXPECTED_DB"
)
```

Không tiếp tục nếu PID, cwd, owner, commit, DSN web/worker hoặc tên database
không khớp chính xác. Điều tra effective unit/environment thay vì sửa lệnh để bỏ
qua assertion.

## Read-only production preflight — chưa chạy

Lần quan sát gần nhất cho thấy production live tại
`/opt/search-tools-pg-release-20260909T032032Z-24ac6d4-clean`, nhưng lệnh dưới
đây cố ý không hardcode release đó. Nó lấy working directory và `DATABASE_URL`
từ PID web/worker đang chạy, không đọc `.env`, không in DSN và không mutate.

```bash
(
  set -euo pipefail
  set +x
  export SYSTEMD_PAGER=cat GIT_PAGER=cat PAGER=cat
  WEB_SERVICE='search-tools-pg.service'
  WORKER_SERVICE='search-tools-import-worker.service'
  EXPECTED_LIVE_COMMIT='24ac6d4860e401efb2ba9c8e490ac204dad770dc'
  EXPECTED_DB='searchtools_pg_r1_rollback_20260906_153842'

  sudo systemctl is-active --quiet "$WEB_SERVICE"
  sudo systemctl is-active --quiet "$WORKER_SERVICE"
  WEB_PID="$(sudo systemctl show "$WEB_SERVICE" -p MainPID --value)"
  WORKER_PID="$(sudo systemctl show "$WORKER_SERVICE" -p MainPID --value)"
  case "$WEB_PID" in ''|*[!0-9]*|0) echo "Web MainPID không hợp lệ" >&2; exit 1;; esac
  case "$WORKER_PID" in ''|*[!0-9]*|0) echo "Worker MainPID không hợp lệ" >&2; exit 1;; esac

  WEB_CWD="$(sudo readlink -f "/proc/$WEB_PID/cwd")"
  WORKER_CWD="$(sudo readlink -f "/proc/$WORKER_PID/cwd")"
  test "$WEB_CWD" = "$WORKER_CWD"
  case "$WEB_CWD" in
    /opt/search-tools-pg-release-*) ;;
    *) echo "Live cwd không thuộc immutable production release root" >&2; exit 1 ;;
  esac
  LIVE_COMMIT="$(sudo env GIT_PAGER=cat git -c safe.directory="$WEB_CWD" -C "$WEB_CWD" rev-parse HEAD)"
  test "$LIVE_COMMIT" = "$EXPECTED_LIVE_COMMIT"
  sudo systemctl cat "$WEB_SERVICE" "$WORKER_SERVICE" >/dev/null

  WEB_DATABASE_URL="$(sudo sh -c 'tr "\0" "\n" < "/proc/$1/environ"' sh "$WEB_PID" | sed -n 's/^DATABASE_URL=//p')"
  WORKER_DATABASE_URL="$(sudo sh -c 'tr "\0" "\n" < "/proc/$1/environ"' sh "$WORKER_PID" | sed -n 's/^DATABASE_URL=//p')"
  test -n "$WEB_DATABASE_URL" && test -n "$WORKER_DATABASE_URL"
  test "$WEB_DATABASE_URL" = "$WORKER_DATABASE_URL"

  WEB_DB="$(psql --dbname="$WEB_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc 'SELECT current_database()')"
  WORKER_DB="$(psql --dbname="$WORKER_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc 'SELECT current_database()')"
  test "$WEB_DB" = "$EXPECTED_DB" && test "$WORKER_DB" = "$EXPECTED_DB"
  READY="$(psql --dbname="$WEB_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc "SELECT to_regclass('products') IS NOT NULL AND to_regclass('app_users') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='products' AND column_name='source_brand')")"
  test "$READY" = t

  unset WEB_DATABASE_URL WORKER_DATABASE_URL
  printf 'Production read-only preflight passed: live commit %s, database %s\n' "$LIVE_COMMIT" "$WEB_DB"
)
```

Output chỉ xác nhận commit và tên database. Nếu service/PID/cwd/commit/DSN hoặc
database không khớp, dừng; không sửa assertion và không chuyển sang `.env`.

## Staging migration/recovery procedure — execution complete

1. Backup đúng database staging theo quy trình hiện có. Chuẩn bị dung lượng cho
   backup, index và WAL.
2. Kiểm tra catalog chỉ đọc trước. Nếu index chưa tồn tại, câu lệnh trả `0` hàng.
   Nếu tồn tại, nó phải là valid/ready, non-unique, không predicate/expression và
   có key `(brand,id)`.

   ```bash
   psql --dbname="$LIVE_DATABASE_URL" -X -v ON_ERROR_STOP=1 -P pager=off -c "SELECT i.indisvalid,i.indisready,i.indisunique,i.indpred IS NULL AS no_predicate,i.indexprs IS NULL AS no_expression,pg_get_indexdef(i.indexrelid) FROM pg_index i WHERE i.indexrelid=to_regclass('idx_products_admin_brand_id')"
   ```

3. Nếu build trước bị ngắt và catalog xác nhận **đúng index này** invalid hoặc
   sai definition, dừng và xin duyệt recovery. Chỉ sau duyệt mới chạy riêng:

   ```bash
   psql --dbname="$LIVE_DATABASE_URL" -X -v ON_ERROR_STOP=1 -c 'DROP INDEX CONCURRENTLY idx_products_admin_brand_id'
   ```

   Không tự động xóa index, dữ liệu, batch hay backup.
4. Chạy migration bằng DSN đã lấy và xác minh từ process staging:

   ```bash
   psql --dbname="$LIVE_DATABASE_URL" -X -v ON_ERROR_STOP=1 -f "$CANDIDATE_RELEASE/sql/migration_025_admin_product_management.sql"
   ```

   Không thêm `-1` hoặc `--single-transaction`: index `(brand,id)` dùng
   `CREATE INDEX CONCURRENTLY` và cố ý nằm ngoài transaction DDL của các bảng.
   Migration tự fail closed nếu index cùng tên invalid hoặc sai definition.
5. Deploy code, restart cả web và worker theo quy trình staging, rồi chạy lại
   read-only preflight với working directory/commit mới.
6. UAT: admin/staff access, search/code/CAS, canonical-brand filter,
   next/previous, create/update, same-code variants, stale form, single/brand
   delete, replay/TTL, positive restore, restore sau Import Center upsert, revoke
   admin trong lúc chờ lock, và concurrent Import Center apply. Chụp 1440/390.

## Dung lượng và phục hồi

Xóa brand copy toàn bộ row sang `product_deleted_rows` rồi mới xóa exact IDs
trong cùng transaction. Không load catalog vào Gunicorn memory. Transaction lỗi
sẽ rollback cả backup lẫn delete.

Restore brand chỉ chạy khi brand chưa có product mới. Restore single còn yêu cầu
ID chưa được dùng lại **và** identity của backup không trùng/mơ hồ theo Brand
Gateway (`code + canonical brand`, rồi `source_brand`, `size`). Nếu không đạt,
hệ thống fail closed và giữ backup để reconciliation thủ công. Backup không tự
hết hạn ở Phase 6C3; retention cần business owner phê duyệt.

## Rollback

- Rollback ứng dụng: chuyển web/worker về release trước. Bảng/index 025 là
  additive nên để nguyên; Import Center/Search cũ không phụ thuộc chúng.
- Rollback dữ liệu: dùng nút **Khôi phục** của đúng batch khi điều kiện không
  chồng lấp đạt. Đối chiếu `row_count`, audit và product count sau restore.
- Không `DROP` Brand Master, aliases, grants, regulatory rules, import history,
  `product_delete_batches` hay `product_deleted_rows`. Gỡ schema sau retention
  cần ticket, backup và duyệt riêng.
