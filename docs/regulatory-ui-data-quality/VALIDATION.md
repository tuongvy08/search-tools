# Menu quy tắc và chất lượng đầu vào tình trạng — 2026-11-02

## Checkpoint hiện hành — CAS-only, HIGH

### Bổ sung đã duyệt 2026-11-04

PO đã đồng ý xử lý CAS chữ số đặc biệt. Hợp đồng v2 làm rõ từ chối, không
tự chuyển đổi. Đã thêm kiểm tra số ASCII khi chuẩn hóa CAS, thông báo nhập
lại bằng số `0-9`; áp dụng cho nhập tay/Excel/preview và khôi phục CAS cũ.
Không sửa dữ liệu cũ trong database. Test mới phủ fullwidth, số Ả Rập,
dạng trộn, dấu gạch đặc biệt, file có dòng đúng trước dòng sai, API check/
create/edit/restore và bảo toàn hàng cũ sau từ chối.

Đã chạy lại toàn bộ các module bên dưới cộng `independent.test_cas_verifier`:
**199 tests, OK** (40,407 giây), hai DOM script và `git diff --check` PASS.
Ca Unicode từng FAIL của verifier v1 hiện PASS. Báo cáo v1 được giữ tại
`specs/regulatory-cas-only/VERIFICATION_RESULT_v1.md`.

Sau PO đăng nhập lại, verifier v2 context sạch đã **PASS cả V1–V6**:
211 test (164 test hiện hành + 35 test độc lập v1 + 12 test mới độc lập v2)
và hai DOM script exit 0. Verifier dùng dữ liệu giả trên PostgreSQL tạm;
không có finding mở. Hash xác nhận không sửa file nguồn nào, chỉ tạo report
và `tests/independent/test_cas_verifier_v2.py`. Báo cáo đầy đủ:
[VERIFICATION_RESULT](../../specs/regulatory-cas-only/VERIFICATION_RESULT.md).
Lỗi OAuth trước đó không tính repair; `attempt=0`, `HUMAN_REVIEW`.
Review chéo chỉ đọc chạy riêng sau verifier PASS: **không có finding chặn**.
Chỉ dùng Read/Glob/Grep, hash nguồn giữ nguyên. Bản gốc nhận xét:
[CROSS_VENDOR_REVIEW](../../specs/regulatory-cas-only/CROSS_VENDOR_REVIEW.md).
Các lưu ý thấp/ngoài phạm vi được giữ ở OPERATIONS; không coi là quyền
thực thi production. Browser local đã kiểm trong lần trước; bổ sung v2
chỉ đổi validation backend, HTTP đã chứng minh lỗi hướng dẫn nhập lại.
PO đã nghiệm thu local, đã commit `e33b0a2`, push và tạo PR #41; staging technical smoke PASS. UAT đăng nhập còn chờ, xem [STAGING](STAGING.md). Chưa merge hoặc ghi production.

### LỊCH SỬ — Checkpoint trước bổ sung

PO đã làm rõ và duyệt tiếp tục: tự động chỉ đối chiếu CAS, tình trạng gán
riêng thắng khi hãng bật ưu tiên. Không triển khai đề xuất loại trừ từng
CAS khỏi quy tắc tên. Tài liệu bên dưới phần này ghi lại lần kiểm tra cũ;
các chỗ nói chưa sửa resolver/chưa chạy PostgreSQL không còn là trạng thái
hiện hành. Nguồn nghiệp vụ mới: `docs/BUSINESS_RULES.md`.

Đã sửa local: resolver dùng chung, form/API/import chỉ CAS; giữ quy tắc
tên/mã cũ để xem/ngừng; fingerprint có phiên bản chính sách; nhãn CAS không
được tạo hoặc chọn cho quy tắc mới/khôi phục; menu thống nhất. Giữ nguyên
tìm kiếm sản phẩm tên/mã/CAS và cờ ưu tiên thủ công của hãng.

Kiểm chứng bằng PostgreSQL 16 tách biệt, Compose project
`search-tools-regulatory-test`, dữ liệu giả; mọi DB test có tiền tố riêng và
được dọn bởi helper. Không đọc `.env`, không dùng DB sản phẩm local có sẵn:

