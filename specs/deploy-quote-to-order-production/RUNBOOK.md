# Runbook — đưa menu "Báo giá → Đơn hàng" lên production (bản `3ceb295`)

**Đích: production, alias `ssh python`.** Không dùng `ssh staging`. Chạy trên Mac của PO, trong thư mục
`/Volumes/DATA/Development/_ops/search-tools`. Mỗi bước dừng lại nếu kết quả không đúng như cột "Mong đợi" và báo lại cho agent; không chạy bước kế.

Bản đang chạy: `1d47a17` → bản mới: `3ceb29570bc5a4e42b0070b3adb9808abed3529e`. Không migration, không ghi database, không đổi `.env`.
Bước 4 dừng web và worker khoảng vài chục giây: **báo nhân viên trước.**

| # | Lệnh | Mong đợi |
| --- | --- | --- |
| 1. Kiểm tra trước (chỉ đọc) | `ssh python 'sudo -n python3 -u - search-tools-pg.service search-tools-import-worker.service /var/backups/search-tools-production' < preflight_readonly.py` | web + worker `active`, chạy tại release `…1d47a17-clean`, cùng một database, không lỗi |
| 2. Gửi gói | `scp search-tools-qto-1d47a17-to-3ceb295.bundle python:/tmp/` rồi `ssh python 'sha256sum /tmp/search-tools-qto-1d47a17-to-3ceb295.bundle'` | hash = `d24e2218e046848044e05db881f57f1283f56a7822d54e20edd7a8eb251e0101` |
| 3. Chuẩn bị release mới (chưa đụng dịch vụ) | `ssh python 'sudo -n python3 -u - --production' < qto-release-3ceb295/prepare_qto_release.py` | `PREPARE complete` và dòng "NO service stop…" |
| 4. Chuyển sang bản mới (**báo nhân viên**) | `ssh python 'sudo -n python3 -u - --production' < qto-release-3ceb295/switch_qto_release.py` | `PRODUCTION QTO SWITCH PASS` (login=200, hash file JS khớp, số liệu giữ nguyên) |
| 5. Kiểm tra sau (chỉ đọc) | lại lệnh ở bước 1 | web + worker `active` tại release mới `…3ceb295-clean`, cùng database |

Sau bước 5, PO đăng nhập, mở menu **Báo giá → Đơn hàng**, thử một file báo giá thật.

Nếu bước 4 lỗi: script tự dừng hai dịch vụ, gỡ đúng hai file cấu hình vừa cài và chạy lại bản cũ (`ROLLBACK DONE`); dữ liệu không bị đổi.
Nếu thấy `ROLLBACK_UNCONFIRMED`: **dừng, không chạy lại script**, báo agent (dịch vụ có thể đang dừng).
Nếu bước 3 lỗi sau khi đã tạo thư mục: báo agent dọn tay trước khi chạy lại (script từ chối chạy lại để khỏi ghi đè).

Quay lại bản trước sau khi đã chuyển xong (chỉ khi PO duyệt riêng): gỡ hai file `zzzzzzzzzzzz-qto-3ceb295.conf` trong
`/etc/systemd/system/search-tools-pg.service.d/` và `search-tools-import-worker.service.d/`, `daemon-reload`, khởi động lại hai dịch vụ.

Kiểm chứng: 30 test của agent + 61 test độc lập của verifier (11/11 biến thể cố ý hỏng bị bắt), xem `VERIFICATION_RESULT.md`.
**Đã chạy trên production 2026-10-04 (UTC ~16:11–16:13): cả 5 bước đạt**, release mới `/opt/search-tools-pg-release-20261004T161144Z-3ceb295-clean`; chi tiết trong `PROJECT_STATE.md`.
