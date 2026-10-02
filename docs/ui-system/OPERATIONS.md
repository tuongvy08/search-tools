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

## Production (`ssh python`) — release 2026-10-02 (UTC ~10:43–10:45)

PO yêu cầu “gộp PR và đưa lên production” sau khi xem staging. PR #29 → #30 → #31 gộp, `main@1d47a1738a57c39d18ef44a721fb1bc6cfdbd16c`. Thay đổi ứng dụng so với `2ec9b6c`: template + CSS + hàm hiển thị `_decorate_status` (`admin_regulatory.py`); không migration, `requirements.txt` không đổi.

- **Quy trình nhẹ (không backup/migration vì DB không bị ghi):** script ngoài Git `_ops/search-tools/ui-release-1d47a17/` dẫn xuất từ prepare/cutover 033 đã chạy trên production.
  - `prepare_ui_release.py` SHA256 `3e7c430c9f4919de0232344c509c82b792624c4501732bfd46b13851ec45a304` — tạo release bất biến mới, launcher `-launchers-rme`, drop-in chờ `zzzzzzzzzzz-ui-1d47a17.conf`; không đụng dịch vụ.
  - `switch_ui_release.py` SHA256 `e762b86abea706a365d02382ac23dbb9d7ee95ffdb2781d2359867b0acfeff48` — gate chỉ đọc → dừng worker, web → cài drop-in → khởi động cả hai → smoke (`/login` 200, `ui_system.css` khớp Git blob, số liệu không đổi, dịch vụ ổn định 5 s). Lỗi sau khi dừng → tự quay về `2ec9b6c` (chỉ gỡ drop-in đúng nội dung của mình, khi cả hai unit đã dừng).
  - Gói `search-tools-ui-2ec9b6c-to-1d47a17.bundle` SHA256 `fce57806497c080c882bf5fdbfef3bfba3b6cfaf26523e1dfac28ec9af0afc6c`.
  - Test developer `test_ui_release.py` 30/30 (SHA256 `18bfef5647c6c7f75935d02518e1dc4ca81c04c3a979148e56331a6900adcf6e`). Verifier độc lập lần 1 FAIL (D1: vòng chờ cổng web không thử lại; D2–D6 nhỏ) → sửa → verifier lần 2 **PASS**, góp thêm M1–M3 nhỏ (giữ tên ngắt của operator, kiểm hàng đợi sau khi dừng web, chờ dừng chậm khi rollback) đã sửa; test độc lập `independent/` 7/7; 2 test `independent2/` ghi lại hành vi M1 cũ nay báo đỏ đúng vì đã sửa.
- **Chạy (PO trên Mac):**
  1. Preflight chỉ đọc: web/worker active tại `…20261002T080450Z-2ec9b6c-clean`, cùng DB, PG 14.24, 030–033 = t, hàng đợi 0, trống 100,6 GiB.
  2. scp gói tới `/tmp` (370 KB).
  3. Prepare: `PREPARE complete`; release mới `/opt/search-tools-pg-release-20261002T104253Z-1d47a17-clean`; launcher đổi đúng 6 dòng đường dẫn; checkpoint `/var/backups/search-tools-production/ui-release-1d47a17/prepared.json`.
  4. PO báo nhân viên → switch: `PRODUCTION UI SWITCH PASS`; số liệu (products|stock|rules|inactive|statuses) `1106803|874|5831|7|7` giữ nguyên.
  5. Postflight chỉ đọc: web pid 2271374, worker pid 2271375 tại release `1d47a17`, cùng DB, 030–033 = t, hàng đợi 0. **PO kiểm tra ứng dụng: ổn.**
- Giữ lại: release `…2ec9b6c-clean` + `…-launchers-rme` và drop-in `zzzzzzzzzz-rme-2ec9b6c.conf` (bản trước); drop-in mới `zzzzzzzzzzz-ui-1d47a17.conf` trong `/etc/systemd/system/<unit>.d/`.
- **Quay lại bản trước (chỉ khi PO duyệt riêng):** dừng worker rồi web, gỡ đúng hai drop-in `zzzzzzzzzzz-ui-1d47a17.conf`, `daemon-reload`, xác nhận WorkingDirectory = release `2ec9b6c`, khởi động lại. Không cần đụng database (bản này không ghi DB).
