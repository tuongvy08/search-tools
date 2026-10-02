# Rehearsal staging — lệnh và evidence theo lượt

Hợp đồng v1 đã tạo trước file này. PO tự chạy trên Mac, agent không SSH. Chỉ staging; không chạy preflight/prepare/cutover production hoặc sửa artifact deploy đã khóa. Chưa có quyền xóa DB tạm trước xác nhận riêng.

## S0.1 — Dịch vụ và đĩa chỉ đọc, chưa kết nối DB

Không dùng script `preflight_readonly.py` cũ. Artifact đó giữ nguyên; kiểm chứng local S0.1 cũ bị chặn (V1 FAIL), không phải sự cố đã quan sát trên server. Lệnh hiện tại chỉ đọc trạng thái đúng hai unit staging và filesystem chứa mã/backup, không đọc runtime DSN, không kết nối PostgreSQL.

Lệnh duy nhất phát trong lượt đầu, chạy ở Terminal trên Mac:

```bash
ssh staging 'systemctl is-active search-tools-staging.service && systemctl is-active search-tools-import-worker.service && df -h -- /srv/search-tools /srv/backups/search-tools'
```

Giải thích cho PO: kiểm tra riêng trang web, rồi riêng bộ xử lý nhập file staging còn chạy; chỉ khi cả hai đạt mới xem dung lượng trống ở nơi chứa mã/bản sao lưu. Chỉ đọc; không dùng pg_dump/pg_restore/psql. Không dừng dịch vụ, không đổi dữ liệu/cấu hình, không in mật khẩu/chuỗi kết nối và không đọc `.env`. Mỗi lần is-active chỉ nhận đúng một unit; dịch vụ đầu lỗi thì không kiểm tiếp dịch vụ sau/đĩa, dịch vụ sau lỗi thì không kiểm đĩa. Lỗi df kết thúc lệnh với mã lỗi; không tự retry.

PO gửi toàn bộ output an toàn, không gửi password/DSN/cookie/nội dung `.env`. Mong đợi: hai dòng `active`, bảng đĩa cho hai vị trí trên và cột Avail; có thể cùng in filesystem/mountpoint `/`. Nếu dịch vụ không active, thiếu đường dẫn, lỗi hoặc đĩa cạn thì dừng, báo PO, không phát lệnh ghi/rerun. Không kết luận DB/033/restore đã đạt từ output này. Dùng alias staging theo hồ sơ; không dùng alias production.

Lệnh này **không** kiểm DB, 033, exact archive, DB data directory/filesystem hoặc đủ đĩa cho restore. Các bước chỉ đọc kế tiếp phải xác minh alias/đích PostgreSQL staging **trước bất kỳ truy vấn DB**, client/peer access, data directory + free bytes, archive cố định (file thường, không symlink, metadata/hash/TOC), và số liệu staging/checkpoint trước dump nếu có. Không tạo DB cho tới khi các điều kiện này được đối chiếu và phần lệnh ghi qua review độc lập.

## S0.2 — File sao lưu có sẵn: chỉ đọc metadata, chưa đọc TOC/DB

Chỉ phát sau output S0.1 được đối chiếu và lệnh S0.2 qua review độc lập:

```bash
ssh staging 'p=/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump; sudo -n test -f "$p" && sudo -n test ! -L "$p" && sudo -n test -s "$p" && sudo -n stat -c "backup_bytes=%s%nbackup_modified=%y" -- "$p" && printf "%s\n" "ARCHIVE_METADATA_OK"'
```

Lệnh chỉ kiểm file cố định tồn tại/là file thường, không symlink, có dữ liệu, rồi đọc kích thước và thời điểm sửa. Biến `p` chỉ nằm trong phiên shell; không sửa file cấu hình. Không đọc dữ liệu trong archive, không backup mới, không dùng công cụ DB, không sửa file/dịch vụ. `sudo -n` không hỏi password; thiếu quyền trả lỗi, không hướng dẫn bỏ guard hoặc tự thử lại. Các phép kiểm nối `&&`: thiếu file, symlink, rỗng hoặc lỗi quyền/stat thì dừng trước bước kế; các test có thể thất bại mà không in dòng nào — output thiếu trường `backup_bytes`/`backup_modified` hoặc dấu `ARCHIVE_METADATA_OK` cũng là chưa đạt, phải báo PO/dừng.

Kết quả thực GNU stat có `%n` in tên file, không phải newline: hai trường metadata cùng một dòng, xen giữa là đúng đường dẫn archive. Đây là lỗi trình bày trong lệnh S0.2; giữ nguyên lệnh lịch sử, không yêu cầu PO chạy lại. Không coi metadata là TOC/restore PASS. Cần so với dấu vết archive trước 033, không tự dùng file khác nếu không đúng dự kiến. Hash/TOC, DB identity và data filesystem còn chưa kiểm; không phát create/restore từ output metadata. Có thể có race giữa các phép đọc; các bước trước restore phải xác minh lại file/hash và đích, không hứa chống người có quyền thay file đồng thời.

## S0.3 — Đọc SHA256 và danh sách archive, không kết nối DB

Chỉ phát sau đối chiếu output S0.2 và review độc lập lệnh này:

