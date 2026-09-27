# Kế hoạch migration và rollback — regulatory-manual-edit

Version 2 — cập nhật theo điều chỉnh PO ngày 2026-09-27, đi cùng [SPEC.md](SPEC.md) và
[VERIFICATION.md](VERIFICATION.md). **Chỉ thiết kế; chưa có file SQL.**
Tên migration dự kiến: `sql/migration_033_regulatory_manual_edit.sql`;
kiểm tra lại số trước khi tạo. Không sửa các migration đã phát hành.

## 1. Phạm vi môi trường

- Lượt này không kết nối database, không chạy SQL, không start container.
- Sau khi được phép implement: chỉ tạo/chạy/thử rollback trên PostgreSQL
  local tách biệt, service `db` của repo; ưu tiên database tạm do test tạo.
- Xác minh host thực thuộc Docker local (không chỉ hostname localhost vì
  có thể là SSH tunnel), port và database name bằng thông tin không chứa
  secret. Không đọc/in `.env`, không kế thừa DATABASE_URL chưa xác minh.
- `tests/pg_temp_db.py` tạo/drop DB tạm nhưng probe/create dùng host từ env;
  kiểm tra đích **trước cả probe**. Không chạy full suite để rồi mới xác minh.
- Không ghi vào `products_local` có dữ liệu thật, không dùng `seed_test.sql`
  để reset. Không có quyền chạy migration/rollback staging hoặc production.

## 2. Schema dự kiến

### 2.1 regulatory_rules

- Thêm `manual_protected BOOLEAN NOT NULL DEFAULT false`.
- Thêm `revision BIGINT NOT NULL DEFAULT 1`, CHECK >0; tăng khi dữ liệu rule
  thực sự thay đổi bởi tay hoặc import trên rule chưa bảo vệ, không tăng cho
  dòng giữ nguyên. Trigger revision so OLD/NEW các cột nghiệp vụ/bảo vệ để
  không bỏ sót writer hiện có (kể cả đồng bộ nhãn/ưu tiên status); không lấy
  số revision tùy ý từ client, không tăng hai lần bởi app và trigger.
- Dùng `is_active`, ID và timestamps hiện có. Không thêm trạng thái pháp chế,
  không đổi unique status_id + match_field + upper(btrim(match_value)).
- Dòng cũ giữ mọi giá trị nghiệp vụ; metadata mặc định false/1, riêng rule
  inactive có sẵn được backfill protection=true và giữ khóa hiện tại để
  import không bật lại. PO đã duyệt ngày 2026-09-27 tại SPEC mục 3.6.
  Không suy đoán nguồn tay từ note/updated_at, không tạo lịch sử giả. Rule
  active cũ chưa được bảo vệ đến khi có thay đổi tay thật được xác nhận;
  không hứa phục hồi lịch sử sửa tay không còn được lưu trước phase này.
- Lần migrate đầu: backfill bảo vệ/khóa trước khi bật trigger revision;
  mọi rule cũ bắt đầu revision=1. Cài trigger trong cùng transaction sau
  backfill. Rerun không backfill lại hoặc đặt lại revision đã thay đổi.

### 2.2 regulatory_rule_manual_keys (tên dự kiến)

- Lưu rule_id, status_id, match_field, match_value của các khóa được bảo vệ,
  thời điểm ghi; unique toàn bảng theo status_id + match_field +
  upper(btrim(match_value)). FK tới rule/status, không ON DELETE CASCADE.
- Có index rule_id. Dùng cùng semantics unique với regulatory_rules;
  chuẩn hóa input chung, không dùng hash mơ hồ làm nguồn quyết định trùng.
- Ghi khóa hiện tại khi lần đầu sửa tay; khi đổi status/value, giữ khóa cũ
  và thêm khóa mới cùng transaction. Rule thuần thêm tay cũng được giữ khóa.
- Backfill khóa cho rule inactive cũ được bảo vệ tại lần migrate đầu tiên;
  không backfill khóa cho dòng active cũ chưa bảo vệ; không thay giá trị
  đối chiếu/active. Backfill chỉ chạy một lần, rerun không diễn giải lại
  dữ liệu mới hoặc lấy khóa mới sau này làm dấu vết lịch sử giả.
- Writer tay và import kiểm tra cả hai bảng dưới khóa regulatory chung;
  collision giữa khóa lịch sử và rule khác phải bị từ chối, không auto-merge.
- Không cho xóa rule có lịch sử/khóa thủ công qua application import. FK
  bảo vệ lịch sử là lớp phòng thủ, không thay thế kiểm tra quyền/application.

### 2.3 regulatory_rule_manual_events (tên dự kiến)

- ID, rule_id (FK RESTRICT), actor_user_id (FK app_users), actor_label
  snapshot, event, reason, before_json, after_json, created_at có múi giờ.
