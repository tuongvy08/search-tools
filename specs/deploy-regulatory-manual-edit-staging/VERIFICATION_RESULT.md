# Verification Result — staging ops / fingerprint correction

Ngày kiểm chứng: 2026-09-29. Verifier độc lập; chỉ kiểm chứng offline phần
fingerprint, guard liên quan và tài liệu được giao. Không thực thi main của
prepare/cutover, signal handlers, SSH/SCP/systemctl, Docker, DB, ứng dụng
hoặc đọc `.env`/EnvironmentFiles thật. Không xác nhận runtime staging hay UAT.

## Kết luận lượt này

**FAIL — IMPLEMENTATION_FAIL tại S1 (guard nhận diện tiến trình web).**

- Bản sửa fingerprint đạt các ca local đã thử, bao gồm running → stopped,
  phát hiện thay đổi cấu hình thật và không lộ giá trị cấu hình.
- Tuy nhiên, phản ví dụ độc lập cho S1: `unit(WEB)` chấp nhận một tiến trình
  chạy script khác khi `search:app` chỉ là một đối số của script đó. Như vậy
  guard chưa chứng minh web thực tế đang chạy đúng ứng dụng đã xác minh.
- Đây là lỗi trong guard hiện có, không phải hồi quy được chứng minh từ hai
  phần fingerprint vừa sửa. Không có baseline Git độc lập cho script ngoài
  repository. Phát hiện này **không chứng minh staging hiện đang chạy sai**.
- S2, backup/migration/server thực và UAT S7: **NOT RUN** trong lượt này.
  Không dùng PASS của helper hoặc review tài liệu để báo deploy/UAT đạt.

## Evidence và phương pháp

Đã chạy tại `/Volumes/DATA/Development/Search-tools`:

1. `.venv/bin/python -B /Volumes/DATA/Development/_ops/search-tools/test_cutover_fingerprint.py`
   — **9/9 PASS**.
2. `.venv/bin/python -B /var/folders/d0/2xc6xwxd3bd53qggrfvmpgvw0000gn/T/opencode/check_staging_entrypoint_gates.py`
   — **30 kiểm tra prepare + 30 kiểm tra cutover PASS**. Harness dùng fixture
   tạm, không chạy main hoặc lệnh server.
3. Harness phản biện riêng chạy qua `.venv/bin/python -B -`, chỉ trong bộ nhớ,
   nạp các class/function được chọn bằng AST, không lưu file test:
   **57 kiểm tra hỗ trợ PASS** (`F=29`, `S1=6`, `S4=10`, `S5=6`, `S3=6`),
   cộng **1 ca phản ví dụ S1 FAIL** trình bày bên dưới. Proc/config/stat,
   subprocess và kết nối đều là fixture/mock. Một lượt nháp harness dừng vì
   lỗi chữ ký lambda của mock; đã sửa harness trong bộ nhớ và chạy lại trọn
   bộ 57 kiểm tra. Lỗi harness đó không được phân loại là lỗi implementation.
4. Review toàn source cutover, AST các nhánh/gate không thực thi main,
   `git diff main -- PROJECT_STATE.md`, hai tài liệu ops và hợp đồng v1.
   Đối chiếu cả **14/14** ví dụ SSH/SCP trong các bash block của runbook:
   tất cả dùng đích `staging`.
5. `git diff --check` — **PASS**. Lệnh Git này không kiểm tra nội dung các
   file ngoài Git; chúng được đọc/review riêng.

Danh tính source ngoài Git tại lúc kiểm chứng (SHA-256):

- `cutover_regulatory_staging.py`:
  `a8c08ffc21b0f75850be63e72b14d41e998a1cbde88a742d785d422397a03576`.
- `test_cutover_fingerprint.py`:
  `cfb6677a9ebcfa4c556f14e518f3f96ee6681ac7cd6988420fc340f153b7e3c0`.

## Yêu cầu PO — fingerprint, rà so sánh và ghi nhận sự cố

Test thực hiện: 9 developer tests và 29 kiểm tra độc lập cho fingerprint;
review mọi nhóm so sánh trong cutover cùng tài liệu sự cố/phục hồi.

Kết quả: **PASS trong phạm vi correction local**; không thay kết luận FAIL S1.

Evidence:

- Đổi PID, thời điểm chạy/dừng, trạng thái kết thúc, kể cả killed/TERM và
  runtime rỗng/n/a: fingerprint không đổi khi path/argv và các nguồn cấu
  hình giữ nguyên. Đổi thứ tự property hoặc atime không làm sai gate.
