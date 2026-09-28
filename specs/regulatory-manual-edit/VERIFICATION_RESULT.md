# Verification Result — regulatory-manual-edit

## Kết luận độc lập

- Ngày kiểm chứng: **2026-09-28**.
- Contract: `VERIFICATION.md` **v2**, base `ee2ee5a`; kiểm tra working tree
  nhánh `feature/regulatory-manual-edit`, gồm các file mới chưa tracked.
- **PASS 20 claim V1–V11, V13–V21 trong phạm vi local đã thực thi.**
- Không tái hiện lỗi V13 của lần trước. Chi tiết mặc định sau retry không
  trả snapshot cũ; chọn event cũ tường minh vẫn xem được.
- Không sửa application code, contract, migration hoặc developer tests.
  Không đọc `.env`, không truy cập staging/production, không push.
- Đây là kết quả verifier, **không phải DONE/READY FOR USER TEST**. Còn gate
  human review và nghiệm thu trình duyệt. Khối Task status cuối file được
  giữ nguyên từ agent chính; verifier không tăng attempt hoặc đổi next_action.

## Môi trường, phương pháp và lệnh đã chạy

- Docker unix socket local, container `search-tools-regulatory-test-db-1`,
  Compose project `search-tools-regulatory-test`, service `db`, port 5432.
- PostgreSQL thực tế: **16.13** (`docker exec … postgres --version`).
- Chỉ dùng database thử prefix `p6a_release_gate_pgtest_`; các fixture được
  tạo và dọn qua helper của project. Worker chỉ chạy `run_once(job_id)`
  trên job fixture, không khởi động worker chung.
- Mỗi nhóm Python độc lập gọi `preflight()` trong
  `scripts/test_regulatory_local.py` trước import application/test hoặc
  kết nối DB; dùng cấu hình test local tương đương runner và bootstrap
  `tests/no_dotenv/sitecustomize.py`. Process con cũng nhận bootstrap này.
- Đọc kết quả rule/audit/key/job bằng connection độc lập sau commit. Expected
  ID, tập trạng thái và kết quả nghiệp vụ được chỉ định từ fixture; không
  dùng planner để tự tạo expected hành động import.
- Công cụ từ chối tạo file `tests/independent/`. Không vượt quyền ghi:
  các test độc lập chạy **trong bộ nhớ**, qua `.venv/bin/python -B -c`
  và `node -e`. Không có file test độc lập mới được tạo.

Lệnh developer/regression đã thực chạy:

```bash
.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v
.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v
node tests/regulatory_manual_dom_test.js
node tests/admin_regulatory_dom_test.js
git diff --check
```

Kết quả: **21/21 test tính năng**, **81/81 test hồi quy**, không skipped;
hai lệnh DOM exit 0; `git diff --check` exit 0.

Các nhóm độc lập thực thi trong bộ nhớ:

- `IndependentManual`: HTTP quyền, literal search/phân trang, validation,
  no-op, replay, Unicode identity, lỗi audit, migration rerun, SQL observation.
- `CorrectedSessionFixture`: đăng nhập mới sau đổi role/grant; request_id
  của actor khác; A→B→A khi inactive; 56 audit và rename snapshot.
- `IndependentImport`: E2E hai mode, Super Admin, khóa hiện tại/lịch sử,
  inactive/vắng file, cleanup + process worker mới, V13 retry/pagination,
  barrier DB thật, lỗi dòng cuối, import invalid, backfill/rollback guard.
- `Additional` và `BoundaryAndResolver`: ngưỡng UTF-8 thực, HTTP preview
  vượt ngưỡng, nguy cơ chia replace cùng scope, writer rename qua HTTP,
  worker bị thu hồi quyền trong lúc chờ khóa, Check License sau deactivate/restore.
- `OldWorkerContracts`: job apply contract cũ đang queued và thiếu schema 033.
- DOM độc lập: response kiểm tra về muộn sau khi đổi form, xác nhận preview
  đã bị hủy, double-click khi save đang pending, hiển thị input HTML như text.

Minh bạch về lần chạy fixture chưa đúng: các nhóm ban đầu có 7 assertion/error
do điều kiện test, không ghi chúng là implementation fail hoặc giấu thành một
tổng suite xanh. Đã kiểm tra và chạy lại phần tương ứng:

1. Ba assertion dùng session có auth_version cũ sau đổi role/grant bị redirect
   302; với session mới, Super Admin nhận 200, actor khác replay nhận 409,
   quick-rule-delete cũ nhận 410.
2. Workbook Unicode lặp có tỷ lệ nén quá cao bị ZIP safety guard chặn trước
   planner; workbook ZIP_STORED hợp lệ sau đó đến được giới hạn chi tiết 8 MiB
   và bị chặn đúng ở cả hai mode, không thay đổi ngưỡng an toàn ứng dụng.
3. UPDATE trực tiếp `regulatory_statuses.priority` không phải writer đồng bộ
   của application; thử lại rename qua HTTP cho revision rule 1→2, confirm
   revision 1 nhận 409.
4. Fixture tính byte ban đầu làm đổi cả nhãn reason từ “giống” sang “xung đột”;
   fixture xung đột từ đầu cho phép thử chính xác 8 MiB ±1 byte ở cả hai mode.
