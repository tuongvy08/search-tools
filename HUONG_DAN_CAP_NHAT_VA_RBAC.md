# Cập nhật dữ liệu & phân quyền theo team (brand)

Tài liệu này bổ sung cho **`HUONG_DAN_LOCAL.md`** (chạy Docker + Postgres trên máy). Làm **theo thứ tự**; nếu bước nào đã xong (ví dụ đã chạy `schema.sql` rồi) thì bỏ qua.

---

## Mục lục

1. [Tư vấn ngắn (ý tưởng)](#1-tư-vấn-ngắn-ý-tưởng)
2. [Trước khi chạy lệnh SQL](#2-trước-khi-chạy-lệnh-sql)
3. [Chạy file SQL trên Mac — hai cách](#3-chạy-file-sql-trên-mac--hai-cách)
4. [Lần đầu: từ bảng products đến phân quyền (checklist)](#4-lần-đầu-từ-bảng-products-đến-phân-quyền-checklist)
5. [Tạo admin, team & user](#5-tạo-admin-team--user)
6. [Gán brand cho team](#6-gán-brand-cho-team)
7. [Cập nhật dữ liệu từ Excel](#7-cập-nhật-dữ-liệu-từ-excel)
8. [Đăng nhập kiểu cũ (chỉ mật khẩu)](#8-đăng-nhập-kiểu-cũ-chỉ-mật-khẩu)
9. [Sau này: API / n8n](#9-sau-này-api--n8n)
10. [Gặp lỗi thường gặp](#10-gặp-lỗi-thường-gặp)

---

## 1. Tư vấn ngắn (ý tưởng)

- **Cập nhật từ Excel (ít khi):** dùng `import_excel.py --replace-brands-from-file` — trong DB chỉ **xóa các dòng đúng brand có trong file**, rồi chèn lại toàn bộ dòng trong file (giống logic cũ). Không cần copy file `.db` lên server.
- **Phân quyền:** cột **`brand`** trên `products` vẫn là tên brand của sản phẩm. Bảng **`team_brands`** quy định **team nào được xem brand nào** — linh hoạt hơn là gắn cứng “team” vào từng dòng sản phẩm.

---

## 2. Trước khi chạy lệnh SQL

### 2.1. Luôn vào đúng thư mục project

Mọi lệnh trong tài liệu giả định bạn đang ở thư mục **`search-tools`** (có thư mục con `sql/`, `scripts/`).

```bash
cd ~/Documents/Mytools/search-tools
```

(Nếu project của bạn nằm chỗ khác, sửa đường dẫn cho đúng.)

### 2.2. Docker phải đang chạy PostgreSQL

```bash
docker compose up -d
docker compose ps
```

Cột trạng thái service `db` phải **running**. Thông tin đăng nhập DB local (trùng với `docker-compose.yml`):

| Biến | Giá trị mặc định |
|------|------------------|
| User | `searchlocal` |
| Mật khẩu | `searchlocal` |
| Database | `products_local` |
| Cổng máy bạn | `5432` |

### 2.3. File `.env` (để Python đọc `DATABASE_URL`)

Trong `search-tools` cần có file **`.env`** với ít nhất:

```
DATABASE_URL=postgresql://searchlocal:searchlocal@127.0.0.1:5432/products_local
```

Khi chạy script Python, nạp biến môi trường:

```bash
set -a && source .env && set +a
```

Hoặc gõ từng dòng `export DATABASE_URL=...` như trong các ví dụ dưới.

### 2.4. Backup (khi đã có dữ liệu thật trên server)

Trên **máy dev chỉ có dữ liệu test** → **không bắt buộc** backup.

Trên **Vultr / production**, trước khi migration hoặc import lớn, nên dùng `pg_dump` (hoặc backup của nhà cung cấp). Chi tiết có thể bổ sung sau khi bạn deploy.

---

## 3. Chạy file SQL trên Mac — hai cách

Các file SQL nằm trong thư mục **`sql/`**:

- `sql/schema.sql` — tạo bảng `products`
- `sql/migration_002_team_rbac.sql` — thêm `teams`, `team_brands`, `app_users`
- `sql/seed_test.sql` — vài dòng mẫu (chỉ để test)

### Cách A — Có lệnh `psql` trên Mac

(Cài bằng Homebrew: `brew install libpq`, rồi có thể cần thêm `libpq` vào PATH theo hướng dẫn Homebrew.)

```bash
cd ~/Documents/Mytools/search-tools
set -a && source .env && set +a
psql "$DATABASE_URL" -f sql/schema.sql
psql "$DATABASE_URL" -f sql/migration_002_team_rbac.sql
```

### Cách B — Không cài `psql` (khuyến nghị nếu bạn gặp `command not found: psql`)

Dùng `psql` **bên trong container** Docker — không cần cài gì thêm trên Mac:

```bash
cd ~/Documents/Mytools/search-tools
docker compose exec -T db psql -U searchlocal -d products_local < sql/schema.sql
docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_002_team_rbac.sql
```

Giải thích nhanh:

- `docker compose exec -T db` — chạy lệnh trong container tên service `db`
- `-U searchlocal` — user PostgreSQL
- `-d products_local` — tên database
- `< sql/...` — đưa nội dung file SQL vào `psql`

**Lưu ý:** Chạy **`schema.sql` trước**, **`migration_002` sau** (migration cần bảng `products` đã tồn tại nếu bạn đã có dữ liệu; trên DB mới thì thứ tự vẫn đúng: schema tạo `products`, migration thêm bảng quyền).

---

## 4. Lần đầu: từ bảng products đến phân quyền (checklist)

Làm lần lượt, đánh dấu từng bước:

| # | Việc cần làm | Lệnh gợi ý (Docker, không cần `psql` trên Mac) |
|---|----------------|--------------------------------------------------|
| 1 | Docker Postgres chạy | `docker compose up -d` |
| 2 | Tạo bảng `products` | `docker compose exec -T db psql -U searchlocal -d products_local < sql/schema.sql` |
| 3 | Thêm bảng phân quyền | `docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_002_team_rbac.sql` |
| 4 | (Tuỳ chọn) Dữ liệu mẫu 4 dòng | `docker compose exec -T db psql -U searchlocal -d products_local < sql/seed_test.sql` |
| 5 | Tạo user admin đầu tiên | Xem [mục 5](#5-tạo-admin--user) |
| 6 | Gán brand cho team (để user không admin thấy dữ liệu) | Xem [mục 6](#6-gán-brand-cho-team) |
| 7 | Chạy app, đăng nhập | `source .venv/bin/activate` → `python search.py` (hoặc đặt `PORT=5001` trong `.env`) |

---

## 5. Tạo admin, team & user

### 5.1. Kích hoạt venv và biến môi trường

```bash
cd ~/Documents/Mytools/search-tools
source .venv/bin/activate
set -a && source .env && set +a
```

### 5.2. Tạo admin đầu tiên (chỉ khi `app_users` còn trống)

```bash
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='đặt-mật-khẩu-của-bạn'
python scripts/bootstrap_admin.py
```

Nếu đã có user trong `app_users`, script sẽ báo và **không** tạo thêm.

### 5.3. Tạo team (team 2, team 3, …)

Sau migration, trong bảng `teams` thường **chỉ có** một dòng (ví dụ **“Team mẫu”** → `id = 1`). Muốn có **team 2, team 3**, bạn phải **tạo thêm dòng** trong `teams` trước — **không** tự có `id = 2` nếu bạn chưa insert.

**Cách 1 — Dùng script (khuyến nghị):**

```bash
set -a && source .env && set +a
python scripts/add_team.py "Tên team 2"
python scripts/add_team.py "Tên team 3"
```

Đặt tên rõ nghĩa (ví dụ `TT Science`, `Lab Mall`, `Kế toán`). Script in ra **`id`** vừa tạo — đó là số bạn dùng trong `add_user.py`.

**Cách 2 — Một lệnh SQL (Docker, không cần `psql` trên Mac):**

```bash
docker compose exec -T db psql -U searchlocal -d products_local -c "
INSERT INTO teams (name) VALUES ('Team 2'), ('Team 3')
ON CONFLICT (name) DO NOTHING;
SELECT id, name FROM teams ORDER BY id;
"
```

(Sửa `'Team 2'`, `'Team 3'` thành tên thật của 3 team; nếu tên trùng với dòng đã có thì `ON CONFLICT` bỏ qua — dùng `SELECT` để xem `id` hiện tại.)

**Xem danh sách team và `id`:**

```bash
docker compose exec -T db psql -U searchlocal -d products_local -c "SELECT id, name FROM teams ORDER BY id;"
```

**Thứ tự đúng:** tạo team trong `teams` → (tuỳ chọn) gán `team_brands` cho team đó → rồi mới `add_user.py … team_id`.

### 5.4. Thêm user thường (không admin)

```bash
set -a && source .env && set +a
python scripts/add_user.py ten_dang_nhap mat_khau 1
```

Số cuối là **`team_id`** phải **tồn tại** trong bảng `teams` (xem mục 5.3). Ví dụ user cho team có `id = 2`:

```bash
python scripts/add_user.py labmall mat_khau 2
```

User chỉ thấy sản phẩm có `brand` nằm trong `team_brands` của team đó (sau khi bạn đã gán brand — mục 6).

---

## 6. Gán brand cho team

**Team mẫu** thường có `id = 1` sau khi chạy migration.

### Gán nhanh: mọi brand đang có trong `products` → team 1

Hữu ích khi bạn đã import hoặc seed dữ liệu và muốn **một team** nhìn thấy hết các brand đó (để test).

```bash
source .venv/bin/activate
set -a && source .env && set +a
python scripts/seed_team_brands.py 1
```

### Dữ liệu mẫu `seed_test.sql` — các brand có trong file

File **`sql/seed_test.sql`** tạo **4 dòng** sản phẩm với đúng **4 giá trị `brand`** (phải khớp ký tự khi gán `team_brands`):

| Brand trong DB mẫu | Gợi ý nội dung |
|--------------------|----------------|
| `Sigma` | Acetone thử nghiệm |
| `Merck` | Ethanol 96% |
| `CẤM NHẬP` | Dòng test cảnh báo CAS |
| `Phụ lục III` | Dòng test phụ lục |

Nếu bạn đã có team **`id` 1, 4, 5** (Team mẫu, labmall, biosciences) và muốn **cả ba team đều xem hết 4 dòng mẫu** khi đăng nhập user thường, chạy:

```bash
cd ~/Documents/Mytools/search-tools
docker compose exec -T db psql -U searchlocal -d products_local < sql/seed_team_brands_sample.sql
```

File **`sql/seed_team_brands_sample.sql`** gán đủ bốn brand trên cho `team_id` **1, 4, 5** (`ON CONFLICT DO NOTHING` nếu đã gán trước đó).

### Gán tùy chỉnh (SQL)

Ví dụ team `2` chỉ xem `Sigma` và `Merck` — chạy bằng Docker:

```bash
docker compose exec -T db psql -U searchlocal -d products_local -c "
INSERT INTO team_brands (team_id, brand) VALUES (2, 'Sigma'), (2, 'Merck')
ON CONFLICT (team_id, brand) DO NOTHING;
"
```

---

## 7. Cập nhật dữ liệu từ Excel

Dòng đầu file Excel phải có đủ cột (không phân biệt hoa thường):  
`name`, `code`, `cas`, `brand`, `size`, `ship`, `price`, `note`

```bash
cd ~/Documents/Mytools/search-tools
source .venv/bin/activate
set -a && source .env && set +a
```

**Giống logic cũ (chỉ xóa các brand có trong file, rồi import lại):**

```bash
python scripts/import_excel.py ~/Desktop/ten_file.xlsx --replace-brands-from-file
```

**Xóa toàn bộ `products` rồi import (nguy hiểm hơn — chỉ khi chắc chắn):**

```bash
python scripts/import_excel.py ~/Desktop/ten_file.xlsx
```

**Chỉ thêm dòng, không xóa trước:**

```bash
python scripts/import_excel.py ~/Desktop/ten_file.xlsx --append
```

---

## 8. Đăng nhập kiểu cũ (chỉ mật khẩu)

Trên form đăng nhập: **để trống** ô tên đăng nhập, chỉ nhập mật khẩu trong `.env`:

- `APP_PASSWORD_MANAGER` — xem mọi brand (giống admin).
- `APP_PASSWORD_STAFF` — user “staff” gắn với team `LEGACY_STAFF_TEAM_ID` (mặc định `1`); team đó phải có dòng trong `team_brands` (dùng `seed_team_brands.py` hoặc SQL).

---

## 9. Sau này: API / n8n

Có thể thêm API (POST + API key) để n8n hoặc Google Sheet đẩy dữ liệu — làm riêng khi bạn cần.

---

## 10. Gặp lỗi thường gặp

### `zsh: command not found: psql`

Bạn **chưa cài** `psql` trên Mac — **không sao**. Dùng **Cách B** ở [mục 3](#3-chạy-file-sql-trên-mac--hai-cách) (lệnh `docker compose exec -T db psql ...`).

### `Cannot connect to the Docker daemon` hoặc container không chạy

Mở **Docker Desktop**, đợi khởi động xong, rồi:

```bash
cd ~/Documents/Mytools/search-tools
docker compose up -d
```

### `password authentication failed` khi Python kết nối DB

Kiểm tra `DATABASE_URL` trong `.env` có **khớp** user/mật khẩu/database với `docker-compose.yml` (`searchlocal` / `searchlocal` / `products_local`).

### Chạy `migration_002` báo lỗi thiếu bảng

Chạy **`sql/schema.sql` trước** (tạo `products`), sau đó mới `migration_002_team_rbac.sql`.

### Đăng nhập user thường không thấy sản phẩm

- Admin (`is_admin = true`) thấy hết.
- User thường chỉ thấy `brand` nằm trong `team_brands` của `team_id` — chạy `seed_team_brands.py` hoặc chèn SQL gán brand.

### `violates foreign key constraint "app_users_team_id_fkey"` — Key (team_id)=(2) is not present

Bảng `teams` **chưa có** dòng với `id = 2` (hoặc id bạn truyền). Tạo team trước: [mục 5.3](#53-tạo-team-team-2-team-3-), rồi chạy lại `add_user.py` với đúng `team_id` sau khi đã `SELECT id, name FROM teams`.

### Cổng 5000 bị chiếm

Trong `.env` thêm:

```
PORT=5001
```

Rồi mở trình duyệt `http://127.0.0.1:5001` (đã hỗ trợ trong `search.py`).

---

## Tóm tắt một dòng

**SQL trên Mac không có `psql`:** luôn có thể dùng  
`docker compose exec -T db psql -U searchlocal -d products_local < sql/TÊN_FILE.sql`  
trong thư mục `search-tools`, sau `docker compose up -d`.
