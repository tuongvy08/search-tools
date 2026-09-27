# Hướng dẫn cập nhật & vận hành (Mac → GitHub → Staging → Production)

Tài liệu dùng **mỗi khi** sửa code, sửa lỗi, hoặc triển khai bản mới. Viết
lại 2026-09-27 cho khớp quy trình thực tế đang dùng (immutable release +
staging bắt buộc trước production) — bản trước mô tả cách làm cũ, đơn giản
hơn (git pull tại chỗ), không còn đúng với thực tế vận hành gần đây. Nguồn
đối chiếu: `ARCHITECTURE.md` (mục Deploy), `docs/phase6c2/OPERATIONS.md`,
`docs/phase6c3/OPERATIONS.md`, `docs/phase6d4/OPERATIONS.md`,
`docs/phase6d6/OPERATIONS.md`, `deploy/` trong repo, và lịch sử vận hành chi
tiết ngoài repo mà người dùng đã chỉ ra (xem `PROJECT_STATE.md`).

**Không có sẵn script để chạy trong tài liệu này.** Mỗi lần release, 3 file
script (preflight/prepare/cutover — xem mục 5) phải được soạn riêng cho đúng
commit/migration của lần đó (bằng tay hoặc nhờ AI soạn), rồi mới copy lên
server và tự chạy. Tài liệu này mô tả **quy trình và những gì cần kiểm tra**,
không phải một bộ lệnh cố định để copy-paste chạy production.

---

## Mục lục

