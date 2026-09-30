# Runbook & ghi nhận STAGING regulatory-manual-edit (`main@2ec9b6c`)

## Trạng thái hiện tại — theo kết quả PO cung cấp

- Backup **37.3 MB**, `pg_restore --list` PASS; migration 033 **COMMIT**,
  code đã chuyển `2ec9b6c`. Script dừng ở `ENVIRONMENT_SOURCE_UNCHANGED`
  trước start: fingerprint cũ so cả runtime fields của ExecStart nên báo
  lệch khi service dừng, **không phải cấu hình bị đổi**.
- PO đã duyệt phục hồi: inspect xác nhận `code_switched`,
  `migration_committed=True`, live target, backup READABLE, footprint 033 có;
  sau đó start cả hai service **code mới**, không rollback DB/code.
- Postflight PO báo: web/worker **active**, cùng commit `2ec9b6c`, cùng DB
  `search_tools_staging`, 030–033 `t`, job 0, login HTTP 200.
- **Không chạy lại Prepare/Cutover/migration/cleanup.** Giữ backup và
  milestone; milestone có thể vẫn ghi `code_switched` vì phục hồi thủ công,
  không xóa/sửa artifact để ép chạy lại. Script sửa fingerprint chỉ được
  kiểm tra local, chưa chạy lại trên staging. Xem sự cố và audit so sánh
  trong [VALIDATION.md](VALIDATION.md).
- Kiểm chứng local bản sửa fingerprint đạt, nhưng verifier có phát hiện
  riêng **S1/IMPLEMENTATION_FAIL** ở guard web hiện có (token `search:app`
  làm đối số cho script khác vẫn có thể được chấp nhận). Chưa sửa ngoài phạm
  vi; chờ PO cho phép xử lý riêng. Không kết luận staging đang chạy sai từ
  fixture này, cũng không coi toàn bộ guard deploy đã PASS. Xem report
  `specs/deploy-regulatory-manual-edit-staging/VERIFICATION_RESULT.md`.

## Anh cần chạy gì

**Lúc này chỉ chờ PO thực hiện/báo UAT staging ở mục 5**: chỉ mã giả
`UAT-STG-`, chỉ upsert, cuối lượt ngừng áp dụng các quy tắc giả. Không chạy
replace_scoped, không mở rộng sang production. Agent không chạy lệnh server.

### Trình tự trước cutover — giữ làm hồ sơ, KHÔNG chạy lại

Các lệnh 1–9 dưới đây mô tả lần triển khai từ baseline cũ, không phải lệnh
phục hồi hiện tại. Rủi ro worker startup đã được PO chấp nhận chỉ staging,
với queue=0 và không ai upload; production chưa xét. Lần retry Prepare trước
đây đã bỏ bước 4–5 vì bundle có sẵn, sau đó mới đến cutover/sự cố nêu trên.

1. **[MAC]** Khai báo đường dẫn local, không có secret:
   ```bash
   REPO='/Volumes/DATA/Development/Search-tools'
   OPS_DIR='/Volumes/DATA/Development/_ops/search-tools'
   BUNDLE="$OPS_DIR/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle"
   ```
2. **[MAC]** Tạo/reuse bundle đúng `a8b4cf4..2ec9b6c`, tự chạy
   `git bundle verify` và kiểm ref `main` đúng SHA đã chốt; không đổi checkout:
   ```bash
   sh "$OPS_DIR/make_regulatory_staging_bundle.sh"
   ```
3. **[CHỈ ĐỌC]** Kiểm tra lại staging đang ở baseline, DB/schema/job đúng; nếu
   job khác 0 hoặc có drift thì dừng:
   ```bash
   ssh staging 'sudo -n python3 -u - search-tools-staging.service search-tools-import-worker.service /srv/backups/search-tools' < "$OPS_DIR/preflight_readonly.py"
   ```
4. **[CHỈ ĐỌC]** Xác nhận file đích bundle chưa tồn tại (exit 0, không cần
   output); nếu tồn tại thì dừng, xử lý cleanup có điều kiện ở mục 2b:
   ```bash
   ssh staging 'test ! -e /tmp/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle && test ! -L /tmp/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle'
   ```
5. **[GHI]** Copy bundle đã verify bằng SCP/alias staging, giữ quyền file
   private; không cần credential GitHub trên server:
   ```bash
   scp -p "$BUNDLE" staging:/tmp/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle
   ```
