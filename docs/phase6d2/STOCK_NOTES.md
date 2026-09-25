# Phase 6D2 — Ghi chú tồn kho — READY FOR REVIEW

Baseline: `5e6cc6bee603d0e4037681ce9533ef030087adc9`.
Checkout: `/Users/truong/.codex/worktrees/phase6d2-stock-note-integration/Search-tools`.
WIP tại checkout `8fdb/Search-tools` được giữ nguyên, không dùng để phát hành.

## Hành vi

- File mẫu có 9 cột: Name, Code, Cas, Brand, Size, Giá tồn kho, Số lượng tồn,
  Hạn sử dụng, Ghi chú. Cột cuối tùy chọn, tối đa 2.000 ký tự, có thể xuống
  dòng; chấp nhận tên tiếng Anh `note` hoặc `notes`. Công thức bị từ chối.
- File cũ 8 header vẫn nhập được, tạo ghi chú trống (không đọc ô thứ 9 không
  có header). Import vẫn thay toàn bộ snapshot, không giữ ghi chú cũ khi bỏ cột.
- Ghi chú không thay đổi identity Brand + Code + Size + Hạn sử dụng.
  Hai dòng chỉ khác kho/ghi chú vẫn bị xem là trùng, không phải quản lý đa kho.
- Preview, change detection và plan digest tính cả ghi chú. Apply và restore
  giữ đúng ghi chú. Search/Find Code trả `Stock_Note` theo `VIEW_NOTE`.
  Preview dùng Jinja autoescape; giao diện kết quả dùng `textContent` và giữ
  xuống dòng, không thực thi HTML.
- Ghi chú tồn kho độc lập với Note của catalog; không thêm vào Copy, xuất dữ
  liệu hoặc báo giá. Không thay products, giá báo giá hay quyền team.

## Tích hợp và migration

Không sửa migration 029/030. `migration_031_stock_notes.sql` đứng sau
`migration_030_admin_menu_permissions.sql` trong test schema.
Migration 031 transactional và idempotent: thêm `stock_note TEXT NOT NULL
DEFAULT ''` cùng CHECK tối đa 2.000 ký tự. Dữ liệu cũ nhận chuỗi trống;
không đổi active snapshot, revision, metadata snapshot hoặc products.

Nếu cột đã tồn tại, migration kiểm tra kiểu TEXT, NOT NULL, default chuỗi
trống và constraint đã validate `stock_items_stock_note_length_check` đúng
biểu thức `length(stock_note) <= 2000`. Schema thiếu/sai bị từ chối và rollback,
không tự sửa hoặc âm thầm bỏ qua. Kiểm tra cố ý nghiêm ngặt: constraint/default
viết khác dù có vẻ tương đương cũng cần người vận hành kiểm tra trước khi thử
lại. Không dùng migration 030 ghi chú từ WIP cũ vì số 030 đã dành cho admin RBAC.

Giữ nguyên product type/Quick Quote Phase 6D3, các actor/grant checks và lock
order Phase 6D4, Check License Phase 6D5. Chỉ thêm 6 dòng vào renderer tồn kho,
1 rule CSS riêng. Cache `script.js`/`styles.css` tăng từ `20260916d5v1` lên
`20260925d5n1` ở mọi template và hai bộ test cache; không đổi version
`quick_quote.js`. Các sửa fixture BytesIO/CAS đã có trên main, không port lại.

## Vận hành và giới hạn

Đây là thay đổi local chờ review, không phải phê duyệt triển khai.
Chưa commit/push/PR, staging/production hoặc chạy migration trên DB thật.

Khi được phê duyệt triển khai: dừng web và tất cả import worker, áp dụng 031
sau 030 bằng `psql -v ON_ERROR_STOP=1 -f sql/migration_031_stock_notes.sql`,
xác minh rồi khởi động đồng bộ mã mới. Migration giữ ACCESS EXCLUSIVE lock;
cần maintenance window phù hợp kích thước tồn kho, không rolling deploy.
Preview tạo bằng mã cũ phải tạo lại vì plan digest đã thêm ghi chú.
Không chạy worker cũ sau khi đã có ghi chú: worker cũ có thể tạo snapshot mới
với ghi chú trống. Việc rollback/cutover cần được review riêng và giữ nguyên
các yêu cầu vận hành admin RBAC của Phase 6D4.

