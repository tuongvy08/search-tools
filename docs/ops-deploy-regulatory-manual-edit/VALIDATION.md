# Evidence local, staging và adapter production — regulatory-manual-edit

**Trạng thái hiện tại:** PO xác nhận UAT staging đạt; adapter production đã
đạt verifier P1–P6 local/mock, chờ human review/phê duyệt, production NOT RUN.
Agent chỉ sửa/test local và ghi nhận evidence PO cung cấp, **không SSH hoặc
chạy lệnh server**. Các mục theo thời gian dưới đây giữ kết quả từng lượt.
Hợp đồng HIGH trước khi soạn runbook:
`specs/deploy-regulatory-manual-edit-staging/VERIFICATION.md`.

## PostgreSQL 14 local (độc lập với DB của người dùng)

- Trước DB probe đã kiểm tra Docker endpoint là unix socket local; container
  tạm **riêng** `postgres:14.24-alpine` chỉ bind `127.0.0.1` trên cổng riêng.
  `SHOW server_version` trả **14.24**, cùng nhánh major/minor với production
  PO đã xác nhận. Không kết nối database local gốc hay `search_tools_uat`.
- Runner tạm lưu **ngoài repository** trong thư mục tạm của agent, set môi
  trường test tường minh + bootstrap chặn dotenv; DB fixture dùng prefix
  `p6a_release_gate_pgtest_` và dọn sau test. Adapter tạm trỏ psql/pg_dump/
  pg_restore *thực* vào container 14 thay vì helper mặc định khóa container
  PostgreSQL 16, không sửa app/test/SQL của repository.
- Kết quả: **24/24 test phase PASS**, **81/81 test hồi quy liên quan PASS**,
  **2 bộ DOM exit 0**, **0 skipped**. Nhóm migration rehearsal bao gồm
  migration 033 bằng psql ON_ERROR_STOP, backfill/rerun, transaction failure,
  rollback guard và pg_dump/restore vào DB tạm khác. Warning nền LibreSSL
  không làm test fail/skip.
- Container tạm đã `docker stop`, Docker `--rm` đã xóa nó cùng dữ liệu test.
  DB UAT local của PO được giữ nguyên. Đây là **bằng chứng local PG14**, không
  chứng minh schema/data production tương thích tuyệt đối hoặc backup server.

## Đối chiếu baseline và tài liệu

- Mac Git: `a8b4cf4` và `6155f25` cùng tree (không khác file); target full
  SHA `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64` thuộc main, không đổi
  `requirements.txt` so với baseline. Không dùng kết luận tree để suy commit
  tiến trình staging khi chưa kiểm tra lại PID/cwd.
- Đã soạn [STAGING_RUNBOOK.md](STAGING_RUNBOOK.md) và ba script mới **ngoài
  repo** ở `_ops/search-tools/`: prepare, cutover, inspect interruption.
  `preflight_readonly.py` cũ của PO được giữ nguyên. Python `ast.parse`
  cả ba script mới PASS; đây chỉ là kiểm tra cú pháp, **không** chạy script
  trên server hoặc diễn tập cutover. `git diff --check` PASS.
- Review tĩnh hai lượt chỉ ra SQL candidate có thể đổi, schema 033 partial,
  mất SSH, entrypoint/cgroup, listener HTTP sai tiến trình và nguy cơ worker
  xử lý job trước khi hậu kiểm DB. Bản draft đã chuyển SQL sang blob Git
  mục tiêu giữ trong bộ nhớ (`psql -f -`), kiểm tra từng dấu vết 033, ghi
  milestone an toàn để chẩn đoán sau ngắt kết nối, đối chiếu unit/argv và
  listener thuộc PID trước stop. Cấu hình nguồn EnvironmentFile được so
  **metadata** trước/sau, không đọc nội dung `.env` hay in Environment.
  Đây là kiểm tra tĩnh/cú pháp, không phải verifier độc lập hoặc chạy server.

## Gate/rủi ro tại checkpoint trước khi PO chạy staging

- Chưa xác minh staging checkout sạch, chính sách backup thực tế,
  hiệu lực của unit/drop-in/entrypoint,
  listener HTTP, trạng thái job và disk **ngay trước cutover**. Preflight
  28/09 là snapshot do PO cung cấp, cần chạy lại.