5. Check License không khớp trả `{"warning": false}`, không có trường
   export_policy; assertion theo response hiện hành đã chạy đạt.

Các warning quan sát được: DeprecationWarning escape trong `search.py`,
urllib3/LibreSSL và ResourceWarning của openpyxl. Không thấy DSN/secret trong
lỗi application được kiểm tra; các warning trên không làm test bị skip.

## Claim V1

Kết quả: **PASS**.

Test thực hiện: HTTP trực tiếp tới list/new/detail/protection và save với
admin chỉ có imports, staff, suspended và session thiếu user ID; Super Admin
không có grant regulatory với session mới; CSRF và revalidation trong lúc chờ khóa.

Evidence: các URL bị từ chối bằng 302/401/403, không đổi snapshot rule/keys/audit;
thiếu/sai CSRF và giả actor bị 400. Super Admin hợp lệ nhận 200. Worker cả hai
mode chờ advisory lock thật, auth_version bị tăng trước khi nhả lock: job
failed, không có rule mới. Registry/permission regression đạt.

Negative case đã thử: dùng URL trực tiếp, actor_user_id giả, legacy session,
CSRF sai, thu hồi phiên trong lúc worker chờ lock; không bypass được quyền.

Residual risk: không kiểm tra provider đăng nhập production; session được tạo
bằng Flask test client, nhưng backend guard/DB đọc quyền là thật.

## Claim V2

Kết quả: **PASS**.

Test thực hiện: 105 rule name có chuỗi `Ắ%_`, backslash và nháy đơn; thêm
inactive, status khác, note-only và giá trị không dấu làm nhiễu. Tìm chữ thường
kết hợp field/status/state qua HTTP.

Evidence: các trang có **50/50/5/0** ID; ghép đúng thứ tự ID fixture, không
trùng/thiếu; không lẫn inactive, status khác, note-only hoặc tên bỏ dấu.
page `1e2`, `-3`, `0`, field/state sai, status không tồn tại đều 400.

Negative case đã thử: `%`, `_`, backslash, nháy đơn không thành wildcard/SQL;
query `<script>alert(1)</script>` trả 200 và không có script thô trong HTML.

Residual risk: không bảo đảm pagination snapshot khi có writer thay đổi danh
sách giữa các request; đây là giới hạn contract đã nêu.

## Claim V3

Kết quả: **PASS**.

Test thực hiện: preview check CAS hợp lệ, confirm HTTP trực tiếp, validation
matrix và replay request_id; DOM độc lập kiểm tra chưa confirm không save.

Evidence: `/rules/check` không đổi rule/key/audit. Confirm `50-00-0` tạo đúng
một rule active/protected/revision=1; note được trim, giữ nội dung HTML dưới
dạng dữ liệu. Confirm đồng thời cùng request_id tạo một thay đổi, một replay.

Negative case đã thử: CAS checksum sai, trắng, field sai, status thiếu,
note 4.001 ký tự, value 501 ký tự, UUID sai, before/actor/timestamp và
active/protection giả: 400, không ghi.

Residual risk: DOM là harness, chưa thao tác trình duyệt thật. Không đòi token
preview tay vốn đã bị bỏ khỏi v2.

## Claim V4

Kết quả: **PASS**.

Test thực hiện: sửa CAS `50-00-0`→`64-17-5` cùng ID, note/status validation,
no-op trên cả rule protected và rule thuần import.

Evidence: no-op giữ toàn bộ snapshot rule/key/audit; sửa hợp lệ tăng revision;
sửa bằng revision cũ nhận 409. Audit chuỗi trước/sau khớp các trạng thái commit.

Negative case đã thử: đổi field, lén đổi is_active, reason toàn whitespace
khi đổi status, revision thiếu/âm/thập phân và status không tồn tại đều bị
chặn. Không tin before do client gửi.

Residual risk: không kiểm tra mọi dạng văn bản Unicode bất hợp lệ ở tầng
transport; có thử Unicode hợp lệ và payload SQL/HTML trong các claim liên quan.

## Claim V5

Kết quả: **PASS**.

Test thực hiện: duplicate normalized/inactive/historical, A→B→A của chính
owner khi inactive, cùng code khác status, Unicode và concurrency.

Evidence: `abc-001` trùng khóa lịch sử trả 409 kèm đúng rule ID, không upsert
note. Owner về `ABC-001` cùng ID và vẫn inactive. Check khác status trả đúng
ID liên quan; create status khác thành công. `straße` và `STRASSE` tạo hai
ID theo SQL identity; ` STRAßE ` bị 409. Developer concurrency: hai request_id
khác nhau cùng khóa chỉ một create, request còn lại 409.

Negative case đã thử: chiếm khóa cũ khi owner đã inactive, khác note,
Unicode có cùng Python casefold nhưng không cùng SQL upper; không gộp sai.

Residual risk: chưa khảo sát mọi collation PostgreSQL khác môi trường local.

## Claim V6

Kết quả: **PASS**.

Test thực hiện: deactivate/restore qua HTTP/service thật, no-op lặp hành động,
endpoint delete cũ và Check License.

