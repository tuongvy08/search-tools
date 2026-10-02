# ui-system — vận hành

## Staging (`ssh staging`)

| Ngày (UTC) | Bản | Gói (SHA256) | Kết quả |
| --- | --- | --- | --- |
| 2026-10-02 | `a8cdae2` (đợt 1–2) từ `7a9333a` | `search-tools-ui-7a9333a-to-a8cdae2.bundle` `7f5a58cf…d2a9` | Gate HEAD cũ + sạch + bundle verify + FETCH_HEAD đúng; checkout `/srv/search-tools`; chỉ restart web; web/worker active, `/login` 200, `/static/ui_system.css` 200, log 0 lỗi. |

Thay đổi ứng dụng: chỉ template + CSS (không Python, không migration, database không đổi).

Quay lại bản trước (chỉ staging):

```bash
ssh staging 'sudo -n -u deploy git -c safe.directory=/srv/search-tools -C /srv/search-tools checkout --quiet --detach 7a9333a0d1632fa883c67022264b53bfddea78ae && sudo -n systemctl restart search-tools-staging.service'
```

## Production

Chưa triển khai. Gom theo cụm đợt; mỗi lần cần PO “đồng ý bắt đầu”.