```bash
ssh staging 'p=/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump; sudo -n test -f "$p" && sudo -n test ! -L "$p" && sudo -n test -s "$p" && sudo -n sha256sum -- "$p" && sudo -n pg_restore --list "$p" && printf "%s\n" "ARCHIVE_TOC_OK"'
```

Chỉ đọc cùng file sao lưu: kiểm lại file thường/không symlink/không rỗng, tính hash để làm mốc, đọc mục lục TOC bằng `pg_restore --list`. Không có `--dbname`/`--create`/`--clean`/`-f`, không thực thi SQL hoặc phục hồi, không tạo file/dump mới, không truy cập DB/dịch vụ/config/.env. Không lấy DSN/password từ môi trường hoặc truyền trong argv. `&&` chặn bước kế và marker khi bất kỳ phép kiểm/hash/list lỗi (kể cả list đã in một phần); thiếu `ARCHIVE_TOC_OK`, lỗi, TOC bất thường/thiếu bảng chính hoặc database nguồn không đúng `search_tools_staging` thì dừng báo PO, không tự đổi client/file hoặc rerun.

Mong đợi hash 64 ký tự, header archive (ngày tạo/DB nguồn/phiên bản dump), mục lục có cả TABLE và TABLE DATA của bốn bảng chính `products`, `stock_items`, `regulatory_rules`, `regulatory_statuses`, và marker cuối. TOC chỉ là chỉ dấu archive đọc được, không chứng minh dữ liệu nonempty/restore thành công; row counts và footprint đầy đủ phải kiểm ở DB tạm sau restore. Không yêu cầu từng mục trong TOC có cùng số lượng với staging hiện tại. Gửi toàn bộ output TOC/metadata an toàn, không row data/secret. Hash/TOC chỉ chứng minh đúng bytes đã đọc ở lượt này; trước restore cần đối chiếu lại hash/metadata, không hứa loại bỏ race nếu người có quyền thay file.

## S0.4 — Xác minh runtime staging trước kết nối DB

Script mới, không sửa artifact cũ: `scripts/staging_restore_identity_readonly.py`, SHA256 `d0a32e3e11e023da62ca37361fa9e34cebcaefde6e9583f34d7159fe2225ec5a` (repair5, 2026-10-02: chỉ chấp nhận đúng định dạng đầu ra, stderr bất kỳ cũng dừng; bản cũ hash `1d3ab0f853046fd3ecf546e11bc4a819bf2c9dddc256e4a353807b12faebc489` bỏ qua stderr nên PO phải chạy lại bản mới, chỉ đọc, trước mọi bước ghi). Chỉ đọc systemctl/proc/Git từ hai unit staging; không SELECT hoặc công cụ PostgreSQL. Chỉ phát sau review độc lập, PO tự chạy từ Mac:

```bash
ssh staging 'sudo -n env LC_ALL=C LANG=C python3 -I -B -' < "/Volumes/DATA/Development/Search-tools/scripts/staging_restore_identity_readonly.py"
```

Locale chỉ nằm trong process, không sửa config server. `-I -B` dùng stdlib, không load dotenv/application và không ghi bytecode; script đi qua stdin, không copy/cài file trên server. Thực hiện riêng mỗi unit: active/running, User deploy, PID hợp lệ, cwd đúng `/srv/search-tools` không symlink, commit đúng feature target `2ec9b6c`; lỗi bất kỳ dừng, không fallback UNKNOWN. Đọc đúng một DATABASE_URL không rỗng từ mỗi tiến trình trong bộ nhớ; cùng DSN; tên DB đúng `search_tools_staging`, host loopback allowlist, port hợp lệ, không fragment/tùy chọn ghi đè host/service. Sai đích hoặc thiếu điều kiện thì chỉ in gate cố định, không in credential/exception thô, không truy vấn DB hoặc retry.

Mong đợi thời điểm UTC, hai dịch vụ active/cùng DB, `live_database=search_tools_staging`, host/port đã qua whitelist, cwd/commit đã kiểm và marker `STAGING_RUNTIME_IDENTITY_OK (no DB connection, no changes)`. Chỉ xuất giá trị identity đã giới hạn; không xuất role/password/DSN hoặc nội dung `.env`. Nếu lỗi, warning, thiếu marker hoặc giá trị không đúng dự kiến thì dừng báo PO; không bỏ kiểm, sửa cấu hình hoặc dùng nguồn `.env` cũ để thử tiếp. Mốc này không chứng minh đã kết nối PostgreSQL/schema/033/data filesystem; các bước đó còn phải kiểm riêng trước ghi.

## S0.5 — Metadata PostgreSQL staging: session chỉ đọc

Chỉ phát sau S0.4 xác minh runtime host loopback/cổng 5432, cùng DB staging đúng cwd/commit, và lệnh này qua review độc lập. Đích DB/cổng explicit, không lấy từ file `.env` hoặc shell default. PO tự chạy một lệnh:

```bash
ssh staging 'sudo -n -u postgres env -i LC_ALL=C LANG=C PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000" /usr/lib/postgresql/16/bin/psql -X -w -h /var/run/postgresql -p 5432 -U postgres -d search_tools_staging -v ON_ERROR_STOP=1 -P pager=off -c "SELECT current_database() AS database_name, version() AS postgres_version; SHOW data_directory; SHOW transaction_read_only; SELECT pg_database_size(current_database()) AS database_bytes;" && printf "%s\n" "STAGING_PG_METADATA_OK"'
```

