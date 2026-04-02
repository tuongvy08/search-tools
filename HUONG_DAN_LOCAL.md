# Hướng dẫn chạy thử trên máy (từng bước nhỏ)

Làm **lần lượt** theo thứ tự. Bỏ qua bước nào đã xong rồi.

**Phân quyền theo team + cập nhật Excel nâng cao:** xem thêm **`HUONG_DAN_CAP_NHAT_VA_RBAC.md`**.

---

## Phần A — Chuẩn bị (một lần)

### Bước 1: Cài Docker Desktop (macOS)

1. Vào trang Docker Desktop cho Mac (bản đúng CPU: Apple Silicon hoặc Intel).
2. Cài đặt và mở **Docker Desktop**; đợi trạng thái sẵn sàng (biểu tượng cá voi, không báo lỗi đỏ).

### Bước 2: Mở Terminal đúng thư mục project

1. Cursor: **Terminal → New Terminal** (hoặc mở **Terminal.app**).
2. Vào thư mục chứa code (sửa đường dẫn nếu project bạn để chỗ khác):

```bash
cd ~/Documents/Mytools/search-tools
```

Mọi lệnh sau giả định bạn **luôn đang ở** thư mục `search-tools` (có thư mục `sql`, `scripts`, file `search.py`).

### Bước 3: Tạo môi trường Python

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Sau bước này, đầu dòng Terminal thường có `(.venv)`.

```bash
pip install -r requirements.txt
```

---

## Phần B — PostgreSQL chỉ trên máy (Docker)

### Bước 4: Bật PostgreSQL bằng Docker

```bash
cd ~/Documents/Mytools/search-tools
docker compose up -d
```

Kiểm tra:

```bash
docker compose ps
```

Service `db` phải **running**. Thông tin đăng nhập mặc định (trùng `docker-compose.yml`):

| | |
|---|---|
| User | `searchlocal` |
| Mật khẩu | `searchlocal` |
| Database | `products_local` |
| Cổng | `5432` |

### Bước 5: Tạo file `.env`

1. Trong thư mục `search-tools`, tạo file **`.env`** (có dấu chấm ở đầu tên file).
2. Dán nội dung (có thể chỉnh mật khẩu đăng nhập web):

```
PORT=5001
DATABASE_URL=postgresql://searchlocal:searchlocal@127.0.0.1:5432/products_local
FLASK_SECRET_KEY=dev-local-key-123
APP_PASSWORD_MANAGER=Truong@2004
APP_PASSWORD_STAFF=Truong@123
DISABLE_IP_ALLOWLIST=1
```

3. Lưu file. (Mặc định dùng cổng **5001** để tránh trùng cổng 5000 trên macOS.)

**Lưu ý:** Không commit file `.env` lên Git (đã có trong `.gitignore`).

**Nạp `.env` vào Terminal** (mỗi lần mở tab Terminal mới có thể cần lại):

```bash
cd ~/Documents/Mytools/search-tools
set -a && source .env && set +a
```

---

## Phần C — Tạo bảng + dữ liệu mẫu

### Bước 6: Chạy `sql/schema.sql` (tạo bảng `products`)

Bạn cần chạy **một lần** để có bảng `products`.

**Cách 1 — Không cài `psql` trên Mac (khuyến nghị nếu gặp `command not found: psql`):**

```bash
cd ~/Documents/Mytools/search-tools
docker compose exec -T db psql -U searchlocal -d products_local < sql/schema.sql
```

**Cách 2 — Đã cài `psql`** (ví dụ `brew install libpq`):

```bash
cd ~/Documents/Mytools/search-tools
set -a && source .env && set +a
psql "$DATABASE_URL" -f sql/schema.sql
```

Không báo lỗi là được.

### Bước 6b (tuỳ chọn — phân quyền team)

Nếu bạn dùng tính năng team/brand và user trong database, chạy thêm:

```bash
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_002_team_rbac.sql
```

Chi tiết đầy đủ: **`HUONG_DAN_CAP_NHAT_VA_RBAC.md`**.

### Bước 6c (tuỳ chọn — regulatory, import audit, tỷ giá DB, IP văn phòng)

Nếu bạn dùng admin import, tỷ giá sửa trên web, hoặc giới hạn IP, chạy lần lượt (bỏ qua file đã chạy trước đó):

```bash
cd ~/Documents/Mytools/search-tools
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_003_regulatory_rules.sql
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_004_import_jobs.sql
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_005_exchange_rates.sql
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_006_office_ip_allowlist.sql
```

Trên máy dev, trong `.env` nên có `DISABLE_IP_ALLOWLIST=1` để không tự khóa chính mình.