Evidence: deactivate trắng bị 400; deactivate/restore hợp lệ giữ ID/bảo vệ.
Gửi lại hành động ở trạng thái đã đạt không tạo event mới. Super Admin gọi
quick-rule-delete cũ nhận 410. Check License với CAS fixture:
**BLOCK → `warning=false` → BLOCK** khi ngừng rồi khôi phục.

Negative case đã thử: lén restore bằng form edit/is_active, delete endpoint
cũ, deactivate không lý do; không xóa row hoặc tự đổi active.

Residual risk: không bao gồm thao tác DBA/SQL ngoài application.

## Claim V7

Kết quả: **PASS**.

Test thực hiện: chuỗi created/deactivated/edited/edited/restored rồi 51 edit;
rename user/status; lỗi ghi audit bằng trigger DB; import cleanup.

Evidence: **56 event**, trang lịch sử **50/6/0**. After của event trước bằng
before của event sau; actor thật và timestamp có timezone. Rename không đổi
snapshot event cũ. Lỗi INSERT audit sau thay rule/keys rollback toàn bộ;
cùng request_id retry sau bỏ fault thành công. HTML note được escape.

Negative case đã thử: `<img ... onerror>` trong note, actor/timestamp giả,
rename sau audit, lỗi audit và cleanup upload/job; không mất lịch sử tay.

Residual risk: phân trang thời gian/ID được kiểm tra trên fixture hữu hạn;
không thử retention nhiều năm hay tải lịch sử rất lớn.

## Claim V8

Kết quả: **PASS**, cả **upsert / replace_scoped**.

Test thực hiện: E2E upload→preview→confirm→apply với Super Admin; rule thêm
tay, rule import được sửa note, rule đã sửa value/status, dòng thuần import.

Evidence: fixture mỗi mode có hành động file **1 thêm / 1 cập nhật / 6 giữ**;
rule protected giữ nguyên toàn bộ cột, timestamps/revision và audit tay;
dòng thuần import cập nhật note thật và dòng mới được thêm.

Negative case đã thử: file note rỗng, khác hoa/thường/khoảng trắng, file trộn
protected/unprotected; `force`, `overwrite`, `overwrite_ids` ở upload bị 400,
không có ngoại lệ Super Admin. Control force cũng bị chặn trong test tính năng.

Residual risk: bảo vệ không có nghĩa chống người có quyền DBA hoặc worker
code cũ; điều này thuộc ranh giới vận hành/runbook.

## Claim V9

Kết quả: **PASS**.

Test thực hiện: file cùng scope có rule tay vắng file và rule thuần import
vắng file, thêm scope CAS và status khác làm nhiễu; chạy hai mode.

Evidence: replace_scoped xóa rule thuần import vắng file, giữ nguyên rule
tay và CAS/status ngoài scope. Upsert giữ dòng thuần import ngoài file.
Rule tay vắng file có chi tiết giữ trong replace, không tính thành dòng file.

Negative case đã thử: yêu cầu overwrite ID, file rỗng và file chỉ khóa CAS
cũ của owner đã đổi status; không xóa rộng hoặc chiếm lại khóa lịch sử.

Residual risk: không thử workbook tối đa 100.000 dòng; bộ fixture đủ phân
biệt scope field/status và behavior bảo vệ.

## Claim V10

Kết quả: **PASS**, cả **upsert / replace_scoped**.

Test thực hiện: inactive tay với file note giống/khác, bỏ dòng khỏi scope,
cleanup/reimport; inactive trước migration qua psql 033 rồi E2E cả hai mode.

Evidence: inactive vẫn false, các cột/ID/revision/timestamps giữ nguyên.
Backfill có protection=true, revision=1, một key và **0 audit giả**; import
không bật lại. Check License không áp dụng rule ngừng; restore tay áp dụng lại.

Negative case đã thử: file có cùng khóa inactive và note “reactivate”,
worker process mới, cờ overwrite, khóa hiện tại sau nhiều lần sửa; không ON
CONFLICT nào trong các luồng thử bật lại rule.

Residual risk: không chứng minh nguồn gốc sửa tay của active legacy, vốn
không thuộc cam kết backfill v2.

## Claim V11

Kết quả: **PASS**, cả **upsert / replace_scoped**.

Test thực hiện: CAS đổi value rồi đổi status; code đổi A→B, đổi status rồi
đổi C và inactive. File chứa tất cả khóa cũ và khóa hiện tại.

Evidence: không tạo rule A/B cũ, không đổi owner; chi tiết liên kết đúng
rule ID. Riêng CAS `50-00-0`→`64-17-5`, rồi chuyển `ĐƯỢC BÁN`: sau apply
hai mode, snapshot rule/key/audit bằng trước import.

Negative case đã thử: file cũ vẫn ghi tình trạng cũ, owner inactive,
chiếm khóa cũ bằng thêm tay và giả overwrite; đều không tái tạo khóa đã giữ.

Residual risk: không có giải phóng khóa lịch sử; đây là giới hạn nghiệp vụ
đã chốt, không phải lỗi verifier bỏ qua.

## Claim V13

Kết quả: **PASS**, cả **upsert / replace_scoped**.

