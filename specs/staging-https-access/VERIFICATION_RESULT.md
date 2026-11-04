# Kiểm chứng độc lập trước thay đổi staging HTTPS

Ngày 2026-11-04. Claude context sạch, chỉ artifact/hợp đồng và mock; không truy cập server, không sửa artifact. SHA256 script: `d05ab44af23073bd726ad21c8cec1e4dabc29c7f58e396149d48de41ffa231bb`. Báo cáo nguyên văn bên dưới; hậu kiểm runtime do agent chính ghi riêng trong `docs/regulatory-ui-data-quality/STAGING.md`.

Tôi không tìm thấy lỗi chặn. Tôi đã chạy 19 case bằng mock `systemctl`, `git`, `sudo` và `ufw` đặt trong `mock/`. Không dùng network, SSH hay firewall thật. `bash -n allow-current-ip.sh` pass. Kết quả nằm ở `result.txt` và harness ở `run-tests.sh`. Tôi không sửa artifact.

| Claim | Result | Classification |
|---|---|---|
| V1: chỉ một IPv4 /32, TCP 443 | PASS | Static và mock; chưa kiểm live |
| V2: đúng staging | PASS | Static và mock; chưa kiểm live |
| V3: TLS hợp lệ, admin vẫn yêu cầu đăng nhập, unit khỏe | Chưa kiểm | Cần hậu kiểm runtime thật |

**V1**
- Case hợp lệ: apply chạy đúng một lệnh `ufw allow from 8.8.8.8 to any port 443 proto tcp`. Dry-run dùng `--dry-run` và không tạo rule.
- Các case sau bị từ chối trước mọi mutation:
  - `8.8.8.8/32` và `/24` (exit 1).
  - Giá trị rỗng (exit 1).
  - Peer SSH không trùng (exit 3).
  - IP private `10.1.1.1` (exit 1).
  - Số 0 đứng đầu `8.8.8.08` (exit 1).
  - IPv6 (exit 1).
  - Thiếu `SSH_CONNECTION` (exit 3).
  - Không có tham số hoặc mode sai (exit 2).
- Script chỉ gọi `ufw allow`. Nó không có lệnh delete, reset, disable hay sửa SSH/HTTP/DB/auth.
- Nó chặn khi UFW không active hoặc default incoming không phải deny (exit 6).

**V2**
- Mỗi case sau dừng trước `ufw allow`:
  - Sai SHA (exit 5).
  - Working tree bẩn (exit 5).
  - `search-tools-pg.service` active (exit 4).
  - Unit staging hoặc worker inactive (exit 1).
- Script không có restart, DB hay thao tác config nào.

**Giới hạn cần lưu ý (không phải finding chặn)**
- Script coi `search-tools-pg.service` là unit production. Tên này và việc nó có đúng là production hay không, mock không xác nhận được. Cần đối chiếu trên server thật.
- Oracle "UFW trước/sau chỉ khác một rule" chưa được kiểm. Script chỉ in status sau apply, không lưu status trước. Việc so sánh phải làm ở postflight.
- IP SSH peer thuộc dải private hoặc CGNAT sẽ bị từ chối (fail-closed).
- Mock không chứng minh được hành vi thật của `ufw --dry-run`, sudo NOPASSWD hay giá trị `SSH_CONNECTION` qua `ssh staging 'bash -s'`. Cả ba cần kiểm ở runtime thật.
