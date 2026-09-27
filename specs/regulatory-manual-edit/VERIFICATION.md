# Verification Contract — regulatory-manual-edit — v2

Tạo TRƯỚC khi implement, 2026-09-27, từ
[specs/_TEMPLATE/VERIFICATION.md](../_TEMPLATE/VERIFICATION.md).
Trạng thái: **cập nhật theo duyệt có điều chỉnh của PO ngày 2026-09-27;
implementation chưa bắt đầu, lượt này chỉ tài liệu**.
Sau khi implement bắt đầu, hợp đồng này bị khóa; agent chính không tự sửa.

Nguồn yêu cầu: [SPEC.md mục 1 (A–H hiện hành)](SPEC.md#1-quyết-định-gốc-của-product-owner--ah).
Chi tiết nghiệm thu: SPEC mục 3–7; migration/rollback: [MIGRATION.md](MIGRATION.md).
**20 claim**: giữ V1–V11, V13–V21 để dễ đối chiếu. V12 v1 (ghi đè một lần)
đã bỏ theo điều chỉnh D; phản biện cố ép ghi đè được giữ ở V8–V11, V13.
V15 thay hợp đồng token preview tay bằng UI preview + kiểm tra revision +
chống gửi lặp. Không coi phê duyệt spec là quyền implement trong lượt này.

## Điều kiện kiểm chứng và oracle chung

- Chỉ local; PostgreSQL thật trong database thử tách biệt đã được xác minh
  và cho phép tạo/xóa. Không đọc `.env`, không probe DB chưa xác minh, không
  sử dụng dữ liệu production. Lượt spec không chạy những test này.
- Fixture tổng hợp, có stable ID/giá trị do test chỉ định. Expected là tập
  bản ghi/giá trị viết độc lập từ hợp đồng; không dùng output của planner,
  resolver hay helper ứng dụng để tự dựng expected rồi so chính nó.
- Snapshot rule so bằng ID và từng giá trị status_id, match_field,
  match_value, note, is_active, manual_protected, revision, timestamps;
  snapshot audit/khóa bảo vệ đếm và đối chiếu nội dung. Query kiểm chứng
  dùng connection độc lập sau commit, không chỉ xem HTTP 200 hoặc mock call.
- Với import: có test HTTP upload → worker preview → HTTP confirm → worker
  apply → đọc DB thật và trang preview/job. Không chỉ gọi apply_plan trực tiếp.
- Mỗi claim cần evidence happy path và ít nhất một case phản biện thực thi.
  Mỗi V8–V11, V13, V14 chạy **cả upsert và replace_scoped** trừ phần chỉ có nghĩa
  ở replace_scoped. Không coi một mode PASS là bằng chứng cho mode kia.
- Test skipped do thiếu PostgreSQL/psql không phải PASS. Phân biệt PASS,
  FAIL, NOT RUN, N/A; không cho HIGH qua gate với kiểm chứng bắt buộc bị skip.

## Claim V1 — Chỉ người có quyền hiện tại được thao tác (A)

Mọi trang/API mới, lịch sử, xác nhận sửa tay và preview/apply import dùng quyền menu
`regulatory`; không mở rộng quyền cho admin chỉ có menu khác hoặc staff.

Oracle:
Super Admin và admin ACTIVE có regulatory truy cập được. Staff, admin chỉ
có imports, session không có user ID, tài khoản bị khóa/hạ quyền nhận từ
chối (403; chưa đăng nhập theo redirect/401 hiện có). Sau request bị chặn,
snapshot rule/audit/job không đổi. Không thêm permission key hoặc ngoại lệ
cho Super Admin ghi đè mục thủ công bằng import.

Test type: integration HTTP + PostgreSQL thật + kiểm tra registry endpoint.

Failure case cần thử:
Gọi URL trực tiếp thay vì menu; giả user/actor trong form; thu hồi grant hoặc
bump auth_version sau preview và trong lúc request/worker chờ khóa ghi.
CSRF thiếu/sai bị 400 và không ghi; account đổi sau guard đầu request vẫn bị chặn.

## Claim V2 — Tìm đúng giá trị quy tắc, lọc và phân trang (F)

Oracle:
Fixture >100 rule, có CAS/code/name, nhiều tình trạng, cả inactive. Từ khóa
chứa chuỗi (không phân biệt hoa/thường, giữ dấu) kết hợp ba bộ lọc trả đúng
tập ID đã viết trước. Mặc định active, 50/trang, ID tăng dần; ghép các trang
trên dữ liệu không đổi bằng đúng expected, không trùng/thiếu. Tên chỉ có trong
products/note/khóa lịch sử không thành kết quả. Trang quá cuối rỗng.

Test type: integration PostgreSQL/HTTP + DOM hoặc manual UAT.

Failure case cần thử:
Nhập `%`, `_`, nháy đơn, chuỗi SQL/XSS; không wildcard, không lỗi 500,
không thực thi script. Page âm/không số và filter không hợp lệ bị từ chối
có kiểm soát; không biến lỗi thành trả hết dữ liệu. Đổi filter giữ đúng khi
chuyển trang; inactive không rò vào bộ lọc active.

## Claim V3 — Thêm hợp lệ, không ghi trước khi xác nhận (G)

Oracle:
Preview trên UI với CAS 50-00-0/code/name hợp lệ không thêm rule/audit/khóa
bảo vệ, không tạo preview record/token DB.
Confirm thêm đúng một ID, active=true, bảo vệ=true, status có sẵn, note đúng;
một event created với before rỗng, actor/thời gian và after đúng DB. Input
được chuẩn hóa nhất quán, không tạo tình trạng mới từ form tay.

Test type: unit validation + integration HTTP/PostgreSQL.

Failure case cần thử:
CAS 123-45-6 sai checksum, giá trị trắng, field không hợp lệ, status không
tồn tại, note >4.000 hoặc code/name >500 ký tự: không ghi bất kỳ rule/event
nào. Bỏ validation UI rồi gửi payload sai trực tiếp vẫn bị chặn; gửi actor/
active/protection giả không thành công. Request trực tiếp **hợp lệ đầy đủ**
được xử lý như confirm, không yêu cầu token preview mà v2 đã bỏ. Bấm hai
lần hoặc mất response rồi retry cùng request_id chỉ một rule/created event.

## Claim V4 — Sửa đúng một ID và không đổi loại đối chiếu (B, G)

Oracle:
Sửa value/status/note qua preview giữ ID/field, chỉ một rule được đổi,
bảo vệ bật, revision tăng, snapshot before/after chính xác. Đổi status_id
bắt buộc lý do không trắng. Sửa note/value không đổi status không bắt buộc
lý do. No-op không tạo event, không đổi revision/protection/timestamp.

Test type: integration HTTP/PostgreSQL + UAT trước/sau.

Failure case cần thử:
Giả match_field hoặc is_active trong payload; bỏ lý do/nhập toàn khoảng
trắng khi đổi status; status không còn tồn tại; thiếu/sai expected_revision,
rule bị đổi sau khi mở form: không ghi, lỗi validation hoặc 409 yêu cầu tải
lại. Server lấy before từ DB của ID được yêu cầu, không từ snapshot client.
Không có cam kết token ràng buộc ID với preview UI; kiểm tra ID/quyền/revision
và validation phía server là ranh giới tin cậy.

## Claim V5 — Trùng khóa dẫn đến mục cũ, khác tình trạng chỉ cảnh báo (E)

Oracle:
Khóa = status_id + loại + upper(btrim(value)), kể cả inactive và khóa lịch
sử bảo vệ. Thêm/sửa đụng khóa trả liên kết đúng ID có sẵn, không upsert ghi
chú, không tạo event. Cùng CAS/code khác status được thêm sau cảnh báo chỉ
rõ các rule liên quan; resolver vẫn dùng thứ tự ưu tiên cũ.

Test type: unit normalization + integration DB/HTTP + DOM/UAT cảnh báo.

Failure case cần thử:
Code ` ABC-001 ` vs `abc-001`, ghi chú khác, rule trùng đang inactive,
khóa cũ của một rule đã sửa và hai request đồng thời tạo cùng khóa.
Chỉ một chủ sở hữu, không trùng row hoặc 500. Chạy thêm Unicode hợp lệ để
phát hiện lệch Python casefold và unique SQL; không âm thầm gộp sai.
Riêng chủ sở hữu sửa A→B→A phải thành công qua preview/confirm, cùng ID,
thêm đúng audit và giữ active hiện có, kể cả rule đang inactive; không
hiểu khóa lịch sử của chính mình là xung đột với một rule khác.

## Claim V6 — Ngừng áp dụng và khôi phục, không xóa hẳn (B, C)

Oracle:
Deactivate có lý do + preview/confirm đổi active true→false, giữ ID/dữ liệu,
bật bảo vệ và thêm đúng một audit. Restore false→true giữ ID/audit/bảo vệ,
thêm restored event. Resolver không áp dụng inactive; sau restore lại xét
rule bình thường. Không có đường UI/API mới xóa cứng rule thủ công.

Test type: integration HTTP/PostgreSQL/resolver + UAT.

Failure case cần thử:
Deactivate thiếu lý do, restore bằng request sửa thông thường, gửi lại
deactivate/restore đã thực hiện hoặc gọi quick-rule-delete cũ: không xóa
row, không audit lặp; endpoint cũ không trở thành đường ghi hoạt động.

## Claim V7 — Lịch sử thủ công chính xác và bền (B)

Oracle:
Chuỗi created→edited→deactivated→restored có đúng số sự kiện, actor thật,
thời gian có múi giờ, reason, snapshot cũ/mới đúng từng bước. Lịch sử trang
rule phân trang đầy đủ, sort thời gian/ID mới trước. Rename status/user sau
đó không sửa nhãn snapshot. Import không sinh event tay giả; purge job không
xóa lịch sử tay/bảo vệ. Lỗi ghi audit rollback cả thay đổi rule.

Test type: integration DB/HTTP + fault injection + DOM/UAT.

Failure case cần thử:
Chèn lỗi tại audit sau khi UPDATE rule; giả actor/timestamp trong request;
đổi label tình trạng rồi xem event cũ; dọn upload/job hết hạn; nhập reason/note
chứa HTML. Không mất audit, không ghi nửa chừng, không XSS.

## Claim V8 — Import luôn giữ khóa hiện tại đã sửa tay (D v2)

Oracle:
Cho ba nhóm fixture: thêm tay, sửa note trên rule nhập cũ, sửa status/value
rồi file trùng khóa hiện tại. File chứa ghi chú khác. Qua đầy đủ hai mode,
snapshot từng rule thủ công (bao gồm active, timestamps/revision/bảo vệ)
không đổi, không có audit tay giả. Dòng thuần import cùng job vẫn cập nhật
đúng để chứng minh không chỉ bỏ chạy cả import.

Test type: integration E2E web/worker/PostgreSQL.

Failure case cần thử:
Ghi chú file rỗng để xóa note thủ công; thay chữ hoa/khoảng trắng; file có
nhiều dòng lẫn protected/unprotected. Không vượt bảo vệ bởi normalize hoặc
UPDATE hàng loạt; cả preview và DB sau apply phải cùng kết quả.
Giả `force`/`overwrite`/danh sách ID xin ghi đè ở HTTP upload/control bị từ
chối có kiểm soát, không ghi; không có UI tùy chọn này. Chạy cả tài khoản
Super Admin và worker với job hợp lệ: không có ngoại lệ cho mục protected.

## Claim V9 — replace_scoped không xóa mục thủ công vắng trong file (D)

Oracle:
Fixture cùng scope Code/Cấm nhập có rule thêm tay, sửa tay, ngừng tay và
rule thuần import; file chứa một code khác trong scope. Ba rule thủ công
giữ nguyên từng cột/ID, rule thuần import active bị xóa theo hành vi cũ.
Scope CAS/Cấm nhập và Code/tình trạng khác không đổi. Preview.deleted chỉ
tính rule thuần import thực sự xóa, mục thủ công vắng file có trong danh
sách giữ. Upsert với cùng file không xóa dòng ngoài file nào.

Test type: integration E2E web/worker/PostgreSQL.

Failure case cần thử:
Giả cờ ghi đè hoặc ID thủ công vắng file ở request không cấp quyền xóa.
Scope có nhiều tình trạng/field, file rỗng và
scope chỉ có dòng xung đột khóa lịch sử không làm xóa rộng ngoài phạm vi.

## Claim V10 — Không import nào tự khôi phục mục ngừng tay (D)

Oracle:
Rule có active=false do thao tác tay, bảo vệ=true. File có cùng khóa. Với
cả hai mode: is_active luôn false, ID/note/revision/timestamps nguyên vẹn,
resolver vẫn không xét rule. Không có lựa chọn import được phép đổi nội
dung rule này. Chỉ restore tay bật lại; sửa note từ file phải làm tay.

Test type: integration E2E + resolver trên PostgreSQL.

Failure case cần thử:
File ghi chú giống hệt (planner dễ gọi là unchanged), file note mới, file
bỏ hẳn dòng trong replace_scoped; retry/replay/apply trực tiếp, restart
worker sau preview. Không có bất kỳ đường ON CONFLICT nào bật active=true.
Thêm fixture inactive có sẵn trước migration (không có audit tay): sau
backfill bảo vệ, import cả hai mode và thử ép cờ ghi đè, vẫn false; không sinh
audit tay giả để giải thích nguồn gốc không biết của dữ liệu cũ.

## Claim V11 — File cũ không tái tạo khóa đã được sửa tay (D, G)

Oracle:
Rule ID R từng là CAS 50-00-0/Cấm nhập, sửa thành CAS 64-17-5/Cấm nhập hoặc
50-00-0/Được bán (hai test riêng, rồi test sửa liên tiếp cả hai). File chứa
khóa cũ và có thể cả khóa hiện tại. Cả hai mode luôn: không tạo row
khóa cũ, không đổi R. Preview liên kết khóa cũ tới đúng R và nêu xung đột.
Khóa lịch sử không xuất hiện như rule active trong resolver/danh sách.

Test type: integration PostgreSQL/HTTP/worker.

Failure case cần thử:
File chứa tất cả các khóa của nhiều lần sửa liên tiếp; R đã inactive;
cố bật ghi đè cho khóa lịch sử hoặc thêm tay chiếm khóa cũ. Không tái tạo,
không âm thầm chuyển R về trạng thái trước. Rule cùng CAS nhưng status khác
chưa từng là khóa được giữ vẫn hợp lệ theo E.

## Claim V13 — Preview import liệt kê đầy đủ và khớp apply (D)

Oracle:
Fixture >50 mục được giữ/xung đột qua nhiều trang, gồm khóa hiện tại, khóa
cũ, inactive, vắng file trong scope. Ghép các trang
bằng đúng tập dự kiến; mỗi mục có ID, lý do, before/proposed/decision và
file row nếu có. Thêm/cập nhật/giữ-bỏ-qua đếm dòng file, tổng bằng số dòng
file hợp lệ; xóa và tổng mục bảo vệ giữ nguyên đếm rule ID phân biệt, nhãn
đơn vị rõ. Không chỉ có 20 mẫu. Ngoài scope không bị báo xung đột giả.
Chi tiết snapshot đầy đủ lưu trong JSON job.preview/event.detail hiện có,
không bảng chi tiết mới. Response trang tối đa 50 mục; list/status chỉ summary.
Sau dọn upload/job.preview, đọc đúng snapshot event hoàn tất của lần chạy
được chọn, không tái tính theo rule sống; thiếu snapshot phải báo không có
bản ghi chi tiết, không giả làm danh sách không xung đột.

Test type: integration HTTP/worker/DB + DOM/manual UAT.

Failure case cần thử:
Xung đột duy nhất nằm sau dòng 20/trang đầu; file chỉ có khóa cũ và khóa
hiện tại của R với note khác: expected 2 dòng giữ/bỏ qua, 1 rule giữ,
0 cập nhật R (kể cả cố ép ghi đè). Rule vắng file được giữ chỉ thêm vào
tổng rule, không tăng tổng dòng file. Người dùng vẫn xem hết được;
không nhân đôi rule bị giữ/xóa; không giấu xung đột
bằng giới hạn sample. Lỗi tải trang chi tiết không hiển thị “không có xung đột”.
Test JSON protection_details ngay dưới/đúng/trên 8 MiB (UTF-8 compact,
không ASCII-escape Unicode): trong ngưỡng giữ đủ; vượt ngưỡng phải preview
fail rõ giới hạn và cách xử lý theo mode, preview_ready=false, apply bị chặn, rule/audit
nguyên vẹn. Thử note Unicode dài để không nhầm số ký tự với byte. Sau retry
hoặc thay rule sau apply, xem job cũ vẫn đúng snapshot, không nhầm event cũ.
Phản biện file lớn cùng scope: với replace_scoped, thông báo chỉ cho chia
theo scope không giao nhau và giữ đủ dữ liệu mỗi scope, không chia theo dòng.
Một scope vượt giới hạn vẫn bị chặn toàn bộ, không auto-split/đổi mode.
Fixture hai nửa cùng scope có rule thuần import của nhau phải chứng minh
apply nửa sau sẽ xóa dòng nửa trước theo hành vi cũ, nên không được quảng
bá cách chia đó là an toàn. Upsert có thể hướng dẫn chia theo dòng.

## Claim V14 — Bảo vệ không biến mất khi hết job hoặc thay phiên (D)

Oracle:
Sau sửa tay và import giữ nguyên, dọn upload/job hết hạn, tạo phiên
mới và worker mới: protection, khóa lịch sử, trạng thái inactive và audit
tay còn nguyên; import lần sau vẫn giữ tuyệt đối. Danh sách giữ/xung đột
của lần import hoàn tất còn xem trong event theo thời hạn nhật ký hiện có.

Test type: integration worker cleanup/PostgreSQL + multi-instance HTTP.

Failure case cần thử:
Dọn preview import hết hạn, vô hiệu hóa tài khoản đã sửa hoặc reload process
giữa preview/apply. Không dùng session/process cache/job JSON làm nguồn duy nhất
cho bảo vệ dài hạn; không cascade xóa rule history khi cleanup upload.

## Claim V15 — UI xem trước, server kiểm tra lại và chống gửi lặp (G, điều chỉnh 6)

Oracle:
UI hiện trước/sau từ dữ liệu/form; sửa form sau preview quay lại so sánh.
Không bảng/token/TTL preview tay. Confirm server kiểm tra quyền sống, CSRF,
validation, trùng khóa, reason và expected_revision của edit/deactivate/
restore dưới khóa. Thiếu/sai revision bị từ chối; khác DB trả 409 yêu cầu
tải lại. Create kiểm tra unique và request_id. Before audit lấy từ DB.
Audit lưu request_id UNIQUE, actor snapshot và digest payload server. Hai
request cùng ID + actor + payload (kể cả khác process/mất response) chỉ một
thay đổi/một event; replay trả kết quả đã làm, không áp dụng lại sau khi rule
đã thay đổi tiếp. Cùng request_id khác actor/payload trả 409, không tiết lộ
nội dung. Mọi replay vẫn kiểm tra quyền/CSRF. Hai request_id khác nhau tạo
cùng khóa chỉ một row/event created, request còn lại dẫn tới rule có sẵn.

Test type: DOM/UI + integration HTTP/PostgreSQL nhiều instance/concurrency.

Failure case cần thử:
Tắt JS/gửi API trực tiếp với revision thiếu/âm/không số/stale, CSRF sai,
loại đối chiếu thay đổi, reason trắng; giả before/actor/revision để bypass
validation: server không tin snapshot client. Request hợp lệ không bị buộc
có token preview đã bỏ. Gửi hai confirm đồng thời; retry create sau khi
rule đã sửa sang khóa mới; cùng request_id đổi note/ID/action hoặc dùng
bởi admin khác; actor bị thu hồi quyền trước replay: không tạo rule thứ hai,
không audit lặp hoặc rò dữ liệu. UUID sai định dạng bị từ chối. Request_id
chỉ được ghi nhận cùng commit; lỗi trước commit không khóa mất lần thử lại.

## Claim V16 — Cạnh tranh không làm mất thay đổi thủ công (D, G)

Oracle:
Hai connection thật với barrier khóa, không chỉ sleep/mocks. Preview import
xong rồi sửa tay trước apply → import stale, không đổi DB. Mở form tay rồi
writer khác thực sự đổi rule đó (gồm đồng bộ nhãn/ưu tiên) trước confirm mới
→ revision tăng, tay stale. Thay đổi chỉ ở rule khác không bắt buộc làm tay
stale; status/quyền/validation vẫn kiểm tra lại tại confirm. Nếu import commit
trước và đổi rule đang sửa, thao tác tay phải tải lại; nếu tay commit trước,
import phải tôn trọng bảo vệ. Kết quả bằng một thứ tự giao dịch hợp lệ, không
ghi đè dựa trên snapshot cũ. Fingerprint nhận cả bảo vệ và khóa lịch sử.

Test type: integration concurrency PostgreSQL (web/worker + barriers).

Failure case cần thử:
Thu hồi grant trong lúc chờ domain lock; concurrent sửa value/status và
replace_scoped; hai người deactivate/restore ngược nhau; thay bảo vệ nhưng
giữ note/value giống cũ. Không bypass stale check, không deadlock treo vô hạn.

## Claim V17 — Thất bại giữa giao dịch không để lại thay đổi một phần

Oracle:
Inject lỗi sau cập nhật rule, sau ghi khóa bảo vệ, tại audit, giữa job có
nhiều dòng và trước commit: snapshot rule/status/audit/keys bằng trước;
job được báo thất bại có kiểm soát, không được báo áp dụng thành công.
Request_id chưa commit thành công không bị coi như đã lưu; retry hợp lệ không
nhân đôi kết quả đã commit.

Test type: integration fault injection + worker cancellation/PostgreSQL.

Failure case cần thử:
Unique conflict ở dòng cuối sau các update đầu, worker bị cancel, DB error
tại audit, gửi hai confirm cùng request_id. Không có nửa job cập nhật hoặc rule
đã sửa nhưng thiếu audit/khóa bảo vệ. Không log DSN/password trong lỗi.

## Claim V18 — Không hồi quy import và kết quả pháp chế hiện có

Oracle:
Với fixture không protected: upsert/replace_scoped giữ hành vi cũ, CAS sai,
dòng trùng trong file, file rỗng vẫn bị từ chối; scope là đúng cặp field/status.
Search/Check License/Quick Quote/export dùng cùng priority và ALLOW/BLOCK;
ngoại lệ sản phẩm có ưu tiên theo brand như trước. Rename/color status
không đổi chính sách, không đánh dấu mọi rule là đã sửa tay. Products,
giá/tồn kho/permission grants không đổi bởi ghi rule.

Test type: existing regression suites + integration fixture kết quả cố định.

Failure case cần thử:
Rule CAS và code khác status cùng khớp; tên substring; nhiều note được gộp;
manual override sản phẩm cho phép trước rule BLOCK; inactive không tác động.
Tên tùy ý/màu tùy ý không trở thành cách suy luận export_policy mới.

## Claim V19 — Migration additive, lặp an toàn, không mất dữ liệu

Oracle:
Trên schema local trước 033 có dữ liệu: apply qua psql ON_ERROR_STOP thành
công; mọi cột nghiệp vụ/count/ID rule/status/products/grants và import cũ
giữ nguyên; active cũ metadata false/1, inactive cũ true/1 và đúng một khóa
bảo vệ hiện tại; bảng audit mới rỗng; JSON job/event cũ không đổi, không
có bảng preview tay/bảng chi tiết import/cột cho phép ghi đè. Không suy đoán nguồn
sửa tay cho active cũ từ note/updated_at. Chạy lại sau khi
có audit/request_id/protection/keys không reset chúng. Unique request_id và
trigger revision có hiệu lực, không tăng hai lần hoặc tăng cho no-op.
Thiếu migration: UI/worker mới
từ chối ghi có kiểm soát; job preview version cũ phải preview lại.

Test type: PostgreSQL migration integration bằng psql thật + HTTP/worker.

Failure case cần thử:
Lỗi giữa migration, rerun sau thay đổi tay, job apply cũ đang queued,
schema thiếu cột, unique lịch sử bị tranh chấp. Không có schema nửa chừng,
không fallback sang import không bảo vệ, không tự backfill lịch sử giả.

## Claim V20 — Rollback có guard và không đánh đổi lịch sử lấy code cũ

Oracle:
DB local thử chưa dùng tính năng: down có guard gỡ đúng cấu trúc mới,
dữ liệu nghiệp vụ cũ nguyên vẹn. Đã có manual event/protected/key,
JSON import contract_version=2 hoặc revision khác 1: down bị từ chối trước thay
đổi schema (kể cả protection/keys do backfill inactive cũ). Rehearsal
restore backup vào DB local khác so bằng snapshot trước. Checklist vận hành
ngăn khởi động worker cũ khi còn dữ liệu mới; không coi giữ cột là đủ bảo vệ.

Test type: integration migration/rollback/backup restore local + review runbook.

Failure case cần thử:
Chỉ có audit nhưng rule đã inactive, hoặc chỉ có job metadata mới; down
không được xóa “vì không còn active rule”. Mô phỏng lỗi rollback, không
DROP CASCADE/restore đè DB người dùng. Nếu không chặn riêng được mọi writer,
runbook yêu cầu dừng web/worker thay vì tuyên bố read-only không đúng.
Fixture chỉ còn revision khác 1, không JSON import v2/audit/keys:
down vẫn từ chối trước mọi thay đổi schema, không suy “không có audit = chưa dùng”.

## Claim V21 — Chỉ cảnh báo chung, không tính số sản phẩm ảnh hưởng (H v2)

Oracle:
Có cảnh báo quy tắc áp dụng toàn danh mục, tên có thể khớp nhiều sản phẩm,
kết quả cuối còn phụ thuộc ưu tiên/ngoại lệ. Không hiện số đếm hoặc số 0
giả. Không có truy vấn đếm products trong luồng form/preview/confirm mới;
không triển khai benchmark/cache/index products cho số đếm ở phase này.

Test type: DOM/UAT + integration kiểm tra truy vấn + review phạm vi diff.

Failure case cần thử:
Preview tên rất rộng hoặc nhập giá trị không khớp sản phẩm nào: cùng cảnh
báo chung, không hiện “0 sản phẩm ảnh hưởng” hay gọi đếm ngầm. Sản phẩm có
override/rule khác thắng không khiến cảnh báo khẳng định chắc chắn đổi kết
quả. Cố bật cờ UI/client yêu cầu đếm không làm endpoint thực hiện số đếm.

## Kế hoạch chạy test sau khi được phép implement

**Chưa chạy.** Các file mới dưới đây là tên dự kiến phải tạo ở lượt code,
không phải test hiện đã có. Dùng `.venv/bin/python`; sau preflight đích local
và cấu hình test an toàn, không in DSN và không source `.env`:

```bash
PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests -p 'test_regulatory_manual*.py' -v
PYTHONPATH=.:tests .venv/bin/python -m unittest test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v
node tests/regulatory_manual_dom_test.js
node tests/admin_regulatory_dom_test.js
```

- Nhóm `test_regulatory_manual*.py` dự kiến gồm chức năng/API, import bảo vệ,
  concurrency và migration/rollback. 19 claim V1–V11, V13–V20 cần PostgreSQL thật; không
  dùng một vài unit test PASS thay cho các test tích hợp bị skip.
- Test migration dùng helper `run_migration_via_psql` sau xác minh môi trường;
  schema dự kiến 033 phải thật sự được áp dụng trong DB tạm. Không chỉ thêm
  file SQL mà quên cập nhật setup fixture.
- Full regression chỉ chạy sau kiểm tra an toàn đích DB cho toàn suite:
  `PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests -v`.
- Ghi command thực tế, phiên bản DB, số pass/fail/skip, evidence không secret.
  Nếu đổi tên file test, cập nhật hướng dẫn chạy ngoài hợp đồng bằng tài liệu
  validation, không sửa claim/oracle sau khi code đã bắt đầu.
- Không benchmark, migration hoặc kiểm chứng production/staging.

## Verifier độc lập và gate hoàn tất

Lượt spec không gọi verifier để giả chứng minh tính năng chưa có. Sau code,
gọi subagent `verifier` trong context sạch, đúng khuôn mục 11, chỉ chứa:

1. Yêu cầu/tiêu chí liên quan trích nguyên văn từ SPEC.md.
2. `specs/regulatory-manual-edit/VERIFICATION.md` đã khóa.
3. Diff code hoặc git range để verifier tự đọc (bao gồm file mới).
4. Lệnh test/khởi động cần thiết, ràng buộc đích local đã xác minh.
5. Lần fail trước nếu có: chỉ Observed/Expected/Reproduction/Classification.

Không gửi suy luận, root cause, kế hoạch hoặc giải thích sửa của agent chính.
Verifier tự thử negative case cho từng claim và ghi VERIFICATION_RESULT.md
theo template. Không tạo result PASS bằng cách đọc lại developer tests.
Agent chính quản lý attempt/max_auto_repairs=2/same_claim_repeat_fail/next_action
theo quy trình; SPEC_AMBIGUOUS hoặc VERIFICATION_CONTRACT_PROBLEM dừng hỏi,
không tự sửa hợp đồng. Sau PASS còn human review/5 câu hỏi ở mục 11;
không tự báo READY FOR USER TEST/DONE trước gate này.

## Ngoài phạm vi

- Tìm các quy tắc tác động tới sản phẩm X, thay resolver/chính sách xuất,
  quyền mới, duyệt hai người, xóa cứng thao tác tay, giải phóng khóa lịch sử,
  bảo vệ trước người có quyền DBA/SQL ngoài ứng dụng.
- Audit chi tiết tay cho lịch sử trước migration; chuyển mọi log import
  thành audit tay; nâng framework/dependency hoặc đổi schema sản phẩm.
- Mọi số đếm sản phẩm ảnh hưởng, kể cả tham khảo; performance
  trên production; deploy/SQL/backup/rollback staging hoặc production.
- Lượt hiện tại chỉ spec: chưa claim bất kỳ test runtime nào PASS.

---

## Thay đổi hợp đồng

Không tự sửa file này sau khi implement đã bắt đầu để làm test dễ pass hơn.
Cần thay đổi claim hoặc oracle:

1. Ghi lý do vào "Change request log" dưới đây.
2. Xin phê duyệt — người dùng cho thay đổi yêu cầu; verifier độc lập đủ nếu
   chỉ làm rõ oracle, không đổi yêu cầu. Agent chính không tự diễn giải lại.
3. Tăng version, ví dụ v2.
4. Chạy lại toàn bộ verification từ đầu, không tính là repair của version cũ.

### Change request log

- 2026-09-27 — PO duyệt spec với điều chỉnh, trước khi implementation bắt
  đầu; tăng v1 → v2 theo yêu cầu trực tiếp. Không phải repair sau test FAIL.
- D: bỏ hoàn toàn lựa chọn ghi đè import; C làm rõ mục thuần import vẫn
  replace_scoped như cũ; giữ khóa lịch sử và backfill inactive; H chỉ cảnh báo.
- Bỏ V12 (ghi đè được chọn). Giữ ID để tra cứu: còn **20 claim** V1–V11,
  V13–V21. V8–V11/V13/V14 giữ negative cases chống ghi đè/xóa/bật lại/tái
  tạo khóa cũ/cleanup; thêm JSON phân trang, snapshot và giới hạn byte.
- V15 chuyển token/TTL/preview DB sang UI preview, server revalidation,
  expected revision và chống gửi lặp trên audit; V3/V4/V16/V17/V19/V20
  đồng bộ theo cơ chế đó. Không còn claim server chứng minh UI đã preview.
- Lưu xung đột trong JSONB job/event hiện có, không bảng chi tiết mới;
  migration/rollback tương ứng v2. Toàn bộ kiểm chứng runtime **NOT RUN**;
  khi được phép code phải chạy đầy đủ hợp đồng v2 từ đầu rồi verifier độc lập.
