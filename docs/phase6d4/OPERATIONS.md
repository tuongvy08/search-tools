# Phase 6D4 — menu quản trị

Chỉ triển khai local trong thay đổi này. Chưa commit, push, tạo PR hoặc chạy staging/production.
Baseline: `ba86c5aa7c76dcc7cf21d4e789da6fb6dc018dbe` (main sau PR #13).
AGENTS.md không được theo dõi trong worktree; đã đọc bản hướng dẫn tại checkout gốc `/Volumes/DATA/Development/Search-tools/AGENTS.md` và PROJECT_STATE.md được chỉ định.

## Phạm vi

- `admin_permissions.py`: registry tường minh cho endpoint, 11 permission key, kiểm tra request, kiểm tra actor trong transaction, cấp quyền và audit.
- `sql/migration_030_admin_menu_permissions.sql`: `app_users.is_super_admin`, `admin_menu_grants`, `admin_rbac_events`; trigger bảo vệ super admin cuối cùng, invalidation và audit.
- Các handler quản trị và ba import worker dùng cùng kiểm tra authoritative. LOCAL/GOOGLE dùng cùng schema và quyền. Staff/team/IP giữ chính sách cũ.
- `_user_nav.html`: chỉ render link được cấp, dùng cùng markup desktop/mobile. Trang Người dùng hiện Super Admin/Admin/Staff, chỉ super admin thấy panel cấp menu và tùy chọn thăng cấp. Admin phụ có menu Người dùng chỉ quản lý staff.
- Cấp admin mới bằng luồng LOCAL/Google hiện hữu tạo admin phụ chưa có menu. Super admin cấp menu hoặc đổi thành super admin tại panel “Quyền menu quản trị”. Hạ xuống staff vẫn dùng form role/team hiện hữu. Panel mới chỉnh cấp/menu cho admin ACTIVE.
- Khóa/tạm lưu trữ và tự hạ xuống staff giữ hạn chế cũ; đổi chính mình từ super admin thành admin phụ được phép nếu còn một ACTIVE super admin khác.

## Migration và vận hành sau review

030 chạy trong một transaction, PostgreSQL 14. Lần đầu chuyển **mọi** admin đang có, kể cả LOCAL/GOOGLE hoặc đang suspend/archive, thành super admin; tăng auth_version và ghi sự kiện migration. Staff không bị sửa. Cột chỉ được tạo/backfill một lần nên chạy lại không nâng lại tài khoản đã hạ cấp.

Migration chỉ sửa auth/RBAC, không sửa bảng products hay dữ liệu nghiệp vụ. Grant có PK `(user_id, permission_key)`, FK tài khoản/người cấp và timestamp. Permission key có CHECK trong DB, registry là nguồn ánh xạ endpoint của ứng dụng.

Không rolling deploy giữa mã cũ và mới. Trong một release được duyệt sau này: kiểm tra live web/worker và DB thật, backup theo gate riêng, dừng cả web/worker, chạy 030, rồi chạy cả hai bằng bản mới. Migration làm phiên admin cũ mất hiệu lực; đăng nhập lại sau cutover. Job đã nhận bằng auth_version cũ phải được xem trước/upload lại nếu worker từ chối.

**Rollback mã cũ có rủi ro quyền:** mã trước 030 coi mọi `is_admin=true` là toàn quyền. Không rollback trực tiếp khi có admin phụ; cần kế hoạch được duyệt để tránh mở rộng quyền cho họ. Không tự drop schema hoặc phục hồi DB. Giữ backup và release trước.

Bootstrap mới bằng `scripts/bootstrap_admin.py` yêu cầu schema 030 và tạo super admin đầu tiên khi bảng người dùng trống, dưới cùng account advisory lock. Phiên mật khẩu manager dùng chung không có app_users/user_id không nhận quyền menu; dùng tài khoản quản trị định danh. Không thay đổi cấu hình login production trong phase này.

## Lock và thu hồi quyền

- Account/role/grant/team writers: account advisory lock `891273465` trước actor/target row. Actor được kiểm tra ACTIVE, auth_version, menu hoặc super status trong transaction; target được khóa trước khi đổi grants. Trigger không cho hạ/suspend/archive ACTIVE super admin cuối cùng.
- Business writers: lấy domain/admission advisory lock (nếu có), rồi khóa actor `FOR SHARE`, kiểm tra phiên và menu authoritative. Khóa actor giữ đến commit. Các writer không có domain advisory lock lấy actor trước business row locks. Nếu thao tác đã giữ actor lock thì revoke đợi commit; nếu revoke thắng trước, thao tác bị từ chối. Không có khoảng trống giữa lần kiểm tra và commit.
- SHARE cho các business actor cho phép job control/cancel đọc và khóa cùng actor mà không tạo vòng chờ với worker. Account/teams dùng UPDATE; grant writes cập nhật auth_version trên target nên xung đột với SHARE.
- Upload size hooks chạy trước CSRF multipart parsing. Mọi POST quản trị có CSRF tập trung, các kiểm tra cũ vẫn giữ. Bổ sung token cho Mạng/IP.
- Role/grant audit chỉ chứa ID actor/target, flags, menu keys và thời gian; không chứa password/hash/Google sub/token. Grant/role changes tăng auth_version; hạ về staff xóa grants để lần thăng cấp sau không tự phục hồi quyền cũ.

## Quy tắc bổ sung endpoint

Mỗi endpoint quản trị mới phải vào `ENDPOINT_PERMISSIONS` với một trong 11 key. Request tới đường dẫn quản trị không ánh xạ bị từ chối; kiểm tra quyền dùng endpoint, không suy quyền từ prefix. Regression đọc route map trong interpreter mới để không lẫn route giả do test khác đăng ký. Viết mutation phải gọi validator trong transaction và giữ thứ tự khóa đã nêu, bao gồm background worker.
