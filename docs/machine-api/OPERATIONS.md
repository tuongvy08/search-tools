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
- **Production:** CHƯA triển khai. Cần PO nói "đồng ý bắt đầu" (mục 4).

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

**Trạng thái staging (2026-11-21):** đã cài (sao lưu mã cũ ở `/srv/search-tools-machine-api/app.bak-20261121`); role `machine_api_internal_ro` đã tạo; thẻ `n8n-bao-gia` (IP n8n `139.180.215.28`, hạn 30 ngày) và thẻ thử `smoke-noibo` (chỉ `127.0.0.1`, hạn 1 ngày — **xóa sau khi dùng**). **Production: CHƯA** — cần PO "đồng ý bắt đầu"; làm đúng các bước ở mục 4 cộng: tạo role nội bộ, thêm biến DSN, thêm 2 module, cấp thẻ nội bộ có IP; verifier + Codex chỉ-đọc phải PASS trước.

**Hoàn tác staging:** khôi phục `app.bak-20261121` → `systemctl restart search-tools-machine-api`; thu hồi thẻ nội bộ (`revoke`); `DROP OWNED BY machine_api_internal_ro; DROP ROLE machine_api_internal_ro;` và xóa dòng `MACHINE_INTERNAL_DATABASE_URL`.

**Bài học (2026-11-21):** (1) ràng buộc khóa ngoại `products.brand → brand_master` nghĩa là "hãng chưa gán tiền tệ" chỉ xảy ra khi hãng bị ngừng (`is_active=false`) hoặc tiền tệ thiếu tỷ giá; (2) Code node của n8n **không có mạng** → việc gọi API phải đặt ở node HTTP Request; (3) kết quả tìm theo mã cũng trả các mã **chứa** từ khóa (T0050000 ra 2 kết quả) nên n8n phải lọc "trùng chính xác" — đã làm trong [integrations/n8n/bao-gia-tu-dong](../../integrations/n8n/bao-gia-tu-dong/README.md).