1. [Luồng tổng quát](#1-luồng-tổng-quát)
2. [Máy Mac — sau khi sửa code](#2-máy-mac--sau-khi-sửa-code)
3. [Khi `git push` bị từ chối](#3-khi-git-push-bị-từ-chối-remote-có-commit-mới)
4. [Khi rebase bị conflict](#4-khi-rebase-bị-conflict)
5. [Deploy lên Staging hoặc Production — 3 bước](#5-deploy-lên-staging-hoặc-production--3-bước)
6. [Thông tin hạ tầng đã ghi nhận](#6-thông-tin-hạ-tầng-đã-ghi-nhận)
7. [Thiết lập lần đầu cho Import Center / worker](#7-thiết-lập-lần-đầu-cho-import-center--worker)
8. [Backup](#8-backup)
9. [Gunicorn & Nginx — tránh 502 / timeout](#9-gunicorn--nginx--tránh-502--timeout)
10. [Kiểm tra sau deploy](#10-kiểm-tra-sau-deploy)
11. [Gỡ lỗi nhanh](#11-gỡ-lỗi-nhanh)

---

## 1. Luồng tổng quát

```
(Máy Mac) Sửa code → commit → push lên GitHub
       ↓
(Máy Mac) Tự soạn/nhờ soạn 3 file script cho STAGING → copy lên Staging → tự chạy qua SSH
       ↓
UAT trên Staging (người dùng tự kiểm tra tính năng) → đạt mới đi tiếp
       ↓
(Máy Mac) Tự soạn/nhờ soạn 3 file script cho PRODUCTION → copy lên Production → tự chạy qua SSH
       ↓
UAT trên Production → đạt thì đóng việc; nếu cutover lỗi, dịch vụ tự phục hồi
bản code/cấu hình cũ (xem mục 5.4)
```

Nguyên tắc bắt buộc: **luôn qua Staging và UAT đạt trước khi đụng tới
Production.** Không có bước nào trong quy trình này tự động SSH vào server —
mọi lệnh trên server đều do người dùng tự chạy sau khi đã xem trước nội
dung.

---

## 2. Máy Mac — sau khi sửa code

*(Chạy trên: Mac)*

```bash
cd /Users/truong/Documents/Mytools/search-tools
git status
git add -A
git commit -m "Mô tả ngắn thay đổi"
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

*(Chạy trên: Mac)*

```bash
cd /Users/truong/Documents/Mytools/search-tools
git pull --rebase origin main
git push origin main
```

---

## 4. Khi rebase bị conflict

*(Chạy trên: Mac)*

Sửa file có dòng `<<<<<<<` / `=======` / `>>>>>>>`, xóa marker và giữ nội
dung đúng, rồi:

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

## 5. Deploy lên Staging hoặc Production — 3 bước

Áp dụng như nhau cho cả Staging và Production (chỉ khác đường dẫn/tên
service — xem mục 6). Mỗi bước là **một file script Python riêng**, đã được
xem/review trước khi chạy. Không gộp tắt các bước, không tự sửa script để bỏ
qua một điều kiện kiểm tra đang chặn.

### 5.1 Bước 1 — Preflight (chỉ đọc, không đổi gì)

*(Chạy trên: server, qua SSH)*

Mục đích: xác nhận đúng trạng thái đang chạy thật trước khi chuẩn bị bất cứ
thứ gì. Đọc trực tiếp từ tiến trình đang chạy (`/proc`), **không** suy đoán
từ file `.env` cũ hay đường dẫn release trước đó — vì đã từng ghi nhận
trường hợp `.env` cũ trỏ tới database khác với database thật đang chạy.

Script preflight kiểm tra (không ghi gì lên server):

- Web và worker đang chạy đúng release/commit nào.
- Database đang dùng thật là database nào, tài khoản chạy dịch vụ là ai.
- Schema/migration đã áp dụng tới đâu, có khớp với bản sắp deploy không.
- Có job import (sản phẩm/pháp chế/tồn kho) nào đang chạy dở không — nếu có,
  dừng lại, không đi tiếp.
- Ổ đĩa còn đủ chỗ cho bản release mới + backup.

Nếu preflight báo lỗi/không khớp: dừng lại, không tự sửa script để bỏ qua,
báo người có trách nhiệm kiểm tra trước.

### 5.2 Bước 2 — Prepare (tải bản mới về, chưa dừng dịch vụ gì)

*(Chạy trên: server, qua SSH)*

Chỉ chạy sau khi Bước 1 đạt. Prepare tạo sẵn:

- Một bản clone code mới (qua HTTPS từ GitHub) vào **thư mục release riêng**
  — dịch vụ đang chạy vẫn dùng thư mục cũ, chưa bị ảnh hưởng.
- Một "checkpoint" (bản chụp cấu hình/thông tin trước khi đổi) để có thể đối
  chiếu/khôi phục nếu bước sau thất bại.

Không có dịch vụ nào bị dừng ở bước này; người dùng vẫn dùng app bình
thường trong lúc chạy Prepare.

### 5.3 Bước 3 — Cutover (bước duy nhất có gián đoạn ngắn)

*(Chạy trên: server, qua SSH)*

Chỉ chạy sau khi Bước 2 đạt và đã có **quyết định rõ ràng cho phép deploy
lúc này** (đặc biệt với Production). Trình tự bên trong cutover:

1. Dừng cả web **và** worker (đảm bảo không ai ghi dữ liệu giữa chừng).
2. Backup đầy đủ database (`pg_dump`) — xem mục 8.
3. Áp dụng migration SQL mới (nếu phase này có) trong cùng một transaction.
4. Chuyển cấu hình dịch vụ (systemd) sang trỏ vào thư mục release mới.
5. Khởi động lại cả web và worker.
6. Tự kiểm tra: đủ số worker Gunicorn, trang đăng nhập trả lời, các file
   tĩnh (CSS/JS) đúng phiên bản, số liệu nghiệp vụ (số sản phẩm, số dòng tồn
   kho...) không đổi ngoài phạm vi migration.

### 5.4 Nếu Cutover thất bại

- Nếu lỗi xảy ra **trước khi** dừng dịch vụ: không có gì thay đổi, an toàn để
  dừng và kiểm tra lại.
- Nếu lỗi xảy ra **sau khi** đã dừng dịch vụ (ví dụ dịch vụ không khởi động
  lại được): cutover tự phục hồi lại **code và cấu hình** của release cũ và
  khởi động lại — không tự động phục hồi (rollback) dữ liệu/migration đã áp
  dụng thành công, vì migration cộng thêm (additive) thường vẫn tương thích
  với code cũ. Backup đầy đủ ở bước 5.3.2 vẫn được giữ lại để khôi phục thủ
  công nếu thật sự cần.
- **Không** tự chạy lại Prepare hoặc Cutover một cách máy móc sau khi thất
  bại/mất kết nối — đọc lại thông báo lỗi, xác nhận trạng thái thật (chạy lại
  Preflight) trước khi thử lại.
- Giữ lại release cũ, checkpoint và backup cho tới khi có quyết định dọn dẹp
  rõ ràng — không tự xóa.

### 5.5 Sau khi Cutover thành công

- Cutover thành công **không đồng nghĩa** UAT đã đạt. Luôn cần người kiểm tra
  thực tế tính năng trên Staging (hoặc Production) trước khi coi phase đó là
  xong — xem checklist UAT tương ứng trong `docs/phase<tên>/UAT.md` nếu có.
- Với Production: chỉ thực hiện sau khi Staging đã qua UAT đạt cho đúng bản
  đó.

---

## 6. Thông tin hạ tầng đã ghi nhận

Ghi theo lịch sử vận hành đã quan sát được; **luôn xác nhận lại bằng bước
Preflight** (mục 5.1) thay vì giả định các giá trị dưới đây còn đúng nguyên
văn, vì tên/đường dẫn có thể đã đổi.

| | Production | Staging |
|---|---|---|
| Thư mục code | `/opt/search-tools-pg-release-<thời điểm>-<commit ngắn>-clean` (mỗi release một thư mục riêng, không sửa đè) | `/srv/search-tools` (một thư mục duy nhất, cập nhật tại chỗ mỗi lần deploy) |
| Service web | `search-tools-pg.service` | `search-tools-staging.service` |
| Service worker | `search-tools-import-worker.service` | `search-tools-import-worker.service` (cùng tên, khác máy) |
| Backup lưu ở | `/var/backups/search-tools-production/<thời điểm>_pre-<phase>` | `/srv/backups/search-tools/<thời điểm>_pre-<phase>` |
| Tài khoản chạy dịch vụ | Ghi nhận trước đây: `searchtools-pg`. File mẫu trong repo (`deploy/search-tools-import-worker.service`) lại ghi `User=searchtools`/`Group=searchtools` (không có "-pg"). **[cần xác nhận]** tên tài khoản chính xác hiện tại — không suy đoán, đọc từ tiến trình thật ở bước Preflight. | Ghi nhận trước đây: `deploy`. **[cần xác nhận]** còn đúng không. |
| Database | Không ghi tên cụ thể ở đây để tránh tài liệu bị lỗi thời/rò thông tin không cần thiết — **luôn lấy tên database từ tiến trình đang chạy thật** (bước Preflight), không suy từ `.env` cũ. | Tương tự. |

Số worker Gunicorn quan sát được trong các lần deploy gần đây là **2**
(không phải 3 như một số tài liệu cũ có thể còn ghi) — xác nhận số thực tế
qua Preflight/kiểm tra sau deploy (mục 10), không tự đặt lại theo trí nhớ cũ.

Web và worker **có thể tạm thời chạy khác release nhau** nếu một phase chỉ
đổi giao diện và không cần khởi động lại worker — đây là chủ đích, không
phải lỗi cấu hình; xem preflight để biết chính xác đang ở release nào.

---

## 7. Thiết lập lần đầu cho Import Center / worker

Chỉ cần làm **một lần** khi máy chủ chưa từng chạy tính năng Import Center
(nhập Excel sản phẩm/quy tắc pháp chế/tồn kho hàng loạt). Không phải bước
lặp lại mỗi lần deploy. Chi tiết đầy đủ: `docs/phase6c2/OPERATIONS.md`.

Tóm tắt các phần cần có (không ghi giá trị cụ thể — xem `.env.example` cho
tên biến, không đọc `.env` thật):

1. Tạo thư mục upload dùng chung cho web và worker (theo biến
   `IMPORT_UPLOAD_DIR` trong `.env.example`), chủ sở hữu đúng tài khoản chạy
   dịch vụ, quyền hạn chế (không cho ai khác đọc/ghi).
2. Cấu hình service worker theo mẫu `deploy/search-tools-import-worker.service`
   (sửa lại tên tài khoản/đường dẫn cho khớp server thật — xem mục 6).
3. Cấu hình Nginx cho riêng đường dẫn `/admin/imports` theo mẫu
   `deploy/nginx-imports.conf.example` (giới hạn kích thước file, timeout
   phù hợp với `IMPORT_MAX_BYTES` trong `.env.example`).
4. Web và worker phải cùng dùng **một** file cấu hình môi trường đã được
   review (không suy luận database đang chạy thật từ `.env` cũ trên server).

---

## 8. Backup

Backup đầy đủ (`pg_dump`) nằm **bên trong** bước Cutover (mục 5.3.2) — không
cần chạy tay riêng mỗi lần deploy bình thường.

Nếu cần backup nhanh **ngoài** quy trình deploy (ví dụ trước khi thử gì đó
thủ công): chạy trên server, dùng đúng `DATABASE_URL` lấy từ tiến trình đang
chạy thật (không phải từ `.env` cũ):

```bash
# (Chạy trên: server) — thay <DATABASE_URL_THẬT> bằng giá trị đọc được
# từ tiến trình đang chạy, không copy từ .env cũ chưa xác minh.
mkdir -p /var/backups/search-tools-production
pg_dump "<DATABASE_URL_THẬT>" -Fc -f "/var/backups/search-tools-production/pg_$(date +%F_%H%M%S).dump"
```

Không tự xóa backup cũ khi chưa được yêu cầu rõ; không có chính sách hết hạn
tự động cho backup của các phase gần đây (retention cần chủ project quyết
định — xem PROJECT_STATE.md).

---

## 9. Gunicorn & Nginx — tránh 502 / timeout

### Gunicorn (systemd)

*(Chạy trên: server)*

Xem cấu hình hiện tại (không sửa tay ngoài quy trình cutover, vì cấu hình
này được cutover cập nhật lại theo đúng thư mục release mới mỗi lần deploy):

```bash
sudo systemctl cat search-tools-pg
```

Nếu cần thay đổi cấu hình chung (số worker, timeout) **ngoài** một lần
deploy bình thường, đó là thay đổi hạ tầng chia sẻ — áp dụng mức HIGH theo
`docs/DEVELOPMENT_WORKFLOW.md`/`AGENTS.md`, cần phê duyệt trước.

### Nginx (chờ upstream lâu)

*(Chạy trên: server)*

Trong `location` proxy tới app, timeout gợi ý (điều chỉnh theo tải thật):

```nginx
proxy_connect_timeout 120s;
proxy_send_timeout 120s;
proxy_read_timeout 120s;
```

Kiểm tra và nạp lại:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**Vấn đề đã ghi nhận, cần xác nhận còn tồn tại hay không**: có lần `curl`
HTTPS trực tiếp từ production báo lỗi tham số Diffie-Hellman quá yếu ("dh key
too small"). Việc này không ảnh hưởng tới UAT qua trình duyệt tại thời điểm
ghi nhận, nhưng là nợ kỹ thuật về hạ tầng (TLS/DH) nên coi là một việc HIGH
riêng (đổi cấu hình TLS chia sẻ), không sửa tiện thể trong một lần deploy
tính năng. **[cần xác nhận]** hiện đã xử lý hay chưa.

---

## 10. Kiểm tra sau deploy

*(Chạy trên: server)*

App có trả lời không:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5001/login
```

Static có chống cache đúng phiên bản (sau bản có `after_request` no-store
cho `.js`/`.css`):

```bash
curl -sI "http://127.0.0.1:5001/static/script.js" | grep -i cache
```

Trình duyệt: mở app → **hard refresh** (Ctrl+F5 / Cmd+Shift+R) → UAT thủ công
theo checklist của phase đó nếu có (`docs/phase<tên>/UAT.md`).

---

## 11. Gỡ lỗi nhanh

*(Chạy trên: server)*

Log ứng dụng (đổi tên service cho đúng Production/Staging — xem mục 6):

```bash
sudo journalctl -u search-tools-pg -n 100 --no-pager
sudo journalctl -u search-tools-import-worker -n 100 --no-pager
```

Theo dõi realtime:

```bash
sudo journalctl -u search-tools-pg -f
```

Nginx:

```bash
sudo tail -n 80 /var/log/nginx/error.log
```

Kết nối DB (dùng `DATABASE_URL` đọc từ tiến trình đang chạy thật, không phải
`.env` cũ):

```bash
psql "<DATABASE_URL_THẬT>" -c "SELECT 1;"
```

---

## Tài liệu liên quan trong repo

- Chạy thử local + Docker Postgres: **`HUONG_DAN_LOCAL.md`**
- RBAC, import, team: **`HUONG_DAN_CAP_NHAT_VA_RBAC.md`**
- Biến môi trường mẫu: **`.env.example`**
- Kiến trúc & mô hình deploy tóm tắt: **`ARCHITECTURE.md`** (mục Deploy)
- Trạng thái/vấn đề đã biết liên quan deploy: **`PROJECT_STATE.md`**
- Vận hành Import Center chi tiết: **`docs/phase6c2/OPERATIONS.md`**
- Ví dụ preflight/rollback chi tiết cho một phase cụ thể (đã chạy thật, có
  nhiều điều kiện an toàn đáng tham khảo khi soạn script mới):
  **`docs/phase6c3/OPERATIONS.md`**, **`docs/phase6d4/OPERATIONS.md`**,
  **`docs/phase6d6/OPERATIONS.md`**