- Đổi riêng path, argv, Environment, EnvironmentFiles, FragmentPath,
  DropInPaths của từng service: fingerprint khác. Đổi riêng device/inode/
  size/mtime của một drop-in worker cũng bị phát hiện; thêm ExecStart record
  thứ hai không bị bỏ qua.
- File thiếu/symlink bị chặn; ExecStart thiếu argv, quote chưa đóng hoặc
  có rác phía sau bị từ chối bằng tên gate, không phát ra sentinel giả.
  Snapshot trả về không chứa raw Environment/sentinel, stdout/stderr rỗng.
- Counterfactual trong AST ở bộ nhớ: chỉ đưa append trở lại dùng `settings`
  nguyên văn như diff cũ được cung cấp. Đổi running → stopped khiến hai
  fingerprint khác nhau; chứng minh ca hồi quy này bắt được lỗi cũ, không
  chỉ là test luôn PASS. Không sửa source để chạy counterfactual.
- Rà toàn bộ so sánh: state/PID được kiểm theo giai đoạn; Git BASE→TARGET;
  schema absent→complete; cgroup/queue kiểm trạng thái hiện tại; DSN/DB phải
  giữ nguyên; counts nghiệp vụ giữ nguyên trong cửa sổ không ghi; listener
  so port, không so inode cũ; disk/backup/version/HTTP/asset dùng ngưỡng hoặc
  đích riêng. Không thấy thêm phép so toàn runtime trước/sau cùng loại lỗi
  ExecStart. Mô phỏng inode socket thay nhưng port giữ nguyên vẫn đạt.
- `VALIDATION.md:132–211` ghi sự cố và phục hồi; runbook mục đầu và mục 6
  tách trạng thái hiện tại khỏi lệnh lịch sử, cấm rerun cutover đã COMMIT,
  ghi start bản mới thay vì rollback, chờ PO báo UAT. Evidence server được
  gắn nguồn PO; thiếu asset/counts sau phục hồi không bị ghi thành PASS.

Negative case đã thử: cấu hình/metadata thật đổi trong khi runtime được
đổi đồng thời; malformed ExecStart chứa sentinel; file thiếu/symlink;
counterfactual raw-output cũ. Các ca xử lý như mô tả trên.

Residual risk: không có output systemctl thật để kiểm tra mọi biến thể
format; không chứng minh chống thay nội dung rồi hoàn nguyên metadata hoặc
race giữa các snapshot; không thực thi fingerprint mới trên staging.

## Claim S1 — Đúng đích và chỉ staging

Test thực hiện: mock `unit`, proc/cwd/cmdline, cấu hình unit và DSN;
review SHA gates và alias trong runbook. Có 6 kiểm tra hỗ trợ PASS nhưng
ca nhận diện web đối kháng bị chấp nhận sai.

Kết quả: **FAIL**.

Classification: **IMPLEMENTATION_FAIL**.

Observed:

- `systemctl show` giả trả active, user đúng, MainPID 101, ExecStart là
  gunicorn `search:app` đúng cấu hình; cwd của PID 101 đúng LIVE.
- Proc cmdline giả là
  `/virtual/other-python\0/virtual/not-web.py\0search:app\0`:
  chương trình thực thi không phải gunicorn/app web; `search:app` chỉ là
  một đối số phụ.
- `unit('web.service')` vẫn trả **101**, không ném GateFailure.
- Output tái hiện: `DECOY_WEB_PROCESS: ACCEPTED; returned PID=101; cmdline executes not-web.py with search:app only as an argument`.
- Source tham chiếu: `verify_entrypoint` nhánh web tại
  `cutover_regulatory_staging.py:127–139`, được `unit` gọi tại dòng 183.

Expected:

Guard xác minh tiến trình web thực tế phải từ chối trạng thái không chạy
entrypoint web đã duyệt; token `search:app` làm đối số cho một script khác
không phải bằng chứng nhận diện app. Không được coi kết quả này là gate
live web đã đạt trước các bước ghi.

Negative case đã thử:

- PID biến mất → `WEB_CWD_READ`; cwd khác → `WEB_CWD_MATCH`; proc title
  `other:app` → `WEB_CMDLINE_ENTRYPOINT`; DSN tên DB khác →
  `PG_STAGING_DATABASE_NAME`: bị chặn đúng.
- Script khác kèm token mồi `search:app`: **không bị chặn**, như trên.
- PID 101 trước restart và 202 sau restart, mỗi lần đều đúng danh tính:
  được chấp nhận, không có phép ép PID mới phải bằng PID cũ.

Reproduction (chỉ fixture, không execute main; chạy từ repository):

