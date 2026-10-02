# Trạng thái project

Cập nhật: 2026-10-02. Đây là trạng thái hiện hành; các mốc cũ giữ nguyên ở [archive](docs/archive/PROJECT_STATE_HISTORY.md).

## Tiếp nối nhanh

- **RELEASE PRODUCTION migration033 — HOÀN TẤT 2026-10-02:** production (`ssh python`) chạy bản `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64` tại `/opt/search-tools-pg-release-20261002T080450Z-2ec9b6c-clean`, migration 033 đã COMMIT (030–033 = t), web + worker cùng DB `searchtools_pg_r1_rollback_20260906_153842`; `PRODUCTION TECHNICAL CUTOVER PASS`, postflight đạt, **PO kiểm tra ứng dụng đạt** và cho nhân viên dùng lại. Backup trước thay đổi: `/var/backups/search-tools-production/regulatory-manual-edit-2ec9b6c/prechange.dump` (32.077.412 byte). Hai script triển khai đã phải sửa vì giả định sai lộ ra trên production (worker exec; `.venv` symlink) — PO duyệt sửa, verifier P1–P6 PASS. Toàn bộ evidence, hash và bài học: [hồ sơ release](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RELEASE_2026-10-02.md). Branch hồ sơ: `ops/production-release-033` (chưa commit lúc ghi).
- **THỬ PHỤC HỒI STAGING — HOÀN TẤT 2026-10-02 (PR #26 đã merge vào `main@367a401`):** backup staging trước 033 phục hồi được vào DB tạm (94,052 giây, không lỗi, bảng chính có dữ liệu, đúng trước 033); DB tạm đã xóa sau khi PO đồng ý. Chi tiết: [OPERATIONS](specs/staging-restore-rehearsal/OPERATIONS.md). Không chứng minh production phục hồi được.
- **Việc kế tiếp:** (1) commit/PR hồ sơ release (branch `ops/production-release-033`); (2) phase mới **thiết kế lại giao diện quản trị quy tắc** theo góp ý PO sau UAT (xem Việc mở).

- Phase vừa xong: regulatory-manual-edit / migration033. PR tính năng #22 (`main@2ec9b6c`), trạng thái #23, quy trình #24, phiếu chuẩn bị #25, thử phục hồi #26, trạng thái #27, dọn test #28 đều đã merge; `main@730654c`.
- Staging (`ssh staging`): chạy bản `2ec9b6c`, có 033; PO đã UAT đạt.
- Production (`ssh python`): chạy bản `2ec9b6c`, có 033 (xem trên). Không suy trạng thái máy chủ từ Git; lần sau kiểm bằng preflight chỉ đọc.
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

- **Phase mới (PO yêu cầu 2026-10-02): thiết kế lại giao diện các trang quản trị quy tắc** (Quy tắc quản lý, Tìm và sửa quy tắc, chi tiết/lịch sử quy tắc, Thêm quy tắc thủ công) — chỉ giao diện, giữ nguyên hành vi/quyền/dữ liệu; làm trên branch riêng, thử trên staging trước, production cần “đồng ý bắt đầu” riêng. **Tiến độ 2026-10-02:** branch `feat/admin-regulatory-ui` (dựa trên `ops/production-release-033`), [SCOPE](docs/admin-regulatory-ui/SCOPE.md); đã làm lại 3 template + `admin_regulatory.css` + tách `_decorate_status` (chip màu ở danh sách/chi tiết); 73/73 test regulatory (DB thử local) + 2 test DOM OK; tự review ảnh chụp desktop/mobile. **Đã triển khai staging 2026-10-02** (agent tự chạy: bundle + checkout `7a9333a` tại `/srv/search-tools`, chỉ restart web; `/login` 200, log sạch) — xem [OPERATIONS](docs/admin-regulatory-ui/OPERATIONS.md). Bước tiếp: PO xem trên staging; PO duyệt + “đồng ý bắt đầu” mới đưa production.
- **Phase ui-system (PO yêu cầu 2026-10-02: đưa toàn bộ giao diện về phong cách mới):** [kế hoạch 5 đợt](docs/ui-system/PLAN.md); **cả 5 đợt xong và đã lên staging** (`db5db36`, branch `feat/ui-system` = PR #31, dựa trên `feat/admin-regulatory-ui`): mọi trang quản trị, đăng nhập/chờ duyệt, Tra cứu, Quick Quote. Bước tiếp: PO dùng thử trên staging (đặc biệt Tra cứu + Quick Quote), gộp #29 → #30 → #31, rồi release production giao diện cần “đồng ý bắt đầu”. PO đồng ý gom PR, gộp sau theo thứ tự #29 → #30 → #31.
- Commit/PR hồ sơ release (`ops/production-release-033`): `SECURITY.md` (bảng alias), hai script chẩn đoán chỉ đọc, test độc lập mới, báo cáo verifier, hồ sơ release, trạng thái.
- PR3 lưu script đã chạy (prepare/cutover/preflight/test) vào Git; PR4 thống nhất hướng dẫn deploy/security/architecture (gồm bài học launcher exec và `.venv` symlink); PR5 kiểm chứng bootstrap và sửa hướng dẫn local/RBAC/README; mỗi PR một branch, chờ PO giữa các PR.
- Dọn: test độc lập `tests/independent/test_production_launcher_exec_cmdline_adversarial.py` có 6 test ghim hash bản trước (lỗi thời); `opencode.json` ngoài phạm vi vẫn chưa commit.
- Các việc riêng chưa giao: xem xét tắt `ENABLE_LEGACY_PASSWORD_LOGIN` trên production nếu không còn cần; cân nhắc CI và xóa file `heroku` rỗng; phase xuất quy tắc ra Excel cần quyết định nghiệp vụ mới.

## Liên kết

- [Hồ sơ release 2026-10-02](docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RELEASE_2026-10-02.md); [archive nguyên văn trạng thái trước PR1](docs/archive/PROJECT_STATE_HISTORY.md); [hồ sơ feature](docs/regulatory-manual-edit/); [hồ sơ vận hành và runbook](docs/ops-deploy-regulatory-manual-edit/); [bản rà soát quy trình](docs/reviews/2026-09_process-review.md); [kế hoạch PR2–PR5](docs/reviews/2026-09_remediation-plan.md).
