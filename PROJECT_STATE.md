# Trạng thái project

Cập nhật: 2026-10-02. Đây là trạng thái hiện hành; các mốc cũ giữ nguyên ở [archive](docs/archive/PROJECT_STATE_HISTORY.md). Nếu chưa có xác nhận mới từ production, coi như **chưa deploy migration033**.

## Tiếp nối nhanh

- **THỬ PHỤC HỒI STAGING — HOÀN TẤT 2026-10-02 (branch `ops/staging-restore-rehearsal`, chờ PO nghiệm thu PR):** PO dùng Claude Code (Hướng A, hạ mức quy trình cho riêng task này, chấp nhận hai điểm rủi ro: kiểm “DB tạm trống” chưa được verifier context sạch chứng nhận lại; vài test phụ cũ còn lỗi thời). Bản sao lưu staging `…-prechange.dump` phục hồi được vào DB tạm: **restore 94,052 giây (05:05:07→05:06:41 UTC)**, không lỗi, bốn bảng chính có dữ liệu (products 1262173, stock_items 2295, rules 6000, statuses 6), đúng trạng thái **trước 033**; staging đang dùng **có** 033 (kiểm chỉ đọc). Chênh +3 rules/+3 inactive so với snapshot 01/10 được giải thích bằng dữ liệu (3 quy tắc tạo 9–14 phút sau lúc sao lưu). DB tạm đã **xóa sau khi PO đồng ý riêng** (giải phóng ≈705 MB); staging/dịch vụ/archive không đổi. Production NOT RUN. Chi tiết: [OPERATIONS](specs/staging-restore-rehearsal/OPERATIONS.md), [phiếu chuẩn bị](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_PREPARATION.md), [verifier](specs/staging-restore-rehearsal/VERIFICATION_RESULT.md). Giới hạn: không chứng minh production (PG14.24) hay từng dòng dữ liệu; thời gian không dự báo cho production.

- Phase: regulatory-manual-edit / migration033; PR tính năng **#22 đã merge** vào `main@2ec9b6c`, PR trạng thái **#23 đã merge** vào `main@8fd6873`, PR2 quy trình **#24 đã merge** vào `main@df2f13e`, phiếu chuẩn bị **#25 đã merge** vào `main@207cd11`. Local main đã fast-forward tới `207cd11`; branch thử phục hồi: `ops/staging-restore-rehearsal`; branch vận hành trước đó: `ops/deploy-regulatory-manual-edit`.
- Staging: migration033 đã COMMIT, web/worker chạy bản `2ec9b6c`; PO xác nhận UAT đạt. Không chạy lại các bước staging chỉ vì đổi tài liệu.
- Production: ứng dụng cũ vẫn đang phục vụ nhân viên; **chưa có bằng chứng deploy bản #22/migration033, coi là CHƯA DEPLOY**. Mốc quan sát trước đó: commit `6155f25`, PG14.24, migration030–032 có, 033 chưa (preflight 28/09 do PO cung cấp). Không suy trạng thái máy chủ hiện tại từ Git.
- Artifact đã qua verifier độc lập P1–P6 **local/mock**, nằm ngoài Git tại `/Volumes/DATA/Development/_ops/search-tools/`; SHA256 tính lại 2026-09-30:
  - `prepare_regulatory_staging.py`: `dd668fb18377ade432503f58b9d3e451d0f7849b75cb7fd430f5f255b499840d`.
  - `cutover_regulatory_staging.py`: `240424034cd4f5b3768aaf757c0b3a917a2e55958a73789ed4c80cf7ef1991e8`.
  - `search-tools-regulatory-6155f25-to-2ec9b6c.bundle`: `3c9f83a474b3c891a81d95a90eccf573be0faf52f024803073154c339e91cddb`.
- Preflight chỉ đọc của PO là artifact riêng, không nằm trong ba SHA256 P1–P6 trên; kiểm tra đúng bản preflight trước khi dùng theo runbook, không tự nhận đã qua verifier P1–P6.
- Báo cáo verifier còn hiệu lực: [production adapter P1–P6](specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT.md) PASS local/mock sau một repair; [evidence/human review](docs/ops-deploy-regulatory-manual-edit/VALIDATION.md). S1 là rủi ro được chấp nhận, **không phải lỗi đã sửa**; PASS local không chứng minh server đã triển khai.
- **Việc kế tiếp duy nhất của release:** PO chọn thời điểm và phê duyệt rõ **“đồng ý bắt đầu”** theo [phiếu chuẩn bị](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_PREPARATION.md), gồm human review/rủi ro và cửa sổ ngừng ghi/upload. Hai điểm chờ đã được PO bỏ để giữ nguyên artifact đã khóa; đợt này chỉ tài liệu/Git rồi dừng, không cấp quyền deploy.

## Quyết định đã chốt và phạm vi phê duyệt

