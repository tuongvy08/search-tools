# Verification Contract — staging regulatory-manual-edit deployment v1

Tạo trước khi soạn runbook STAGING của task HIGH. Đây là hợp đồng cho **kế
hoạch và các gate khi thực thi sau này**, không phải bằng chứng staging đã
deploy. Không sửa sau khi bắt đầu thực thi runbook trừ quy trình đổi hợp
đồng mục 11 `docs/DEVELOPMENT_WORKFLOW.md`. Không thay hợp đồng tính năng
`specs/regulatory-manual-edit/VERIFICATION.md` đã khóa từ PR #22.

## Claim S1 — Đúng đích và chỉ staging

Oracle: mọi lệnh có hiệu lực ghi chỉ dùng alias `staging`, xác minh live
web/worker và database thực tế từ tiến trình; SHA candidate đúng đầy đủ
`2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`. Không có lệnh ghi chạy
qua `ssh python`, không đụng service Dify/n8n/Search-tools cũ. Chưa chạy
bất kỳ lệnh [GHI] trước khi PO duyệt runbook.

Test type: review runbook và đối chiếu kết quả preflight ngay trước cutover.

Failure case cần thử: PID thay đổi, live checkout bẩn, web và worker khác DB
hoặc người chạy dán nhầm alias SSH production; phải dừng, không ghi.

## Claim S2 — PostgreSQL 14 local không động dữ liệu người dùng

Oracle: container PG14 riêng chỉ bind loopback, ảnh/container/port đã xác
minh trước khi test import/DB probe; test chỉ tạo/drop DB dùng prefix tạm,
không kết nối local gốc hoặc `search_tools_uat`. Migration 033, rollback,
phase tests và hồi quy liên quan thực thi thật trên PG14, không skip test bắt
buộc. Container tạm được dọn có kiểm soát sau test.

Test type: local integration và kiểm tra đầu ra/test count/skip.

Failure case cần thử: runner trỏ nhầm PG16/DB UAT, psql fallback trỏ service
`db` khác, test skip vì không có psql; không được báo PG14 PASS.

## Claim S3 — Có backup có thể đọc trước migration

Oracle: sau khi cả web/worker dừng, lấy `pg_dump -Fc` đầy đủ của chính DB
staging đang chạy; file thuộc backup dir được bảo vệ, lệnh thành công, kích
thước >0 và `pg_restore --list` thành công trước khi cho phép migration 033.
Không đưa DSN/password vào argv/log/output.

Test type: review script, quan sát output exit code/size/list, preflight đĩa.

Failure case cần thử: hết đĩa, dump lỗi/0 byte, `pg_restore --list` lỗi,
DB đích khác web/worker: dừng trước SQL; không coi `--list` là đã thử restore.

## Claim S4 — Không có writer cũ trong migration

Oracle: số job queued/running ở cả ba queue bằng 0 ngay sát cutover; dừng
worker trước, xác minh không còn PID/job running, sau đó dừng web và xác
minh. Không chạy worker cũ khi 033 đã backfill protected rule.

Test type: review thứ tự/gate + evidence trạng thái dịch vụ/queue.

Failure case cần thử: job được nhận giữa preflight cũ và stop hoặc worker
còn child process dù `systemctl stop` trả 0: dừng, không migration.

## Claim S5 — Migration/code đồng bộ, dữ liệu khác giữ nguyên

Oracle: chỉ chạy `migration_033_regulatory_manual_edit.sql` bằng psql
`-X -v ON_ERROR_STOP=1` trên DB đã xác minh, kết quả schema/trigger và
backfill inactive đúng; code đúng SHA 2ec9b6c; web/worker đều chạy đúng
bản mới trên cùng DB. Số dữ liệu ngoài phạm vi migration giữ nguyên.

Test type: schema/postcutover smoke + đối chiếu snapshot trước/sau.

Failure case cần thử: SQL lỗi giữa transaction, checkout sai SHA, web mới
nhưng worker cũ, schema 033 đã một phần: dừng/điều tra, không báo thành công.

## Claim S6 — Rollback không xóa lịch sử/bỏ bảo vệ

Oracle: trước migration COMMIT có thể phục hồi code cũ chỉ sau xác minh
schema rollback; sau COMMIT không tự chạy worker/code cũ hoặc down SQL033,
không drop protected/audit; giữ writer dừng và xin phê duyệt forward fix hoặc
restore backup sang DB khác rồi đối chiếu. Không tự hứa rollback database.

Test type: review runbook nhánh lỗi và diễn tập lý luận theo checkpoint.

Failure case cần thử: 033 đã backfill inactive nên rollback code cũ có thể
ghi đè/xóa rule được bảo vệ: runbook phải dừng ở trạng thái an toàn.

## Claim S7 — Smoke/UAT staging trước khi nghĩ đến production

Oracle: xác minh web/worker/DB/033, HTTP login/assets, các count ngoài phạm
vi, quyền admin và xung đột import trên fixture staging được duyệt; PO xác
nhận staging UAT. Không có bước ghi production trong runbook staging hoặc
quyền production ngầm định từ UAT local.

Test type: smoke read-only + UAT người dùng trên staging khi được phép.

Failure case cần thử: chỉ kiểm tra HTTP 200 nhưng worker sai release, UAT
trên dữ liệu staging thật không được duyệt, hoặc tự mở rộng sang production.

## Ngoài phạm vi

Không thực thi staging trong lượt soạn runbook; không backup/migration/service
trên server, không lập runbook production, không chứng minh restore thật chỉ
bằng `pg_restore --list`. Các thay đổi hướng dẫn deploy cũ được ghi nhận xử
lý cuối task riêng, không sửa trong lượt này.

---

## Thay đổi hợp đồng

Sau khi bắt đầu thực thi runbook HIGH, không tự sửa claim/oracle để dễ PASS.
Cần ghi lý do vào Change request log, xin phê duyệt (PO nếu đổi yêu cầu;
verifier nếu chỉ làm rõ oracle), tăng version và chạy lại verification từ đầu.

### Change request log

Chưa có.