```sh
.venv/bin/python -B scripts/test_regulatory_local.py test_regulatory_cas_only test_regulatory_status_cleanup test_phase6d1_regulatory test_regulatory_manual_service test_regulatory_manual_http test_regulatory_manual_import test_regulatory_manual_display test_search_compliance_precedence test_search_result_display test_regulatory_status_validation test_admin_nav_shell_ui -q
# 103 tests, OK
.venv/bin/python -B scripts/test_regulatory_local.py test_admin_brand_compliance test_quote_assistant_api -q
# 59 tests, OK
.venv/bin/python -B scripts/test_regulatory_local.py test_regulatory_cas_only test_regulatory_manual_display -q
# 8 tests, OK; kiểm lại sau bổ sung restore nhãn sai và câu chữ giao diện
node tests/regulatory_manual_dom_test.js
node tests/admin_regulatory_dom_test.js
git diff --check
# PASS
```

Test dọn nhãn gồm CLI mặc định chỉ đọc; xóa đúng ID và giữ audit; references
từ rule/product/recovery/manual-key/status-event; dữ liệu đổi sau kiểm;
giao dịch cạnh tranh giữ tham chiếu; lỗi audit rollback xóa. Bảng recovery
trong test dùng fixture FK tối thiểu đúng quan hệ production, không phải
rehearsal phục hồi toàn bộ database. Chưa chạy script trên production.

Browser local với template/static thật, Flask riêng và dữ liệu giả (không DB):
menu active trên trang thêm và tên cũ; biểu tượng Font Awesome có glyph;
mở/đóng menu bằng Escape; form mới chỉ có CAS, nhãn CAS sai vắng khỏi lựa
chọn; quy tắc tên cũ readonly, chỉ cho ngừng và xem trước đúng trước/sau.
Không bấm lưu vào ứng dụng thật. Đây là smoke giao diện; HTTP/database
được kiểm riêng bằng các test bên trên.

**Verifier đã chạy sau khi PO đăng nhập lại:** Claude dùng bản xuất sạch hiện
hành, không có `.env`, database thật hoặc hồ sơ production. Kết quả ở
[VERIFICATION_RESULT](../../specs/regulatory-cas-only/VERIFICATION_RESULT.md):
V1/V2/V4/V5/V6 PASS; V3 có một phát hiện `SPEC_AMBIGUOUS`: chữ số toàn-rộng
như `６４-１７-５` được lưu nguyên dạng và không khớp CAS thông thường.
35 test độc lập: 34 PASS, 1 FAIL; verifier báo 157 test hiện hành PASS.
Không sửa file nguồn nào trong export; chỉ tạo report và
`tests/independent/test_cas_verifier.py` (đã kiểm SHA256 các file trước/sau).

Theo workflow mục 11, đã dừng để hỏi PO có bổ sung việc từ chối chữ số đặc
biệt và hướng dẫn nhập lại CAS thông thường không. Chưa sửa hợp đồng hoặc
implementation theo phát hiện này. `attempt=0`, `next_action=ESCALATE_USER`;
không tính `SPEC_AMBIGUOUS` là một lần repair. Sau PO chốt cần cập nhật version
hợp đồng, test và chạy verifier mới với context sạch. Review chéo sau PASS
chưa chạy. Verifier mở rộng thêm test migration ngoài phạm vi nhưng không
chạy được do thiếu psql runner; không có migration trong bản sửa này.
Chưa commit/push/staging/deploy hoặc ghi production. Không báo DONE.

Kế hoạch bước tiếp theo và rủi ro chuyển chính sách: [OPERATIONS](OPERATIONS.md).

## Lịch sử kiểm tra ban đầu — trước làm rõ nghiệp vụ CAS-only

Branch: `codex/fix-regulatory-menu-status`. Mức MEDIUM cho phần local.
PO giao Codex tiếp tục thực hiện sau lượt rà soát; cho phép mở web và PO
đăng nhập để kiểm tra. Chưa có phê duyệt commit/push/deploy hoặc chỉnh dữ
liệu production cụ thể.

## Đã xác minh và sửa local

- Thanh menu dùng chung chưa nhận các đường dẫn con `/admin/regulatory/`;
  đã bổ sung nhận diện cho nút Quản trị và mục Quy tắc quản lý, có kiểm tra
  ranh giới đường dẫn để `/admin/regulatory-other` không bị đánh dấu nhầm.
- Các trang danh sách, thêm/sửa và chi tiết giữ/xung đột thiếu Font Awesome;
  đã dùng cùng phiên bản biểu tượng và CSS menu với trang tổng quan.
- Danh mục tình trạng động có thể nhận nhãn chỉ chứa mã CAS qua thêm/đổi
  tên hoặc import quy tắc. Thêm validation dùng chung: tên tình trạng không
  được chỉ là chuỗi có hình thức CAS (kể cả sai checksum); báo hướng dẫn
  chuyển CAS sang cột/ô đối chiếu. Tên phân loại tự đặt vẫn được chấp nhận.
