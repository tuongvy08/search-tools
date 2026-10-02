# ui-system — vận hành

## Staging (`ssh staging`)

| Ngày (UTC) | Bản | Gói (SHA256) | Kết quả |
| --- | --- | --- | --- |
| 2026-10-02 | `a8cdae2` (đợt 1–2) từ `7a9333a` | `search-tools-ui-7a9333a-to-a8cdae2.bundle` `7f5a58cf…d2a9` | Gate HEAD cũ + sạch + bundle verify + FETCH_HEAD đúng; checkout `/srv/search-tools`; chỉ restart web; web/worker active, `/login` 200, `/static/ui_system.css` 200, log 0 lỗi. |
| 2026-10-02 | `0ba845a` (đợt 3) từ `a8cdae2` | `search-tools-ui-a8cdae2-to-0ba845a.bundle` `97af061e…361d` | Cùng gate + chỉ restart web; web/worker active, `/login` 200, `ui_system.css` 200, log 0 lỗi. Thay đổi: template + CSS. |
| 2026-10-02 | `db5db36` (đợt 4–5) từ `0ba845a` | `search-tools-ui-0ba845a-to-db5db36.bundle` `eda66833…67c0` | Cùng gate + chỉ restart web; web/worker active, `/login` 200 (giao diện mới), `quick_quote_ui.css` 200, log 0 lỗi. Thay đổi: template + CSS. |
| 2026-10-02 | `900a5f9` (sửa theo góp ý PO: chữ nút/tab đang chọn; cột Tồn kho luôn thấy) từ `db5db36` | `search-tools-ui-db5db36-to-900a5f9.bundle` `27be5705…3503` | Cùng gate + chỉ restart web; web/worker active, `/login` 200, CSS ghim cột tồn kho được phục vụ, log 0 lỗi. |

Thay đổi ứng dụng: chỉ template + CSS (không Python, không migration, database không đổi).

Quay lại bản trước (chỉ staging):

```bash
ssh staging 'sudo -n -u deploy git -c safe.directory=/srv/search-tools -C /srv/search-tools checkout --quiet --detach <bản trước, ví dụ a8cdae2684d5e419681480519f4990711be78687> && sudo -n systemctl restart search-tools-staging.service'
```

## Production

Chưa triển khai. Gom theo cụm đợt; mỗi lần cần PO “đồng ý bắt đầu”.