6. **[GHI]** Prepare candidate bằng Git **local + bundle**, kiểm SHA mục tiêu;
   chưa dừng web/worker hoặc chạy migration:
   ```bash
   ssh staging 'sudo -n python3 -u -' < "$OPS_DIR/prepare_regulatory_staging.py"
   ```
7. **[CHỈ ĐỌC]** Preflight lại ngay trước cửa sổ ghi, bảo đảm queue = 0 và
   không ai upload đến khi cutover/smoke hoàn tất:
   ```bash
   ssh staging 'sudo -n python3 -u - search-tools-staging.service search-tools-import-worker.service /srv/backups/search-tools' < "$OPS_DIR/preflight_readonly.py"
   ```
8. **[GHI]** Khi được PO cho phép cutover: dừng worker rồi web → backup và
   `pg_restore --list` → migration 033 → code đúng target → start cả hai → smoke:
   ```bash
   ssh staging 'sudo -n python3 -u -' < "$OPS_DIR/cutover_regulatory_staging.py"
   ```
9. **[CHỈ ĐỌC]** Postflight: cả hai service phải ở `2ec9b6c`, cùng DB,
   dấu vết 033 có và job = 0; sai thì chưa UAT/production:
   ```bash
   ssh staging 'sudo -n python3 -u - search-tools-staging.service search-tools-import-worker.service /srv/backups/search-tools' < "$OPS_DIR/preflight_readonly.py"
   ```
10. **[GHI — giao diện staging]** Thử đúng ba mã `UAT-STG-` ở mục 5,
    upload file upsert đã soạn trong `_ops`; **không replace_scoped**. Cuối
    UAT ngừng áp dụng các quy tắc giả, kiểm tra không còn mã thử active.

Cleanup khi prepare lỗi **không thuộc luồng chạy bình thường**; chỉ dùng
lệnh mục 2b sau khi PO xác nhận. Không chạy bất kỳ lệnh qua `ssh python`.

**HIGH — staging đang chạy bản mới sau phục hồi được PO duyệt; UAT còn chờ.**
Bản sửa fingerprint chỉ kiểm thử trên Mac. Không có lệnh production
trong runbook này. Tất cả lệnh server do
PO tự chạy **từ Mac** qua alias `ssh staging`; agent không SSH. Các file
script được soạn ngoài repository trong `_ops/search-tools/`; agent không
truyền/chạy script trên staging. Hợp đồng gate trước thực thi:
`specs/deploy-regulatory-manual-edit-staging/VERIFICATION.md`.

## 0. Phạm vi và bằng chứng trước cutover (lịch sử)

- Theo preflight PO cung cấp ngày 28/09: staging **máy riêng**, web
  `search-tools-staging.service`, worker `search-tools-import-worker.service`,
  user `deploy`, cwd `/srv/search-tools`, commit `a8b4cf4`; database
  `search_tools_staging`, PG **16.15**, ~1171 MB; schema 030–032 có, 033 chưa,
  job queued/running = 0, ~56.7 GB trống. Đó là **snapshot theo thời điểm**,
  không thay preflight ngay sát cutover. `a8b4cf4` và `6155f25` có cùng Git
  tree (đã đối chiếu trên Mac), không có nghĩa staging đã chạy 6155f25.
- SHA mục tiêu đầy đủ: `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`.
  Staging theo tài liệu là checkout **cập nhật tại chỗ**, khác release bất
  biến production. Không đổi unit Nginx, service khác, `.env`, DNS hoặc DB
  trong phase này. Dify/n8n và `search-tools.service` cũ trên máy production
  **ngoài phạm vi**; không lệnh nào dùng `ssh python`.
- DB production PG14.24 đã được rehearsed **riêng trên Mac** trước khi soạn
  runbook: container `postgres:14.24-alpine` riêng chỉ bind loopback, test DB
  tên tạm; **24/24 test phase + 81/81 hồi quy + 2 bộ DOM PASS, 0 skip**.
  `migration_033`, rollback guard và pg_dump/restore vào DB khác được chạy
  trong các test đó. Container tạm đã dừng với `--rm`. Đây không phải bằng
  chứng production đã migrate hoặc đã deploy; production cần task riêng.