- Thêm `request_id UUID NOT NULL UNIQUE`, `request_digest` do server tính
  từ payload chuẩn hóa và actor ID snapshot bất biến. Dùng audit thành công
  làm biên nhận chống gửi lặp, không cần bảng request/preview mới. Cùng ID
  + actor + payload trả kết quả đã lưu; khác actor/payload từ chối 409.
  Luôn kiểm tra quyền/CSRF hiện tại trước đọc kết quả replay; không dùng
  request_id như token cấp quyền. Role/user thay đổi không xóa biên nhận.
- Event: created / edited / deactivated / restored. Không tạo event “import
  sửa tay”. Audit append-only ở luồng ứng dụng; không có API sửa/xóa event.
- actor_user_id có thể ON DELETE SET NULL để giữ lịch sử nếu tài khoản được
  xóa bằng luồng được phép khác; actor_label snapshot luôn còn. Không dùng
  CASCADE xóa lịch sử. Đây không phải audit chống DBA can thiệp.
- Reason không trắng khi deactivated hoặc edited thay status_id; kiểm tra
  phía server và ràng buộc DB tương ứng. Giữ nguyên JSON trước/sau khi rename
  nhãn tình trạng. Index (rule_id, created_at, id) cho lịch sử phân trang.

### 2.4 Không tạo bảng preview tay

- Bỏ hoàn toàn `regulatory_rule_manual_previews` của bản v1. Không có token,
  TTL, payload preview hoặc consumed_at trong schema mới.
- UI tự hiện trước/sau. Confirm gửi payload, request_id và expected_revision
  cho edit/deactivate/restore; server đọc DB và kiểm tra lại đầy đủ. Create
  dùng unique khóa + request_id. Không lưu snapshot form vào DB chỉ để preview.
- Audit request_id và revision giải quyết gửi lặp/cạnh tranh trên nhiều
  worker; không thay bảng preview bằng cache trong process.

### 2.5 Import dùng JSONB sẵn có, không thêm bảng/cột job

- Không thêm `regulatory_import_protection_details`, cột lựa chọn ghi đè
  hay metadata quyền ghi đè. Số mục tay thường ít; job đã có `preview JSONB`
  và events đã có `detail JSONB`, đủ lưu danh sách snapshot đầy đủ. Tránh
  thêm schema/lifecycle/cleanup riêng khi không có nhu cầu thực tế.
- JSON kế hoạch thêm `contract_version=2`, `protection_details` gồm thứ tự,
  rule ID, file row nếu có, before/proposed/decision và loại xung đột. Phiên
  bản thiếu/cũ không được apply; job cũ chưa áp dụng phải preview lại.
- Phân trang 50 mục từ JSON trên server. List/status chỉ trả summary;
  chi tiết một trang không trả cả mảng. Không giới hạn 20 mẫu, không tính
  lại từ dữ liệu rule sống khi xem tác vụ đã hoàn tất.
- `preview_completed`/`apply_completed` lưu snapshot vào event detail theo
  cơ chế hiện có trong regulatory_import_jobs.py. Cleanup có thể xóa upload,
  import rows và job.preview; chi tiết hoàn tất đọc từ event tương ứng theo
  phase/lần chạy được chọn. Không lấy nhầm event cũ sau retry. Giữ trong thời
  hạn nhật ký job, không dọn audit/khóa bảo vệ tay cùng upload.
- Phần protection_details tối đa 8 MiB JSON UTF-8 compact, không ASCII-escape
  Unicode. Vượt thì preview thất bại rõ ràng, preview_ready=false, không apply;
  không âm thầm cắt mảng hoặc apply một phần. Upsert có thể chia theo dòng;
  replace_scoped chỉ chia theo scope trường+tình trạng không giao nhau và
  giữ đủ dữ liệu mỗi scope. Một scope riêng vẫn quá lớn thì báo giới hạn và
  dừng, không đề nghị chia scope thành nhiều lần replace hoặc tự đổi mode.
- Fingerprint/plan digest phải gồm revision, bảo vệ, khóa lịch sử và version
  kế hoạch; không có lựa chọn ghi đè. JSON mới là thay đổi code, **không cần
  migration thêm cột import**. Migration không sửa JSON job cũ.

## 3. Cách chạy migration local ở lượt sau

1. Kiểm tra đúng local/test, schema thực đã có các migration phụ thuộc tới
   032. Dừng web/worker local, không có job apply đang chạy. Backup DB local
   cần bảo vệ; DB thử có tên riêng và quyền bỏ được xác nhận.
2. Chụp counts/giá trị các cột nghiệp vụ rule/status/products, quyền admin,
   bảng import cũ. Lưu evidence không secret, không chỉ đếm tổng dòng.
3. Chạy migration qua psql với ON_ERROR_STOP trong transaction được khai
   báo trong file. Không dựa vào nhiều statement autocommit. Migration
   additive, chạy lại không reset metadata/lịch sử đã ghi.
