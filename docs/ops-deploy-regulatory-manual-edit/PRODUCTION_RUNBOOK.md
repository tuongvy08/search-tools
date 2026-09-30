# PRODUCTION — regulatory-manual-edit / 2ec9b6c — một trang để PO duyệt

**UAT staging: PO xác nhận đạt. CHƯA CHẠY production.** Không đụng Dify/n8n/hplc/app cũ.
Đích: alias `python`; user `searchtools-pg`; web `search-tools-pg.service`;
worker `search-tools-import-worker.service`; PostgreSQL/client **14**;
DB giữ nguyên **`searchtools_pg_r1_rollback_20260906_153842`**;
backup `/var/backups/search-tools-production`.
Release cũ `/opt/search-tools-pg-release-20260927T013108Z-6155f25-clean`;
release mới riêng `/opt/search-tools-pg-release-<UTC>-2ec9b6c-clean`, **không sửa đè release cũ**.

Hai script hiện có dùng **`--production`**; bỏ flag vẫn là staging. Không sửa
EnvironmentFile, unit gốc, drop-in cũ hay shared venv. S1 giữ nguyên, PO đã
chấp nhận rủi ro; không coi S1 đã sửa. Chỉ chạy sau verifier và PO duyệt [GHI].

## Năm bước do PO tự chạy trên Mac, từng bước một

Dừng nếu bước nào lỗi; không chạy lại tự động. Sau Prepare xem diff đường dẫn
launcher đã che context; xác nhận đúng release mới trước bước 4. Ngừng mọi ghi/
upload trong cửa sổ deploy, queue=0; worker có thể nhận job ngay khi khởi động.
```bash
OPS_DIR='/Volumes/DATA/Development/_ops/search-tools'
PF='sudo -n python3 -u - search-tools-pg.service search-tools-import-worker.service /var/backups/search-tools-production'
# 1 [CHỈ ĐỌC] Preflight: đúng live/DB/PG14, 033 chưa có, job=0, đủ đĩa.
ssh python "$PF" < "$OPS_DIR/preflight_readonly.py"
# 2 [GHI] Bundle production từ BASE6155f25, không credential HTTPS.
scp -p "$OPS_DIR/search-tools-regulatory-6155f25-to-2ec9b6c.bundle" python:/tmp/search-tools-regulatory-6155f25-to-2ec9b6c.bundle
# 3 [GHI] Release MỚI + copy launcher, chỉ stage drop-in ở checkpoint.
ssh python 'sudo -n python3 -u - --production' < "$OPS_DIR/prepare_regulatory_staging.py"
# 4 [GHI] Chỉ sau khi PO duyệt diff và cửa sổ dừng dịch vụ.
ssh python 'sudo -n python3 -u - --production' < "$OPS_DIR/cutover_regulatory_staging.py"
# 5 [CHỈ ĐỌC, chỉ sau cutover] Postflight target/schema/service rồi PO UAT.
ssh python "$PF" < "$OPS_DIR/preflight_readonly.py"
```
Bước 4: stop worker → stop web → `pg_dump -Fc` + `pg_restore --list` → 033
`psql -X -v ON_ERROR_STOP=1` COMMIT → copy `zzzzzzzzzz-rme-2ec9b6c.conf`
vào đúng hai `<unit>.d` → daemon-reload → WD/ExecStart cả hai đúng release
mới → start → target/DB/schema/login200/JS hash/counts. Checkpoint, dump và
milestone ở `/var/backups/search-tools-production/regulatory-manual-edit-2ec9b6c/`.

## Nếu lỗi / thời gian dừng / nghiệm thu

Trước COMMIT: script chỉ start hai service cũ nếu chứng minh chưa COMMIT/chưa
lắp drop-in. Sau COMMIT hoặc chưa rõ: dừng hai writer, báo PO; nếu stop lỗi
sẽ báo `HOLD_STOP_UNCONFIRMED`. Giữ artifacts, không rerun/down/restore đè DB/
worker cũ; phục hồi bản mới cần PO duyệt riêng. Không đọc `.env`/in DSN.
Downtime ước tính **2–5 phút**, dự phòng **10–15**, chưa đo production, lỗi có
thể kéo dài. Sau PASS: PO mở đăng nhập, tra cứu, quản trị quy tắc; không tạo
rule giả trên production. Local/mock PASS không thay nghiệm thu trên server.
