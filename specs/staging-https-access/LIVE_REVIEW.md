# Review chéo và kiểm HTTPS độc lập — 2026-11-04

Claude phiên context sạch khác, chỉ Read/Bash; tự chạy hai curl HTTPS ẩn danh từ máy Mac của workspace. Không truy cập SSH hoặc credential. Báo cáo nguyên văn:

**Claim V3 (phần kiểm live, chỉ đọc):** PASS một phần, kèm giới hạn bên dưới.

| Kiểm tra | Kết quả |
|---|---|
| `/login` | `login=200 TLS=0`. TLS verify OK, không dùng `-k`. |
| `/admin/regulatory/rules` khi chưa đăng nhập | `anonymous_admin=302 redirect=https://staging.search.standards.vn/login`. Khách bị chuyển về login, không nhận nội dung admin. |

**Rà artifact `allow-current-ip.sh`:** tôi không thấy lỗi chặn.
- Script chỉ gọi `ufw allow ... port 443 proto tcp` cho đúng một IPv4.
- Script từ chối trước khi thay đổi nếu IP không hợp lệ hoặc không phải IP public. Nó cũng từ chối khi IP không trùng SSH peer, SHA sai, working tree bẩn, unit staging hoặc worker không active, hoặc unit `search-tools-pg` active. Nó cũng dừng khi UFW không active hoặc default incoming không phải deny.
- Không có lệnh delete, reset, restart, và không đụng DB hay auth.
- Một điểm nhỏ, không chặn: nếu `SSH_CONNECTION` không được đặt thì `set -u` làm script thoát với exit 1 thay vì 3. Script vẫn đóng an toàn trước khi thay đổi gì. `result.txt` ghi exit 3 cho case này, nên con số đó có thể sai trong harness thật.

**Giới hạn thực tế**
- Hai lệnh curl chạy từ máy hiện tại. Tôi không biết máy này có phải máy PO hay có đúng IP quản trị không. Việc truy cập được chỉ chứng minh endpoint trả lời từ nguồn này. Nó không chứng minh rule UFW đã được thêm, và cũng không chứng minh lỗi truy cập ban đầu đã được sửa cho IP của PO.
- Tôi không kiểm firewall hay service trên server. Các phần chưa kiểm là:
  - UFW active và default incoming deny;
  - web và worker active sau thay đổi;
  - UFW trước/sau chỉ khác một rule;
  - `search-tools-pg.service` có đúng là unit production không;
  - sudo NOPASSWD, `ufw --dry-run`, và `SSH_CONNECTION` qua `ssh staging 'bash -s'`.
- Tôi không kiểm hiển thị login trên browser.
- Tôi không chạy lại 19 mock vì không thấy lỗi mới cần chứng minh.

**Kết luận:** V1 và V2 giữ nguyên PASS ở mức static/mock. V3 chỉ PASS ở phần HTTPS/TLS và bảo vệ admin với khách, từ nguồn đã chạy curl. Các phần V3 còn lại cần kiểm trên server thật hoặc từ máy PO.


## Đối chiếu phạm vi của agent chính

Lượt review này không kiểm firewall/service/browser và không được ghi là đã kiểm các phần đó. Agent chính đã kiểm trực tiếp qua alias staging và browser, evidence ở `docs/regulatory-ui-data-quality/STAGING.md`. Nguồn curl là cùng máy Mac của workspace. Harness vòng đầu đặt SSH_CONNECTION rỗng, không bỏ biến: empty cho exit 3; biến unset làm set -u thoát sớm exit 1, đều chưa mutation. Không thay artifact để làm review pass.
