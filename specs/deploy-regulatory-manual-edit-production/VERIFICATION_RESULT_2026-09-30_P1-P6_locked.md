# Verification Result — deploy-regulatory-manual-edit-production

Verifier độc lập; ngày 2026-09-29. Đối chiếu hợp đồng production adapter v1
đã khóa, không sửa oracle hoặc hợp đồng staging/feature.

**Kết luận: PASS P1–P6 trong phạm vi local/AST/mock/filesystem/Git fixture.**
Không chạy server, SSH, systemctl thật, PostgreSQL hay ứng dụng. Không đọc
`.env` thật, không sửa application code, không commit/push. PASS này không
phải bằng chứng production đã triển khai và không cấp quyền chạy [GHI].

## Định danh và lệnh kiểm chứng

- Branch: `ops/deploy-regulatory-manual-edit`.
- Prepare đang kiểm: Git blob `47769cc2ccca4c0a8e3417856383557122c1085d`;
  đối chiếu diff từ `f4e5acf67d0041510896425c1e6e9d27957aa777`.
- Cutover đang kiểm: Git blob `080bc6f4d4b30091a1162978da52cac856e1e513`;
  đối chiếu diff từ `2d6ca1b94a00b81c806fc02a0dac29a90c9eb762`.
- `git hash-object` xác nhận hai file hiện tại khớp hai snapshot trên.
- `.venv/bin/python -B /Volumes/DATA/Development/_ops/search-tools/test_production_adapter.py`:
  **30/30 PASS**, gồm Git fixture thực lấy baseline và bundle local.
- `.venv/bin/python -B /Volumes/DATA/Development/_ops/search-tools/test_cutover_fingerprint.py`:
  **10/10 PASS**.
- `.venv/bin/python -B /var/folders/d0/2xc6xwxd3bd53qggrfvmpgvw0000gn/T/opencode/check_staging_entrypoint_gates.py`:
  **30 kiểm tra/script, 60/60 PASS**.
- `git diff --check`: PASS.
- Thử thêm ca độc lập bằng `.venv/bin/python -B -c`, tải test harness với
  `runpy.run_path(..., run_name="independent")`, dùng AST-isolated namespace
  và monkeypatch trong bộ nhớ. Không tạo file test độc lập. Chi tiết input,
  điểm tiêm lỗi và kết quả bên dưới; không dựa riêng vào developer assertions.

## Claim P1 — Profile và launcher đúng phạm vi

Test thực hiện: chạy profile/launcher tests, 60 gate checks staging, review
diff hai script và thử độc lập actual argv của worker khi ExecStart hợp lệ.

Kết quả: **PASS**.

Evidence:
- Không flag chọn staging/user deploy; `--production` chọn
  `search-tools-pg.service` và `search-tools-import-worker.service`, user
  `searchtools-pg`, baseline `6155f25`, đúng DB/backup root production và PG14.
- Launcher `-I -B` hợp lệ được nhận. Wrong Python/role/path và unknown flag
  bị từ chối. PG client16 bị chặn tại `PG_DUMP_CLIENT_MAJOR`, trace dịch vụ
  rỗng và cả hai service cũ vẫn running.
- Flow mock chặn mọi lệnh dịch vụ ngoài đúng hai unit; production không
  checkout/fetch release cũ. Staging gate checks vẫn đạt.

Negative case đã thử độc lập: giữ nguyên cấu hình launcher worker đúng,
  nhưng actual process argv lần lượt đảo `-B -I`, thêm đối số `search:app`,
  hoặc chạy `web.py`. Cả ba input trên cả hai script, **6/6 bị từ chối** tại
  `PRODUCTION_LAUNCHER_CMDLINE`.

Residual risk: S1 web fallback vẫn tồn tại và được PO chấp nhận trong hợp
đồng; không coi kết quả này là đã sửa S1. Chưa quan sát process/systemd thật.

## Claim P2 — Prepare bất biến và giữ nguyên venv

Test thực hiện: Git fixture thực dùng bundle production; filesystem fixture
cho owner/mode/venv/checkpoint; thử độc lập bundle sai, checkpoint đã có,
venv không phải symlink và release cũ đổi commit.

Kết quả: **PASS**.