Chỉ SELECT/SHOW metadata, không rows nghiệp vụ; không tạo DB, migration, backup/restore, role/extension hoặc service write. Quyền OS postgres dùng local peer qua socket chuẩn; không password/DSN trong argv. `env -i` bỏ PGHOST/PGHOSTADDR/PGSERVICE/PGDATABASE/PGUSER/PGPASSWORD/PGOPTIONS kế thừa, chỉ đặt locale C và read-only/statement timeout cho process; không đổi cấu hình server. Client 16 dùng đường dẫn explicit để tránh wrapper locale/default-major khác. `-X` không đọc psqlrc, `-w` không hỏi password, ON_ERROR_STOP + `&&` chặn marker khi lỗi; không thử lại hoặc chuyển socket/port/DB/client/quyền nếu lỗi.

Mong đợi: database `search_tools_staging`, server PostgreSQL 16.x đúng nguồn đã quan sát, data directory tuyệt đối, transaction_read_only `on`, database_bytes >0 và marker cuối. Marker chỉ chứng minh lệnh exit 0, không tự xác nhận peer/socket là đúng instance của TCP app hoặc data filesystem đủ đĩa. Nếu bất thường/thiếu marker/readonly off/PG không đúng thì dừng và báo PO; không phát lệnh ghi. Phải đối chiếu metadata/socket instance với runtime trước ghi; filesystem chứa data_directory và footprint 033/bảng/counts còn phải kiểm riêng. Data directory là metadata phục vụ kiểm disk, không đọc file dữ liệu PostgreSQL.

## S0.6 — Dung lượng filesystem chứa dữ liệu PostgreSQL

Chỉ dùng đúng data directory PO đã quan sát trong S0.5 (`/var/lib/postgresql/16/main`), không đoán từ thư mục mã/backup. Chỉ phát sau review độc lập:

```bash
ssh staging 'sudo -n env -i LC_ALL=C LANG=C /usr/bin/df -B1 -- /var/lib/postgresql/16/main && printf "%s\n" "STAGING_DB_DISK_OK"'
```

Chỉ đọc thông tin filesystem, không đọc file dữ liệu DB, không truy vấn PG/tạo file hoặc sửa config/dịch vụ. Env sạch/locale C chỉ cho process. `df -B1` in bytes để đối chiếu chính xác; thiếu đường dẫn, quyền/công cụ hoặc df lỗi thì `&&` không in marker, dừng không retry/fallback. Chưa tạo DB hoặc restore.

Agent phải đối chiếu cột Available với buffer **max(5×1024³,4×DB nguồn)**; từ S0.5 DB=1.228.176.407 byte thì buffer=5.368.709.120 byte (4×DB=4.912.705.628). Thiếu free space, cảnh báo/lỗi, output không đọc được hoặc thiếu marker thì dừng báo PO, không tự dọn file/dữ liệu để lấy chỗ. Marker chỉ exit 0, không chứng minh đủ đĩa. Trước bước ghi phải cập nhật lại disk/DB size/gate nếu có thay đổi hoặc nghỉ; không dùng compressed dump 37 MB làm dự báo dung lượng restore. Đây là buffer lập kế hoạch, chưa phải đo mức dùng đĩa/thời gian restore thật; tablespace và instance đích cần xác minh riêng trước ghi.

## S0.7 — Instance staging, footprint 033 và counts nguồn (chỉ đọc)

File SQL mới `SOURCE_CHECK.sql` trong hồ sơ task, SHA256 `05a868759de8611451bd3aec060fc2b6d70bce31f5ed54943a41fe9b394f2951`; không sửa migration/artifact cũ. Chỉ phát sau S0.4–S0.6 đã đối chiếu và review độc lập. PO tự chạy một lệnh từ Mac, SQL qua stdin, không copy file lên server:

```bash
ssh staging 'p=/var/lib/postgresql/16/main/postmaster.pid; sudo -n test -f "$p" && sudo -n test ! -L "$p" && sudo -n test -s "$p" && sudo -n env -i LC_ALL=C LANG=C /usr/bin/ss -H -ltnp "sport = :5432" && sudo -n -u postgres env -i LC_ALL=C LANG=C PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000" /usr/lib/postgresql/16/bin/psql -X -w -h /var/run/postgresql -p 5432 -U postgres -d search_tools_staging -v ON_ERROR_STOP=1 -P pager=off -x -f - && printf "%s\n" "STAGING_SOURCE_CHECK_OK"' < "/Volumes/DATA/Development/Search-tools/specs/staging-restore-rehearsal/SOURCE_CHECK.sql"
```