4. Kiểm tra schema, FK, unique, default; so toàn bộ snapshot nghiệp vụ cũ.
   Chạy lại migration trên DB thử đã có rule bảo vệ/audit, chứng minh không
   reset các dữ liệu mới. Thử lỗi giữa chừng, chứng minh không có schema dở.
5. Chạy tests local cả web + worker mới. Job preview phiên bản cũ bị yêu cầu
   preview lại; apply cũ queued không được thực hiện bằng kế hoạch cũ.
6. Không cho worker cũ hoạt động song song. Kiểm tra cơ chế chặn phiên bản
   schema không hỗ trợ trước mọi ghi regulatory; thiếu migration phải lỗi
   có kiểm soát, không fallback sang luồng import không bảo vệ.

Không ghi lệnh `psql` nhắm DB thật trong spec để tránh bị chạy nhầm. Lệnh
test rehearsal dự kiến nằm ở VERIFICATION.md, chỉ dùng sau preflight local.

## 4. Rollback — không làm mất audit hoặc mở lại import cũ

### 4.1 Migration thất bại trước commit

Rollback transaction. Xác minh schema/dữ liệu trước đó nguyên vẹn, không
khởi động web mới khi schema chưa đủ. Không rerun “thử vận may” vào DB khác.

### 4.2 Migration thành công, chưa có dữ liệu tính năng mới

Trên DB local thử đã được phép: dừng web/worker; kiểm tra không có audit,
khóa bảo vệ, kế hoạch/event import contract_version=2, protected=true hoặc
revision khác 1. Nếu tất cả chưa dùng, có thể chạy down migration có
guard chỉ gỡ đúng schema mới. Phải thử bằng psql thật, không DROP CASCADE.
Nếu không chứng minh được “chưa dùng”, từ chối down và dùng 4.3.
Nếu migration đã bảo vệ inactive cũ thì guard cũng từ chối down; không
ngoại lệ gỡ bảo vệ với lý do “chưa có ai sửa tay sau migration”.

### 4.3 Đã có thay đổi thủ công hoặc import theo hợp đồng mới

- **Không tự chạy down/drop schema hoặc trả worker cũ vào hoạt động.**
  Code cũ có thể xóa/ghi đè dù cột bảo vệ vẫn còn trong DB.
- Rollback vận hành an toàn: dừng worker import regulatory và các đường
  ghi regulatory, giữ nguyên schema/dữ liệu/audit; chỉ đọc nếu bảo đảm mọi
  đường ghi bị chặn. Nếu chưa có cách chặn riêng đã kiểm chứng, dừng toàn
  bộ web/worker local, không tuyên bố read-only trong khi vẫn còn route ghi.
- Khuyến nghị sửa tiến (forward fix) rồi kiểm chứng lại. Muốn quay về trạng
  thái DB trước migration: restore backup vào **DB local mới**, đối chiếu
  rồi xin duyệt chuyển. Mất các thay đổi sau backup là hậu quả phải chấp
  thuận; không âm thầm khôi phục đè DB đang dùng.
- Giữ backup/audit hiện tại để không mất dấu quyết định pháp chế. Không coi
  git checkout hoặc revert code là phục hồi dữ liệu.

### 4.4 Khả năng kiểm chứng rollback

Test local phải chứng minh: down thành công khi chưa dùng; down từ chối khi
đã có dữ liệu; failure giữa migration không để lại thay đổi; phục hồi backup
vào DB thử khác cho kết quả bằng snapshot trước. Không xóa database của người
dùng. Chưa có bất kỳ rehearsal nào ở lượt spec.

## 5. Tác động/rủi ro còn lại

- Số dòng bảo vệ và lịch sử tăng theo sửa tay, không dọn theo TTL upload;
  theo dõi kích thước ở local trước khi chốt index tối ưu thêm.
- Khóa lịch sử giữ vô thời hạn có thể chặn nhu cầu cố ý tái sử dụng giá trị
  cũ cho một rule khác trong cùng tình trạng: giới hạn đã duyệt, không tự bỏ.
- Dữ liệu cũ có định dạng không chuẩn phải được báo khi edit/preflight;
  không tiện thể chuẩn hóa toàn bộ hoặc gộp trùng trong migration này.
- Staging/production rollout, backup thật và cutover cần yêu cầu/phê duyệt
  riêng; tài liệu này **không cấp quyền** thao tác các môi trường đó.

## 6. Change log v1 → v2 (2026-09-27)

Theo điều chỉnh PO trước implementation: bỏ bảng preview tay, bảng chi tiết
import và mọi metadata ghi đè. Chỉ thêm hai bảng (khóa lịch sử, audit tay)
cùng protection/revision trên rule; audit có unique request_id chống gửi lặp.
JSON job/event hiện có chứa chi tiết giữ/xung đột; không thêm cột import.
Giữ backfill inactive và rollback có guard, sửa guard theo schema v2.
Chưa tạo bất kỳ file migration hoặc chạy rehearsal nào.
