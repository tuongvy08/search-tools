# Verification Contract — release production menu "Báo giá → Đơn hàng" (v1)

Tạo trước khi sửa script ops. Phạm vi lượt này: chuẩn bị + kiểm chứng local/mock
hai script `prepare_qto_release.py` / `switch_qto_release.py` (dẫn xuất từ
`ui-release-1d47a17/` đã chạy thành công trên production 2026-10-02), **không
chạy server**. PO nói "đồng ý bắt đầu" (2026-10-04) cho việc chuẩn bị release;
việc CHẠY trên production vẫn do PO tự chạy từng lệnh sau khi xem runbook.

## Yêu cầu gốc của PO

1. Đưa `main@3ceb29570bc5a4e42b0070b3adb9808abed3529e` (menu Báo giá → Đơn hàng,
   PR #33/#34) lên production thay bản đang chạy `1d47a1738a57c39d18ef44a721fb1bc6cfdbd16c`.
2. Cùng quy trình nhẹ đã chạy được 2026-10-02: prepare release bất biến mới →
   switch (dừng worker, web → drop-in → khởi động lại cả hai → smoke) → tự quay
   về bản cũ nếu lỗi. Không migration, không ghi database, không đổi `.env`.
3. Thay đổi ứng dụng so với bản đang chạy: module Python mới `quote_to_order.py`,
   sửa `search.py` và `_user_nav.html`, file mẫu `assets/quote_to_order/*.xlsx`,
   JS/CSS/template mới, tài liệu. `requirements.txt` không đổi.

## Claim Q1 — Profile, hằng số và tên artifact đúng bản này

Oracle: với `--production`, BASE = `1d47a1738a57c39d18ef44a721fb1bc6cfdbd16c`,
TARGET = `3ceb29570bc5a4e42b0070b3adb9808abed3529e`, LIVE =
`/opt/search-tools-pg-release-20261002T104253Z-1d47a17-clean`, checkpoint
`/var/backups/search-tools-production/qto-release-3ceb295`, bundle
`/tmp/search-tools-qto-1d47a17-to-3ceb295.bundle`, tên release mới kết thúc
`-3ceb295-clean`, drop-in `zzzzzzzzzzzz-qto-3ceb295.conf` (sắp SAU mọi drop-in
đang có: `zzzzzzzzzz-rme-2ec9b6c.conf`, `zzzzzzzzzzz-ui-1d47a17.conf`). Không có
cờ `--production` thì dừng `PRODUCTION_ONLY`. Không còn tham chiếu nhầm tới bản
`2ec9b6c`/`ui-1d47a17` làm đích.

Test type: AST/mock + static review.

Failure case cần thử: thiếu cờ, hằng số cũ còn sót, drop-in mới sắp TRƯỚC drop-in
`ui-1d47a17` (phải bị từ chối `PRODUCTION_DROPIN_LAST_UNUSED`).

## Claim Q2 — Prepare bất biến, đúng target, kiểm tra file mới

Oracle: release mới tạo từ baseline `1d47a17` + bundle, HEAD = TARGET, sạch,
`requirements.txt` không đổi, release đang chạy không bị checkout/ghi, `.venv` là
symlink cùng đích, owner/mode giống bản cũ, launcher copy chỉ đổi đường dẫn;
cổng kiểm tra bắt buộc có `quote_to_order.py`, `assets/quote_to_order/bang-hang-hoa_template.xlsx`,
`templates/quote_to_order.html`, `static/quote_to_order.js`, `static/quote_to_order.css`.
Bundle phải có đúng `TARGET refs/heads/main`. Không dừng/restart dịch vụ, không ghi systemd.

Test type: Git fixture thật + bundle thật `1d47a17..3ceb295` + mock runner.

Failure case cần thử: bundle sai head/SHA, thiếu một file mới, release cũ đã đổi
HEAD, đường dẫn candidate/checkpoint đã tồn tại, requirements bị đổi.

## Claim Q3 — Switch: thứ tự, drop-in, không ghi dữ liệu

Oracle: gate chỉ đọc (cùng DB, schema 033, hàng đợi 0, `/login` 200) → dừng worker
→ dừng web → cài đúng hai drop-in mới (nội dung = WorkingDirectory + ExecStart
launcher mới, không đè unit/EnvironmentFile) → daemon-reload → xác minh
WorkingDirectory/ExecStart → start cả hai → số liệu nghiệp vụ không đổi → smoke.
Không backup/migration/SQL ghi.

Test type: mock trace toàn luồng + static review.

Failure case cần thử: drop-in mới đã tồn tại hoặc không đứng cuối, staged drop-in
sai nội dung, daemon-reload lỗi, WorkingDirectory sai, DB web ≠ worker.

## Claim Q4 — Smoke chứng minh đúng bản mới

Oracle: sau start, `/login` = 200 và file tĩnh `static/quote_to_order.js` phục vụ
bằng đúng nội dung blob Git tại TARGET (hash khớp); hai service ổn định 5 giây
sau start với PID không đổi. Sai hash/HTTP → fail bằng tên gate an toàn và quay về.

Test type: mock HTTP/process.

Failure case cần thử: asset trả nội dung khác, HTTP 500/403, service chết sau start.

## Claim Q5 — Tự quay về bản cũ, không để dở dang

Oracle: mọi lỗi sau khi dừng dịch vụ → dừng hai service, chỉ gỡ đúng drop-in mới
do lượt chạy này cài (nội dung khớp), daemon-reload, xác nhận WorkingDirectory trở
về `1d47a17`, start lại, báo `ROLLBACK DONE`; không đổi dữ liệu. Drop-in lạ/đã
bị sửa thì KHÔNG xóa, báo `ROLLBACK_UNCONFIRMED`. Drop-in `rme` và `ui-1d47a17`
cũ không bị đụng.

Test type: fault injection/mock.

Failure case cần thử: lỗi giữa chừng khi cài drop-in thứ hai, start thất bại,
drop-in bị sửa trước khi rollback, tín hiệu ngắt phiên SSH.

## Claim Q6 — Bảo mật và runbook

Oracle: không in `.env`, DSN, mật khẩu, launcher context; thông báo lỗi chỉ là tên
gate cố định. Runbook ≤ 1 trang, lệnh do PO chạy: preflight chỉ đọc → scp bundle
→ prepare → (PO báo nhân viên) → switch → postflight chỉ đọc; mỗi lệnh ghi rõ alias
`ssh python`. Không tuyên bố production đã triển khai.

Test type: static review + mock stdout với secret giả.

Failure case cần thử: exception chứa secret giả; alias nhầm `ssh staging`.

## Ngoài phạm vi

Không chạy server; không sửa logic ứng dụng/migration; không đổi quyền, `.env`,
unit gốc hay venv; không kiểm chứng nghiệp vụ tính giá (PO tự UAT trên staging và
sau release). PASS local không thay evidence production.

## Thay đổi hợp đồng

Khóa từ lúc sửa script; thay claim/oracle cần log, phê duyệt, version mới và
kiểm chứng lại theo mục 11. Không tự sửa để làm test pass.

### Change request log

Chưa có.