- **Rủi ro chưa triệt tiêu, PO đã chấp nhận CHỈ STAGING:** worker nhận job ngay
  khi systemd khởi động web/worker bản mới. So metadata nguồn environment
  không thể chứng minh chống sửa nội dung rồi hoàn nguyên timestamp hoặc
  thay đổi bí mật đúng lúc startup. Điều kiện phê duyệt: queue=0 và không ai
  upload trong cửa sổ triển khai. Giữ các gate hiện có; production xét riêng,
  không kế thừa phê duyệt. Không âm thầm sửa unit/DB để đổi cơ chế startup.
- `pg_restore --list` của archive sau `pg_dump -Fc` chỉ xác nhận TOC đọc
  được, không chứng minh khôi phục đầy đủ. Restore rehearsal trên staging DB
  mới cần phê duyệt riêng, tuyệt đối không restore đè DB đang chạy.
- Sau migration COMMIT, worker cũ không được chạy lại dù code cũ còn được
  giữ: 033 backfill bảo vệ rule inactive. Không có rollback DB tự động;
  fail/unknown COMMIT thì dừng writer và điều tra bằng script chỉ đọc.
- Chưa có UAT staging hoặc production; chưa có kết luận deploy PASS/verifier
  độc lập cho triển khai server. Không dùng kết quả local thay UAT staging.

## Điều chỉnh theo PO trước khi chạy — 2026-09-29

- Staging không có credential HTTPS cho repo private: thay fetch GitHub
  bằng bundle incremental do Mac tạo từ `a8b4cf4..main`, chỉ khi main đúng
  target `2ec9b6c`; `git bundle verify` và list-heads phải đạt. Helper Mac
  có thể verify lại artifact đã tồn tại, không ghi đè; SCP theo alias staging
  do PO tự chạy, **chưa thực hiện** trong lượt này.
- Bundle đã tạo/verify trên Mac ngoài repo: ref duy nhất `refs/heads/main`
  trỏ đúng full SHA target. Có ba prerequisite commits thuộc lịch sử nền;
  prepare verify lại trên staging trước tạo candidate. Fetch chỉ từ bundle
  local; không HTTPS/token, không fallback bỏ gate khi thiếu prerequisite.
- Cleanup prepare chỉ đúng candidate, prepare checkpoint và remote bundle
  của task. Cần PO xác nhận không có prepare/cutover đang chạy; script chặn
  khi đã có cutover milestone/dump hoặc baseline/service/path bất thường.
  Không chạy cleanup, SSH hoặc SCP trong lượt soạn này.
- Đã soạn file `uat-stg-regulatory-upsert-2ec9b6c.xlsx` ngoài repo trong _ops:
  một sheet, ba cột, ba dòng mã **đều bắt đầu UAT-STG-**, dữ liệu giả,
  không formula/error/macro/external link. Đọc lại bằng openpyxl/ZIP đạt.
  Parser workbook thực của application cũng nhận đúng 3 dòng Code UAT-STG-;
  kiểm tra này chỉ đọc file local, chặn dotenv, không kết nối DB/server.
- UAT staging chỉ thêm/sửa/ngừng/khôi phục đúng ba mã giả đã chốt và upsert
  file trên, **không replace_scoped**, không sửa dữ liệu nghiệp vụ thật.
  Kết thúc phải ngừng áp dụng toàn bộ rule giả của lần UAT, giữ ID/lịch sử.
- `VERIFICATION.md` của feature và hợp đồng ops hiện có không được sửa;
  quyết định PO được ghi tại runbook và checkpoint, không làm dễ oracle.
- Kiểm tra local Git thực: tạo repo tạm **chỉ có lịch sử baseline a8b4cf4**,
  xác nhận chưa có object target; verify bundle/prerequisites, clone candidate,
  fetch bundle và checkout target `2ec9b6c` sạch đều PASS. Không network,
  không đổi ref/working tree repository chính, không gọi script server.
- `sh -n` helper Mac và `ast.parse` năm file Python liên quan đều PASS;
  review tĩnh độc lập bundle/cleanup/UAT không phát hiện blocker trong phạm
  vi thay đổi này. Không coi syntax/static review là deploy/server verifier PASS.
- `git diff --check` đạt; không thay application code/test/SQL. Chưa chạy
  scp/SSH, cleanup, prepare hoặc cutover trên staging; chưa commit/push.

## Sửa gate worker theo chẩn đoán PO — 2026-09-29

