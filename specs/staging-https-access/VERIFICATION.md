# Staging HTTPS access — Verification v1

PO 2026-11-04 yêu cầu dùng `ssh staging` tìm và sửa lỗi không truy cập được staging. HIGH; chỉ staging. Hợp đồng lập trước thay đổi firewall.

## Claim V1 — Phạm vi kết nối hẹp

Chỉ bổ sung quyền HTTPS TCP 443 cho đúng một IPv4 của kết nối quản trị hiện tại; không mở mọi nguồn hoặc subnet, không xóa quyền cũ, không đổi SSH/HTTP/database/auth.

Oracle: dry-run và trạng thái UFW trước/sau chỉ khác một allow TCP 443 nguồn /32; default incoming deny giữ nguyên. Test type: command review + server postflight. Negative: địa chỉ có CIDR, rỗng, hoặc không trùng SSH peer phải bị script từ chối trước mutation.

## Claim V2 — Đúng staging

Chỉ thay đổi khi unit staging active, unit production inactive, checkout đúng bản đã triển khai và SSH peer trùng nguồn đã xác nhận.

Oracle: guard mismatch dừng trước UFW mutation; không có thao tác DB, restart, thay application/config khác. Test type: script adversarial mocks + review. Negative: sai SHA hoặc unit production active.

## Claim V3 — Truy cập và bảo vệ ứng dụng

URL HTTPS staging từ máy PO truy cập được với TLS hợp lệ; trang quản trị vẫn yêu cầu xác thực. Web/worker khỏe, không cần tắt firewall hay bỏ đăng nhập.

Oracle: curl HTTPS không dùng -k trả login 200; admin không trả nội dung quản trị cho khách; browser hiện login; UFW active và các unit active sau thay đổi. Negative: request admin chưa đăng nhập vẫn bị chặn/redirect login. Nếu lỗi kết nối vẫn tồn tại, không tuyên bố PASS và không tự mở rộng nguồn mạng.

## Hoàn tác / ngoài phạm vi

Nếu cần hoàn tác, chỉ xóa chính rule nguồn /32 TCP 443 vừa thêm sau đối chiếu trạng thái; giữ rule có sẵn. Không sửa production, DNS, database, credential, auth, không làm staging công khai cho mọi IP. Không cam kết IP động sẽ không đổi trong tương lai.

## Change request log

Không có.
