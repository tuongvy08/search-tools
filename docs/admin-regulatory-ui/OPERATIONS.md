# admin-regulatory-ui — vận hành

## Staging (`ssh staging`) — 2026-10-02, agent tự chạy theo quyết định PO “staging được tự do triển khai”

- Trước: staging chạy `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, checkout `/srv/search-tools` sạch, web `search-tools-staging.service` + worker `search-tools-import-worker.service` active.
- Gói Git trên Mac: `/Volumes/DATA/Development/_ops/search-tools/search-tools-ui-2ec9b6c-to-7a9333a.bundle` (`2ec9b6c..feat/admin-regulatory-ui`, head `7a9333a0d1632fa883c67022264b53bfddea78ae`), SHA256 `eda1b95492e7c0bb1ca1e6a2f4d7ad3df949b677595d5de1b914794d62a997dd`; chép tới `staging:/tmp/` cùng SHA256.
- Thay đổi ảnh hưởng ứng dụng so với `2ec9b6c`: chỉ `admin_regulatory.py`, `static/admin_regulatory.css`, 3 template `admin_regulatory*.html` (còn lại là tài liệu/test). Không migration, database không đổi.
- Chuyển mã (user `deploy`, có gate HEAD cũ + sạch + bundle verify + FETCH_HEAD đúng): `CODE_SWITCHED head=7a9333a`, sau đó checkout vẫn sạch.
- Chỉ restart web: active, pid 2558, cổng 127.0.0.1:5001, `/login` = 200, CSS mới được phục vụ; worker giữ nguyên (không dùng phần đã đổi). Nhật ký web 5 phút sau restart: 0 dòng error/traceback/exception.

### Quay lại bản trước (nếu cần, chỉ staging)

```bash
ssh staging 'sudo -n -u deploy git -c safe.directory=/srv/search-tools -C /srv/search-tools checkout --quiet --detach 2ec9b6c940c89802cce9fc77150e4f2b8dcfff64 && sudo -n systemctl restart search-tools-staging.service'
```

## Production

Chưa triển khai. Cần PR #29/#30 được duyệt, PO xem trên staging và nói “đồng ý bắt đầu” riêng; dùng quy trình release bất biến của production (`ssh python`), không dùng cách cập nhật tại chỗ của staging.