- PO đã chạy Prepare và báo dừng tại `read-only gates`: web đã đạt, worker
  dùng Python trong venv versioned và entrypoint tuyệt đối. Git HEAD đúng
  baseline, working tree sạch, chưa candidate/checkpoint; bundle đã có trên
  staging. Đây là evidence PO cung cấp; agent không chẩn đoán trên server.
- Cả prepare và cutover dùng cùng logic so realpath Python ExecStart/argv/
  cmdline với `realpath(/srv/search-tools/.venv/bin/python)`, không pin tên
  thư mục venv. Nhận script relative hoặc absolute đúng live repository;
  vẫn xác định đúng script Python thực thi, không chỉ tìm token đâu đó.
  Gate web giữ nguyên. Cutover áp dụng lại cùng helper sau startup.
- Mọi assertion lỗi dùng tên gate cố định; lỗi I/O bất ngờ chỉ báo tên bước
  an toàn. Không in cấu hình, argv, DSN hay nội dung exception. Ví dụ:
  `WORKER_EXECSTART_PYTHON_REALPATH`, `WORKER_CMDLINE_ENTRYPOINT`.
- `ast.parse` hai script PASS; **30 kiểm tra gate local/script (60 tổng)**
  PASS với symlink venv/Python phiên bản, hai entrypoint, sai Python/script,
  token script giả sau entrypoint khác, `-c`/`-m`, web cũ và wrapper giả lập
  pre/post-start. Test chỉ nạp class/function qua AST và dùng fixture local;
  không chạy main script, systemctl, SSH hoặc DB. Review tĩnh độc lập scope
  này đạt; không tuyên bố đã kiểm chứng runtime staging.
- Runbook ghi lần retry hiện tại bỏ bước **4–5**, đi thẳng **bước 6 Prepare**;
  không SCP lại hoặc cleanup vì bundle có và candidate/checkpoint chưa có.
  Nếu gate tiếp theo fail thì gửi tên gate, không bỏ kiểm tra để đi tiếp.
- Không thay feature code/tests/SQL hoặc hợp đồng kiểm chứng; không chạy
  lệnh nào trên server trong lượt sửa, không commit/push.

## Sự cố fingerprint khi cutover và phục hồi — PO báo 2026-09-29

### Observed / Expected

- PO báo: backup **37.3 MB**, `pg_restore --list` PASS, migration 033
  **COMMIT**, live checkout `2ec9b6c`. Script dừng ở
  `ENVIRONMENT_SOURCE_UNCHANGED` trước start services.
- Fingerprint cũ chứa nguyên output ExecStart, gồm pid/start_time/stop_time/
  code/status. Khi service dừng, các trường này đổi dù cấu hình path/argv
  không đổi. Gate phải phân biệt biến động runtime với thay đổi cấu hình.
- Đã tái hiện **local trước sửa**: test
  `FingerprintTests.test_running_then_stopped_same_configuration` FAIL khi
  chỉ thay runtime fields của worker từ running sang stopped. Không kết
  nối staging/database hoặc gọi systemctl trong test tái hiện.

### Phục hồi do PO duyệt và thực hiện (không phải agent)

1. Inspect chỉ đọc xác nhận checkpoint `code_switched`,
   `migration_committed=True`, live target, backup READABLE và footprint 033 `t`.
2. PO cho start cả `search-tools-staging.service` và
   `search-tools-import-worker.service` **bản mới**, không rollback code/DB.
3. Postflight theo PO: web/worker active, commit `2ec9b6c`, cùng DB
   `search_tools_staging`, 030–033 `t`, queued/running 0, login HTTP 200.

Đây **không phải** script đã chạy trọn tới `technical_smoke_passed` tự động.
Milestone có thể vẫn ở `code_switched`. Không rerun cutover, không xóa dump/
milestone/candidate, không down 033 hoặc tạo backup đè. Chưa có kết quả UAT
staging đạt; chưa báo hash asset/counts sau startup thủ công trong evidence PO.

### Sửa trên Mac và kiểm tra local

- `_ops/search-tools/cutover_regulatory_staging.py`: fingerprint chỉ gồm
  **path/argv của ExecStart**, Environment, EnvironmentFiles, FragmentPath,
  DropInPaths và metadata file (path/device/inode/size/mtime; không atime).
  Không giữ nguyên raw output systemctl. Config được chuẩn hóa rồi hash,
  không in hoặc trả raw Environment trong snapshot fingerprint.