- PO cho phép thử restore staging: PO tự chạy từng lệnh agent đưa, chỉ dùng backup có sẵn, kiểm đĩa trước, restore vào DB tạm mới có tên rõ/ngày; không ghi đè DB đang dùng, không dừng dịch vụ, không tạo backup mới, không dùng password/DSN trong argv. Bất thường phải dừng; xóa DB tạm phải hỏi PO riêng. Sau hoàn tất ghi kết quả/thời gian vào trạng thái và phiếu, tạo PR riêng; không merge hoặc triển khai production/PR3–PR5. Agent không tự truy cập server.
- **Quyết định PO 2026-09-30:** bỏ yêu cầu hai điểm chờ để không sửa script/artifact đã khóa; thay bằng **một lần “đồng ý bắt đầu” trước khi thực thi** tại thời điểm PO chọn. Đọc tĩnh xác nhận lỗi tạo/kiểm tra sao lưu chặn migration033 và chuyển release; các gate dừng tự động giữ nguyên. Sau COMMIT/không rõ không tự chạy bản cũ; phục hồi ngoài kế hoạch phải duyệt riêng.
- Production dùng release bất biến, shared `.venv`, launcher riêng và một drop-in mỗi unit chỉ thay WorkingDirectory/ExecStart. PO đã chấp nhận giữ S1 chưa sửa; chỉ được hiệu chỉnh local hai script trong task adapter cũ, **không** suy thành quyền chạy server hay sửa artifact đã duyệt trong đợt này.
- Rủi ro worker có thể nhận job trước khi hậu kiểm kết thúc: chấp nhận trước đây **chỉ cho staging** với queue=0 và không upload. Cần PO chấp thuận riêng rủi ro này, thời điểm ngừng ghi/upload và các bước [GHI] production; nếu điều kiện production khác hoặc artifact/hash đổi thì dừng và đánh giá lại, không dùng lại phê duyệt staging.
- Giữ nguyên script/bundle, hợp đồng kiểm chứng, VALIDATION và kết quả verifier đã khóa trong hồ sơ ops; không truy cập server hay DB thật. Backup production và kiểm tra trước/sau thuộc release riêng, không có quyền ngầm từ việc sửa tài liệu.

## Blocker hiện hành

- Release production migration033: đang chờ phê duyệt human review/rủi ro/cửa sổ thực thi riêng; chưa có bằng chứng deploy. Các báo cáo staging FAIL ở mốc cũ đã có kết quả/ngoại lệ về sau, không coi là blocker mới.
- Blocker “thiếu hai điểm chờ” **đã được PO quyết định bỏ**, không còn chặn release: dùng một lần phê duyệt trước bắt đầu, giữ nguyên artifact và các gate. Không có thay đổi script, hợp đồng hoặc evidence đã khóa.
- Đợt B (PR3–PR5): chưa đủ điều kiện vì production chưa xác nhận deploy thành công. PR1/PR2 tài liệu không bị chặn bởi việc này.

## Việc mở

- Phiếu chuẩn bị production đã merge qua PR #25; giữ nguyên một lần phê duyệt trước release và các artifact/evidence đã khóa.
- **Việc kế tiếp duy nhất của release:** chọn thời điểm và nhận “đồng ý bắt đầu”; kiểm tra đích/hash và các gate hiện có trong lần thực thi được duyệt, không suy dữ liệu máy chủ từ Git.
- Rehearsal staging (HIGH): **hoàn tất** (xem Tiếp nối nhanh); còn việc: tạo PR riêng cho nhánh `ops/staging-restore-rehearsal` (chỉ file liên quan, không `opencode.json`), PO nghiệm thu; **không merge/deploy production**. Việc phụ chưa giao: dọn/sửa vài test phụ cũ trong `tests/independent/` (đếm sai ngưỡng, định dạng lỗi cũ, flaky) và cân nhắc có giữ các test cần Docker trong Git không.
- Sau khi có xác nhận production thành công: PR3 lưu script đã chạy, PR4 thống nhất hướng dẫn deploy/security/architecture, PR5 kiểm chứng bootstrap và sửa hướng dẫn local/RBAC/README; mỗi PR một branch và chờ PO giữa các PR.
- Các việc riêng chưa giao: xem xét tắt `ENABLE_LEGACY_PASSWORD_LOGIN` trên production nếu không còn cần; cân nhắc CI và xóa file `heroku` rỗng; đề xuất phase xuất quy tắc ra Excel và cải tiến giao diện quản trị cần quyết định nghiệp vụ mới. Không tự làm kèm.

## Liên kết

- [Archive nguyên văn trạng thái trước PR1](docs/archive/PROJECT_STATE_HISTORY.md); [hồ sơ feature](docs/regulatory-manual-edit/); [hồ sơ vận hành và runbook](docs/ops-deploy-regulatory-manual-edit/); [bản rà soát quy trình](docs/reviews/2026-09_process-review.md); [kế hoạch PR2–PR5](docs/reviews/2026-09_remediation-plan.md).