- Trong `_ops` ngoài Git có sẵn `preflight_readonly.py` của PO, giữ nguyên.
  Các script chuẩn bị: `make_regulatory_staging_bundle.sh`,
  `prepare_regulatory_staging.py`, `cutover_regulatory_staging.py`,
  `inspect_regulatory_staging_after_interruption.py` và
  `cleanup_regulatory_staging_prepare.py`. File mẫu UAT:
  `uat-stg-regulatory-upsert-2ec9b6c.xlsx`. Trên Mac dùng biến thư mục:

```bash
OPS_DIR='/Volumes/DATA/Development/_ops/search-tools'
```

**Không in ra hoặc copy vào báo cáo** `DATABASE_URL`, mật khẩu, token, nội
dung `.env`. Python lấy kết nối web và worker từ `/proc/<PID>/environ` trong
bộ nhớ, so sánh chúng, dùng PG* môi trường của subprocess cho `pg_dump`/psql;
stderr của lệnh DB/Git bị giữ kín, argv không chứa DSN/mật khẩu. Không dùng
các ví dụ cũ truyền `pg_dump <URI>`/`psql --dbname=<URI>` trong hướng dẫn
chung vì chúng lộ thông tin qua argv. Lỗi kết nối: dừng, không bỏ gate.

## 1. [CHỈ ĐỌC] Preflight trước cutover từ baseline cũ (lịch sử)

**Máy chạy:** Mac, gửi script đã review vào SSH staging, không tạo file trên
server. **Lệnh:**

```bash
ssh staging 'sudo -n python3 -u - search-tools-staging.service search-tools-import-worker.service /srv/backups/search-tools' < "$OPS_DIR/preflight_readonly.py"
```

**Mong đợi:** đúng hai unit active, user `deploy`, cwd `/srv/search-tools`,
commit cũ `a8b4cf4` ở cả hai (không chỉ `commit=UNKNOWN`); web/worker cùng
DB `search_tools_staging`, PG 16.15; 030–032 `t`, 033 `f`; **cả ba** queue
product/regulatory/stock queued/running = 0; đường dẫn backup hiện hữu và
đủ đĩa. Ghi lại SHA và số liệu trước, không in DSN. Nếu `UNKNOWN`, PID/cwd
khác, repo bẩn, schema thiếu/033 đã có, job xuất hiện hoặc không xác minh
backup/đĩa: **DỪNG**, không tự chạy prepare/cutover. Dấu vết schema không
thay thế migration ledger hoặc kiểm tra định nghĩa schema chi tiết.

## 2. [MAC] Bundle → [GHI] SCP/Prepare candidate ngoài checkout live (lịch sử)

Repo GitHub private; **staging không có credential HTTPS**. Không fetch
GitHub từ staging. Script `make_regulatory_staging_bundle.sh` trên Mac
khóa `refs/heads/main` ở đúng target, kiểm baseline là ancestor, rồi tạo
bundle incremental bằng các lệnh dưới (đã nằm trong script, không cần chạy
hai lần):

```bash
git -C "$REPO" bundle create "$BUNDLE" a8b4cf406b5275adf154791d4019427ed6318547..main
git -C "$REPO" bundle verify "$BUNDLE"
git -C "$REPO" bundle list-heads "$BUNDLE"
```

Nếu bundle local đã tồn tại, helper chỉ verify, **không ghi đè**; sai SHA/ref
thì dừng. Chỉ chứa commit đã có trong Git, không lấy working tree ops đang
sửa hoặc `.env`. Bundle private nằm ngoài repo, quyền hạn chế; không commit.
Bundle incremental có prerequisites: prepare phải verify trong repo live
để chứng minh staging có đủ commit nền; thiếu thì dừng, không fallback
HTTPS/full clone hay bỏ assertion. Sau bước kiểm file đích vắng ở đầu
runbook, copy bằng đúng lệnh:

```bash
scp -p "$BUNDLE" staging:/tmp/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle
```

**Chỉ sau khi PO duyệt riêng** runbook/prepare, xác minh cửa sổ bảo trì,
bundle đã copy và vị trí backup. **Máy chạy:** Mac →
`ssh staging`. **Lệnh:**

```bash
ssh staging 'sudo -n python3 -u -' < "$OPS_DIR/prepare_regulatory_staging.py"
```