- Không bỏ gate `ENVIRONMENT_SOURCE_UNCHANGED`: thay path/argv/environment,
  nguồn unit/drop-in/file hoặc metadata vẫn làm fingerprint khác. Parse
  ExecStart thiếu/sai phải fail bằng tên gate an toàn, không lộ giá trị.
- Test lưu ngoài repo cạnh script: `_ops/search-tools/test_cutover_fingerprint.py`.
  Chỉ nạp class/function qua AST, mock systemctl và metadata, chặn mọi đọc
  nội dung file; không chạy main script hoặc truy cập server/DB/`.env`.
  Sau sửa **9/9 test PASS**: running→worker stopped→cả hai stopped; từng trường
  runtime thay riêng; thay từng
  cấu hình thật; metadata thật đổi; đổi thứ tự property/atime không ảnh hưởng;
  path/argv quoted/multiple records; malformed/missing data; không in config.
- Chạy lại mô phỏng entrypoint cũ: **30/30 mỗi script prepare/cutover (60
  tổng) PASS**, gồm realpath và worker script absolute/relative. Cú pháp
  hai script đạt. Bản fingerprint mới chưa thực thi trên staging.

### Rà toàn bộ các phép so sánh còn lại trong cutover

- **Service state/PID/cwd/entrypoint:** trước/sau start kiểm active/PID mới;
  sau stop kiểm inactive/PID 0. Không so PID mới bằng PID cũ. Cwd/entrypoint
  là danh tính cấu hình, vẫn phải đúng. Giữ nguyên các gate này.
- **Cgroup/process worker:** sau stop phải rỗng/biến mất, không so danh sách
  process trước bằng sau; đây là gate chống writer còn sống, không được nới.
- **Listener:** inode chỉ dùng xác minh socket thuộc PID hiện tại. Qua
  restart so port, không so inode cũ/mới. Không thấy lỗi tương tự ExecStart.
- **Git/schema:** yêu cầu BASE→TARGET, schema 033 all-absent→complete;
  không bắt SHA/schema mới bằng baseline cũ. Giữ kiểm tra backfill/audit.
- **DB/queue/counts:** DSN và tên DB phải giữ, queue phải 0 ở từng gate;
  counts products/stock/rules/inactive/statuses bất biến trong cửa sổ không
  ghi nghiệp vụ. Không so backend PID, transaction ID, row timestamps hoặc
  audit runtime trước/sau. Không bỏ kiểm dữ liệu khi có sai khác.
- **Metadata file:** device/inode/size/mtime là của file cấu hình, không là
  socket; không so atime nên systemd đọc file không tự tạo drift. Các field
  runtime ExecStart là chỗ duy nhất tìm thấy thuộc lỗi running/stopped này.
- **Backup/free space/client version/HTTP/assets/milestone:** mỗi gate so
  với ngưỡng hoặc giá trị đúng của giai đoạn; không yêu cầu runtime của
  hai giai đoạn giống nhau. HTTP cần 200, asset mới so target mới.
- **Rủi ro khác đã ghi nhận, không tự sửa ngoài yêu cầu:** kiểm readiness
  ngay sau start có thể quá sớm; counts chụp trước stop có thể bị thay đổi
  bởi người sửa tay/xác nhận preview dù không upload; các lần đọc /proc/queue
  là snapshot; nhánh HOLD không chứng minh stop thành công nếu lệnh stop lỗi.
  Đây không phải cùng lỗi fingerprint. Cần kiểm trạng thái thực khi có lỗi,
  giữ cửa sổ không ghi nghiệp vụ; không nới gate hoặc suy HOLD là evidence.

**Bước tiếp theo:** chờ PO báo UAT staging đạt với fixture UAT-STG-/upsert
và ngừng áp dụng mã giả khi xong. Chưa soạn runbook hoặc chạy production.

### Verifier độc lập phần correction — kết quả và điểm cần PO chốt

- Báo cáo: `specs/deploy-regulatory-manual-edit-staging/VERIFICATION_RESULT.md`.
  Verifier chạy lại **9/9 tests fingerprint + 60 kiểm tra entrypoint local**
  và **57 kiểm tra hỗ trợ độc lập đạt**, nhưng có thêm một phản ví dụ S1
  **FAIL — IMPLEMENTATION_FAIL**. Không gộp thành kết luận tất cả đều PASS.
