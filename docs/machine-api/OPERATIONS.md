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