**Mong đợi:** script kiểm tra 2 unit/cwd/user, SHA cũ chính xác và live Git
sạch; kiểm bundle regular file không symlink/hardlink, owner root/deploy,
đặt quyền đọc riêng cho deploy (chỉ artifact của task). Chạy
`git bundle verify` và kiểm duy nhất `refs/heads/main` đúng target **trước
khi tạo candidate**. Clone baseline từ live với `--no-hardlinks`, fetch
`refs/heads/main` từ bundle local, kiểm `FETCH_HEAD` và checkout target,
`requirements.txt` không đổi;
ghi checkpoint không có secret trong thư mục backup staging. **Không dừng
web/worker**, không migrate, không đổi live code ở bước này. Nếu thiếu/hỏng
bundle/prerequisite, candidate/checkpoint đã tồn tại, live checkout bẩn,
SHA drift hoặc effective ExecStart/process argv không thể xác nhận đúng app
theo gate dưới đây, hoặc cần thay dependency/unit: dừng, không
chèn credential vào URL, không force/reset. Chỉ cleanup đúng mục 2b sau PO
xác nhận, không tự xóa candidate/checkpoint để retry. Khối prepare cần được
review trước khi chạy; việc ghi này **chưa được phép trong lượt hiện tại**.

**Gate runtime đã điều chỉnh theo chẩn đoán PO:** Python của worker trong
ExecStart, argv và cmdline được so bằng `realpath` với
`/srv/search-tools/.venv/bin/python`, không ghi cứng tên thư mục venv có
phiên bản. Entrypoint worker nhận đúng một trong `scripts/import_worker.py`
hoặc `/srv/search-tools/scripts/import_worker.py`. Web giữ nguyên gate đã
đạt. Cutover dùng cùng quy tắc khi kiểm tra trước dừng và sau khi start.
Nếu lỗi: in **tên gate**, ví dụ `WORKER_EXECSTART_PYTHON_REALPATH` hoặc
`WORKER_CMDLINE_ENTRYPOINT`; không in ExecStart/cmdline/giá trị cấu hình,
DSN hay nội dung exception. PO đã chạy qua gate này ở lần cutover vừa báo;
không chạy lại Prepare vì staging hiện ở target và migration đã COMMIT.

### 2a. [CHỈ ĐỌC] Preflight lại sau Prepare

**Máy chạy:** Mac → staging; chạy lại **đúng lệnh bước 1**. Live web/worker
vẫn phải ở commit cũ, DB và job/đĩa không drift. Candidate và checkpoint
đã được script Prepare tự kiểm tra bằng SHA, không dùng chúng làm bằng chứng
web/worker đang chạy code mới.

### 2b. [GHI — chỉ khi PO xác nhận] Cleanup prepare dừng giữa chừng

Không dùng `rm -rf` wildcard, không xóa live checkout, `.env`, database,
dump, thư mục backup cha hay checkpoint cutover. PO xác nhận **không có
prepare/cutover còn chạy**, lỗi thuộc prepare, live vẫn baseline cũ và hai
service đang active; sau đó mới chạy từ Mac:

```bash
ssh staging 'sudo -n python3 -u - CONFIRM-CLEANUP-PREPARE-2ec9b6c' < "$OPS_DIR/cleanup_regulatory_staging_prepare.py"
```

Script chỉ cho xóa đúng **ba đường dẫn trên staging**:

- Candidate `/srv/search-tools-regulatory-2ec9b6c-candidate`.
- Checkpoint prepare `/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prepared.json`.
- Bundle đã copy `/tmp/search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle`.

Thiếu artifact thì bỏ qua. Symlink/path/owner bất thường, service/cwd/commit
không đúng hoặc đã có dump/milestone cutover (kể cả milestone `.new`) thì
**từ chối toàn bộ cleanup trước khi xóa**. Giữ nguyên artifact nếu không
chứng minh được trạng thái. Không xóa bản bundle trên Mac để còn truyền lại.
Khi cleanup hoàn tất, chạy lại **preflight → kiểm bundle đích vắng → scp →
prepare**, không nhảy tới cutover. Nếu cutover từng bắt đầu, chỉ dùng kiểm
tra chỉ đọc mục 6a và quy trình rollback, không dùng cleanup này.

