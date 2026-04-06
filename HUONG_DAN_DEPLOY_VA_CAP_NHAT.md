# Hướng dẫn cập nhật & vận hành (Mac → GitHub → Vultr)

Tài liệu dùng **mỗi khi** sửa code, sửa lỗi, hoặc triển khai bản mới. Chỉ cần làm **theo thứ tự** và copy khối lệnh tương ứng.

---

## Mục lục

1. [Luồng tổng quát](#1-luồng-tổng-quát)
2. [Máy Mac — sau khi sửa code](#2-máy-mac--sau-khi-sửa-code)
3. [Khi `git push` bị từ chối (remote có commit mới)](#3-khi-git-push-bị-từ-chối-remote-có-commit-mới)
4. [Khi rebase bị conflict](#4-khi-rebase-bị-conflict)
5. [VPS Vultr — cập nhật ứng dụng](#5-vps-vultr--cập-nhật-ứng-dụng)
6. [Migration PostgreSQL (khi có file SQL mới)](#6-migration-postgresql-khi-có-file-sql-mới)
7. [Backup DB nhanh trước thay đổi lớn](#7-backup-db-nhanh-trước-thay-đổi-lớn)
8. [Gunicorn & Nginx — tránh 502 / timeout](#8-gunicorn--nginx--tránh-502--timeout)
9. [Kiểm tra sau deploy](#9-kiểm-tra-sau-deploy)
10. [Gỡ lỗi nhanh](#10-gỡ-lỗi-nhanh)

---

## 1. Luồng tổng quát

```
Sửa code trên Mac → commit → push origin main
       ↓
SSH vào Vultr → cd /opt/search-tools-pg → git pull → pip (nếu cần) → migration SQL (nếu có) → restart service
       ↓
Trình duyệt: hard refresh (Ctrl+F5 / Cmd+Shift+R)
```

Đường dẫn deploy mặc định trong tài liệu: **`/opt/search-tools-pg`**. Nếu server bạn đặt khác, thay trong mọi lệnh.

Service systemd mặc định: **`search-tools-pg`**.

---

## 2. Máy Mac — sau khi sửa code

```bash
cd /Users/truong/Documents/Mytools/search-tools
git status
git add -A
git commit -m "Mô tả ngắn: ví dụ sửa Find code, thêm migration 008"
git push origin main
```

Chỉ add từng file (khi không muốn commit hết):

```bash
cd /Users/truong/Documents/Mytools/search-tools
git add search.py templates/index.html static/script.js
git commit -m "Mô tả thay đổi"
git push origin main
```

---

## 3. Khi `git push` bị từ chối (remote có commit mới)

```bash
cd /Users/truong/Documents/Mytools/search-tools
git pull --rebase origin main
git push origin main
```

---

## 4. Khi rebase bị conflict

Sửa file có dòng `<<<<<<<` / `=======` / `>>>>>>>`, xóa marker và giữ nội dung đúng, rồi:

```bash
cd /Users/truong/Documents/Mytools/search-tools
git add <đường-dẫn-file-đã-sửa>
git rebase --continue
```

Lặp cho tới khi rebase xong, sau đó:

```bash
git push origin main
```

Hủy rebase (quay lại trước `pull --rebase`):

```bash
git rebase --abort
```

---

## 5. VPS Vultr — cập nhật ứng dụng

SSH vào server, rồi:

```bash
cd /opt/search-tools-pg

# (Một lần trên server, nếu từng báo dubious ownership)
git config --global --add safe.directory /opt/search-tools-pg

git fetch origin
git pull origin main

source .venv/bin/activate
pip install -r requirements.txt
deactivate

sudo systemctl restart search-tools-pg
sudo systemctl status search-tools-pg --no-pager
```

---

## 6. Migration PostgreSQL (khi có file SQL mới)

Chạy **sau** `git pull`, **trước** hoặc **sau** restart (thường trước restart cũng được). Thay tên file cho đúng bản bạn thêm (ví dụ `migration_007_...`, `migration_008_...`).

```bash
cd /opt/search-tools-pg
set -a && source .env && set +a
psql "$DATABASE_URL" -f sql/migration_007_products_code_upper_trim_index.sql
psql "$DATABASE_URL" -c "ANALYZE products;"
```

Nhiều file migration (lần lượt):

```bash
cd /opt/search-tools-pg
set -a && source .env && set +a
psql "$DATABASE_URL" -f sql/migration_007_products_code_upper_trim_index.sql
psql "$DATABASE_URL" -f sql/migration_008_check_cas_perf_indexes.sql
# psql "$DATABASE_URL" -f sql/migration_009_....sql
psql "$DATABASE_URL" -c "ANALYZE products;"
psql "$DATABASE_URL" -c "ANALYZE regulatory_rules;"
```

---

## 7. Backup DB nhanh trước thay đổi lớn

```bash
sudo /usr/local/bin/backup_search_tools_pg.sh
ls -lh /opt/backups/search-tools-pg
```

(Nếu chưa có script backup, tạo theo hướng dẫn đã dùng trước đó hoặc dùng lệnh một dòng:)

```bash
cd /opt/search-tools-pg
set -a && source .env && set +a
mkdir -p /opt/backups/search-tools-pg
pg_dump "$DATABASE_URL" -Fc -f "/opt/backups/search-tools-pg/pg_$(date +%F_%H%M%S).dump"
```

---

## 8. Gunicorn & Nginx — tránh 502 / timeout

### Gunicorn (systemd)

Xem cấu hình hiện tại:

```bash
sudo systemctl cat search-tools-pg
```

Tạo override (chỉnh lại `ExecStart`/`User`/`bind` cho khớp server):

```bash
sudo systemctl edit search-tools-pg
```

Ví dụ nội dung override (sửa cho đúng máy bạn):

```ini
[Service]
ExecStart=
ExecStart=/opt/search-tools-pg/.venv/bin/gunicorn --workers 3 --timeout 120 --bind 127.0.0.1:5001 search:app
```

Áp dụng:

```bash
sudo systemctl daemon-reload
sudo systemctl restart search-tools-pg
```

### Nginx (chờ upstream lâu)

Trong `location` proxy tới app, thêm (hoặc tăng giá trị):

```nginx
proxy_connect_timeout 120s;
proxy_send_timeout 120s;
proxy_read_timeout 120s;
```

Kiểm tra và nạp lại:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Kiểm tra sau deploy

Trên VPS — app có trả lời không:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5001/login
```

Static có chống cache (sau bản có `after_request` no-store cho `.js`/`.css`):

```bash
curl -sI "http://127.0.0.1:5001/static/script.js" | grep -i cache
```

Trình duyệt: mở app → **hard refresh** (tải lại bỏ cache).

---

## 10. Gỡ lỗi nhanh

Log ứng dụng:

```bash
sudo journalctl -u search-tools-pg -n 100 --no-pager
```

Theo dõi realtime:

```bash
sudo journalctl -u search-tools-pg -f
```

Nginx:

```bash
sudo tail -n 80 /var/log/nginx/error.log
```

Kết nối DB:

```bash
cd /opt/search-tools-pg
set -a && source .env && set +a
psql "$DATABASE_URL" -c "SELECT 1;"
```

---

## Checklist một dòng (copy khi quen tay)

**Mac:** `cd ~/Documents/Mytools/search-tools` → `git add -A` → `git commit -m "..."` → `git push origin main`  
**Vultr:** `cd /opt/search-tools-pg` → `git pull origin main` → `source .venv/bin/activate && pip install -r requirements.txt && deactivate` → (SQL nếu có) → `sudo systemctl restart search-tools-pg` → **hard refresh** trình duyệt.

---

## Tài liệu liên quan trong repo

- Chạy thử local + Docker Postgres: **`HUONG_DAN_LOCAL.md`**
- RBAC, import, team: **`HUONG_DAN_CAP_NHAT_VA_RBAC.md`**
- Biến môi trường mẫu: **`.env.example`**
