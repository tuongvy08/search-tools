# Search-tools

Ứng dụng nội bộ (Flask + PostgreSQL) để nhân viên công ty kinh doanh hóa chất
tra cứu sản phẩm, kiểm tra tình trạng pháp chế (quản lý xuất nhập khẩu hóa
chất) và lập báo giá nhanh. Đang chạy production cho nhân viên công ty; xem
[SECURITY.md](SECURITY.md) trước khi thao tác ngoài local/dev.

## Ứng dụng làm gì

- **Product Search**: tìm sản phẩm theo tên, mã (code) hoặc CAS, có gợi ý
  (autocomplete), lọc theo brand/quy cách và theo "chỉ hiện kết quả có tồn
  kho". Kết quả bị giới hạn theo brand mà team của người dùng được xem
  (`team_brands`) và theo các quyền (permission) của team.
- **Check License / Check CAS**: tra cứu nhanh tình trạng quản lý (pháp chế)
  của một hoặc nhiều mã CAS theo danh mục quy tắc quản lý (`regulatory_rules`
  + `regulatory_statuses`).
- **Find Code / Advanced Search**: tra cứu theo danh sách mã hàng loạt, tìm
  kiếm nâng cao theo nhiều điều kiện.
- **Quick Quote (Trợ lý báo giá)**: nhập file yêu cầu báo giá (Excel/CSV) của
  khách, đối chiếu (match) với danh mục sản phẩm theo tên/mã/CAS, rồi xuất
  báo giá vào file mẫu Excel (BG template) của công ty, giữ nguyên định dạng
  và công thức của file mẫu.
- **Copy/Export kết quả**: sao chép hoặc xuất kết quả tìm kiếm ra TSV/Excel,
  chỉ gồm các cột người dùng có quyền xem.
- **Khu vực Admin** (`/admin/...`): quản lý sản phẩm, import dữ liệu (Excel),
  quy tắc quản lý (regulatory), tồn kho (stock), tỷ giá, team & phân quyền,
  người dùng (LOCAL + Google Workspace), lịch sử đăng nhập, giới hạn IP văn
  phòng.

Người dùng: nhân viên sales/kinh doanh của công ty (tra cứu, báo giá) và một
số tài khoản admin (quản trị dữ liệu, quyền, tồn kho, import).

## Kiến trúc tóm tắt

Chi tiết đầy đủ: [ARCHITECTURE.md](ARCHITECTURE.md). Tóm tắt: Flask (Python)
phục vụ cả giao diện HTML và API JSON nội bộ, PostgreSQL là kho dữ liệu duy
nhất, import/nhập dữ liệu lớn chạy nền qua một worker process riêng
(`scripts/import_worker.py`), không có frontend framework hay build step
(HTML/CSS/JS thuần trong `templates/` và `static/`).

## Chạy thử trên máy (local)

Hướng dẫn từng bước chi tiết: **[HUONG_DAN_LOCAL.md](HUONG_DAN_LOCAL.md)**
(phân quyền theo team + import Excel nâng cao: xem thêm
[HUONG_DAN_CAP_NHAT_VA_RBAC.md](HUONG_DAN_CAP_NHAT_VA_RBAC.md)). Tóm tắt
nhanh:

```bash
docker compose up -d                         # PostgreSQL local (service `db`)
# tạo file .env (xem .env.example), tối thiểu DATABASE_URL + FLASK_SECRET_KEY
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose exec -T db psql -U searchlocal -d products_local < sql/schema.sql
docker compose exec -T db psql -U searchlocal -d products_local < sql/seed_test.sql   # dữ liệu mẫu, tùy chọn
set -a && source .env && set +a
python search.py                              # mặc định cổng 5001 nếu PORT=5001 trong .env
```

Các tính năng nâng cao (team/RBAC, import center, regulatory, stock, tỷ giá,
Google OIDC, giới hạn IP) cần thêm các migration tương ứng trong `sql/` —
xem danh sách migration bên dưới và hai hướng dẫn HUONG_DAN_* ở trên.

Nếu chạy các luồng import nền (Import Center, regulatory import, stock
import) trên máy local, cần chạy thêm worker cùng lúc với app:

```bash
python scripts/import_worker.py
```

## Kiểm tra (test)

```bash
# Test Python (unittest); một số test cần PostgreSQL local đang chạy
# (tự tạo/xóa database tạm qua tests/pg_temp_db.py), tự skip nếu không có.
PYTHONPATH=.:tests python -m unittest discover -s tests -v

# Test DOM phía JS (Node thuần, không cần cài package) — chạy từng file:
node tests/search_stock_only_dom_test.js
node tests/admin_regulatory_dom_test.js
# ... (xem toàn bộ danh sách trong tests/*.js)
```

Không có CI tự động chạy các lệnh trên trong repo hiện tại (`.github/` chỉ
có `PULL_REQUEST_TEMPLATE.md`) — chạy thủ công trước khi merge. Xem
[PROJECT_STATE.md](PROJECT_STATE.md) mục "Vấn đề đã biết".

## Tài liệu

- [ARCHITECTURE.md](ARCHITECTURE.md) — module chính, mô hình dữ liệu, đăng
  nhập/phân quyền, import & worker, deploy, quyết định kỹ thuật.
- [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md) — quy tắc nghiệp vụ (tìm
  kiếm, pháp chế, báo giá, tồn kho, team/phân quyền, tỷ giá).
- [PROJECT_STATE.md](PROJECT_STATE.md) — trạng thái hiện tại, việc dở dang.
- [SECURITY.md](SECURITY.md) — nguyên tắc chung + quy tắc riêng của
  Search-tools (production, secret, database, deploy).
- [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md) — quy trình
  làm việc với AI (Understand → Inspect → Plan → Implement → Verify → Report).
- [HUONG_DAN_LOCAL.md](HUONG_DAN_LOCAL.md) — chạy thử local từng bước.
- [HUONG_DAN_CAP_NHAT_VA_RBAC.md](HUONG_DAN_CAP_NHAT_VA_RBAC.md) — cập nhật
  dữ liệu Excel, team/RBAC.
- [HUONG_DAN_DEPLOY_VA_CAP_NHAT.md](HUONG_DAN_DEPLOY_VA_CAP_NHAT.md) — quy
  trình deploy/cập nhật Mac → GitHub → Vultr.
- `docs/phase*/` — hồ sơ từng phase đã triển khai (SCOPE/OPERATIONS/
  VALIDATION/UAT); xem [PROJECT_STATE.md](PROJECT_STATE.md) để biết phase
  nào đã merge vào `main`.
- `PHASE6C01_LOCAL_REPORT.md` — báo cáo phase cũ (team capabilities), giữ
  làm hồ sơ lịch sử.
