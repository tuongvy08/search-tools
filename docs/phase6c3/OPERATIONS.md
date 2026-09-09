# Phase 6C3 — Quản lý sản phẩm

**RELEASE HOLD.** Chỉ triển khai local; chưa SSH, migrate, mutate UAT, deploy
staging/production hoặc merge `main`. Staging gate phải hoàn tất và được duyệt
trước khi soạn cutover production.

## Read-only staging preflight

Chạy nguyên khối dưới đây trên staging. Khối nằm trong subshell nên lỗi không
đóng SSH hiện tại. Nó không in DSN, không bật tracing, tắt pager, lấy database từ
môi trường của **cả web lẫn worker đang chạy**, và không dùng `.env` hay một
release cũ làm nguồn sự thật.

Operator phải đặt hai service, candidate release, commit và database mong đợi.
Staging/candidate phải nằm dưới `/srv/search-tools/`, tách khỏi thư mục immutable
production.

```bash
(
  set -euo pipefail
  set +x
  export SYSTEMD_PAGER=cat GIT_PAGER=cat PAGER=cat
  : "${WEB_SERVICE:?Đặt WEB_SERVICE staging}"
  : "${WORKER_SERVICE:?Đặt WORKER_SERVICE staging}"
  : "${CANDIDATE_RELEASE:?Đặt candidate dưới /srv/search-tools/releases}"
  : "${EXPECTED_COMMIT:?Đặt full commit SHA đã review}"
  : "${EXPECTED_DB:?Đặt đúng tên database staging}"

  case "$CANDIDATE_RELEASE" in
    /srv/search-tools/releases/*) ;;
    *) echo "Candidate không thuộc staging release root" >&2; exit 1 ;;
  esac
  test -f "$CANDIDATE_RELEASE/sql/migration_025_admin_product_management.sql"

  WEB_PID="$(sudo systemctl show "$WEB_SERVICE" -p MainPID --value)"
  WORKER_PID="$(sudo systemctl show "$WORKER_SERVICE" -p MainPID --value)"
  case "$WEB_PID" in ''|*[!0-9]*|0) echo "Web MainPID không hợp lệ" >&2; exit 1;; esac
  case "$WORKER_PID" in ''|*[!0-9]*|0) echo "Worker MainPID không hợp lệ" >&2; exit 1;; esac

  WEB_CWD="$(sudo readlink -f "/proc/$WEB_PID/cwd")"
  WORKER_CWD="$(sudo readlink -f "/proc/$WORKER_PID/cwd")"
  case "$WEB_CWD" in /srv/search-tools/*) ;; *) echo "Web không chạy từ staging root" >&2; exit 1;; esac
  case "$WORKER_CWD" in /srv/search-tools/*) ;; *) echo "Worker không chạy từ staging root" >&2; exit 1;; esac
  test "$WEB_CWD" = "$WORKER_CWD"

  WEB_USER="$(sudo systemctl show "$WEB_SERVICE" -p User --value)"
  WORKER_USER="$(sudo systemctl show "$WORKER_SERVICE" -p User --value)"
  test -n "$WEB_USER" && test "$WEB_USER" = "$WORKER_USER"
  test "$(stat -c %U "$CANDIDATE_RELEASE")" = "$WEB_USER"
  CANDIDATE_COMMIT="$(sudo -u "$WEB_USER" env GIT_PAGER=cat git -C "$CANDIDATE_RELEASE" rev-parse HEAD)"
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

## Migration sau khi staging preflight được duyệt

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