- Correction fingerprint và rà so sánh running/stopped đạt trong phạm vi
  offline: runtime không ảnh hưởng; thay cấu hình/metadata vẫn bị phát hiện;
  counterfactual dùng raw settings như trước làm test FAIL. Không thấy phép
  so runtime cũ/mới thứ hai cùng loại ngoài fingerprint đã sửa.
- **Phát hiện riêng S1:** mock ExecStart đúng gunicorn nhưng proc cmdline là
  Python chạy `not-web.py` với `search:app` làm đối số phụ; `unit(WEB)` vẫn
  trả PID thay vì từ chối. Đây là guard web hiện có, không phải bản sửa
  fingerprint, và không chứng minh staging thực tế đang chạy sai.
- Không tự sửa guard web: yêu cầu trước đó giữ web như hiện tại, lượt này
  giới hạn sửa fingerprint và audit các so sánh. Ghi nhận để PO cho phép
  sửa riêng trước khi dùng lại gate cho triển khai tiếp theo. **Không dùng
  test fingerprint PASS như bằng chứng toàn bộ gate deploy PASS.**
- `attempt=0/max_auto_repairs=2`, `same_claim_repeat_fail=none`,
  `next_action=ESCALATE_USER` cho lượt verifier ops này; chưa auto repair S1.
  UAT staging vẫn chờ PO; không lệnh server, không runbook production,
  không commit/push, không thay hợp đồng.

## PO xác nhận UAT staging đạt — 2026-09-29

PO đã báo **UAT staging đạt** sau phục hồi, trên code `2ec9b6c` và schema
033. Đây là nghiệm thu do PO thực hiện; agent không chạy lệnh server.
Đã soạn draft production một trang để review, không thực thi production.
Preflight có thể dùng lại với tham số; prepare/cutover hiện hard-code
staging và cập nhật checkout tại chỗ, chưa hỗ trợ đổi hai unit sang release
production bất biến chỉ bằng tham số. Các lệnh ghi trong draft chưa kích
hoạt. S1 guard web vẫn là phát hiện chưa được xử lý/chấp thuận cho production;
UAT staging đạt không tự giải quyết phát hiện đó hoặc cấp quyền ghi production.

## Adapter production — local hoàn tất, chờ PO duyệt — 2026-09-29

PO cung cấp mô hình immutable release/launcher/drop-in và chấp nhận S1 là
rủi ro đã biết, không sửa. Hợp đồng mới tạo **trước implement**, không thay
đổi: `specs/deploy-regulatory-manual-edit-production/VERIFICATION.md`.
Chỉ sửa hai script ops hiện có bên ngoài repo với flag `--production`; không
flag vẫn staging. Không sửa app, migration033 hoặc feature tests.

### Artifacts và lệnh đã chạy local

Các file ở `/Volumes/DATA/Development/_ops/search-tools/`, không trong Git:

- `prepare_regulatory_staging.py`: SHA256
  `dd668fb18377ade432503f58b9d3e451d0f7849b75cb7fd430f5f255b499840d`.
- `cutover_regulatory_staging.py`: SHA256
  `240424034cd4f5b3768aaf757c0b3a917a2e55958a73789ed4c80cf7ef1991e8`.
- `search-tools-regulatory-6155f25-to-2ec9b6c.bundle`: SHA256
  `3c9f83a474b3c891a81d95a90eccf573be0faf52f024803073154c339e91cddb`.
  Bundle được tạo local bằng `git bundle create ... 6155f257dc685ff367cc4431d752b2adcb9fbe9a..main`;
  ref duy nhất `refs/heads/main` đúng full target, prerequisite duy nhất
  BASE6155f25. Không dùng quyền tạo bundle để suy quyền SCP.

Chạy từ repository bằng Python project, không chạy main ops với boundary thật:

```bash
.venv/bin/python -B /Volumes/DATA/Development/_ops/search-tools/test_production_adapter.py
.venv/bin/python -B /Volumes/DATA/Development/_ops/search-tools/test_cutover_fingerprint.py
.venv/bin/python -B /var/folders/d0/2xc6xwxd3bd53qggrfvmpgvw0000gn/T/opencode/check_staging_entrypoint_gates.py
git diff --check
```