```bash
.venv/bin/python -B - <<'PY'
import ast, pathlib, re, shlex, os
from unittest import mock
p = pathlib.Path('/Volumes/DATA/Development/_ops/search-tools/cutover_regulatory_staging.py')
t = ast.parse(p.read_text())
names = {'GateFailure', 'unit', 'verify_entrypoint',
         'worker_python_matches', 'worker_entrypoint'}
ns = dict(pathlib=pathlib, re=re, shlex=shlex, os=os,
          LIVE=pathlib.Path('/virtual/live'), WEB='web.service',
          WORKER='worker.service', OWNER='fixture-user')
safe = ast.Module(body=[n for n in t.body
    if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names],
    type_ignores=[])
exec(compile(safe, str(p), 'exec'), ns)
start = ('ExecStart={ path=/virtual/live/.venv/bin/gunicorn ; '
         'argv[]=/virtual/live/.venv/bin/gunicorn search:app ; '
         'ignore_errors=no; pid=101; }')
ns['checked'] = lambda args, **kw: start if 'ExecStart' in args else (
    'ActiveState=active\nSubState=running\nMainPID=101\nUser=fixture-user')
with mock.patch.object(pathlib.Path, 'resolve', return_value=ns['LIVE']), \
     mock.patch.object(pathlib.Path, 'read_bytes', return_value=(
         b'/virtual/other-python\0/virtual/not-web.py\0search:app\0')):
    try:
        pid = ns['unit']('web.service')
    except ns['GateFailure'] as error:
        print('REJECTED', str(error))
    else:
        print('ACCEPTED', pid)
PY
```

Hiện tại in `ACCEPTED 101`; mong đợi bị từ chối.

Residual risk: chưa chạy nguyên cutover, kể cả main với mock; không khẳng
định đã xảy ra bước ghi hoặc web staging thật có trạng thái này. Alias SSH,
DB đang chạy và sự phê duyệt trên server không được xác minh bằng local test.

## Claim S2 — PostgreSQL 14 local không động dữ liệu người dùng

Test thực hiện: không chạy; PostgreSQL/Docker nằm ngoài quyền lượt này.

Kết quả: **NOT RUN**.

Evidence: chỉ đọc các số liệu PG14 được ghi trong tài liệu; không coi đó là
kết quả độc lập của verifier này.

Negative case đã thử: không thực thi ca PG16/DB UAT/psql thiếu vì không được
chạy DB/runner; không kết luận PASS cho claim này.

Residual risk: toàn bộ oracle integration S2 chưa được kiểm chứng lại.

## Claim S3 — Backup đọc được trước migration; không lộ secret

Test thực hiện: 6 ca độc lập về kết nối/secret với sentinel giả; review
argv và luồng pg_dump → size/list → SQL trong source.

Kết quả: **PASS cho tiểu tiêu chí secret offline; backup thật NOT RUN**.

Evidence: DSN/password fixture chỉ được chuyển vào PG* environment.
Mock bắt lệnh `query` thấy argv không chứa sentinel, stderr là DEVNULL,
stdout/stderr của helper không phát ra sentinel. Lỗi subprocess chứa
sentinel trở thành gate cố định `PG_READ_ONLY_QUERY`; lỗi bất ngờ qua
`failure_gate` không trả nội dung exception. Source dump/psql không truyền
DSN/password vào argv; output DB bị capture, không report raw cấu hình.

Negative case đã thử: options kết nối không cho phép bị chặn bằng
`PG_CONNECTION_OPTIONS`; subprocess lỗi mang secret giả không bị in ra.

Residual risk: không dump/restore/list thật, không kiểm disk/permission
server và không mô phỏng main gặp hết đĩa/zero-byte. `--list` không được
diễn giải thành restore đã thành công.

## Claim S4 — Không có writer cũ trong migration

Test thực hiện: 10 ca helper local về service stop, cgroup và ba queue;
review thứ tự gate trước backup/migration.

Kết quả: **PASS cho các guard local đã thử; runtime server NOT RUN**.

Evidence: inactive nhưng MainPID vẫn 202 bị chặn tại `WORKER_STOPPED`;
inactive/PID 0 được chấp nhận. Cgroup cha rỗng nhưng child còn PID 777 bị
chặn tại `WORKER_CGROUP_EMPTY`; cgroup đã bị xóa được chấp nhận. Mỗi queue
product/regulatory/stock được thử riêng cả `1|0` và `0|1`: đều bị chặn bằng
gate `QUEUE_EMPTY_<TABLE>`. Source kiểm lại queue sau stop worker và sau
stop web, trước backup/SQL.