Kiểm thử dùng PostgreSQL 14 trong container dùng một lần, chỉ bind localhost;
mọi business write nằm trong DB `pgtest` do test helper tạo. Không dùng `.env`
hay database nghiệp vụ. Psql adapter cục bộ chỉ cho phép container test cố định,
đúng host/port và tên DB chứa `pgtest_`.

## Bằng chứng kiểm thử — 25/09/2026

- Python 3.12, dependencies của project; PostgreSQL 14 thật, bao gồm `psql`
  theo từng statement (không bọc transaction từ runner).
- Focused cuối: **95/95 pass, 0 skip**, 13,718 giây. Gồm notes/parser/migration,
  Phase 6D2, admin RBAC, admin UX/nav và cache contracts.
- Full cuối: **1.064 test; 1.061 pass, 3 skip, 0 fail/error**, 79,539 giây,
  chạy `python -m unittest discover -s tests -v` với `PYTHONPATH=.:tests` và
  `DATABASE_URL` trỏ container test, không phải DB ứng dụng.
- Ba skip thuộc `RealTemplateExportTests`: export API với active template,
  namespace/calc chain full và over-capacity, source template không đổi.
  Nguyên nhân: không cung cấp `QUOTE_TEMPLATE_FIXTURE`. Không có skip do thiếu
  PostgreSQL hoặc psql.
- Node syntax của `static/script.js`; DOM suites `stock_notes_dom_test.js`,
  `license_results_dom_test.js`, `admin_ux_dom_test.js`,
  `admin_regulatory_dom_test.js`, `quick_quote_export_dom.js`: pass.
  `git diff --check`: pass. Đây là kiểm thử DOM tự động, chưa có browser visual QA.
- Regression migration kiểm tra backfill/reapply và giữ nguyên business rows;
  thiếu/sai default, nullable, VARCHAR/INTEGER, thiếu CHECK, CHECK yếu/sai cột/
  chưa validate đều fail closed. Psql kiểm chứng rollback khi schema không hợp
  lệ và khi lỗi xảy ra ngay lúc ADD COLUMN; DB từ chối NULL/2.001 ký tự.
- Regression ghi chú kiểm tra 8/9 header, alias, ô không có header, 2.000/2.001
  ký tự, formula/XSS, note-only change/digest, apply/search/find-code, có/không
  VIEW_NOTE, restore, file cũ xóa ghi chú trong snapshot mới, identity không đổi.

Log cục bộ (ngoài Git):

- `/tmp/stock-notes-integration.PKqiIa/focused-tests-final.log`
- `/tmp/stock-notes-integration.PKqiIa/full-tests-final.log`

Lần full đầu chỉ lỗi một kỳ vọng cache Quick Quote còn dùng version cũ;
đã cập nhật kỳ vọng (không đổi logic Quick Quote) và chạy lại toàn bộ như trên.

## File thay đổi và self-review

- Backend: `admin_stock.py`, `stock.py`, `stock_import_jobs.py`, `team_permissions.py`.
- UI: `static/script.js`, `static/styles.css`, `templates/admin_stock.html`.
- Cache references: `templates/index.html`, `templates/quick_quote.html`,
  `templates/admin_regulatory.html`.
- Migration mới: `sql/migration_031_stock_notes.sql`.
- Tests: `tests/pg_temp_db.py`, `tests/test_phase6d2_stock.py`,
  `tests/test_stock_notes.py`, `tests/test_stock_notes_migration_pg.py`,
  `tests/stock_notes_dom_test.js`, `tests/test_static_asset_versions.py`,
  `tests/test_quick_quote.py`.
- Tài liệu: `docs/STOCK_NOTES.md`.

Self-review diff/security/deployment: chỉ mở rộng trường ghi chú, không thay
actor validation, khóa writer, SQL parameterization, identity, product type,
Check License controls hoặc mapping xuất báo giá. Không có query tồn kho bổ
sung theo từng dòng. VIEW_NOTE được lọc tại query serializer và recursive
redaction. Migration không có UPDATE/DELETE trên dữ liệu nghiệp vụ; không sửa
migration cũ. Chưa phát hiện lỗi chặn review trong phạm vi đã kiểm chứng.
Các giới hạn còn lại là fixture báo giá thật, visual QA và rehearsal trên
staging/production — tất cả chưa thực hiện trong task local này.
