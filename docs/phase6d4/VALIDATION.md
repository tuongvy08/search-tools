# Phase 6D4 — READY FOR REVIEW

Kiểm chứng local ngày 2026-09-16. Baseline và HEAD giữ nguyên:
`ba86c5aa7c76dcc7cf21d4e789da6fb6dc018dbe`.
Không commit, push, tạo PR, staging hoặc production.

## Findings và cách xử lý

- Quyền `is_admin` cũ mở toàn bộ trang và API quản trị. Bổ sung registry 67 endpoint / 11 menu; menu ẩn theo quyền và request bị chặn độc lập với UI. Route quản trị đã đăng ký nhưng chưa ánh xạ bị từ chối, kể cả super admin.
- Menu Người dùng trước đây có thể đổi role và thao tác lên admin khác. Validator chung chạy trong transaction của các handler LOCAL/GOOGLE hiện hữu: admin phụ chỉ quản lý staff, không thăng cấp hoặc sửa tài khoản admin. Endpoint cấp menu mới chỉ dành cho super admin.
- Kiểm tra phiên trước khi chờ khóa chưa đủ để chặn quyền đã bị thu hồi. Writer/worker kiểm tra actor sau domain/admission lock và giữ row lock tới commit; account writers dùng cùng advisory lock trước actor/target. Các race test quan sát trạng thái chờ khóa thực trong PostgreSQL.
- Bảo vệ “admin cuối” cũ không phân biệt admin phụ. Migration/trigger bảo vệ ACTIVE super admin cuối cùng khi hạ cấp, suspend hoặc archive, kể cả hai thao tác đồng thời.
- CSRF tập trung có thể parse multipart trước giới hạn upload. Hook CSRF được đặt sau size hooks hiện hữu; regression xác nhận stock/import quá lớn trả 413 trước khi parse token.

## Kết quả kiểm thử

- **Focused: 110/110 đạt, 0 bỏ qua** — 19,982 giây. Gồm 26 test RBAC mới cùng các regression account, lifecycle, import/worker và migration liên quan.
- **Full suite: 1.048 test; 1.045 đạt, 3 bỏ qua, 0 lỗi** — 84,740 giây, chạy `python -m unittest discover -s tests -v`.
- Dùng PostgreSQL **14** thật trong container tạm riêng, không dùng DB phát triển hoặc DB nghiệp vụ. Migration 030 được kiểm tra bằng psql thật sau schema 029, chạy hai lần; kiểm tra admin inactive cũng được backfill, staff không đổi, hạ cấp không bị backfill lại, dữ liệu product và xmin không đổi.
- Các kiểm tra psql/migration trong full suite đã chạy, không bị bỏ qua vì thiếu psql. Wrapper local chỉ chuyển stdin vào psql của container kiểm thử và giới hạn DB mang tên pgtest.
- `admin_ux_dom_test.js`, `admin_regulatory_dom_test.js`, `quick_quote_export_dom.js`: đạt. `git diff --check`: đạt.
- Browser QA trên mã cuối: desktop 1440×1000 và mobile 390×844. Super admin thấy đủ menu và panel 11 quyền; lưu quyền thành công. Admin QA sau đăng nhập lại chỉ thấy Người dùng + Mạng/IP, không thấy panel cấp quyền hoặc lựa chọn nâng role, không tràn ngang. Browser dùng tài khoản LOCAL giả; parity GOOGLE được kiểm tra tự động, không chạy luồng OAuth Google thật.

Ba test bỏ qua đều thuộc `RealTemplateExportTests`, do không có `QUOTE_TEMPLATE_FIXTURE`:

- `test_export_api_with_active_template_returns_downloadable_workbook`
- `test_namespaces_and_calc_chain_fixed_for_one_full_and_over_capacity`
- `test_source_template_not_modified`

Log đầy đủ: [focused-tests.log](focused-tests.log), [full-tests.log](full-tests.log).
Ảnh QA: [super desktop](users-super-desktop.png), [super mobile](users-super-mobile.png), [admin desktop](users-admin-desktop.png), [admin mobile](users-admin-mobile.png).

## File thay đổi

- Mới: `admin_permissions.py`, `sql/migration_030_admin_menu_permissions.sql`, `tests/test_admin_menu_permissions.py`, tài liệu và ảnh trong `docs/phase6d4/`.
- Account: `admin_google_users.py`, `admin_lifecycle.py`, `admin_teams.py`, `scripts/bootstrap_admin.py`.
- Business/worker: `search.py`, `admin_products.py`, `admin_import_center.py`, `admin_regulatory.py`, `currency_rates.py`, `import_jobs.py`, `regulatory_import_jobs.py`, `stock_import_jobs.py`.
- UI: `templates/_user_nav.html`, `templates/admin_users.html`, `templates/admin_network.html`.
- Fixture và regression hiện hữu trong `tests/`: cập nhật schema 030, admin/grant rõ ràng, session invalidation và thứ tự khóa. Không bỏ qua kiểm tra transaction actor trong test bảo mật.

## Rủi ro và giới hạn còn lại

- Cần migration 030 và cutover đồng bộ web/worker; phiên admin cũ hết hiệu lực. Không rolling deploy hoặc rollback mã cũ khi có admin phụ vì mã cũ coi họ là toàn quyền. Chi tiết tại [OPERATIONS.md](OPERATIONS.md).
- Ba test workbook thực cần fixture ngoài repo để chạy; không tuyên bố đã xác minh bằng template production.
- Browser QA dùng dữ liệu tổng hợp local. Chưa kiểm chứng môi trường staging/production, Google OAuth thật hoặc hạ tầng production theo đúng phạm vi được giao.
- Endpoint quản trị mới phải được thêm vào registry và mutation phải dùng validator trong transaction. CLI ngoài HTTP vẫn theo quyền vận hành riêng.

Self-review không còn phát hiện chặn review trong phạm vi đã kiểm tra; đây là trạng thái chờ review, không phải phê duyệt triển khai.