## 3. [GHI] Cutover STAGING — hồ sơ lần chạy, không chạy lại

**Chỉ chạy sau khi PO xem cả script, phê duyệt cửa sổ gián đoạn staging và
UAT tiếp theo.** Máy chạy: Mac → staging. **Một lệnh:**

```bash
ssh staging 'sudo -n python3 -u -' < "$OPS_DIR/cutover_regulatory_staging.py"
```

Các bước **bên trong** script này diễn ra đúng thứ tự dưới đây; tất cả đều
trong phạm vi **[GHI]** của lệnh trên, trừ các assertion được ghi [CHỈ ĐỌC]:

1. **[CHỈ ĐỌC] Cổng cuối:** checkpoint/candidate sạch và SHA target chính
   xác; file SQL/assets khớp Git blob target, không symlink; effective unit
   và process entrypoint/argv phải chạy từ venv/code đang review. Hai tiến trình
   live cùng DB staging, schema 030–032 có; **từng dấu vết** 033 (bảng,
   cột, trigger, function, index) đều chưa có; nếu một phần đã có, dừng. Ba
   queue queued/running bằng 0 và filesystem backup còn buffer cho dump/WAL.
   Chụp row counts products, stock_items, regulatory_rules (cả inactive),
   statuses. Xác minh socket listener **thuộc PID web**, bind được trên IPv4
   loopback và không có socket khác tranh cùng port; GET login **trước khi
   dừng web**. Không đoán port hay thay đổi proxy. Ghi nhận cấu hình
   ổn định: **ExecStart path/argv**, Environment, EnvironmentFiles,
   FragmentPath, DropInPaths và `stat` metadata file (không atime),
   **không đọc nội dung `.env`**. **Dừng** nếu bind/đích drift.
2. **[GHI] Dừng worker trước:** `systemctl stop` đúng
   `search-tools-import-worker.service`, xác minh MainPID=0/inactive,
   cgroup rỗng và không còn import_worker process trong live cwd. Kiểm
   tra lại ba queue.
   Nếu job mới xuất hiện: **dừng**; không migration.
3. **[GHI] Dừng web:** `systemctl stop search-tools-staging.service`, xác
   minh đã dừng; kiểm tra job = 0 một lần nữa. Nếu lệch: **dừng**, không
   backup/migration tiếp.
4. **[GHI] Backup DB đang dùng thật:** `pg_dump -Fc` ra file **mới** trong
   backup staging, quyền umask hạn chế; kiểm tra exit code + size >0.
   **[CHỈ ĐỌC] `pg_restore --list` phải đọc được archive trước SQL.**
   Nếu fail: **dừng**, không migration. `--list` chỉ kiểm tra TOC, không
   chứng minh backup restore đầy đủ; cần restore rehearsal trên DB khác
   nếu muốn gate mạnh hơn, phải được duyệt riêng.
5. **[GHI] Chạy đúng migration 033:** script lấy nguyên SQL từ Git blob
   **đã kiểm SHA tại target**, giữ bytes trong bộ nhớ rồi gửi qua stdin cho
   `psql -X -v ON_ERROR_STOP=1 -f -`; không chạy lại file mutable của
   candidate sau khi backup. SQL tự có `BEGIN/COMMIT` và advisory lock.
   Sau đó **[CHỈ ĐỌC]** xác minh hai bảng, cột/trigger đang bật và index
   valid/ready,
   inactive cũ được bảo vệ và có khóa; không tạo manual event giả, revision
   1, các row counts ngoài phạm vi giữ nguyên. Nếu `psql` báo lỗi hoặc
   không biết đã COMMIT chưa: **dừng**, đọc lại schema và hỏi trước khi retry.
6. **[GHI] Chuyển code staging tại chỗ:** fetch target từ candidate đã
   kiểm tra, checkout detached đúng SHA `2ec9b6c`; **không** `git pull` trực
   tiếp khi đang chạy, không `reset --hard`, không sửa `.env`/unit/dependency.
   Nếu checkout sai hoặc bẩn: **dừng**, giữ hai service tắt.