Test thực hiện: 53 rule qua hai trang; retry thực bằng HTTP; chọn event lịch
sử; lỗi lần retry tiếp; cleanup sau apply; thay rule sống sau apply; 8 MiB
UTF-8 compact ±1 byte và HTTP preview Unicode vượt ngưỡng.

Evidence:

- Trang trả **50/3/0** detail, ghép đúng 53 ID; status response không chứa
  protection_details. Các fixture mixed import kiểm tra current/historical/
  inactive/absent và tổng dòng file riêng với tổng rule.
- **Upsert:** event cũ `3`, preview mới `6`, apply event `9`.
- **Replace_scoped:** event cũ `18`, preview mới `21`, apply event `24`.
- Sau retry: DB `queued`, `preview_ready=false`, `preview=NULL`;
  URL mặc định **404**, URL explicit event cũ **200** trước khi worker chạy.
- Preview mới lấy note sau retry; snapshot explicit cũ không đổi. Khi đang
  queued apply, mặc định cũng không thay bằng snapshot preview cũ.
- Sau cleanup, URL mặc định lấy đúng apply_completed. Đổi rule sống không
  sửa snapshot; event của job khác bị 404. Retry fail do file hash sai:
  mặc định 404, không trả snapshot cũ như lần xử lý hiện tại.
- Với note Unicode: **8.388.607 byte** và **8.388.608 byte** giữ đủ **360
  detail** ở cả hai mode; **8.388.609 byte** bị từ chối. Byte được đo lại bằng
  `json.dumps(..., ensure_ascii=False, separators=(',', ':')).encode('utf-8')`.
  Chỉ bước dựng fixture tạm tăng ceiling để đo; assertions chạy ngưỡng thật.
- Workbook ZIP_STORED 360 dòng note Unicode dài qua HTTP: cả hai mode
  preview **failed**, `preview_ready=false`, confirm apply bị chặn, rule
  không đổi. Upsert hướng dẫn chia theo dòng; replace hướng dẫn scope không
  giao nhau, không chia một nhóm thành nhiều lần thay thế.
- Hai file HALF-A rồi HALF-B cùng scope replace: cuối cùng chỉ còn HALF-B,
  chứng minh chia tùy ý theo dòng là không an toàn.

Negative case đã thử: xung đột ngoài trang đầu; URL mặc định ngay sau retry
khi attempts chưa tăng; event của job khác; retry thất bại; Unicode byte
khác character count; vượt ngưỡng và file replace chia cùng scope.

Reproduction của lỗi lần trước và kết quả lần này:

1. DB thử mới, tạo rule thủ công; với mỗi mode upload file cùng khóa, chạy
   `run_once(job_id)` để hoàn tất preview, lưu ID preview_completed.
2. HTTP POST `/admin/regulatory/jobs/<id>/retry` với CSRF hợp lệ; không chạy
   worker tiếp. Đọc DB bằng connection khác: queued/false/NULL.
3. GET protection mặc định: 404, thông báo chưa có snapshot hoàn tất;
   GET `?event_id=<event cũ>`: 200.
4. Sửa note tay, chạy worker preview; snapshot mặc định có event mới/note
   mới; selector event cũ giữ nguyên snapshot trước đó.
5. Confirm apply, chạy worker, cleanup upload/preview và sửa rule sống:
   snapshot apply vẫn nguyên vẹn.

Residual risk: HTTP/SQL và DOM harness, chưa UAT lỗi mạng thật trên trình
duyệt. Không benchmark JSON hàng loạt trên dữ liệu production.

## Claim V14

Kết quả: **PASS**, cả **upsert / replace_scoped**.

Test thực hiện: import giữ rule→expire job→cleanup→đọc event→upload mới,
worker Python process mới→apply.

Evidence: job.preview thành NULL nhưng chi tiết event còn đúng. Rule tay,
inactive, keys lịch sử và audit không đổi; lần import bằng process mới tiếp
tục giữ đúng protected snapshots ở cả hai mode.

Negative case đã thử: dọn upload/preview và bỏ toàn bộ state process worker
cũ; protection không phụ thuộc cache/session/job JSON.

Residual risk: chỉ mô phỏng restart local; không restart service đang phục
vụ người dùng và không chứng minh retention hạ tầng bên ngoài application.

## Claim V15

Kết quả: **PASS**.

Test thực hiện: hai HTTP confirm đồng thời cùng request_id; replay create
sau khi rule đổi khóa; actor khác dùng UUID cũ; UI preview bất đồng bộ.

Evidence: hai request cùng ID nhận 200, một replay, một row/event created.
Replay không tạo lại khóa cũ. Actor khác với quyền hợp lệ nhận 409 và
rule_id null. Payload khác nhận 409. Developer test thu hồi quyền trước
replay chặn ghi. Revision sai/thiếu và CSRF sai bị từ chối.

Negative case đã thử: response check về muộn sau khi form đã đổi: preview
không mở lại, confirm gửi **0** save. Preview mới rồi double-click khi save
pending gửi **1** request, chứa đúng giá trị mới. Mất response/retry giữ
request_id được kiểm tra thêm trong developer DOM suite.

Residual risk: chưa thử trình duyệt/device thật hoặc proxy làm mất response;
idempotency DB được thử với hai connection/request thật.