Chỉ đọc listener cổng app đã xác minh và SELECT metadata/counts của đúng DB staging. Trước query kiểm metadata PID là file thường/không symlink/không rỗng ở đúng data directory đã quan sát; lỗi chặn cả listener/query. SQL chỉ đọc tối đa 64 byte đầu `postmaster.pid` (metadata PID, không dữ liệu bảng/config/credential); chỉ xuất số PID hợp lệ hoặc NULL, không in nội dung file. Không in row data/DSN/password, không sửa DB/dịch vụ/role/extension; locale C/env sạch/read-only/timeout chỉ cho process. `ss` lỗi chặn query, SQL thiếu quyền/bảng hoặc lỗi chặn marker, không retry/fallback. Có race giữa phép kiểm metadata/query; không hứa chống người có quyền thay file/config đồng thời. Query read-only không chứng minh bảo vệ toàn hệ thống chống writer khác.

Agent đối chiếu output trước bước kế: đúng DB/PG16/port5432/data_dir/read-only on; PID postmaster từ peer query phải khớp PID `postgres` của listener IPv4 phục vụ 127.0.0.1:5432 (127.0.0.1 hoặc wildcard 0.0.0.0) ở output ss. Nếu không có PID/listener, không khớp, nguồn sai, warning/lỗi/marker thiếu thì dừng. Encoding/locale/tablespace ghi làm metadata, không tự thay cấu hình. Cả footprint030–032 và chín cờ033 phải true (`t`); trigger ROW BEFORE UPDATE đúng function và enabled O/A, index valid/ready và identity unique. Đây là footprint, không test lại nội dung function/nghiệp vụ của phase.

Ghi số dòng bốn bảng và rule inactive như **snapshot staging hiện tại**, không phải số trước dump; bảng chính thiếu/rỗng hoặc số liệu bất thường thì dừng báo PO, không suy backup hỏng chỉ từ staging hiện tại. Inactive có thể 0; không coi là bảng chính rỗng. Chênh lệch khi đối chiếu DB tạm sau restore có thể do dùng staging sau backup; nhỏ có giải thích ghi nhận/đi tiếp, lớn bất thường dừng. Không đòi counts hiện tại bằng snapshot backup. Marker chỉ exit 0, không tự chứng minh instance/cờ/counts phù hợp; phải nhìn output. Chưa phát create/restore/drop từ marker. Trước ghi còn phải kiểm đúng DB tạm chưa tồn tại, không dịch vụ trỏ vào, archive/hash/đĩa/gate đích hiện hành.

## S1.1 — Tạo duy nhất DB tạm TRỐNG, chưa restore

**Cập nhật 2026-10-02 (repair5, PO chọn Claude Code/Hướng A): script đã sửa theo whitelist đầu ra, hash mới `a9fe5d3f44a84a51a954621c2379277483ee0e76add9292b1ccb83381c4f397c`; CHƯA qua verifier độc lập, vẫn KHÔNG CHẠY S1.1 cho tới khi verifier PASS và PO được đưa lệnh.** Mô tả BLOCKED dưới đây là lịch sử của bản hash cũ `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16`: **BLOCKED — KHÔNG CHẠY S1.1:** sau repair4 PO chỉ đạo, verifier vẫn V2 FAIL ở warning nối cùng dòng listener và phát hiện V1 FAIL ở warning bị che/bỏ qua trong script S0.4 cũ không đổi. Chỉ local/mock, không kết luận server thật có warning. Các ca version/create/ACL warning trước đã PASS, nhưng chưa đạt toàn safety gate. Hash S1.1 hiện hành `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16`; hợp đồng và artifact ops cũ giữ nguyên. Dừng/escalate, không tự sửa tiếp hoặc phát WRITE; PO có giới hạn30 phút trước nghỉ, khuyên chưa tạo DB hôm nay. Chưa có DB tạm để xóa, không chạy tiếp khi PO vắng mặt.

Tên chọn từ mốc snapshot nguồn UTC: `search_tools_restore_test_20261001_064530`, không phải DB ứng dụng. Script mới tự chứa `scripts/staging_restore_create_temp.py`, SHA256 `a9fe5d3f44a84a51a954621c2379277483ee0e76add9292b1ccb83381c4f397c`, không sửa/import artifact ops cũ hoặc script identity S0.4. Chỉ phát sau review độc lập các bước ghi liên quan; PO tự chạy một lệnh trên Mac:

```bash
ssh staging 'sudo -n env LC_ALL=C LANG=C python3 -I -B - --create' < "/Volumes/DATA/Development/Search-tools/scripts/staging_restore_create_temp.py"
```

Các prechecks trước WRITE đều chỉ đọc: root + flag `--create` đúng, tên tạm hợp lệ/khác source; hai unit active/deploy/cwd/commit/DSN đúng staging 127.0.0.1:5432, không in credential; PID file thường/không symlink và TCP listener khớp; archive canonical/không symlink/size `37333943`/mtime `2026-09-29 08:03:10.692114220+00`/SHA256 đã ghi, giữ fingerprint khi đọc; peer PG16.15/source schema033/UTF8/locale libc en_US.UTF-8/pg_default đúng, **DB tạm chưa tồn tại**. Kiểm locale OS khả dụng, binary createdb16 và free space **thư mục base của pg_default**, buffer max(5 GiB,4×source DB size), không chỉ dump nén. Thiếu quyền/công cụ/locale/hash/disk/identity/collision thì dừng, không tự đổi tên/quyền/client hoặc thử lại.