Negative case đã thử: job xuất hiện tại lần kiểm queue sau preflight;
child còn sống dù MainPID đã hết. Không dùng `systemctl stop` trả 0 làm
bằng chứng duy nhất.

Residual risk: không diễn tập trọn cửa sổ cutover hoặc tiến trình thật;
không chứng minh không có race/restart/writer ngoài các snapshot.

## Claim S5 — Migration/code đồng bộ, dữ liệu khác giữ nguyên

Test thực hiện: 6 ca local schema/listener; review SHA/schema/count gates,
SQL lấy từ approved blob và argv `psql -X -v ON_ERROR_STOP=1 -f -`.

Kết quả: **PASS cho các ca helper local đã thử; migration/dữ liệu/server
NOT RUN**. Không kết luận web thực tế đúng ứng dụng khi S1 còn FAIL.

Evidence: schema có riêng một footprint trước migration bị chặn tại
`SCHEMA_033_ALL_ABSENT`; sau migration thiếu trigger bị chặn tại
`SCHEMA_033_COMPLETE`; trigger disabled và index thiếu valid/ready bị chặn
bằng gate tương ứng. Socket inode 501→900 với PID 101→202, port giữ 5001,
được chấp nhận; thêm listener cạnh tranh cùng port bị chặn tại
`WEB_IPV4_LISTENER_OWNERSHIP`. Review thấy các nhánh sai SHA ở candidate,
baseline, sau checkout và sau startup, không đòi SHA mới bằng baseline.

Negative case đã thử: schema partial, trigger disabled, index không đủ và
listener không độc quyền. Không chạy SQL lỗi transaction hoặc code switch.

Residual risk: không có kiểm chứng SQL/backfill/row counts/rollback thật
trong lượt này; process/code/server vẫn cần evidence riêng.

## Claim S6 — Rollback không xóa lịch sử/bỏ bảo vệ

Test thực hiện: review AST handler và diễn tập theo checkpoint trên tài liệu,
không execute handler/main.

Kết quả: **PASS cho chính sách/luồng lỗi được review; phục hồi server NOT RUN**.

Evidence: với nhánh `services_stopped` và `migration_committed=True`, các
lệnh handler chỉ yêu cầu stop worker/web; không checkout code cũ, start,
down SQL hoặc restore. Nhánh chưa biết COMMIT không tự restart/rollback.
Runbook mục 6 yêu cầu giữ writer dừng khi lỗi sau COMMIT, không xóa
protected/audit, restore chỉ vào DB khác để đối chiếu rồi xin duyệt.

Negative case đã thử: lỗi sau backfill/COMMIT trước hoặc trong startup;
mất kết nối khiến không rõ COMMIT; không thể dùng checkpoint hoặc backup
TOC để tự cho phép code cũ chạy. Tài liệu không cấp quyền rollback DB tự động.

Residual risk: handler có thể gặp lỗi stop; review không chứng minh service
thật đã dừng. Tài liệu đã ghi giới hạn này, không lấy chữ HOLD làm evidence.

## Claim S7 — Smoke/UAT staging trước production

Test thực hiện: chỉ review ranh giới tài liệu; không truy cập staging/UI.

Kết quả: **NOT RUN đối với smoke/UAT server**; ranh giới không mở production
trong tài liệu được review **PASS**.

Evidence: runbook chỉ còn yêu cầu chờ PO UAT, fixture UAT-STG-/upsert,
không replace_scoped; chưa ghi UAT đạt. Hai file trong thư mục docs task là
STAGING_RUNBOOK.md và VALIDATION.md, không có runbook production tại đó.
Tất cả 14 ví dụ SSH/SCP dùng staging; tài liệu yêu cầu phê duyệt production
riêng và không kế thừa rủi ro đã được chấp nhận cho staging.

Negative case đã thử (review tài liệu): chỉ login HTTP 200, local tests
PASS hoặc phục hồi thủ công thành công không được coi là UAT đạt/quyền
deploy production; không dùng script sửa fingerprint làm lý do rerun
migration/cutover đã COMMIT. Các diễn giải đó bị runbook cấm rõ.

Residual risk: PO UAT, quyền admin, import conflict, cleanup fixture,
asset hash và counts trên staging chưa được verifier này quan sát.

Khối Task status bên dưới được giữ nguyên như agent chính đã giao; verifier
không cập nhật attempt, ngân sách repair hoặc next_action.

## Task status

attempt: 0 / max_auto_repairs: 2
same_claim_repeat_fail: none
next_action: ESCALATE_USER