## Claim V16

Kết quả: **PASS**.

Test thực hiện: worker apply chờ domain advisory lock thực, connection giữ
lock commit sửa tay rồi nhả; cả hai mode. Revoke auth_version trong lúc
worker chờ. Status rename qua endpoint HTTP khi form tay đã mở.

Evidence: quan sát `pg_stat_activity.wait_event='advisory'` trước khi commit
writer. Import stale nhận failed, note “manual winner” và protection giữ
nguyên. Worker bị revoke không ghi rule. Rename thực đồng bộ rule revision
**1→2**; confirm revision 1 nhận **409**. Không treo hết timeout test.

Negative case đã thử: commit tay sau preview nhưng trước worker được khóa,
thu hồi quyền lúc chờ, writer nhãn làm form tay stale; không mất sửa tay.

Residual risk: thử lịch xen kẽ có kiểm soát, không phải stress test mọi lịch
thread/connection hoặc chứng minh hình thức không deadlock.

## Claim V17

Kết quả: **PASS**.

Test thực hiện: trigger DB làm lỗi audit sau UPDATE/ghi key; trigger lỗi
dòng import cuối sau dòng đầu hợp lệ, cả hai mode; developer fault/worker
transaction regression.

Evidence: snapshot rule/key/audit sau lỗi bằng trước. Bỏ audit fault rồi
retry cùng request_id thành công. Import lỗi dòng cuối báo failed, không có
apply_completed và không giữ dòng FIRST-OK đã xử lý trước LAST-FAIL.

Negative case đã thử: lỗi PostgreSQL thật ở audit và cuối job nhiều dòng;
không chỉ mock response hoặc kiểm tra HTTP 200.

Residual risk: không kill tiến trình PostgreSQL/container hoặc mô phỏng
storage failure; cancellation/rollback được phủ bởi regression hiện có.

## Claim V18

Kết quả: **PASS**.

Test thực hiện: 81 regression tests, import fixture protected/unprotected,
invalid workbook cả hai mode và Check License ngừng/khôi phục.

Evidence: các suite search precedence, Check License, Quick Quote/export,
manual product override, policy/color/status và permissions đều đạt.
Independent file rỗng, duplicate normalized và CAS checksum sai báo failed,
giữ snapshot DB. Thuần import vẫn cập nhật hoặc xóa đúng scope.

Negative case đã thử: code/CAS/name nhiều tình trạng, manual ALLOW trước
BLOCK qua regression; inactive không tác động; import duplicate/empty
không biến thành xóa dữ liệu.

Residual risk: không chạy toàn bộ repository test suite; đã chạy các suite
được giao và liên quan trực tiếp, không coi đó là mọi kiểm chứng toàn hệ thống.

## Claim V19

Kết quả: **PASS**.

Test thực hiện: psql thật trên schema trước 033; backfill inactive rồi
import; rerun sau audit và khóa lịch sử; job contract cũ queued apply;
thiếu schema; migration failure regression.

Evidence: cột nghiệp vụ legacy inactive nguyên vẹn, metadata true/1,
một key, audit rỗng; các snapshot status/products/grants đối chiếu không đổi.
Rerun có manual event/key không reset dữ liệu. Migration fault rollback
schema (developer test). Worker apply thiếu contract_version bị failed
ở cả hai mode. Rename bảng keys để schema thiếu: HTTP save **503**, worker
preview cả hai mode failed với thông báo 033, protected row không đổi.

Negative case đã thử: queued apply phiên bản cũ, thiếu cấu trúc bảo vệ,
rerun sau có lịch sử; không fallback về import không bảo vệ.

Residual risk: fixture schema có dữ liệu tổng hợp, không migration trên
snapshot production hoặc xác minh mọi kiểu dữ liệu legacy thực tế.

## Claim V20

Kết quả: **PASS**.

Test thực hiện: down unused, down sau backfill/manual use, revision-only,
event-only khi job.preview đã xóa; backup/restore local từ suite migration;
review runbook vận hành.

Evidence: down khi chưa dùng thành công, có thể up lại. Down có revision
khác 1 nhưng không audit/keys bị từ chối, snapshot không đổi. Fixture chỉ
còn event contract v2, không rule và job.preview NULL cũng bị từ chối,
bảng audit còn nguyên. Backup restore vào DB thử khác đạt đối chiếu fixture.
Runbook yêu cầu dừng web/worker nếu chưa bảo đảm chặn riêng mọi writer.

Negative case đã thử: không có active rule/audit không đồng nghĩa chưa dùng;
backfill inactive, revision-only và import event độc lập đều giữ guard.

Residual risk: rehearsal backup/restore nhỏ ở local, không chứng minh thời
gian khôi phục, dung lượng hoặc backup production. Không có quyền deploy/down thật.

## Claim V21

Kết quả: **PASS**.

Test thực hiện: form/check/confirm với tên rất rộng `a` và giá trị không
khớp `UNMATCHABLE-872364`; quan sát execute trên connection/cursor DB thật,
không mock kết quả query.

