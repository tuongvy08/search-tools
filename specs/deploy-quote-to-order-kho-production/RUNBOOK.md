# Runbook — đưa cột "Kho yêu cầu" (menu Báo giá → Đơn hàng) lên production (bản `09610a1`)

**Đích: production, alias `ssh python`.** Không dùng `ssh staging`. Chạy trên Mac của PO, trong thư mục
`/Volumes/DATA/Development/_ops/search-tools` (lệnh `cd` ở bước 0). Mỗi bước dừng lại nếu kết quả không đúng cột "Mong đợi" và báo lại cho agent; không chạy bước kế.

Bản đang chạy: `3ceb295` → bản mới: `09610a19931a1b862dd4f8b7020c170efbab65f5`. Không migration, không ghi database, không đổi `.env`.
Bước 4 dừng web và worker khoảng vài chục giây: **báo nhân viên trước.**

| # | Lệnh | Mong đợi |
| --- | --- | --- |
| 0. Vào thư mục | `cd /Volumes/DATA/Development/_ops/search-tools` | — |
| 1. Kiểm tra trước (chỉ đọc) | `ssh python 'sudo -n python3 -u - search-tools-pg.service search-tools-import-worker.service /var/backups/search-tools-production' < preflight_readonly.py` | web + worker `active`, chạy tại release `…20261004T161144Z-3ceb295-clean`, commit `3ceb2957…`, cùng một database, 030–033 = t, hàng đợi 0 |
| 2. Gửi gói | `scp search-tools-kho-3ceb295-to-09610a1.bundle python:/tmp/` rồi `ssh python 'sha256sum /tmp/search-tools-kho-3ceb295-to-09610a1.bundle'` | hash = `5a3a5383c8dff268cbc7628f9ec6a44ebabbc217f1cafa75cca9905de5184269` |
| 3. Chuẩn bị release mới (chưa đụng dịch vụ) | `ssh python 'sudo -n python3 -u - --production' < kho-release-09610a1/prepare_kho_release.py` | `PREPARE complete` và dòng "NO service stop…" |
| 4. Chuyển sang bản mới (**báo nhân viên**) | `ssh python 'sudo -n python3 -u - --production' < kho-release-09610a1/switch_kho_release.py` | `PRODUCTION QTO SWITCH PASS` (login=200, hash file JS khớp, số liệu giữ nguyên) |
| 5. Kiểm tra sau (chỉ đọc) | lại lệnh ở bước 1 | web + worker `active` tại release mới `…09610a1-clean`, commit `09610a19…`, cùng database |

Sau bước 5, PO đăng nhập, mở **Báo giá → Đơn hàng**, thử báo giá có/không có cột "Kho yêu cầu" và tải file đơn hàng 14 cột.

Nếu bước 4 lỗi: script tự dừng hai dịch vụ, gỡ đúng hai file cấu hình vừa cài và chạy lại bản cũ (`ROLLBACK DONE`); dữ liệu không bị đổi.
Nếu thấy `ROLLBACK_UNCONFIRMED`: **dừng, không chạy lại script**, báo agent (dịch vụ có thể đang dừng).
Nếu bước 3 lỗi sau khi đã tạo thư mục: báo agent dọn tay trước khi chạy lại (script từ chối chạy lại để khỏi ghi đè).

Quay lại bản trước sau khi đã chuyển xong (chỉ khi PO duyệt riêng): gỡ hai file `zzzzzzzzzzzzz-qto-09610a1.conf` trong
`/etc/systemd/system/search-tools-pg.service.d/` và `search-tools-import-worker.service.d/`, `daemon-reload`, khởi động lại hai dịch vụ
(các drop-in `rme`, `ui-1d47a17`, `qto-3ceb295` cũ giữ nguyên nên bản `3ceb295` sẽ chạy lại).

**Đã chạy trên production 2026-10-05: cả 5 bước đạt** (release mới `/opt/search-tools-pg-release-20261005T101557Z-09610a1-clean`); chi tiết trong `PROJECT_STATE.md`.