Evidence:
- Git fixture sau Prepare: release cũ vẫn baseline, release mới HEAD đúng
  `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, status sạch.
- `.venv` mới resolve về shared venv cũ; mtime interpreter dùng chung không
  đổi. Root release giữ mode/owner; hai drop-in chỉ ở checkpoint.
- Prepare không có stop/reload hoặc ghi systemd; fixture systemd chưa tồn
  tại sau real-Git Prepare. Lần Prepare thứ hai không ghi đè artifacts.

Negative case đã thử độc lập:
- Giữ Git fixture thật nhưng đổi kết quả `git bundle list-heads` thành SHA
  40 số 0: `BUNDLE_TARGET_HEAD`; candidate/checkpoint chưa được tạo.
- Candidate chưa tồn tại nhưng checkpoint đã tồn tại: main dừng tại
  `PRODUCTION_CHECKPOINT_UNUSED`, không gọi external subprocess hay tạo candidate.
- Mock `.venv` cũ không phải symlink: `SHARED_VENV_SOURCE`; không tạo venv
  mới hoặc checkpoint.
- Khi `finish_production_prepare` kiểm lại Git cũ, trả commit khác:
  `PRODUCTION_OLD_RELEASE_UNCHANGED`; không có `prepared.json` thành công.

Residual risk: filesystem fixture chạy với uid/gid local, chưa chứng minh
quyền root/searchtools-pg, ACL hoặc dung lượng thật trên server. Prepare lỗi
có thể để lại candidate/launcher/checkpoint chưa hoàn tất; không coi chúng
là một Prepare thành công hoặc cho phép tự chạy lại.

## Claim P3 — Copy launcher chỉ đổi path, không lộ secret qua diff

Test thực hiện: kiểm bytes trước/sau, hash manifest, mode/owner, nguồn không
đổi; thử symlink/non-Python/thiếu role/thiếu path; adversarial context độc lập.

Kết quả: **PASS**.

Evidence:
- File đích bằng đúng `old_bytes.replace(old_release, new_release)`;
  source bytes giữ nguyên. Diff chỉ in path và `<unchanged>`.
- Symlink, file non-Python, thiếu worker hoặc không có old release reference
  bị chặn trước khi destination được tạo.
- Hai staged drop-in đúng tên `zzzzzzzzzz-rme-2ec9b6c.conf`; nội dung chỉ có
  section Service, WorkingDirectory, ExecStart reset và ExecStart mới.

Negative case đã thử độc lập: trả bytes của launcher web có một dòng chỉ
secret giả, dòng tiếp theo chứa **hai** old release references xen giữa
secret giả. Copy vẫn đúng bytes replacement; output không chứa sentinel,
`secret=` hoặc `second=`. Các phần context đó không lọt qua diff.

Residual risk: chỉ kiểm filesystem/launcher giả local, không đọc nội dung
launcher production hoặc xác nhận metadata thực của `launchers-phase6d9`.

## Claim P4 — Thứ tự cutover và chuyển release có kiểm soát

Test thực hiện: mock full trace; fault dump/list/drop-in/reload/WD; bộ test
fingerprint; thêm queued job và wrong ExecStart độc lập.

Kết quả: **PASS**.

Evidence:
- Trace thành công: stop worker → stop web → dump → list → SQL → install
  web drop-in → install worker drop-in → reload → kiểm WD web/worker →
  start cả hai. Review argv xác nhận `pg_dump -Fc`, `pg_restore --list`,
  `psql -X -v ON_ERROR_STOP=1 -f -`, SQL lấy từ approved Git blob.
- Dump/list lỗi không tới SQL/drop-in, start lại bản cũ. Drop-in đã có,
  không đứng cuối, sai staged content, reload lỗi và WD sai đều không cho
  đạt TECHNICAL PASS; post-COMMIT đi vào HOLD.
- Fingerprint 10/10: bỏ qua runtime PID/time/status nhưng phát hiện thay
  Environment, EnvironmentFiles, unit/drop-in sources hoặc metadata.
  Planned-switch chỉ bỏ qua thay đổi ExecStart và đúng new drop-in dự kiến.

Negative case đã thử độc lập:
- Trong `activate_production_release`, giữ WD đúng nhưng trả ExecStart của
  worker trỏ `web.py`: `PRODUCTION_NEW_EXECSTART`, HOLD, cả hai writer dừng,
  **không có start nào** trong trace.
- Dùng hàm `pending` thật với query trả `1|0` cho queue:
  `QUEUE_EMPTY_REGULATORY_IMPORT_JOBS`; trace rỗng, không stop/SQL.

Residual risk: systemd precedence/reload, PG archive và migration chưa chạy
thật; `pg_restore --list` không phải bằng chứng restore thành công. Cần cửa
sổ ngừng ghi/upload như runbook, không suy rằng mock chứng minh không có
concurrent writer bên ngoài.

## Claim P5 — Phục hồi phân biệt COMMIT và trạng thái chưa rõ

Test thực hiện: fault injection trước SQL, rollback xác minh được, mất
response sau COMMIT, SQL còn active/unknown, copy thứ hai/start lỗi; thêm
lỗi milestone sau COMMIT và stop thật sự không đổi running state trong mock.

Kết quả: **PASS**.

Evidence:
- Dump/list/stop lỗi trước SQL và SQL rollback xác minh được: khởi động lại
  đúng hai service cũ, không lắp drop-in mới.
- Mất response sau COMMIT, SQL active/unknown, lỗi copy thứ hai, reload,
  partial start: HOLD; không restart code cũ hoặc chạy lại SQL.
- Recovery chỉ phát lệnh start/stop đúng hai unit, không down/restore DB.

Negative case đã thử độc lập:
- Cho SQL thành công rồi `milestone("migration_committed")` ném OSError
  chứa secret giả: `migration_committed=True`, có SQL nhưng không có
  install/start, hai writer dừng, có HOLD, không lộ secret.
- Gọi recovery khi COMMIT đã có; worker stop ném lỗi **trước khi đổi trạng
  thái mock**. Worker vẫn running, web được dừng; output có
  `HOLD_STOP_UNCONFIRMED WORKER`, vẫn thử dừng web và không báo old services
  restarted. Phân biệt rõ mong muốn giữ dừng với việc đã xác minh dừng.

Residual risk: chưa kiểm lỗi kết nối DB/systemd thật hoặc mất điện/SIGKILL;
handler không đảm bảo thực thi khi process bị cưỡng bức kết thúc. Trạng thái
HOLD_STOP_UNCONFIRMED cần PO xử lý, không thể diễn giải thành hai writer đã dừng.

## Claim P6 — Postflight và bảo mật

Test thực hiện: SHA/DSN/HTTP/hash/secret exception faults; chạy lại ca fail
trước; thử wrapper process thực bằng mocked `/proc`; review argv/output và runbook.

Kết quả: **PASS**. Ca P6 fail trước không còn tái hiện trên snapshot hiện tại.

Evidence:
- SHA sai, DSN khác, HTTP500, JS sai và exception chứa secret giả đều không
  đạt TECHNICAL PASS; post-start faults đi vào HOLD, không in sentinel.
- Gate so sánh JS HTTP với SHA256 của target Git blob đã lấy ở preflight,
  không lấy file mutable sau start làm oracle.
- Review lệnh PG: credentials truyền qua environment, không nằm trong argv.
  Raw Environment chỉ vào digest trong bộ nhớ; fingerprint test cấm đọc
  nội dung env/unit files. Các exception được báo bằng gate, không raw context.
- Runbook 48 dòng, một trang nội dung ngắn, **5 bước/lệnh vận hành** (cộng
  hai biến shell), alias `python`, PG14 và DB/backup đúng; ghi rõ CHƯA CHẠY
  production, cần verifier/PO duyệt và UAT sau cutover.

Negative case đã thử độc lập:
- Chạy lại input của lần fail trước: tại bước `read-only local HTTP smoke`,
  mock `Path.read_bytes` cho JS và HTTP body cùng thành
  `b"different bytes, NOT target Git blob"`.
  Observed: gate `NEW_JS_ASSET_HASH`, `HOLD:` có mặt,
  `TECHNICAL CUTOVER PASS` vắng mặt, cả hai running flags là false;
  hai event cuối là stop worker → stop web.
  Expected: từ chối asset ngoài target, giữ dừng và báo PO — đạt.
- Dùng hàm `unit` thật thay vì stub của flow; ActiveState/User hợp lệ nhưng
  `/proc/123/cwd` resolve về old release trong post-start new LIVE. Web và
  worker bị từ chối lần lượt tại `WEB_CWD_MATCH`, `WORKER_CWD_MATCH`.

Residual risk: login/JS/DSN/DB trên server và UAT nghiệp vụ NOT RUN. Không đo
thời gian dừng thực; bố cục trang in phụ thuộc renderer. Worker có thể nhận
job ngay sau start, trước khi postflight kết thúc, như runbook đã cảnh báo.

## Task status

- attempt: 1
- max_auto_repairs: 2
- same_claim_repeat_fail: false
- next_action: HUMAN_REVIEW_WAIT_PO_APPROVAL

Agent chính cập nhật next_action sau khi nhận kết quả độc lập; giữ attempt=1
và same_claim_repeat_fail=false. Kết quả lượt này: **PASS toàn bộ claim
local; chuyển human review theo quy trình**, không tự phê duyệt production.
