# Cổng API máy — Vận hành, triển khai, hoàn tác

Thiết kế: [PLAN](PLAN.md). Hợp đồng kiểm chứng: [specs/machine-api-readonly/VERIFICATION.md](../../specs/machine-api-readonly/VERIFICATION.md). Hướng dẫn cho người dựng bot: [HUONG_DAN_KET_NOI](HUONG_DAN_KET_NOI.md).

## 1. Thành phần trên máy chủ

| Thành phần | Vị trí (staging; production dùng cùng cấu trúc, release bất biến) | Ghi chú |
| --- | --- | --- |
| Mã + môi trường Python riêng | `/srv/search-tools-machine-api/{app,venv}` | `app/` chỉ gồm 8 module + `scripts/machine_token.py` (+ `requirements.txt`); không chung môi trường với ứng dụng nhân viên |
| Tài khoản hệ thống | `searchtools-machine` (nologin) | không đọc được `/etc/search-tools/*` của ứng dụng nhân viên, không ghi được thư mục mã |
| Cấu hình dịch vụ | `/etc/search-tools-machine/machine.env` (0600, root) | chỉ có `MACHINE_DATABASE_URL`, `MACHINE_API_TOKENS_FILE`; không chứa thẻ |
| File thẻ | `/var/lib/search-tools-machine/tokens.json` (0600, chủ `searchtools-machine`) | chỉ lưu mã băm SHA-256; đọc lại tự động ≤ 5 giây |
| Dịch vụ | `search-tools-machine-api.service` (Gunicorn 127.0.0.1:8100) | 1 worker × 8 luồng, `CPUQuota=50%`, `MemoryMax=512M`, `ProtectSystem=strict`, `NoNewPrivileges` |
| Tài khoản database | `machine_api_ro` — SELECT trên đúng 8 bảng, **theo cột** với `products`/`teams`/`stock_items` ([mẫu](../../sql/machine_readonly_role.sql)) | không ghi được, không đọc được cột giá/ghi chú nội bộ, không đọc được bảng người dùng |
| Nginx | `/etc/nginx/conf.d/machine-api.conf` (log_format, limit_req_zone) + `/etc/nginx/snippets/machine-api-location.conf` được `include` trong server HTTPS | ghi đè `X-Forwarded-For`, giới hạn tần suất, log không chứa từ khóa |

## 2. Việc hằng ngày

Cấp/thu hồi/xem thẻ: xem [HUONG_DAN_KET_NOI mục 2](HUONG_DAN_KET_NOI.md). Luôn chạy công cụ bằng chính tài khoản dịch vụ (`sudo -u searchtools-machine .../python scripts/machine_token.py ...`) để file thẻ giữ đúng chủ và quyền 0600.

Xem nhật ký: `journalctl -u search-tools-machine-api -o cat` (một dòng `INFO machine_api.access: {JSON}`/lần gọi: thẻ, điểm vào, mã, số kết quả, IP, ms; **không** có từ khóa/thẻ/giá). Nginx: `/var/log/nginx/machine_api.access.log`.

Tắt khẩn cấp: `revoke` thẻ (≈ vài giây) hoặc `systemctl stop search-tools-machine-api`. Ứng dụng nhân viên không bị ảnh hưởng.

## 3. Trạng thái triển khai