Evidence: **84 SQL statements**, **0 query FROM/JOIN products** trong luồng
được quan sát. Cả hai form hiện cảnh báo phụ thuộc ưu tiên/ngoại lệ, không
hiện “0 sản phẩm”. Payload `count_products=true` nhận 400; payload hợp lệ
vẫn lưu được. Diff không thêm count/cache/index products cho tính năng này.

Negative case đã thử: tên rất rộng, tên không có sản phẩm, cờ client yêu cầu
đếm; không xuất hiện truy vấn đếm ngầm hay khẳng định số ảnh hưởng.

Residual risk: không đo hiệu năng production; việc không triển khai số đếm
là phạm vi đã chốt, không phải thiếu benchmark cần tự bổ sung.

## Lượt bổ sung — polish hiển thị — 2026-09-28

### Phạm vi và kết luận lượt này

- **PASS trong phạm vi kiểm chứng lại V3, V4, V7, V9, V13, V15.** Đã chạy
  negative/adversarial case độc lập cho cả sáu claim; chưa tìm được hành vi
  vi phạm trong phạm vi hiển thị được giao. Không thay kết luận/evidence các
  lượt trước, không tuyên bố kiểm chứng mới toàn bộ 20 claim.
- Hợp đồng dùng nguyên bản `VERIFICATION.md` v2 tại `ee2ee5a`; kiểm tra
  `git diff ee2ee5a -- specs/regulatory-manual-edit/VERIFICATION.md` rỗng.
- V3/V4/V15 bị ảnh hưởng tại nhãn loại đối chiếu và UI so sánh trước/sau;
  V7 tại lịch sử, boolean và thời gian; V9/V13 tại đơn vị xác nhận xóa,
  nhật ký import và trang snapshot bảo vệ. Không thay đổi oracle hợp đồng.
- Đọc diff `ee2ee5a` → working tree `feature/regulatory-manual-edit` và
  source/test liên quan, kể cả helper, JS và template chưa được Git theo dõi.
  Không sửa application code, không commit/push, không sửa Task status.

### Môi trường và lệnh thực thi

- Runner preflight xác nhận Docker endpoint unix socket, container
  `search-tools-regulatory-test-db-1`, project `search-tools-regulatory-test`,
  service `db`, image `postgres:16-alpine`, cổng 5432, `pg_isready` thành công.
- Chỉ fixture tổng hợp trong database tạm prefix `p6a_release_gate_pgtest_`,
  có cleanup; dotenv bị chặn trước import ứng dụng và trong process Python
  con. Không đọc `.env`, không dùng database UAT, không khởi động worker chung,
  không truy cập staging/production. Worker được gọi `run_once(job_id)` trên
  đúng job fixture. Các query đối chiếu sau commit dùng connection độc lập.
- `.venv/bin/python scripts/test_regulatory_local.py test_regulatory_manual_display -v`
  → **3/3 PASS**, 1,607 giây, không skip.
- `.venv/bin/python scripts/test_regulatory_local.py discover -s tests -p 'test_regulatory_manual*.py' -v`
  → **24/24 PASS**, 10,511 giây, không skip; gồm lại 3 test hiển thị trên.
- `.venv/bin/python scripts/test_regulatory_local.py test_phase6d1_regulatory test_admin_menu_permissions test_search_compliance_precedence test_product_manual_compliance_import -v`
  → **81/81 PASS**, 8,037 giây, không skip.
- `node tests/regulatory_manual_dom_test.js` → PASS cả cas/code/name.
- `node tests/admin_regulatory_dom_test.js` → exit 0.
- Suite độc lập trong bộ nhớ `IndependentDisplay` → **4/4 PASS**, 5,273
  giây, không skip: `test_v3_v4_v15`, `test_v7`, `test_v9_v13_replace`,
  `test_v9_v13_upsert`. Parent gọi `preflight()` từ runner trước import test;
  child dùng cùng env test và `tests/no_dotenv` ở đầu PYTHONPATH, upload tạm.
- DOM độc lập qua `node` stdin → **3/3 nhóm PASS**, lần lượt cas/code/name;
  thực thi `static/regulatory_manual.js`, không sao chép logic renderer.
- `git diff --check` → exit 0.
- Cảnh báo quan sát: urllib3/LibreSSL, Python invalid escape `\d`, và
  ResourceWarning file workbook trong regression. Không làm test fail/skip;
  không diễn giải thành bằng chứng môi trường production an toàn.
- Công cụ từ chối ghi `tests/independent/`; không ghi test bằng công cụ
  khác. Các probe riêng chạy trong bộ nhớ theo quyền được cấp. Cách tái hiện
  và expected cụ thể được ghi dưới đây; không có file test độc lập được lưu.

### Claim V3 — lượt polish

Test thực hiện: HTTP kiểm tra dữ liệu → confirm cho cả CAS `50-00-0`, code
`REPLAY-CODE`, name `Tên kiểm chứng`; DOM tạo mới của suite cung cấp và
probe độc lập với response kiểm tra bị trì hoãn.

Kết quả: **PASS**.

Evidence: `/rules/check` trả 200 nhưng snapshot toàn bộ sáu bảng
rule/status/keys/manual-events/jobs/import-events không đổi. Confirm hợp lệ
trả 200. GET form giữ mã `cas/code/name` trong hidden input nhưng ô readonly
hiện `CAS/Mã sản phẩm/Tên sản phẩm`; GET không ghi dữ liệu. Suite DOM cung
cấp kiểm tra preview chưa gọi save ở cả ba loại.