Kết quả: **30/30 adapter tests, 10/10 fingerprint tests, 60/60 entrypoint
checks PASS**, không skip. Git fixture thực chỉ lấy baseline+ancestors từ
repo local, verify/fetch bundle rồi prepare release target sạch; fixture
filesystem được dọn. Các boundary systemd/PG/HTTP dùng mock, không truy cập
server, network, DB hoặc `.env`. File entrypoint harness trong thư mục tạm
có thể mất sau khi dọn cache/reboot; không coi nó là test đã version-control.

### Vòng verifier và repair

- Lượt đầu: P1–P5 PASS; P6 **IMPLEMENTATION_FAIL**. Khi JS trên release và
  HTTP body cùng đổi sau start, script vẫn báo TECHNICAL PASS. Đã thêm test
  tái hiện; test **FAIL trước sửa**, PASS sau sửa. Oracle HTTP dùng hash từ
  target Git blob đã xác minh, không lấy lại hash từ file mutable sau start.
- Sau repair lần1: verifier context sạch kiểm lại **P1–P6 PASS**, gồm ca
  fail trước và adversarial cases mới cho từng claim. Ca JS drift nay báo
  `NEW_JS_ASSET_HASH`, HOLD, stop worker+web; không TECHNICAL PASS.
- Báo cáo: `specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT.md`.
  `attempt=1/2`, không claim fail lặp; đang chờ human review, không tự approve.

### Human review — Claim → Test → Result → Independent verifier → Residual risk

1. **P1: đúng hai dịch vụ và profile.** Test sai flag/role/Python/PG16,
   hồi quy staging → bị chặn, default staging giữ nguyên → verifier PASS.
   Rủi ro: S1 web fallback đã được PO chấp nhận, chưa sửa; runtime server chưa thử.
2. **P2: không sửa release cũ hoặc shared venv.** Git/filesystem fixture và
   thử bundle sai/checkpoint cũ/venv sai → release mới sạch đúng target,
   sai điều kiện bị chặn → verifier PASS. Rủi ro: UID/GID/ACL/đĩa Linux thật
   chưa đo; prepare lỗi có thể để lại artifacts, không tự cleanup/rerun.
3. **P3: copy launcher chỉ đổi path, không lộ secret.** So byte/mode/owner,
   symlink/non-Python và secret giả cùng dòng → replacement đúng, context
   không xuất hiện, drop-in chỉ ở checkpoint → verifier PASS. Rủi ro:
   chưa đọc launcher production; PO kiểm diff an toàn sau Prepare.
4. **P4: đúng thứ tự và chỉ chuyển sau COMMIT.** Trace cùng fault dump/list/
   reload/WD/ExecStart/queued job → lỗi chặn bước tiếp hoặc HOLD; config
   ngoài release không bị bỏ kiểm → verifier PASS. Rủi ro: mock không chứng
   minh systemd/DB thật; `pg_restore --list` không chứng minh restore đầy đủ.
5. **P5: recovery không chạy bản cũ sau COMMIT.** Inject rollback, COMMIT
   mất response, SQL active/unknown, partial copy/start/stop → chỉ restart
   cũ khi chứng minh chưa COMMIT, còn lại HOLD, stop lỗi báo chưa xác nhận
   → verifier PASS. Rủi ro: mất điện/SIGKILL không chạy được handler;
   `HOLD_STOP_UNCONFIRMED` cần PO xử lý, không có nghĩa writer đã dừng.
6. **P6: target/DB/login/JS đúng, output an toàn.** Inject SHA/DSN/HTTP/JS
   sai, JS+HTTP cùng drift và secret exception → gate an toàn + HOLD, không
   PASS giả → verifier PASS. Rủi ro: server/UAT/downtime thật chưa thử;
   worker có thể nhận job trước postflight. Phê duyệt rủi ro startup cho
   staging không tự áp dụng production; PO cần duyệt riêng trước chạy.

**Năm câu PO cần xác nhận trước approve:** (1) sáu đảm bảo trên đúng nhu cầu?
(2) test có thật sự bắt lỗi? — có ca đỏ trước sửa và fault injection;
(3) evidence quan sát được đủ chưa? — có output/trace/Git fixture/report,
nhưng chưa có server; (4) còn tình huống vận hành quan trọng nào cần thử?
(5) chấp nhận các rủi ro còn lại và cửa sổ không ghi/upload/queue0 không?

Runbook giữ tối đa một trang, năm thao tác PO tự chạy. Dừng tại checkpoint
local này; **không SSH/scp/systemctl/DB server, không commit/push**, không
tuyên bố production READY/DONE trước phê duyệt hoặc production đã triển khai.