Thay đổi được phép ở bước này: `createdb` chỉ tạo tên tạm mới từ `template0` trống, owner là role postgres **đã có**, cùng UTF8/locale nguồn, pg_default. Connection maintenance đặt explicit source staging, CREATE chỉ tạo object mới trong catalog cluster; không sửa dữ liệu/schema/ACL của DB ứng dụng. Sau tạo, chỉ trên **DB tạm mới**: REVOKE CONNECT từ PUBLIC để tránh app dùng nhầm và COMMENT nhãn task/hash để nhận diện về sau. Không tạo/xóa/đổi role hoặc extension, không migration/backup/restore, không stop/restart/service/config/secret write. Các session ghi chỉ dùng cho createdb/metadata DB tạm; query helper cấm mode write trên source/DB khác.

Hậu kiểm readonly: DB tạm có OID dương/khác source, owner postgres, nhãn khớp, PUBLIC CONNECT đã tắt, UTF8/locale/pg_default đúng, public user tables=0; runtime còn đúng, instance PID và archive fingerprint/hash không đổi. In start/end UTC và elapsed **tạo DB** (không phải thời gian restore), tên/OID cho bước đối chiếu/xóa sau. Chỉ marker `STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)` sau toàn bộ hậu kiểm đạt. Không diễn giải marker precreate thành đã tạo/restore.

Nếu bất kỳ lỗi sau khi CREATE được phát (kể cả timeout/mất kết nối/không rõ kết quả/ACL-comment/hậu kiểm lỗi), giữ DB tạm nếu có và archive; in HOLD, không báo thành công, không DROP/cleanup/rerun hoặc áp dụng migration để sửa kết quả. Xóa vẫn phải hỏi PO riêng. OS process bị kill/reboot có thể không in HOLD: khi nối lại chỉ kiểm trạng thái trước, không tự rerun. Không hứa ngăn người có quyền đổi cấu hình/file đồng thời; prechecks là snapshot, createdb collision được DB chặn không overwrite.

## S1.2 — Khôi phục file sao lưu vào DB tạm (GHI, chỉ DB tạm)

Script mới tự chứa `scripts/staging_restore_run_temp.py`, SHA256 `113532b1ee1e1326867ece83f83f1aa835bd019091dce6c7ff5e817f411a57db`. **Chỉ phát sau verifier độc lập PASS.** PO tự chạy một lệnh trên Mac:

```bash
ssh staging 'sudo -n env LC_ALL=C LANG=C python3 -I -B - --restore' < "/Volumes/DATA/Development/Search-tools/scripts/staging_restore_run_temp.py"
```

Gate chỉ đọc trước ghi (cùng bộ guard như S1.1, cập nhật tại thời điểm chạy): root + cờ `--restore`; hai unit staging active/commit/DSN đúng; PID postmaster khớp listener; archive size/mtime/SHA256 đúng; nguồn PG16.15 + schema033 đủ + DB tạm **đang tồn tại**; đĩa data ≥ max(5GiB, 4×DB nguồn); `pg_restore` client 16; DB tạm đúng OID 18535, owner postgres, nhãn, PUBLIC CONNECT tắt, **0 bảng, 0 vật thể người dùng (schema phụ, function, type, extension ngoài plpgsql, trigger, v.v.), 0 session**.

Thay đổi duy nhất: `pg_restore --exit-on-error --single-transaction --dbname=<DB tạm>` với archive qua stdin (không --clean/--create/--no-owner, không -j), đích explicit DB tạm, không DSN/password trong argv. Một giao dịch duy nhất: lỗi thì không để lại dữ liệu dở dang. stdout/stderr rỗng khi đạt; mọi output lạ hoặc mã lỗi → `DUNG` + `HOLD`, không DROP/cleanup/rerun. Nếu lỗi, chỉ in **nhãn cố định** (ROLE_MISSING, PERMISSION_DENIED, OBJECT_ALREADY_EXISTS, DISK_OR_MEMORY, ARCHIVE_UNREADABLE, EXTENSION_PROBLEM, CONNECTION_PROBLEM hoặc UNCLASSIFIED); không bao giờ in nguyên văn lỗi của công cụ vì có thể chứa dữ liệu.

Hậu kiểm (chỉ đọc trên DB tạm): ≥1 bảng, bốn bảng chính tồn tại và có dữ liệu (>0), số rule inactive, footprint 030–032 đều true, **chín cờ 033 đều false và chín kiểm theo TÊN (bất kể hình dạng: bảng/cột/function/trigger/index 033) đều vắng** (trước 033); runtime/instance/archive không đổi, DB staging đang dùng không đổi (OID). In giờ UTC bắt đầu/kết thúc, **thời gian restore thực tế**, kích thước DB tạm, các counts và cờ. Marker `STAGING_TEMP_RESTORE_COMPLETED` chỉ sau toàn bộ hậu kiểm; counts vẫn phải được agent đối chiếu với snapshot nguồn (products 1262173, stock_items 2295, rules 6003, statuses 6, inactive 3 tại 2026-10-01 06:45 UTC; chênh nhỏ có giải thích được ghi lại, chênh lớn/bảng trống dừng báo PO). Marker không thay việc đọc số liệu.

