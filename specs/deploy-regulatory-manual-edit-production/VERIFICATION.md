# Verification Contract — production adapter v1

Tạo trước khi sửa hai script ops. Phạm vi lượt này chỉ hiệu chỉnh và kiểm
chứng local/mock, **không chạy server**; không sửa hợp đồng staging/feature.
PO đã chấp nhận S1 là rủi ro đã biết, không yêu cầu sửa guard staging cũ.

## Yêu cầu gốc của PO

1. Gate production nhận Python `-I -B` và launcher `<release>-launchers-*/web.py`
   hoặc `worker.py`; Python kiểm theo realpath như trước.
2. Prepare tạo release mới từ bundle, đúng SHA 2ec9b6c, owner/quyền giống
   release cũ, `.venv` symlink cùng đích; copy launchers-phase6d9 chỉ thay
   đường dẫn release cũ→mới, in diff. Hai drop-in
   `zzzzzzzzzz-rme-2ec9b6c.conf` chỉ ở checkpoint, chưa đặt vào systemd.
3. Cutover giữ chuỗi worker stop→web stop→pg_dump -Fc/list→033 ON_ERROR_STOP.
   Sau COMMIT mới copy hai drop-in, daemon-reload, xác minh WorkingDirectory
   cả hai là release mới, start cả hai; kiểm commit/DB/login/JS.
4. Lỗi trước COMMIT thì start service bản cũ; sau COMMIT giữ dừng và báo PO.
5. Alias python, user searchtools-pg, PG/client 14, DB giữ tên
   searchtools_pg_r1_rollback_20260906_153842, backup root production.

## Claim P1 — Profile và launcher đúng phạm vi

Oracle: không có flag vẫn là staging; `--production` chọn đúng hai unit,
user/BASE6155f25/DB/backup/PG14. Launcher production đúng role/parent release,
Python realpath trùng venv; staging gate cũ giữ hành vi, S1 được ghi nhận là
rủi ro chấp nhận chứ không báo đã sửa. Không có lệnh dịch vụ khác.

Test type: local AST/mock + static review.

Failure case cần thử: sai profile, launcher role/đường dẫn/Python khác, PG
client16 trong profile14; từ chối, không in config/secret.

## Claim P2 — Prepare bất biến và giữ nguyên venv

Oracle: repo mới từ baseline + bundle, HEAD target sạch; release cũ không
checkout/update. Venv mới là symlink tới cùng đích cũ; không ghi vào đích dùng
chung. Owner/group/mode root release mới giống cũ. Prepare không stop/reload
services hoặc ghi thư mục systemd.

Test type: filesystem/Git fixture local + mock runner.

Failure case cần thử: path mới/checkpoint đã có, venv thiếu, bundle SHA sai,
release cũ đổi; dừng thay vì ghi đè. Không chạm live hoặc ứng dụng khác.

## Claim P3 — Copy launcher chỉ đổi path, không lộ secret qua diff

Oracle: nội dung mỗi file copy bằng chính old_bytes.replace(old_release,
new_release), nguồn nguyên vẹn, mode/owner được giữ. Diff chỉ thể hiện path
thay đổi; context có thể chứa secret không được in. Hai file web/worker và
checkpoint drop-in đúng chỉ có WorkingDirectory, ExecStart reset/new.

Test type: local filesystem/mock stdout.

Failure case cần thử: launcher symlink, file .env/non-Python, thiếu web hoặc
worker, nội dung không có path cũ, dòng chứa path và secret giả: dừng/che
context, không sửa logic khác hoặc in secret. Không ghi systemd ở Prepare.

## Claim P4 — Thứ tự cutover và chuyển release có kiểm soát

Oracle: zero queues, dừng worker rồi web, dump custom/list PASS rồi mới SQL033.
Chỉ sau SQL COMMIT mới lắp hai drop-in đúng tên 10 z, không overwrite unit
gốc/EnvironmentFile, daemon-reload, xác minh cả WD và ExecStart mới rồi start.
Metadata/fingerprint cấu hình ngoài thay đổi release dự kiến giữ nguyên.

Test type: mock trace toàn luồng + static review; server NOT RUN.

Failure case cần thử: dump/list lỗi, file drop-in sai/có sẵn/không cuối,
daemon-reload hoặc WD sai; không start bản sai, không tiếp bước ghi ngoài scope.

## Claim P5 — Phục hồi phân biệt COMMIT và trạng thái chưa rõ

Oracle: lỗi trước SQL hoặc đã xác minh transaction rollback/schema033 còn
vắng thì khởi động lại đúng hai service cũ (chưa có drop-in mới). Nếu đã
COMMIT hoặc không thể chứng minh chưa COMMIT thì giữ hai writer dừng, báo
PO, không checkout code cũ/down/restore DB. Lỗi stop phải được báo, không
giả khẳng định đã dừng.

Test type: fault injection/mock trước/sau SQL và startup.

Failure case cần thử: SQL COMMIT xong nhưng client mất response; lỗi copy
drop-in thứ hai hoặc start một service. Không tự mở worker cũ hoặc chạy lại
migration; giữ checkpoint/backup.

## Claim P6 — Postflight và bảo mật

Oracle: sau start kiểm lại hai process/cwd/commit target, DSN cùng DB cũ,
schema033, HTTP login200 và JS hash khớp target. Không .env, credential argv
hoặc raw Environment/launcher context trong output. Runbook tối đa một trang,
4–5 lệnh do PO chạy; không mặc định production đã triển khai.

Test type: mocked HTTP/DB/process + static review.

Failure case cần thử: SHA/DSN/HTTP/hash sai hoặc exception chứa secret giả:
fail bằng tên gate an toàn, dừng; không sửa Dify/n8n/hplc/app cũ.

## Ngoài phạm vi

Không thực thi server, không thay application code/migration033, không sửa
S1 staging cũ, không nâng runtime/venv, không rollback DB tự động. PASS local
không thay evidence production hoặc phê duyệt chạy [GHI].

## Thay đổi hợp đồng

Khóa từ lúc sửa script; thay claim/oracle cần log, phê duyệt, version mới và
kiểm chứng lại theo mục 11. Không tự sửa để làm test pass.

### Change request log

Chưa có.
