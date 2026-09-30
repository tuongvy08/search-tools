# Trạng thái project

Cập nhật: 2026-09-30. Đây là trạng thái hiện hành; các mốc cũ giữ nguyên ở [archive](docs/archive/PROJECT_STATE_HISTORY.md). Nếu chưa có xác nhận mới từ production, coi như **chưa deploy migration033**.

## Tiếp nối nhanh

- Phase: regulatory-manual-edit / migration033; PR tính năng **#22 đã merge** vào `main@2ec9b6c` (PR tài liệu #21 cũng đã merge). Branch PR1 hiện tại: `docs/state-cleanup`; branch vận hành trước đó: `ops/deploy-regulatory-manual-edit`.
- Staging: migration033 đã COMMIT, web/worker chạy bản `2ec9b6c`; PO xác nhận UAT đạt. Không chạy lại các bước staging chỉ vì đổi tài liệu.
- Production: ứng dụng cũ vẫn đang phục vụ nhân viên; **chưa có bằng chứng deploy bản #22/migration033, coi là CHƯA DEPLOY**. Mốc quan sát trước đó: commit `6155f25`, PG14.24, migration030–032 có, 033 chưa (preflight 28/09 do PO cung cấp). Không suy trạng thái máy chủ hiện tại từ Git.
- Artifact đã qua verifier độc lập P1–P6 **local/mock**, nằm ngoài Git tại `/Volumes/DATA/Development/_ops/search-tools/`; SHA256 tính lại 2026-09-30:
  - `prepare_regulatory_staging.py`: `dd668fb18377ade432503f58b9d3e451d0f7849b75cb7fd430f5f255b499840d`.
  - `cutover_regulatory_staging.py`: `240424034cd4f5b3768aaf757c0b3a917a2e55958a73789ed4c80cf7ef1991e8`.
  - `search-tools-regulatory-6155f25-to-2ec9b6c.bundle`: `3c9f83a474b3c891a81d95a90eccf573be0faf52f024803073154c339e91cddb`.
- Preflight chỉ đọc của PO là artifact riêng, không nằm trong ba SHA256 P1–P6 trên; kiểm tra đúng bản preflight trước khi dùng theo runbook, không tự nhận đã qua verifier P1–P6.
- Báo cáo verifier còn hiệu lực: [production adapter P1–P6](specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT.md) PASS local/mock sau một repair; [evidence/human review](docs/ops-deploy-regulatory-manual-edit/VALIDATION.md). S1 là rủi ro được chấp nhận, **không phải lỗi đã sửa**; PASS local không chứng minh server đã triển khai.
- **Việc kế tiếp duy nhất của release:** PO xem xét năm câu human review và rủi ro còn lại; chỉ sau khi phê duyệt riêng mới xem xét cửa sổ triển khai production theo [runbook](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RUNBOOK.md). Trong đợt tài liệu này, hoàn thành PR1 rồi dừng chờ PO quyết định có làm PR2 hay không.

## Quyết định đã chốt và phạm vi phê duyệt

- PO đã cho phép commit, push và tạo PR1 `docs/state-cleanup`, bao gồm hồ sơ phase chưa theo dõi Git nếu quét sạch secret và bản kế hoạch PR2–PR5. Dừng sau PR1, chờ phản hồi mới làm PR2. Không có quyền thao tác staging/production; PR3–PR5 chỉ bắt đầu sau khi có xác nhận production deploy thành công.
- Production dùng release bất biến, shared `.venv`, launcher riêng và một drop-in mỗi unit chỉ thay WorkingDirectory/ExecStart. PO đã chấp nhận giữ S1 chưa sửa; chỉ được hiệu chỉnh local hai script trong task adapter cũ, **không** suy thành quyền chạy server hay sửa artifact đã duyệt trong đợt này.
- Rủi ro worker có thể nhận job trước khi hậu kiểm kết thúc: chấp nhận trước đây **chỉ cho staging** với queue=0 và không upload. Cần PO chấp thuận riêng rủi ro này, thời điểm ngừng ghi/upload và các bước [GHI] production; nếu điều kiện production khác hoặc artifact/hash đổi thì dừng và đánh giá lại, không dùng lại phê duyệt staging.
- Giữ nguyên script/bundle, hợp đồng kiểm chứng, VALIDATION và kết quả verifier đã khóa trong hồ sơ ops; không truy cập server hay DB thật. Backup production và kiểm tra trước/sau thuộc release riêng, không có quyền ngầm từ việc sửa tài liệu.

## Blocker hiện hành

- Release production migration033: đang chờ phê duyệt human review/rủi ro/cửa sổ thực thi riêng; chưa có bằng chứng deploy. Các báo cáo staging FAIL ở mốc cũ đã có kết quả/ngoại lệ về sau, không coi là blocker mới.
- Đợt B (PR3–PR5): chưa đủ điều kiện vì production chưa xác nhận deploy thành công. PR1/PR2 tài liệu không bị chặn bởi việc này.

## Việc mở

- PR1 `docs/state-cleanup`: hoàn tất commit/push/tạo PR và chờ PO phản hồi; sau đó mới làm PR2 `docs/agent-workflow`.
- Sau PR2: trình bày ngắn phạm vi, rủi ro và phê duyệt cần có trước production; không deploy trong đợt này.
- Sau khi có xác nhận production thành công: PR3 lưu script đã chạy, PR4 thống nhất hướng dẫn deploy/security/architecture, PR5 kiểm chứng bootstrap và sửa hướng dẫn local/RBAC/README; mỗi PR một branch và chờ PO giữa các PR.
- Các việc riêng chưa giao: xem xét tắt `ENABLE_LEGACY_PASSWORD_LOGIN` trên production nếu không còn cần; cân nhắc CI và xóa file `heroku` rỗng; đề xuất phase xuất quy tắc ra Excel và cải tiến giao diện quản trị cần quyết định nghiệp vụ mới. Không tự làm kèm.

## Liên kết

- [Archive nguyên văn trạng thái trước PR1](docs/archive/PROJECT_STATE_HISTORY.md); [hồ sơ feature](docs/regulatory-manual-edit/); [hồ sơ vận hành và runbook](docs/ops-deploy-regulatory-manual-edit/); [bản rà soát quy trình](docs/reviews/2026-09_process-review.md); [kế hoạch PR2–PR5](docs/reviews/2026-09_remediation-plan.md).