Rủi ro đã biết (verifier chạy PG16 thật): nếu phiên SSH đứt hoặc hết timeout 3600 giây thì script in HOLD nhưng tiến trình pg_restore **có thể vẫn chạy tiếp và commit** — không được coi là đã hủy; trạng thái là KHÔNG RÕ, phải kiểm DB tạm chỉ đọc (số bảng/counts) trước mọi việc khác, không rerun/xóa; archive đọc bằng root rồi đưa qua stdin nên không cần quyền đọc của user postgres.

**Chấp nhận rủi ro của PO (2026-10-02):** S1.2 phát đi dù claim V2 (kiểm DB tạm trống) FAIL hai lần liên tiếp ở verifier; bản đã sửa (hash `113532b1ee1e1326867ece83f83f1aa835bd019091dce6c7ff5e817f411a57db`) chỉ được kiểm lại bằng bộ ma trận của verifier chạy cục bộ (0/29 lọt, positive control đạt), chưa có verifier context sạch chứng nhận. Các phần V3/V4/V5/an toàn restore đã PASS trên PG16 thật (Docker, dữ liệu giả).

## S3 — Xóa DB tạm (XÓA, chỉ DB tạm; PO đã đồng ý xóa riêng 2026-10-02)

PO trả lời "đồng ý xóa" sau khi thấy kết quả restore và giải thích chênh lệch (câu hỏi riêng theo yêu cầu gốc). Script mới tự chứa `scripts/staging_restore_drop_temp.py`, SHA256 `88309119973333349a378cd6e91abf31eaf08b3ee69cb72acd022edfdd1139b6`. **Chỉ phát sau verifier độc lập PASS.** PO tự chạy một lệnh:

```bash
ssh staging 'sudo -n env LC_ALL=C LANG=C python3 -I -B - --drop' < "/Volumes/DATA/Development/Search-tools/scripts/staging_restore_drop_temp.py"
```

Gate trước xóa (chỉ đọc; bất kỳ lỗi/output lạ → dừng, KHÔNG xóa): root + cờ `--drop`; hai unit staging active/commit/DSN trỏ **chỉ** DB staging (không dịch vụ nào dùng DB tạm); PID postmaster khớp listener; archive size/mtime/SHA256 đúng; `dropdb` client 16; nguồn đúng staging PG16.15 + schema033; DB tạm **đang tồn tại** với đúng OID 18535, owner postgres, nhãn, PUBLIC CONNECT tắt và **0 session**. Chỉ một lệnh ghi: `dropdb -w ... --maintenance-db=search_tools_staging <DB tạm>` — **không --force, không --if-exists, không interactive, không terminate backend**: còn session thì máy chủ từ chối, không ép.

Hậu kiểm: danh sách database sau = danh sách trước **trừ đúng DB tạm**, OID DB staging không đổi, hai dịch vụ vẫn active, instance PID và archive (hash) không đổi; in dung lượng trống trước/sau. Marker `STAGING_TEMP_DATABASE_DROPPED` chỉ sau toàn bộ hậu kiểm. Lỗi sau khi DROP được phát → `HOLD` (trạng thái không rõ), không retry/force.

## Các bước còn lại

S1-restore và S2/S3 chưa chuẩn bị lệnh/chưa được chạy; không phát cả chuỗi để PO chạy tự động. S1.1 tạo DB chỉ sau review, không ghi đè. Không áp dụng 033 lên bản phục hồi. Trước xóa phải hỏi PO riêng.

## Evidence hiện tại