Negative case đã thử: dùng chính nhãn tiếng Việt/CAS hoa làm `match_field`,
giả `actor`, giả `manual_protected=false`, giá trị toàn khoảng trắng: tất cả
HTTP 400, sáu bảng không đổi. DOM độc lập sửa note khi `/check` chưa trả về:
response cũ không mở review; bấm confirm sau đó tạo **0 request save**.

Residual risk: probe DOM dùng mô hình DOM trong Node, không phải trình
duyệt thật; không xác nhận lại UAT 8/8 của PO trong lượt này.

### Claim V4 — lượt polish

Test thực hiện: mỗi loại cas/code/name tạo rule rồi sửa note `v1` → `v2`;
đối chiếu row DB, nhãn readonly, payload và revision.

Kết quả: **PASS**.

Evidence: HTTP edit hợp lệ 200, cùng ID và `match_field`, revision **1 → 2**,
note `v2`; mỗi rule đúng 2 event created/edited. DOM độc lập dùng rule
inactive ID 77/revision 9: hai phía hiển thị cùng nhãn đã dịch, trạng thái
vẫn “Ngừng áp dụng”; payload giữ mã field, ID/revision, không gửi `is_active`.

Negative case đã thử: giả đổi field sang loại khác, đổi status với lý do
trắng, expected_revision=0, thêm `is_active=false` đều 400 và không đổi
snapshot. Sau edit thành công, gửi request_id mới nhưng revision 1 → 409,
không đổi DB/audit. Suite hồi quy cũng chạy no-op và status không tồn tại.

Residual risk: chỉ kiểm chứng lại vùng tác động của polish và regression
được nêu; không thay evidence concurrency chi tiết của lượt trước.

### Claim V7 — lượt polish

Test thực hiện: HTTP created → edited → deactivated → restored; đổi tên
tình trạng qua endpoint `/statuses` và đổi username fixture sau khi audit
đã được ghi. Đọc phần lịch sử và so nguyên row audit trước/sau rename.

Kết quả: **PASS**.

Evidence: đúng **4 event**, after.is_active lần lượt true/true/false/true.
Audit vẫn nguyên sau rename; HTML lịch sử giữ `manual_admin` và nhãn tình
trạng cũ, không thay bằng tên mới. Không có “ID tình trạng”. Có đủ các ô
`Có → Không`, `Không → Có`, `— → Có` (created) và bảo vệ `Có → Có`.
Fixture rule thuần import được sửa tay hiện bảo vệ **Không → Có**, không
nhầm false với thiếu giá trị. JSON lưu vẫn giữ status_id/boolean gốc.

Negative case đã thử: note/reason chứa `<svg onload=...>`, `<img ...>`,
`<script>bad()</script>` không thành thẻ HTML thực thi; lịch sử có chuỗi
escape. Mốc giờ được gán vào fixture trước khi đọc để kiểm tra độc lập:

- `2026-09-28T06:01:59Z` → **28/09/2026 13:01**.
- `2026-12-31T18:05:00Z` → **01/01/2027 01:05**.
- `2024-02-28T18:00:00Z` → **29/02/2024 01:00**.
- `2026-09-28T00:01:00-06:00` → **28/09/2026 13:01**.

Sáu bảng không đổi sau GET lịch sử. Suite được cung cấp chạy thêm lỗi tại
ghi audit và xác nhận rollback toàn bộ thay đổi rule/key.

Residual risk: không tái chạy độc lập toàn bộ phân trang lịch sử/purge của
lượt trước; dùng evidence trước đó cho phần không đổi bởi polish.

### Claim V9 — lượt polish

Test thực hiện: fixture import độc lập cho **cả upsert và replace_scoped**:
52 rule tay (một rule có khóa cũ và khóa hiện tại), thêm một rule tay inactive
vắng file, một rule thuần import vắng file, một rule ở tình trạng khác.
Upload HTTP workbook 54 dòng → worker preview → confirm HTTP → worker apply.

Kết quả: **PASS**.

Evidence: replace_scoped preview xóa **1 rule**, UI hiện đúng “Nhập 1 để
xác nhận số quy tắc sẽ xóa”; apply thực sự chỉ bỏ ID thuần import vắng file.
Upsert xóa **0**, vẫn còn ID đó. Mọi cột của 53 rule tay và rule ngoài scope
giữ nguyên; audit tay giữ nguyên ở cả hai mode. Dòng NEW được thêm đúng một
row để chứng minh import không bị bỏ chạy toàn bộ.

Negative case đã thử: gửi số **54** (số dòng file) thay cho **1** để xác nhận
xóa bị từ chối, redirect có `err=`, sáu bảng nguyên vẹn. Thêm `force=1` ở
confirm cả hai mode → 400, không ghi. Rule inactive vắng file không bị xóa
hoặc bật active. Test hiển thị được cung cấp còn chạy số xóa **0, 1, 3** và
từ chối số xác nhận sai, chứng minh text không hardcode 1.