7. **[GHI] Khởi động cả web và worker bản mới** bằng một lệnh systemd cho
   đúng hai unit staging; chỉ sau khi metadata nguồn môi trường và unit
   của cả web/worker vẫn nguyên so với khi tiến trình cũ được xác minh.
   Fingerprint không chứa PID/start_time/stop_time/code/status của ExecStart;
   không so nguyên output systemctl giữa active và inactive.
   **[CHỈ ĐỌC]** xác minh đều active, PID/cwd/commit,
   cùng database và schema 033. Smoke HTTP trực tiếp listener web thực
   (không đoán cổng staging): GET login 200; asset
   `regulatory_manual.js` khớp SHA candidate; row counts ngoài phạm vi như
   trước. Nếu smoke lỗi **sau 033 COMMIT**, script giữ/ngắt cả hai writer,
   **không tự chạy worker cũ**.

**Các lệnh thay đổi bên trong script chỉ nhắm staging**, không chạy service
khác. Script chỉ in bước/số liệu an toàn và không tự thực hiện production.
PO đã chạy tới `code_switched` và phục hồi thủ công sau gate fingerprint
báo lệch giả; không diễn giải thành toàn bộ script tự chạy tới cuối/PASS.
**Quyết định PO:** chấp nhận residual risk worker có thể nhận job ngay khi
start **CHỈ STAGING**, vì queue = 0 và không ai upload. Điều kiện này phải
giữ từ preflight sát cutover đến khi smoke/postflight hoàn tất; nếu không
duy trì được thì dừng, không coi phê duyệt là bỏ gate. Nguồn EnvironmentFile
vẫn so metadata, không đọc `.env`; không đổi unit/quyền DB để lách gate.
Production sẽ xem xét riêng, không kế thừa chấp thuận rủi ro này.

## 4. [CHỈ ĐỌC] Postflight độc lập sau cutover kỹ thuật

**Kết quả hiện có theo PO:** cả hai service active/commit target, cùng DB
staging, 030–033 có, job 0, login HTTP 200. Không yêu cầu chạy lại trong
lượt sửa này; chưa có báo cáo UAT staging đạt.

**Máy chạy:** Mac → staging, chỉ nếu bước 3 báo PASS. Chạy lại **lệnh bước
1** để kiểm tra cả web và worker đều commit mục tiêu, cùng DB, 030–033 `t`,
job vẫn 0, disk đủ. `a8b4cf4` cũ vẫn phải có thể tham chiếu trong Git để
phục hồi tình huống trước migration. Nếu có bất kỳ sai khác nào thì chưa
nghiệm thu, không mở production.

## 5. [GHI — chỉ sau phê duyệt UAT staging] Nghiệm thu trên staging

**CẤM chạy `replace_scoped` trong UAT staging này**, kể cả trên mã giả.
Không dùng hai workbook UAT local của phase trước. Chỉ thử ba mã riêng:

- `UAT-STG-RME-2EC9B6C-EDIT`
- `UAT-STG-RME-2EC9B6C-STOP`
- `UAT-STG-RME-2EC9B6C-IMPORT`

File đã soạn trên Mac: **`$OPS_DIR/uat-stg-regulatory-upsert-2ec9b6c.xlsx`**,
một sheet, đúng Code/Tình trạng quản lý/Ghi chú quản lý, ba dòng có đúng ba
mã trên. Nhãn tình trạng trong file là **Được bán**. Dùng nguyên file,
không thêm mã khác, không đổi mode. Kịch bản theo thứ tự:

1. **[CHỈ ĐỌC — UI]** Dùng admin có menu regulatory, tìm
   `UAT-STG-RME-2EC9B6C-` với trạng thái **Tất cả**: phải chưa có mục nào;
   xác nhận danh mục đã có đúng nhãn **Được bán**. Nếu tên trạng thái đã đổi,
   mã đã tồn tại hoặc bị giữ trong khóa lịch sử khi thêm: **dừng**, không
   tái sử dụng/sửa mục cũ hoặc tạo thêm tình trạng chỉ để chạy file mẫu.
2. **[GHI — UI]** Thêm **EDIT**, loại Mã sản phẩm, tình trạng Được bán,
   ghi chú `UAT-STG: ghi chú tay V1`. Sửa thành `UAT-STG: ghi chú tay V2`,
   xem trước/xác nhận; ghi lại ID và kiểm tra audit đúng tài khoản/thời gian.
3. **[GHI — UI]** Thêm **STOP**, loại Mã sản phẩm, Được bán; ngừng áp dụng
   có lý do → khôi phục → ngừng áp dụng lần nữa. ID giữ nguyên, lịch sử còn;
   để STOP inactive trước import. Không tạo IMPORT bằng tay.