- Import kiểm tra cả lúc đọc workbook lẫn dựng kế hoạch để dữ liệu đã parse
  trước đó không bỏ qua validation. Không sửa migration, resolver, chính sách
  xuất báo giá, dữ liệu cũ hoặc ẩn các tình trạng đã tồn tại.

## Kiểm chứng

PASS — 35 bài kiểm tra, không cần DB; chạy lại với bootstrap chặn dotenv
và DSN giả trỏ cổng local không sử dụng:

```sh
REGULATORY_LOCAL_TEST=1 PYTHONPATH=tests/no_dotenv:.:tests \
FLASK_SECRET_KEY=synthetic-regulatory-test GOOGLE_AUTH_ENABLED=false \
ENABLE_LEGACY_PASSWORD_LOGIN=false \
DATABASE_URL=postgresql://unused:unused@127.0.0.1:1/unused \
.venv/bin/python -B -m unittest test_regulatory_status_validation \
test_admin_nav_shell_ui test_phase6d1_regulatory.Phase6D1PureTests -v
```

- Workbook thật chứa dữ liệu giả: CAS đúng cột vẫn đọc được; CAS trong cột
  tình trạng bị từ chối với số dòng. Kế hoạch upsert/replace_scoped từ chối
  đầu vào sai trước truy vấn DB.
- HTTP thêm/đổi tên tình trạng (mock ranh giới DB): trả lỗi tiếng Việt,
  không phát lệnh ghi khi tên là CAS. Đây không phải kiểm chứng transaction
  PostgreSQL thật.
- Menu: kiểm cả admin/staff, đường dẫn con, đường dẫn gần giống, logout/CSRF.
- `node tests/regulatory_manual_dom_test.js`: PASS ba trường CAS/mã/tên;
  preview, payload/revision, retry và invalidation vẫn đúng.
- `git diff --check`: PASS.
- Browser local với Flask preview độc lập, template/static thật và dữ liệu
  giả, không DB: trang danh sách và thêm mới hiển thị đủ biểu tượng, nút
  Quản trị active; mở menu thấy Quy tắc quản lý active; Escape đóng menu.
  Harness ban đầu thiếu filter thời gian nên trang thêm trả 500; bổ sung
  `regulatory_vn_time` sẵn có vào harness, reload và kiểm lại đạt. Không có
  thay đổi application code để xử lý lỗi harness.
- Môi trường Python có cảnh báo urllib3/LibreSSL đã tồn tại; test vẫn PASS.
- Chưa chạy tích hợp PostgreSQL, staging hoặc nghiệm thu production.

## Kết quả tiếp tục sau đăng nhập và việc còn lại

1. PO đã đăng nhập, kiểm tra web chỉ đọc hoàn tất trong phạm vi [REPAIR_PLAN](REPAIR_PLAN.md).
   Hai nhãn `1563-66-2`, `944-22-9` không có quy tắc liên kết (lọc tất cả),
   nhưng chưa kiểm toàn bộ tham chiếu sản phẩm khác/lịch sử. UI không có
   chức năng xóa tình trạng hoặc tìm toàn hệ thống theo tình trạng thủ công.
2. CAS `629-14-1`: đã xác nhận quy tắc tên Diethyl ether đang hoạt động với
   nội dung cảnh báo trùng ảnh; 13 sản phẩm CAS này đều không có gán riêng,
   9 tên khớp chuỗi. Quy tắc theo CAS Được bán đang ngừng. Không thể tắt toàn
   bộ quy tắc tên an toàn: danh mục còn dung dịch chứa Diethyl ether và sản
   phẩm thiếu CAS. Không đưa ra kết luận pháp lý về hóa chất.
3. Lập phương án chỉnh đúng nguồn với danh sách ảnh hưởng và trước/sau;
   phần sửa production cần phê duyệt cụ thể và gate HIGH theo workflow.
   Không thêm ngoại lệ Được bán để che nguyên nhân chưa rõ.
4. Commit/push/phát hành bản local sau khi được PO cho phép. Không coi việc
   chặn đầu vào mới là đã dọn hai nhãn sai đang tồn tại.

Trạng thái: phần menu/validation local đã kiểm chứng; toàn task chưa hoàn
thành. Đã có [phương án sửa có giới hạn](REPAIR_PLAN.md) và hợp đồng HIGH
dự thảo, chờ PO duyệt phần thay đổi quy tắc và kiểm tra/dọn tình trạng.
