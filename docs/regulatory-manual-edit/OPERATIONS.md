# Local verification / rollback — regulatory-manual-edit

Chỉ môi trường thử local; không dùng tài liệu này như quyền deploy server.
Hợp đồng v2 khóa tại commit `ee2ee5a`; không sửa VERIFICATION.md.

## Đích đã xác minh trong phiên triển khai

- Docker context `desktop-linux`, unix socket local; project Compose riêng
  `search-tools-regulatory-test`, service `db`, image `postgres:16-alpine`.
- Container `search-tools-regulatory-test-db-1`, published port 5432.
  Không dùng container/database của project khác. Không đọc `.env`.
- Tạo container bằng Compose của repo với `--env-file /dev/null`, project
  riêng (không dùng volume `search-tools` local của người dùng).
- Test runner tự xác minh lại Docker endpoint/labels/port trước import test
  hoặc mở DB. Kết nối bảo trì `postgres` chỉ để tạo/drop DB tạm;
  dữ liệu test trong DB prefix `p6a_release_gate_pgtest_`, cleanup theo helper.
- `tests/no_dotenv/sitecustomize.py` chặn load_dotenv cho process tests và
  process Python con. Không khởi động app/test bằng lệnh bỏ qua bootstrap này.

## Lệnh developer tests / verifier

```bash
.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v
.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v
node tests/regulatory_manual_dom_test.js
node tests/admin_regulatory_dom_test.js
```

Runner không tự khởi động Docker. Nếu thiếu container thì dừng; chỉ khi đã
được phép dùng local test mới khởi động bằng:

```bash
docker compose --env-file /dev/null -f docker-compose.yml -p search-tools-regulatory-test up -d db
```

Migration/rollback test qua psql thật và fixture trước 033. Không chạy SQL
trực tiếp vào `products_local`; không seed/reset dữ liệu local của người dùng.

## Rollback

- Dừng mọi web/worker ghi regulatory trước khi đổi phiên bản. Không chạy
  worker cũ với dữ liệu được bảo vệ; code cũ không hiểu protection.
- Migration lỗi trước commit: transaction rollback, không chạy app mới.
- `sql/rollback_033_regulatory_manual_edit.sql` chỉ trên DB local thử đã xác
  minh: guard từ chối nếu có protected/revision thay đổi/khóa/audit/JSON job
  hoặc event contract v2. Không DROP CASCADE, không tắt guard để rollback.
- Nếu đã dùng tính năng: giữ schema/audit, dừng writer, ưu tiên forward fix.
  Nếu chưa chứng minh chặn được riêng mọi writer, dừng toàn bộ web/worker;
  không tuyên bố read-only mà vẫn để route import hoạt động.
- Muốn phục hồi dữ liệu: backup → restore vào DB local mới → đối chiếu →
  xin duyệt chuyển. Git revert không khôi phục DB. Mất thay đổi sau backup
  phải được người dùng chấp thuận; không restore đè database hiện hành.
- Test migration có rehearsal pg_dump/restore vào DB tạm khác, không phải
  bằng chứng backup/rollback production. Staging/production cần yêu cầu riêng.

## Giới hạn

- JSON chi tiết giữ/xung đột tối đa 8 MiB, không cắt bớt hoặc apply một phần.
  Upsert có thể chia theo dòng; replace_scoped chỉ chia các scope không giao
  nhau, đầy đủ mỗi scope. Không chia một scope thành nhiều lần replace.
- Số sản phẩm ảnh hưởng chưa làm; không đếm products khi xác nhận rule.
- UI được kiểm tra bằng Flask test client + Node DOM harness và PO đã
  nghiệm thu browser local 8/8 + hiển thị 4/4. Chưa staging/production;
  không tự coi developer tests là verifier PASS.

## UAT local đã được PO duyệt (2026-09-28)

- Database UAT riêng chứa dữ liệu tổng hợp, tạo trên môi trường local đã
  xác minh; áp dụng schema và migrations tới 033. Không clone hay đọc DB
  local gốc. Web và worker chỉ xử lý regulatory cùng trỏ DB UAT, ứng dụng
  chỉ bind loopback; không chạy worker tổng hợp trên dữ liệu UAT.
- Thông tin đăng nhập và cấu hình launcher UAT nằm trong thư mục tạm riêng
  của agent, ngoài repository, không commit. Không ghi mật khẩu vào tài liệu,
  log hoặc Git. File Excel mẫu tổng hợp nằm trong `uat-samples/` của phase.
- Không tự chạy lại setup/reseed khi DB UAT đã tồn tại. Sau khi khởi động
  lại máy phải xác minh lại đích, trạng thái dịch vụ và DB, giữ nguyên dữ
  liệu UAT; nếu mất thông tin đăng nhập thì xử lý có kiểm soát, không đoán
  mật khẩu hoặc xóa database.
- UAT browser/checklist: [UAT.md](UAT.md). PO xác nhận chức năng 8/8 và
  hiển thị 4/4 đạt; chưa staging/production/merge.