4. **[GHI — upload/preview]** Upload file nói trên, chỉ chọn **Thêm / cập
   nhật (`upsert`)**. Preview lần đầu phải là **1 thêm / 0 cập nhật / 0 xóa /
   2 giữ-bỏ qua**, **2 rule được bảo vệ, 2 chi tiết**; cả ba mã đều bắt đầu
   `UAT-STG-`. Khác số liệu, có mã thật hoặc nhầm replace_scoped: **không apply**.
5. **[GHI — confirm]** Áp dụng đúng preview trên: EDIT vẫn ghi chú tay V2,
   STOP vẫn inactive, IMPORT được thêm với ghi chú mẫu. Lịch sử sửa tay
   không bị viết lại. Có thể thử hai tab cùng sửa **EDIT**: tab cũ bị yêu
   cầu tải lại, không đè lần lưu mới. Chỉ thao tác các ID giả đã ghi nhận.
6. **[GHI — UI, bắt buộc kết thúc UAT]** Ngừng áp dụng **EDIT** và **IMPORT**
   với lý do `Kết thúc UAT staging regulatory-manual-edit`; STOP đã ngừng
   thì giữ nguyên. Không xóa cứng, không SQL DELETE, không import để dọn.
   Nếu UAT dừng giữa chừng, sau khi xác minh trạng thái chỉ ngừng các mục
   giả đã tạo thành công của lần này, không quét/tác động mã khác.
7. **[CHỈ ĐỌC — UI]** Lọc ba mã trên: **Đang áp dụng = 0**, **Ngừng áp dụng
   = 3** nếu đã chạy đủ kịch bản; các ID/audit được giữ. Ghi kết quả đạt/không
   đạt và ID giả vào biên bản. Nếu còn mục giả active, UAT chưa hoàn tất.

Kiểm tra menu/quyền, tìm/lọc, lịch sử giờ Việt Nam trong phạm vi fixture trên.
Không sửa sản phẩm/CAS hoặc các quy tắc nghiệp vụ thật; không chạy Check
License/import trên dữ liệu thật để mở rộng UAT trong lượt này.

**Mong đợi:** PO xác nhận staging UAT đạt hoặc ghi case FAIL. Không coi smoke
HTTP là thay thế UAT. Chưa soạn/hành động production trong runbook này.

## 6. Dấu hiệu dừng và ranh giới rollback

### Phục hồi sự cố fingerprint — đã được PO duyệt và thực hiện

Đây là bản ghi phục hồi **bản mới sau migration COMMIT**, không phải nhánh
rollback về baseline cũ và không phải lệnh cần chạy lại:

1. **[CHỈ ĐỌC, PO đã chạy]** Inspect theo mục 6a: checkpoint `code_switched`,
   `migration_committed=True`, live `2ec9b6c`, backup READABLE và schema 033 có.
2. **[GHI, PO duyệt riêng và đã chạy]** Start **hai service bản mới**:
   `systemctl start search-tools-staging.service search-tools-import-worker.service`.
3. **[CHỈ ĐỌC, PO đã chạy]** Postflight xác nhận trạng thái hiện tại ở đầu
   tài liệu. Không down 033, không checkout code cũ, không restore/xóa dữ liệu.

Nếu có sự cố khác, không mặc định áp dụng lại cách phục hồi này. Giữ guard
và xin phê duyệt theo trạng thái DB/code thực; không dùng sửa fingerprint
như lý do tự start service hoặc bỏ qua một thay đổi cấu hình thật.

### 6a. [CHỈ ĐỌC] Nếu SSH/script bị ngắt hoặc không rõ mốc COMMIT

**Máy chạy:** Mac → staging, đọc milestone không có secret, service/cwd/code
và metadata archive; không tự restart hoặc rollback. Lệnh để PO chạy sau
khi quay lại kết nối:

```bash
ssh staging 'sudo -n python3 -u -' < "$OPS_DIR/inspect_regulatory_staging_after_interruption.py"
```