- S0.1 server — output PO đã gửi: hai unit active; `/dev/vda2` 75G, used 15G, avail 57G, use 21%, mount `/` cho cả mã/backup. Không suy đủ đĩa DB từ kết quả này.
- S0.2 metadata archive: PO đã chạy lần đầu sau nghỉ; `backup_bytes=37333943`, `backup_modified=2026-09-29 08:03:10.692114220 +0000`, `ARCHIVE_METADATA_OK`. Tên file giữa các trường do GNU stat `%n`, không phải lỗi dữ liệu. Chưa phát create/restore/drop, không có DB tạm do task tạo.
- **Chỉnh evidence 2026-10-02:** SHA256 archive ghi lần đầu chỉ 63 ký tự (mất chữ `c` đầu khi chép). PO chạy lại lệnh chỉ đọc tính hash, nhận đủ 64 ký tự `cc021ca2…473f` + `ARCHIVE_SHA256_OK`; giá trị này thay thế trong script/hồ sơ.
- S0.3 archive hash/TOC server — output PO đã gửi: SHA256 `cc021ca2848d75b98ec103726d9d42e1cabb3e39df36aa6d03e04b9789b7473f`; archive created `2026-09-29 08:03:02 UTC`, DB `search_tools_staging`, format CUSTOM/gzip, 376 entries, dump version 1.15-0, server/pg_dump 16.15. Marker `ARCHIVE_TOC_OK` có. TOC có TABLE/TABLE DATA: products 230/4111, stock_items 279/4160, regulatory_rules 234/4115, regulatory_statuses 270/4151. Các bảng manual_keys/manual_events, function update_regulatory_rule_revision và trigger zz_regulatory_rule_revision riêng 033 không có trong TOC. Không suy số dòng, cột/footprint đầy đủ hoặc restore PASS từ TOC.
- **Bất thường S0.3 — PO đã đồng ý tiếp tục:** Perl cảnh báo locale LC_CTYPE=UTF-8/LANG=en_US.UTF-8 không được hỗ trợ, tự fallback rồi hoàn tất đọc TOC. Đã dừng báo PO và nhận đồng ý dùng LC_ALL=C/LANG=C chỉ trong process lệnh sắp tới. Không có lỗi archive/restore được quan sát; chưa restore. Không rerun S0.3, ghi config server hoặc sửa `.env`/dịch vụ.
- S0.4 runtime identity server — output PO: inspected UTC `2026-09-30T15:52:22.444993+00:00`; web/worker active/cùng DB; DB `search_tools_staging`, host 127.0.0.1, port 5432, cwd `/srv/search-tools`, commit `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, marker `STAGING_RUNTIME_IDENTITY_OK (no DB connection, no changes)`. Không warning/lỗi. Chưa chứng minh PG instance/schema/data filesystem; script/hợp đồng giữ nguyên.
- **S0.4 chạy lại bản mới (script hash `d0a32e3e…ec5a`) — output PO 2026-10-02 UTC `03:41:50.174784`:** web/worker active, cùng DB; `live_database=search_tools_staging`, host `127.0.0.1`, port `5432`, cwd `/srv/search-tools`, commit `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, marker `STAGING_RUNTIME_IDENTITY_OK (no DB connection, no changes)`. Không warning/lỗi (script mới dừng với mọi diagnostic, nên đây là bằng chứng sạch hơn bản đầu). Chỉ đọc.
- S0.5 metadata DB server — output PO: `search_tools_staging`, PostgreSQL 16.15 Ubuntu/x86_64, data_directory `/var/lib/postgresql/16/main`, transaction_read_only `on`, database_bytes `1228176407`, marker `STAGING_PG_METADATA_OK`. Không warning/lỗi. Có đoạn đầu lệnh bị cắt trong paste, nhưng các trường/marker đầy đủ từ lệnh đã phát; không yêu cầu rerun. Không có bước ghi/DB tạm. Peer/socket/TCP instance chưa đối chiếu đầy đủ trước ghi.
- S0.6 đĩa dữ liệu DB server — output PO: `/dev/vda2`, 1B-blocks `79941234688`, used `15751888896`, available `60691943424`, use 21%, mount `/`, marker `STAGING_DB_DISK_OK`. Available vượt buffer `5368709120` dựa trên S0.5 DB size; đủ dự phòng ở mốc đọc, không chứng minh restore. Chưa có bước ghi.
- S0.7 source server — PO gửi UTC `2026-10-01 06:45:30.598206+00`; PID listener IPv4/IPv6 `3993835` khớp postmaster peer; đúng staging/PG16.15/port5432/data_dir/read-only on. Source size `1228176407`; UTF8/libc (`c`), collate/ctype `en_US.UTF-8`, icu_locale trống, tablespace `pg_default`. Counts hiện tại products `1262173`, stock_items `2295`, regulatory_rules `6003`, statuses `6`, inactive `3`; bốn bảng chính nonempty, inactive là count riêng. 030–032 và chín cờ033 đều true. Marker có/không warning. Đây là snapshot nguồn hiện tại, không phải snapshot trước dump hoặc proof dữ liệu trong backup nonempty.
- **S1.1 chạy thật — PO 2026-10-02 UTC `03:51:27`:** toàn bộ gate chỉ đọc đạt (nguồn PID `3993835`, source_database_bytes `1228209175`, data_base_free_bytes `60673347584` ≥ required `5368709120`), rồi lệnh createdb **lỗi**: `DUNG: TEMP_CREATE_COMMAND` + `HOLD: CREATE attempted`. Chưa biết DB tạm có được tạo hay chưa và nguyên nhân (script cố ý không in lỗi thô). **HOLD: không rerun, không xóa/cleanup.** Bước kế: lệnh chỉ đọc `TEMP_STATE_CHECK.sql` + đối chiếu log PostgreSQL.
- **S3 XÓA DB tạm — script hash `88309119…39b6`, PO chạy 2026-10-02 UTC: THÀNH CÔNG (sau khi PO đồng ý xóa riêng trong chat).** Gate trước xóa đạt (`temp_oid=18535 temp_user_tables=48 temp_sessions=0`); `databases_before=postgres,search_tools_restore_test_20261001_064530,search_tools_staging,template0,template1`; bắt đầu `05:42:45.297748`, kết thúc `05:42:45.862480`, `drop_elapsed_seconds=0.565`; `temp_database_exists_after=NO`, `databases_after=postgres,search_tools_staging,template0,template1`; free `59816255488` → `60521213952`, `freed_bytes=704958464`; archive sha256 không đổi, `live_database_untouched=YES services_active=YES`; marker `STAGING_TEMP_DATABASE_DROPPED`. Không DUNG/HOLD. **Không còn DB tạm nào của task trên staging.**
- **S1.2 RESTORE THẬT — script hash `113532b1…57db`, PO chạy 2026-10-02 UTC: THÀNH CÔNG.** Bắt đầu `05:05:07.188448`, kết thúc `05:06:41.240902`, **thời gian restore thực tế `94.052` giây**; không DUNG/HOLD. Gate trước ghi đạt (free `60664930304` ≥ required `5368709120`). DB tạm `search_tools_restore_test_20261001_064530` oid 18535: `temp_user_tables=48`, `temp_bytes=704871447`. Counts DB phục hồi: products `1262173`, stock_items `2295`, regulatory_rules `6000`, statuses `6`, rules inactive `0`. Footprint 030/031/032 = true; chín cờ 033 (hình dạng) và chín cờ theo TÊN đều false => đúng trạng thái TRƯỚC 033. archive sha256 không đổi, `live_database_untouched=YES`; marker `STAGING_TEMP_RESTORE_COMPLETED`. **Đối chiếu snapshot nguồn 01/10 06:45 UTC (products 1262173, stock_items 2295, rules 6003, statuses 6, inactive 3):** products/stock_items/statuses khớp tuyệt đối; rules chênh 3 và inactive chênh 3 — staging hiện có thêm 3 quy tắc đều ở trạng thái ngừng dùng. **Giải thích bằng dữ liệu (COUNTS_EXPLAIN.sql, PO chạy 2026-10-02, chỉ đọc):** staging có 3 quy tắc `created_after_backup`, cả 3 đều inactive, tạo từ `2026-09-29 08:12:25` đến `08:16:57 UTC` (9–14 phút SAU lúc sao lưu `08:03:02`), và 4 quy tắc `updated_after_backup`; DB phục hồi có 0 quy tắc tạo/sửa sau sao lưu, `latest_created=2026-04-01 13:47:48 UTC`. => chênh lệch (+3 rules, +3 inactive) hoàn toàn do hoạt động trên staging sau sao lưu (khớp thời điểm thử/UAT sau khi áp migration 033), nhỏ và có giải thích: ghi nhận, đi tiếp theo chỉ đạo PO. Không có bảng chính trống/thiếu, không chênh lớn. Chưa xóa DB tạm.
- **S1.1 chạy lại (script hash `a9fe5d3f…397c`) — PO 2026-10-02 UTC `04:08:27`: THÀNH CÔNG.** Gate chỉ đọc đạt (PID `3993835`, source bytes `1228209175`, free `60673167360` ≥ required `5368709120`); `create_elapsed_seconds=0.523`; `temp_database=search_tools_restore_test_20261001_064530 temp_oid=18535 temp_owner=postgres`; `temp_public_connect=NO temp_user_tables=0 label_verified=YES`; `archive_sha256=cc021ca2…473f unchanged=YES`; marker `STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)`. DB tạm tồn tại, TRỐNG, chưa restore. Thời gian này là thời gian TẠO DB, không phải restore.
- **Kiểm chỉ đọc sau HOLD — PO 2026-10-02:** `TEMP_STATE_CHECK.sql` trả `(0 rows)`; log PostgreSQL không có dòng chứa tên DB tạm. => DB tạm KHÔNG tồn tại, không cần cleanup. Nguyên nhân (đối chiếu tài liệu PG16 createdb): script truyền `--connection-limit=3`, tùy chọn **không tồn tại** ở createdb 16 nên công cụ từ chối phía client, máy chủ chưa nhận lệnh. Đã bỏ tùy chọn (không thuộc yêu cầu PO); script tạo mới hash `a9fe5d3f44a84a51a954621c2379277483ee0e76add9292b1ccb83381c4f397c`; thêm test đối chiếu argv với danh sách tùy chọn createdb16 chính thức. Chờ verifier lần 7, rồi PO chạy lại S1.1 (lần thứ hai; lần đầu không tạo gì).
- (Lịch sử, đã lỗi thời) S1.1 tạo DB tạm server: NOT RUN — **review an toàn V1/V2 FAIL sau repair4**. S1.1 developer13/13 và độc lập cũ15/15 PASS; bộ additional_output mới1 PASS/3 methods FAIL (4 failure records), gồm S0.4 và listener inline warning. Dừng/escalate, không tự repair thêm/rerun. Target dự kiến `search_tools_restore_test_20261001_064530`, chưa có DB tạm do task tạo; hash FAIL hiện hành `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16`, giữ evidence/hợp đồng, không đổi PASS local thành PASS server.
- Create/restore/counts/schema/delete: NOT RUN.
- Thời gian restore thực tế: chưa có.
- Production: NOT RUN, ngoài phạm vi.

## Tiếp nối sau khi PO nghỉ

**PO yêu cầu lưu checkpoint và quay lại sau:** xem `CHECKPOINT.md`. Hiện PAUSED/BLOCKED V1/V2, chưa phát WRITE/chưa có DB tạm; không cần cleanup. Chưa được chỉ đạo repair5; không tự sửa tiếp hoặc chạy server khi PO vắng mặt. S0.1–S0.7 đã có output, không rerun tự động; snapshots cũ không chứng minh hiện tại. Khi quay lại đọc state/checkpoint/diff, nhận chỉ đạo xử lý blocker, giữ hợp đồng/budget/artifact ops cũ và verifier sạch. Chỉ sau gate đạt mới cập nhật điều kiện chỉ đọc trước bất kỳ ghi staging; PO tự chạy từng lệnh, dừng khi bất thường và hỏi riêng trước xóa nếu sau này đã tạo DB tạm.
