# Trạng thái project

Cập nhật: 2026-10-02. Đây là trạng thái hiện hành; các mốc cũ giữ nguyên ở [archive](docs/archive/PROJECT_STATE_HISTORY.md).

## Tiếp nối nhanh

- **RELEASE PRODUCTION migration033 — HOÀN TẤT 2026-10-02 (nay đã được release giao diện thay bản chạy):** bản `2ec9b6c` tại `/opt/search-tools-pg-release-20261002T080450Z-2ec9b6c-clean`, migration 033 đã COMMIT (030–033 = t), web + worker cùng DB `searchtools_pg_r1_rollback_20260906_153842`; `PRODUCTION TECHNICAL CUTOVER PASS`, postflight đạt, **PO kiểm tra ứng dụng đạt** và cho nhân viên dùng lại. Backup trước thay đổi: `/var/backups/search-tools-production/regulatory-manual-edit-2ec9b6c/prechange.dump` (32.077.412 byte). Hai script triển khai đã phải sửa vì giả định sai lộ ra trên production (worker exec; `.venv` symlink) — PO duyệt sửa, verifier P1–P6 PASS. Toàn bộ evidence, hash và bài học: [hồ sơ release](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RELEASE_2026-10-02.md). Hồ sơ đã gộp qua PR #29.
- **THỬ PHỤC HỒI STAGING — HOÀN TẤT 2026-10-02 (PR #26 đã merge vào `main@367a401`):** backup staging trước 033 phục hồi được vào DB tạm (94,052 giây, không lỗi, bảng chính có dữ liệu, đúng trước 033); DB tạm đã xóa sau khi PO đồng ý. Chi tiết: [OPERATIONS](specs/staging-restore-rehearsal/OPERATIONS.md). Không chứng minh production phục hồi được.
- **RELEASE PRODUCTION GIAO DIỆN MỚI — HOÀN TẤT 2026-10-02:** production (`ssh python`) chạy `1d47a1738a57c39d18ef44a721fb1bc6cfdbd16c` tại `/opt/search-tools-pg-release-20261002T104253Z-1d47a17-clean` (PR #29 → #30 → #31 đã gộp). `PRODUCTION UI SWITCH PASS`, số liệu giữ nguyên, postflight đạt, **PO kiểm tra ứng dụng ổn**. Quy trình nhẹ prepare/switch (không migration, tự quay về nếu lỗi), verifier độc lập PASS sau một vòng sửa. Evidence + hash: [ui-system/OPERATIONS](docs/ui-system/OPERATIONS.md). Branch hồ sơ `ops/production-ui-release`.

- Phase vừa xong: giao diện mới (admin-regulatory-ui #30, ui-system #31, hồ sơ release 033 #29; `main@1d47a17`) và trước đó regulatory-manual-edit / migration033. PR tính năng #22 (`main@2ec9b6c`), trạng thái #23, quy trình #24, phiếu chuẩn bị #25, thử phục hồi #26, trạng thái #27, dọn test #28 đều đã merge; `main@730654c`.
- Staging (`ssh staging`): chạy `900a5f9` (nội dung = `main@1d47a17`), có 033.
- Production (`ssh python`): chạy `1d47a17`, có 033 (xem trên). Không suy trạng thái máy chủ từ Git; lần sau kiểm bằng preflight chỉ đọc.
- Artifact triển khai đã chạy nằm ngoài Git tại `/Volumes/DATA/Development/_ops/search-tools/` (hash trong hồ sơ release); bản khóa gốc ở `locked-2026-09-30/`. PR3 sẽ đưa script đã chạy vào Git.

## Quyết định đã chốt và phạm vi phê duyệt

- **Quyết định PO 2026-10-02 — staging:** staging chỉ dùng để thử, không có người dùng thật; agent **được tự do triển khai/deploy và thử nghiệm trên staging** mà không cần hỏi lại từng lần (vẫn ghi evidence, không lộ secret, không đụng production từ staging). **Production** là hệ thống nhân viên đang dùng: mọi thao tác vẫn cần PO nói rõ “đồng ý bắt đầu” và theo SECURITY/quy trình HIGH. Quyết định này thay cho ràng buộc cũ “agent không tự truy cập server” **chỉ đối với staging**. Alias: staging=`ssh staging`, production=`ssh python` (bảng trong `SECURITY.md`).
- **Quyết định PO 2026-10-02 — script triển khai:** khi artifact đã khóa sai với thực tế production, được sửa đúng phạm vi lỗi sau khi PO đồng ý, kèm test bằng dữ liệu đo thật (chỉ đọc) và verifier độc lập; dùng lại cần PO nói lại “đồng ý bắt đầu”.
- PO dùng Claude Code thay OpenCode; với task HIGH dùng verifier độc lập là subagent context sạch theo mẫu mục 11. Khi cần quyền cho thao tác nhạy cảm, PO chuyển chế độ quyền sang **Manual** và duyệt từng bước.
- Production dùng release bất biến, shared `.venv` (symlink), launcher riêng exec sang chương trình thật, và một drop-in mỗi unit chỉ thay WorkingDirectory/ExecStart. S1 (nhận diện web qua fallback) là rủi ro đã chấp nhận, chưa sửa.
- Sau 033 không quay về bản cũ; lỗi phát sinh → sửa tiến tới; phục hồi từ backup cần PO duyệt riêng, vào DB mới, không đè DB đang dùng.

## Blocker hiện hành

- Không có blocker release. Đợt B (PR3–PR5) nay đủ điều kiện bắt đầu vì production đã xác nhận deploy thành công.

## Việc mở

- Giao diện mới (admin-regulatory-ui + ui-system) đã lên production 2026-10-02; việc còn lại: theo dõi góp ý nhân viên.
- PR3 lưu script đã chạy (prepare/cutover/preflight/test, và `ui-release-1d47a17/`) vào Git; PR4 thống nhất hướng dẫn deploy/security/architecture (gồm bài học launcher exec và `.venv` symlink); PR5 kiểm chứng bootstrap và sửa hướng dẫn local/RBAC/README; mỗi PR một branch, chờ PO giữa các PR.
- Dọn: test độc lập `tests/independent/test_production_launcher_exec_cmdline_adversarial.py` có 6 test ghim hash bản trước (lỗi thời); `opencode.json` ngoài phạm vi vẫn chưa commit.
- Các việc riêng chưa giao: xem xét tắt `ENABLE_LEGACY_PASSWORD_LOGIN` trên production nếu không còn cần; cân nhắc CI và xóa file `heroku` rỗng; phase xuất quy tắc ra Excel cần quyết định nghiệp vụ mới.

## Liên kết

- [Hồ sơ release 2026-10-02](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RELEASE_2026-10-02.md); [archive nguyên văn trạng thái trước PR1](docs/archive/PROJECT_STATE_HISTORY.md); [hồ sơ feature](docs/regulatory-manual-edit/); [hồ sơ vận hành và runbook](docs/ops-deploy-regulatory-manual-edit/); [bản rà soát quy trình](docs/reviews/2026-09_process-review.md); [kế hoạch PR2–PR5](docs/reviews/2026-09_remediation-plan.md).