**Mong đợi:** thấy checkpoint `read_only_gate_passed` → `worker_stopped` →
`web_stopped` → `backup_verified` → `migration_attempted` →
`migration_committed` → `code_switched` → `services_started` →
`technical_smoke_passed_uat_pending` tùy mốc. Script ghi milestone trên đĩa
trước/sau gate; kết quả này chỉ là **chỉ dấu**, không chứng minh DB đã/ chưa
COMMIT nếu mất kết nối đúng lúc SQL. Query tùy chọn qua OS user postgres
chỉ mang tính gợi ý, phải chứng minh cùng DB của web/worker đã xác minh
trước đó; nếu peer-auth không có hoặc không xác minh được đích, **giữ hai
service dừng** và báo PO, không tự lấy `f` làm quyền chạy code cũ. SIGKILL,
reboot hoặc mất kết nối bất ngờ vẫn có thể bỏ qua handler dừng, nên trước
khi phục hồi phải xác minh service thực và schema từ database **đúng đích**.

- **Trước migration COMMIT:** nếu candidate/backup/migration lỗi, giữ web và
  worker dừng, xác minh **tất cả** dấu vết 033 vẫn vắng trên đúng DB đã đối
  chiếu, backup/checkpoint còn nguyên, live checkout vẫn là baseline.
  Chỉ sau khi review xác nhận không có
  transaction dở và DB vẫn đúng baseline mới được PO **duyệt riêng** khôi
  phục hai service bản cũ. Lệnh [GHI] **có điều kiện**, chỉ sau phê duyệt
  hồi phục đó và xác minh toàn bộ gate trên:

  ```bash
  ssh staging 'sudo -n systemctl start search-tools-staging.service search-tools-import-worker.service'
  ```

  Nếu DB/checkout không chắc chắn, không chạy lệnh này. Không tự rerun toàn
  script sau SSH mất kết nối.
- **Sau migration COMMIT (kể cả chưa UAT):** 033 đã backfill các rule inactive
  thành protected. **Không tự chạy worker/code cũ hoặc down 033.** Giữ writer
  dừng nếu smoke fail; ưu tiên forward fix bản mới đã review. Backup chỉ được
  restore vào DB **mới** để đối chiếu rồi xin duyệt chuyển DB, vì restore về
  snapshot sẽ mất thay đổi sau dump. Không restore đè DB staging, không drop
  protected/audit, không xóa backup/candidate/checkpoint khi chưa có quyết
  định dọn dẹp. `rollback_033...sql` ghi rõ LOCAL/test only, **không chạy**.
- Mất kết nối hoặc trạng thái không biết đã COMMIT: chạy lệnh kiểm tra 6a,
  không gọi preflight cũ nếu web/worker đang dừng (nó cần PID active), và
  dừng báo PO; không tự thử lại hay bỏ gate.

## Giới hạn & việc đang chờ

- Bằng chứng staging hiện tại do PO cung cấp; agent không tự SSH/xác minh.
  Backup đọc được TOC, chưa chứng minh restore đầy đủ trên server. Chưa có
  báo cáo hash asset/counts sau startup thủ công; không tự ghi nhận PASS cho
  những kiểm tra chưa được báo. UAT staging vẫn **PENDING**.
- Rà toàn script không thấy thêm phép so sánh runtime trước/sau như lỗi
  ExecStart. Còn rủi ro quan sát quá sớm sau start hoặc thao tác nghiệp vụ
  giữa các lần chụp count; không nới gate vì những khả năng này. Khi triển
  khai sau phải giữ cửa sổ không ghi dữ liệu (không upload, sửa tay hoặc
  xác nhận preview) và đối chiếu trạng thái thực nếu gate báo lệch.
- Verifier phát hiện riêng guard nhận diện web chưa đủ chặt (S1), khác lỗi
  runtime fingerprint; cần PO duyệt sửa phạm vi đó trước khi tái sử dụng
  script cho lần triển khai khác. Lượt này giữ guard web theo yêu cầu cũ.
- Script fingerprint sửa lần này **chưa chạy lại trên server**; không tự
  retry cutover đã COMMIT. Bất kỳ thay đổi yêu cầu/gate HIGH cần xem
  `docs/DEVELOPMENT_WORKFLOW.md` mục 11, không tự sửa hợp đồng sau cutover.
- Production là phase thực thi sau staging UAT và phê duyệt riêng, phải soạn
  runbook PG14/immutable release riêng; **không lấy runbook staging dùng lại
  cho production**.