- **Staging (2026-11-15):** đã cài theo mục 1 (`staging.search.standards.vn`, database `search_tools_staging`, team thử "Chatbot khách hàng (thử)" id 166 với 5 hãng, thẻ thử có hạn 7–30 ngày). Evidence kiểm tra hạ tầng (V11) ghi ở mục 5.
- **Production (2026-11-22, PO duyệt):** ĐÃ triển khai, chỉ thẻ NỘI BỘ cho n8n (chưa cấp thẻ khách, chưa tạo team khách). Máy `ssh python` (`search.standards.com.vn`), database `searchtools_pg_r1_rollback_20260906_153842`. Mã cài từ commit `0827450` (machine_api.py sha256 `3dfc7c24…`), thư mục `/srv/search-tools-machine-api/{app,venv}`, bản sao release ở `/root/machine-deploy-20261122`; tài khoản `searchtools-machine`; 2 role DB `machine_api_ro`, `machine_api_internal_ro` (đã kiểm: chỉ-đọc, không đọc được `app_users`, role khách không đọc được giá); nginx: file `/etc/nginx/conf.d/machine-api.conf` + `/etc/nginx/snippets/machine-api-location.conf` + 1 dòng `include` trong `sites-available/search-tools-production` (bản gốc ở `/root/nginx-backup-20261122/search-tools-production.orig`); `/login`, `/search` của nhân viên không đổi (200/401 trước và sau). Thẻ `n8n-bao-gia-prod` (nội bộ, hạn 90 ngày đến 2027-02-20, IP cho phép `172.18.0.3` = container n8n và `139.180.215.28` = máy chủ; nếu container n8n khởi động lại và đổi IP nội bộ thì gọi API sẽ bị 403 — cấp lại thẻ với IP mới). Kiểm tra: giá VND khớp truy vấn SQL độc lập (T0050000 = 2.930.000 đ; EPT0050000 = 4.566.000 đ), `ethanol` xen đủ 5 hãng, không thẻ/sai thẻ 401.
  - **Hoàn tác production:** gỡ dòng `include` khỏi `search-tools-production` (hoặc khôi phục file ở `/root/nginx-backup-20261122/`) → `nginx -t` → `reload`; `systemctl disable --now search-tools-machine-api`; `DROP OWNED BY machine_api_ro; DROP ROLE machine_api_ro; DROP OWNED BY machine_api_internal_ro; DROP ROLE machine_api_internal_ro;`; xóa `/srv/search-tools-machine-api`, `/etc/search-tools-machine`, `/var/lib/search-tools-machine`, tài khoản `searchtools-machine`.
  - **Chưa làm / việc mở:** `REVOKE CREATE ON SCHEMA public FROM PUBLIC` (PG14; hiện PUBLIC còn quyền CREATE — không dùng được qua API, chỉ là lớp phòng thủ thêm; cần kiểm tra ảnh hưởng ứng dụng trước); theo dõi 24 giờ; thẻ khách cần PO chọn hãng + tạo team.

## 4. Kế hoạch production (HIGH) — chờ PO duyệt

Điều kiện trước: verifier độc lập PASS + Codex chỉ-đọc không finding chặn; PO nghiệm thu UAT trên staging; PO **chọn các hãng khách được hỏi** (quyết định nghiệp vụ) và tự tạo team "Chatbot khách hàng" trên trang admin với đúng các hãng/trường đó (không cấp quyền giá).

Các bước (mỗi bước có kiểm tra trước/sau; dừng ngay nếu một kiểm tra không đạt):

1. **Preflight chỉ đọc** trên `ssh python`: dịch vụ web/worker đang chạy, DB đúng (`searchtools_pg_r1_rollback_20260906_153842`, PostgreSQL 14), `nginx -t` hiện tại sạch, dung lượng đĩa, cổng 8100 còn trống, 8 bảng đích tồn tại.
2. Tạo tài khoản `searchtools-machine`, thư mục `/srv/search-tools-machine-api/{app,venv}` (đúng bố cục đã thử trên staging; không dùng chung thư mục/venv của ứng dụng nhân viên), môi trường Python riêng (`pip install -r requirements.txt`), file cấu hình 0600.
3. **Database:** chạy mẫu `sql/machine_readonly_role.sql` (tạo role, chỉ SELECT; không đổi dữ liệu/lược đồ; hoàn tác bằng `DROP ROLE`). Không cần migration.
4. Cài unit, `systemctl enable --now`; kiểm tra chỉ lắng nghe `127.0.0.1:8100`.
5. **Nginx (hạ tầng dùng chung):** thêm conf.d + snippet + `include`; `nginx -t` PHẢI đạt trước khi `reload`; sao lưu file site trước khi sửa. Kiểm tra ngay `/login` và `/search` của nhân viên vẫn 200.
6. Cấp thẻ (hạn ≤ 90 ngày lúc đầu), smoke test từ bên ngoài bằng HTTPS: không thẻ 401, có thẻ 200, không có trường giá, thẻ hết quyền 403.
7. Theo dõi 24 giờ: nhật ký, tải CPU, độ trễ `/login`.

Hoàn tác production (không chạm dữ liệu): gỡ dòng `include` khỏi server Nginx → `nginx -t` → `reload`; `systemctl disable --now search-tools-machine-api`; (tùy chọn) `DROP ROLE machine_api_ro` theo phần gỡ bỏ trong file mẫu SQL; xóa thư mục release.