Residual risk: đây là kiểm chứng lại đơn vị/xác nhận và phạm vi xóa quanh
polish, không phải lượt mới cho mọi biến thể scope của hợp đồng.

### Claim V13 — lượt polish

Test thực hiện: dùng fixture V9, trong file có đồng thời khóa cũ và khóa
hiện tại của cùng rule; HTTP đọc cả hai trang snapshot, xem nhật ký, retry
trước worker tiếp theo, rồi apply. Expected đếm từ fixture, không lấy từ
planner để tạo expected.

Kết quả: **PASS**.

Evidence:

- Cả hai mode: **54 dòng file = 1 thêm + 0 cập nhật + 53 giữ/bỏ qua**.
- Upsert: **52 rule được bảo vệ**, **53 chi tiết**, trang **50 + 3**;
  xóa 0. Replace_scoped: **53 rule được bảo vệ**, **54 chi tiết**, trang
  **50 + 4**; xóa 1. Ghép ID từ HTML bằng đúng multiset fixture (rule có
  hai khóa xuất hiện hai chi tiết, nhưng chỉ tính một rule được bảo vệ).
- Snapshot hiện “Mã sản phẩm”, escape HTML từ file/note; mốc cuối năm trên
  trang snapshot và nhật ký cùng hiện **01/01/2027 01:05**.
- Nhật ký HTTP hiện `<td>manual_admin</td>`, không còn ô tác nhân `user:<id>`.
  Suite cung cấp kiểm tra thêm username chứa HTML, worker “Hệ thống” và
  actor không còn tồn tại; GET job/list/protection không sửa dữ liệu lưu.

Negative case đã thử: xung đột/giữ nằm cả sau trang đầu; rule tay vắng file
chỉ tăng tổng rule/chi tiết, không tăng số dòng file. Retry bằng CSRF hợp lệ
ở cả hai mode: GET protection mặc định **404**, chọn `event_id` hoàn tất
cũ tường minh **200** và vẫn hiện đúng giờ snapshot cũ. Worker preview mới
hoàn tất rồi apply thành công; trang mặc định **200** trở lại. Tái hiện lỗi
V13 được cung cấp không còn xuất hiện. Suite hồi quy chạy thêm ngưỡng 8 MiB
và test retry/new snapshot của developer.

Residual risk: không chạy độc lập lại byte-boundary cả hai mode trong lượt
hiển thị này; giữ evidence lượt trước cho hợp đồng giới hạn snapshot đầy đủ.

### Claim V15 — lượt polish

Test thực hiện: cả ba loại field, confirm create → edit tiếp → retry create
bằng đúng request_id/payload; thử payload khác, thiếu CSRF và edit stale.
DOM độc lập cố gây race khi kiểm tra preview và double-click lúc save pending.

Kết quả: **PASS**.

Evidence: retry create sau edit trả **200/replayed=true**, DB vẫn note `v2`,
revision 2, đúng 2 event, không tạo rule/audit mới. Cùng request_id đổi note
→ **409**; replay thiếu CSRF → **400**; edit request_id mới/revision cũ →
**409**. Snapshot sáu bảng không đổi sau tất cả request bị từ chối/replay.
DOM cả cas/code/name: sửa form khi check pending làm review cũ vô hiệu;
preview mới dùng note mới; double-click khi save pending chỉ **1 request**,
controls khóa trong lúc gửi và được mở lại sau response. Payload vẫn dùng
field kỹ thuật và expected_revision gốc; renderer không dùng innerHTML.

Negative case đã thử: chính các replay sau thay đổi tiếp, request_id dùng
payload khác, CSRF thiếu, revision stale và race DOM trên; không chỉ thử
retry ngay sau happy path. Suite hiện có cũng chạy concurrent request và
thu hồi actor trong lúc chờ khóa.

Residual risk: DOM giả lập không chứng minh trải nghiệm trên mọi trình
duyệt; lượt này không tự xác nhận lại UAT bằng trình duyệt của PO.

### Giới hạn kết luận và bàn giao

- Không có FAIL mới để phân loại ở scope này. PASS là kết quả các probe
  thực thi nêu trên, không phải chứng minh tuyệt đối không còn lỗi.
- Git range được cấp bao gồm cả implementation của feature trước polish,
  không có baseline riêng ngay trước lượt hiển thị. Vì vậy không khẳng
  định bằng diff rằng toàn bộ thay đổi từ `ee2ee5a` chỉ là hiển thị. Điều
  được chứng minh tại runtime: đọc/render không sửa sáu bảng liên quan,
  mã field/revision gửi đi không bị dịch, các guard và kết quả apply/replay
  trong fixture vẫn giữ yêu cầu nghiệp vụ.
- Evidence cũ giữ nguyên. Khối Task status dưới đây do agent chính quản lý,
  verifier không đổi attempt/max_auto_repairs/same_claim_repeat_fail/next_action.
- Không cập nhật `docs/regulatory-manual-edit/VALIDATION.md` vì ngoài quyền
  ghi của verifier; agent chính có thể dẫn kết quả bổ sung này vào VALIDATION.
  Không commit; dừng để agent chính/PO review theo quy trình.

## Task status

attempt: 1 / max_auto_repairs: 2
same_claim_repeat_fail: none
next_action: HUMAN_REVIEW