### Bước 7: Nạp dữ liệu mẫu (4 dòng test)

**Docker:**

```bash
cd ~/Documents/Mytools/search-tools
docker compose exec -T db psql -U searchlocal -d products_local < sql/seed_test.sql
```

**Có `psql` trên máy:**

```bash
set -a && source .env && set +a
psql "$DATABASE_URL" -f sql/seed_test.sql
```

File `seed_test.sql` sẽ **xóa hết** sản phẩm trong bảng rồi thêm **4 dòng mẫu** — chỉ phù hợp **DB test**.

---

## Phần D — Chạy app và thử trình duyệt

### Bước 8: Chạy Flask

```bash
cd ~/Documents/Mytools/search-tools
source .venv/bin/activate
set -a && source .env && set +a
python search.py
```

Terminal sẽ hiện dòng kiểu `Running on http://127.0.0.1:5000` hoặc **5001** nếu bạn đặt `PORT=5001` trong `.env`.

### Bước 9: Mở trình duyệt

1. Mở đúng địa chỉ: `http://127.0.0.1:5000` **hoặc** `http://127.0.0.1:5001` (khớp với `PORT` và dòng “Running on…”).
2. Đăng nhập:
   - **Kiểu cũ:** để trống ô username (nếu form có), chỉ nhập mật khẩu trong `.env` (`APP_PASSWORD_MANAGER` hoặc `APP_PASSWORD_STAFF`).
   - **User trong DB:** nhập username + password (sau khi đã chạy `bootstrap_admin.py` — xem `HUONG_DAN_CAP_NHAT_VA_RBAC.md`).
3. Tìm thử: `Acetone` hoặc `67-64-1` — phải có kết quả nếu seed đã chạy.

Nếu lỗi: copy **toàn bộ** thông báo trong Terminal (và trình duyệt nếu có) để xử lý tiếp.

---

## Phần E — Sau khi test ổn: dữ liệu thật

### Cách 1: Chuyển từ file SQLite cũ (`.db`)

1. Copy `products.db` vào máy (ví dụ `~/Downloads/products.db`).
2. Trong Terminal:

```bash
cd ~/Documents/Mytools/search-tools
source .venv/bin/activate
set -a && source .env && set +a
python scripts/migrate_sqlite_to_postgres.py ~/Downloads/products.db
```

Script **xóa hết** sản phẩm trong Postgres rồi copy từ SQLite.

### Cách 2: Nhập từ Excel

1. Dòng đầu sheet đúng tên cột:

| name | code | cas | brand | size | ship | price | note |
|------|------|-----|-------|------|------|-------|------|

2. Chạy (đổi đường dẫn file):

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/import_excel.py ~/Desktop/du_lieu.xlsx
```

Chỉ thêm không xóa: thêm `--append`.  
Cập nhật theo brand (xóa đúng brand trong file rồi import): `--replace-brands-from-file` — xem `HUONG_DAN_CAP_NHAT_VA_RBAC.md`.

---

## Gặp lỗi nhanh

| Triệu chứng | Gợi ý |
|-------------|--------|
| `command not found: psql` | Dùng lệnh `docker compose exec -T db psql ... < sql/...` trong mục Bước 6–7, không cần cài `psql`. |
| Docker không chạy | Mở Docker Desktop, rồi `docker compose up -d`. |
| Cổng bị chiếm | Đặt `PORT=5001` trong `.env`, mở đúng cổng đó. |
| Python không tìm thấy module | `source .venv/bin/activate` trước khi `python search.py`. |

Chi tiết thêm: **`HUONG_DAN_CAP_NHAT_VA_RBAC.md`** mục “Gặp lỗi thường gặp”.

---

## Tóm tắt thứ tự nhanh

1. Docker → `docker compose up -d`
2. `.env` (có `DATABASE_URL` + tuỳ chọn `PORT`)
3. `sql/schema.sql` → tạo bảng (Docker hoặc `psql`)
4. (Tuỳ chọn) `sql/migration_002_team_rbac.sql` + hướng dẫn RBAC
5. `sql/seed_test.sql` → dữ liệu mẫu
6. `python search.py` → thử trình duyệt
7. Khi ổn: `migrate_sqlite_to_postgres.py` hoặc `import_excel.py`

---

## Khi nào đưa lên Vultr?

Chỉ khi **trên máy đã chạy ổn** với dữ liệu thật hoặc bản copy. Trên server: tạo `.env` riêng, **backup DB production** trước khi migration/import lớn, rồi deploy — không đụng production cho đến khi bạn chủ động cập nhật.