## 5. Evidence staging 2026-11-15 (V11)

Đo trên staging qua Nginx + TLS thật (gọi từ chính máy chủ bằng `--resolve`, vì IP máy đang làm việc chưa nằm trong danh sách IP được phép vào HTTPS của staging; tôi không tự sửa tường lửa):

| Kiểm tra | Kết quả |
| --- | --- |
| (a) cổng Gunicorn chỉ lắng nghe loopback | `ss -ltn`: `127.0.0.1:8100`; kết nối `IP-máy-chủ:8100` từ bên ngoài thất bại |
| (b) HTTPS: không thẻ / có thẻ / HTTP | 401 / 200 / 301 chuyển sang HTTPS; chứng chỉ hợp lệ (`ssl_verify_result=0`) |
| (b') không có `Set-Cookie` ở mọi phản hồi | 0/4 điểm vào |
| (c) giả IP bằng `X-Forwarded-For`/`X-Real-IP` với thẻ chỉ cho `192.0.2.1` | 3/3 kiểu giả đều 403 (Nginx ghi đè header) |
| (d) dồn 200 lời gọi/20 luồng bằng một thẻ | 34 × 200, 166 × 429 (giới hạn tần suất Nginx); `/login` nhân viên trong lúc bị dội: 0,14–0,80 s, vẫn 200 |
| (e) dừng dịch vụ máy | `/login` nhân viên vẫn 200; cổng máy 502; bật lại → 200 |
| (f) tài khoản dịch vụ | chạy bằng `searchtools-machine`, `NoNewPrivileges`, `ProtectSystem=strict`; không ghi được thư mục mã; đọc `/etc/search-tools/staging.env` và `machine.env` đều bị từ chối |
| nhật ký Nginx của cổng | không chứa từ khóa hay thẻ (`grep` = 0) |

Hai lỗi phát hiện NHỜ thử thật trên staging (test cục bộ không thấy được; verifier độc lập cũng bắt được khi chạy Gunicorn thật) đã sửa và triển khai lại staging — xem mục 6.

## 6. Bài học / lỗi đã bắt được khi thử thật

Sau sửa (staging, 2026-11-15): dòng truy vết xuất đúng vào journald chỉ có id thẻ/điểm vào/mã/số kết quả/IP/ms; tìm từ khóa thử `SECRETKEYWORD123` → 0 lần xuất hiện trong journald; role DB đọc `products.price`/`stock_items.stock_price_vnd` → "permission denied"; Nginx `error_log` của khối `/machine/` ở mức `crit`.

1. Nhật ký truy cập mặc định của Gunicorn (`--access-logfile -`) ghi cả query string, tức **từ khóa khách tìm** → đã bỏ khỏi unit.
2. Dòng nhật ký riêng của cổng không được xuất ra khi chạy dưới Gunicorn (logger chưa có handler) → đã thêm handler ghi ra stderr.
3. Nhánh `main` cục bộ có thể **cũ** so với `origin/main`; phép kiểm tra "không sửa ứng dụng nhân viên" phải so với `origin/main`.

## 7. Thẻ NỘI BỘ có giá (2026-11-21) — cho n8n lập báo giá

Kế hoạch: [INTERNAL_PRICE](INTERNAL_PRICE.md). Hợp đồng: [specs/machine-api-internal-price/VERIFICATION.md](../../specs/machine-api-internal-price/VERIFICATION.md).

**Khác thẻ khách:** `profile=internal`; không gắn team, thấy mọi hãng và mọi trường của hàng hóa + giá + pháp chế + tồn kho (giá gốc, hệ số ship, tiền tệ, tỷ giá, giá VND đúng công thức ứng dụng `round(giá × ship × tỷ giá, -3)`, ghi chú sản phẩm, lô tồn kho kèm giá/ghi chú tồn kho); tối đa 50 kết quả (mặc định 20); **bắt buộc** `allowed_ips`; hạn tối đa 90 ngày (công cụ cấp thẻ chặn dài hơn); thẻ khách **không bao giờ** có giá.

**Hạ tầng thêm so với mục 1:**

| Thành phần | Ghi chú |
| --- | --- |
| Role PostgreSQL `machine_api_internal_ro` | tạo bằng [sql/machine_internal_role.sql](../../sql/machine_internal_role.sql): SELECT theo cột trên `products`, `stock_items`, `stock_state`, `regulatory_*`, `brand_compliance_settings`, `brand_master`, `currency_rates`; không đọc được `teams`, `team_brands`, bảng người dùng/đăng nhập; chỉ-đọc |
| `MACHINE_INTERNAL_DATABASE_URL` trong `/etc/search-tools-machine/machine.env` (0600) | kết nối riêng cho thẻ nội bộ; thiếu biến này thì thẻ nội bộ bị từ chối (503), **không** lùi về kết nối khách |
| 2 module thêm vào thư mục mã | `currency_rates.py`, `admin_permissions.py` (tập module từ 8 lên 10) |

**Cấp / thu hồi thẻ nội bộ** (bằng tài khoản dịch vụ, như thẻ khách):

```bash
sudo -u searchtools-machine /srv/search-tools-machine-api/venv/bin/python /srv/search-tools-machine-api/app/scripts/machine_token.py \
  new --file /var/lib/search-tools-machine/tokens.json --id n8n-bao-gia --profile internal \
  --allowed-ip <IP-n8n> --days 30 --rate-per-minute 300
```

**Trạng thái staging (2026-11-21):** đã cài (sao lưu mã cũ ở `/srv/search-tools-machine-api/app.bak-20261121`); role `machine_api_internal_ro` đã tạo; thẻ `n8n-bao-gia` (IP n8n `139.180.215.28`, hạn 30 ngày) và thẻ thử `smoke-noibo` (chỉ `127.0.0.1`, hạn 1 ngày — **xóa sau khi dùng**). **Production: ĐÃ triển khai 2026-11-22 (xem mục 3);** làm đúng các bước ở mục 4 cộng: tạo role nội bộ, thêm biến DSN, thêm 2 module, cấp thẻ nội bộ có IP; verifier + Codex chỉ-đọc phải PASS trước.

**Hoàn tác staging:** khôi phục `app.bak-20261121` → `systemctl restart search-tools-machine-api`; thu hồi thẻ nội bộ (`revoke`); `DROP OWNED BY machine_api_internal_ro; DROP ROLE machine_api_internal_ro;` và xóa dòng `MACHINE_INTERNAL_DATABASE_URL`.

**Bài học (2026-11-21):** (1) ràng buộc khóa ngoại `products.brand → brand_master` nghĩa là "hãng chưa gán tiền tệ" chỉ xảy ra khi hãng bị ngừng (`is_active=false`) hoặc tiền tệ thiếu tỷ giá; (2) Code node của n8n **không có mạng** → việc gọi API phải đặt ở node HTTP Request; (3) kết quả tìm theo mã cũng trả các mã **chứa** từ khóa (T0050000 ra 2 kết quả) nên n8n phải lọc "trùng chính xác" — đã làm trong [integrations/n8n/bao-gia-tu-dong](../../integrations/n8n/bao-gia-tu-dong/README.md).

## 8. Tra hàng loạt `POST /products/batch` (2026-11-26)

Thiết kế + cách gọi: [BATCH](BATCH.md). Hợp đồng: [specs/machine-api-batch/VERIFICATION.md](../../specs/machine-api-batch/VERIFICATION.md). Không cần thẻ mới, không đổi role DB, không migration. Chỉ đổi: `machine_api.py` (1 file) và **một khối Nginx con** trong snippet `/etc/nginx/snippets/machine-api-location.conf`:

```nginx
    location = /machine/v1/products/batch {
        client_max_body_size 128k;
        client_body_buffer_size 128k;
        proxy_pass http://127.0.0.1:8100;
    }
```

(đặt TRONG `location /machine/ { ... }`, trước dấu `}` cuối; các đường khác giữ `client_max_body_size 8k`).

**Triển khai (staging và production cùng các bước):** sao lưu `machine_api.py` và snippet Nginx → cài `machine_api.py` mới (0644 root) → `systemctl restart search-tools-machine-api` → `is-active` + `/health` → thêm khối Nginx → `nginx -t` PHẢI đạt → `systemctl reload nginx` → `/login` nhân viên 200 trước/sau → smoke.

**Hoàn tác:** chép lại bản sao lưu `machine_api.py` → restart dịch vụ; chép lại snippet đã sao lưu → `nginx -t` → reload. Không có thay đổi dữ liệu.

### Staging (2026-11-26) — đã cài

- Sao lưu: `/root/machine_api.py.before-batch-20261126` (sha256 `3dfc7c24…`, giống production), `/root/machine-api-location.conf.before-batch-20261126`. Bản mới sha256 `8d1b7136…` (lượt 1), sau sửa lần 1: `05700e7c…`, **bản cuối sau sửa lần 2 (hợp đồng v2): `4b44868f…`** — verifier lượt 3: B1–B8 PASS. `nginx -t` đạt; `/login` 200 trước và sau reload; không thẻ → 401.
- Thẻ thử tạm (chỉ `127.0.0.1`, hạn 1 ngày, giá trị trong file root-only `/root/.sb_*.tok`, không in ra): `smoke-batch-noibo` (nội bộ), `smoke-batch-khach` (team 166). **Thu hồi + xóa file sau khi xong.**
- Đo qua Nginx + TLS thật (gọi từ chính máy chủ bằng `--resolve`; dữ liệu staging 1.262.173 sản phẩm):

| Kiểm tra | Kết quả |
| --- | --- |
| 490 mã thật (lấy mẫu ngẫu nhiên, 5 mã đổi chữ thường + khoảng trắng) + 10 mã không có, kèm pháp chế + tồn kho, thẻ nội bộ | 200, **0,30 giây**, 0,6 MB; 500 dòng đúng thứ tự và đúng `ref`; 490 FOUND, 10 NOT_FOUND, 0 bị cắt |
| Cùng gói, không `include` | 200, 0,19–0,22 giây |
| 500 CAS ngẫu nhiên, đủ `include` | 200, 1,4 giây, 134 dòng bị cắt (> 20 sản phẩm/CAS) |
| 500 CAS phổ biến nhất (mặc định 20/mục) | 200, 3,8–4,0 giây, 8 MB; `/login` nhân viên trong lúc chạy: 8/8 lần 200, chậm nhất 0,03 giây |
| 500 CAS phổ biến nhất, `max_per_item=50` | 400 `too_many_results` sau 2 giây (đúng thiết kế, không cắt thầm lặng) |
| Đối chiếu 15 sản phẩm ngẫu nhiên với `/products?q=<mã>` | **15/15 y hệt** (giá, pháp chế, tồn kho) |
| Giá VND của 10 sản phẩm so với truy vấn SQL độc lập (`round(giá×ship×tỷ giá,-3)`) | **10/10 khớp từng đồng** |
| Thẻ khách (team 166): 500 CAS phổ biến nhất | Lượt 1: **503 sau 8,1 giây** (verifier bắt; planner dùng chỉ mục hãng). Sau sửa lần 1: **200, 1,6 giây** (2/2 lần) |
| Thẻ khách (team 166): 200 mã + 100 CAS | 200, 0,24–1,1 giây; 0 khóa giá/ghi chú; 0 sản phẩm ngoài hãng của team; `max_per_item`=10; có `price_notice` |
| Thân 94,5 KB hợp lệ tới batch / 100 KB tới `/products` / 140 KB tới batch | 200 / **413** / **413** (Nginx chỉ nới đúng đường batch) |
| Nhật ký Nginx + journald của cổng sau khi chạy | 0 lần xuất hiện thẻ, mã hàng thử, `ref` |

- Đo lại trên bản cuối `4b44868f…` (2026-11-27): 500 mã đủ `include` 0,31 giây; không `include` 0,17 giây; 500 CAS ngẫu nhiên 1,3 giây; 500 CAS phổ biến nhất 3,7 giây; thẻ khách 0,2 giây; 15/15 trùng `/products`; 10/10 giá khớp SQL; Nginx 200/413/413 như trên. (Bản trung gian đọc lô bằng LATERAL từng mã chậm gấp đôi — 7,8 giây — nên đã đổi sang một lượt đọc + ROW_NUMBER theo từng mã; bài học: với bảng nhỏ, LATERAL lặp hàng nghìn lần tốn hơn một lần quét.)
- **Chưa chạy qua n8n trên staging:** n8n hiện chỉ còn credential trỏ thẻ **production** (`Search_Tools_Machine_API_Noi_bo`); credential thẻ staging cũ đã không còn. Lượt chạy thật qua n8n (B9) làm ngay sau khi production được duyệt (chỉ đọc).

### Production — CHƯA (chờ PO nói "đồng ý bắt đầu")
