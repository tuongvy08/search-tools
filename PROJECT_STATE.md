# Trạng thái project

Cập nhật: 2026-09-27 (checkpoint spec sửa quy tắc pháp chế thủ công;
chưa triển khai code hoặc migration).

## Trạng thái hiện tại

Search-tools là ứng dụng Flask + PostgreSQL **đang chạy production** cho
nhân viên công ty. Tính đến commit `6155f25` (nhánh `main`), lịch sử merge
gần nhất theo `git log --oneline --merges` là:

- PR #20 (`codex/phase6d9-search-workspace`) — sắp xếp lại giao diện Product
  Search (chỉ trình bày, không đổi route/permission/query — xem
  `docs/phase6d9/BRIEF.md`).
- PR #19 (`codex/phase6d8-stock-only-filter`) — lọc "chỉ hiện kết quả có
  tồn kho" trên Product Search.
- PR #18 (`codex/phase6d7-search-stock-suggest`) — tìm theo tên hàng tồn
  kho + endpoint gợi ý tìm kiếm.
- PR #17 (`codex/phase6d6-stock-quick-edit`) — sửa nhanh 1 dòng tồn kho.
- PR #16 (`codex/phase6d2-stock-notes`) — ghi chú tồn kho (`stock_note`).
- PR #15 (`codex/phase6d5-check-license-selection`), #14 (`phase6d4-admin-menu-rbac`),
  #13 (`fix-admin-login-access`), #12 (`fix-product-import-replace`),
  #11 (`phase6d3-preparation-type`), #10 (`phase6d2-pg14-compat`),
  Phase 6D2 inventory snapshot (merge trực tiếp, không qua số PR), #8
  (`phase6d1-1-status-colors`), #7 (`phase6c4-admin-ux`), #6
  (`phase6c3-product-management`), #5 (`phase6c2-admin-import-center`), #4
  (`phase6c1-release`), #3 (`phase6c0-1-team-capabilities`), #2
  (`feature/admin-lifecycle-v1`), #1 (Release 1 — quote assistant + canonical
  pricing).

**[xác nhận từ người dùng, 2026-09-27]** Production hiện chạy **bản mới nhất
của `main`** (tới PR #20 / Phase 6D9). Quy trình chuẩn của team: mỗi phase
sau khi làm xong được deploy lên **staging thật** (hạ tầng riêng: web
`search-tools-staging.service`, worker `search-tools-import-worker.service`,
thư mục `/srv/search-tools`, database `search_tools_staging`) để UAT, chỉ
triển khai production sau khi staging đạt — nên các UAT checklist trong
`docs/phase6d7/`, `phase6d8/`, `phase6d9/` ghi "pending" là trạng thái tại
thời điểm viết phase doc, không phản ánh tình trạng hiện tại.

**Nguồn bổ sung quan trọng** (2026-09-27, do người dùng chỉ ra): một
`PROJECT_STATE.md` chi tiết hơn nhiều (618 dòng) từ các phiên điều phối
Codex trước đây cho project này, lưu **cục bộ trên máy Product Owner** tại
`/Users/truong/Documents/Codex/2026-08-16/referenced-chatgpt-conversation-this-is-an/`
(không nằm trong Git, chỉ có trên máy đó — không giả định file này còn tồn
tại/đúng như vậy trên máy khác hoặc trong tương lai). File đó ghi lại toàn
bộ lịch sử preflight/prepare/cutover từng phase, quyết định nghiệp vụ chi
tiết (đối chiếu với `SPEC_REGULATORY_STOCK.md` cùng thư mục — đã dùng để bổ
sung docs/BUSINESS_RULES.md) và mô hình deploy thực tế (xem ARCHITECTURE.md
mục Deploy). Theo lần cập nhật cuối cùng ghi nhận trong file đó (cũng ngày
2026-09-27), cutover production cho Phase 6D8+6D9 mới ở trạng thái "PREPARED,
AUTHORIZED, sẵn sàng cho người dùng chạy" — chưa có output xác nhận đã chạy
xong tại thời điểm đó; xác nhận của người dùng trong phiên tài liệu này (sau
đó cùng ngày) rằng production đã chạy bản mới nhất được hiểu là cutover đó
đã hoàn tất sau thời điểm file kia được ghi. Không tự suy ra thêm chi tiết
vận hành (PID, tên release cụ thể...) ngoài hai nguồn này; đọc lại file đó
hoặc hỏi người dùng nếu cần mốc thời gian chính xác hơn.

Bối cảnh lịch sử xa hơn (không còn là rủi ro hiện tại, giữ lại để tránh nhầm
lẫn khi đọc phase docs cũ): `docs/phase6c3/OPERATIONS.md` (Phase 6C3, PR #6)
ghi **"PRODUCTION HOLD"** tại thời điểm đó và production khi đó đang ở
commit `24ac6d4` (PR #5) trên database tên có "rollback"
(`searchtools_pg_r1_rollback_20260906_153842`), tức từng có một khoảng trễ
giữa `main` và production quanh 2026-09-06 — khoảng trễ đó đã được thu hẹp
nhiều lần qua từng phase kể từ đó.

Golden Development Template đã merge vào `main` qua PR #21, commit
`db26d40` (xác minh bằng Git ngày 2026-09-27). Branch hiện tại cho phase mới:
`feature/regulatory-manual-edit`, tạo từ đúng commit này của `main`.

## Việc đang mở

- **HIGH — regulatory-manual-edit — spec v2 đã cập nhật theo duyệt có điều
  chỉnh của người dùng ngày 2026-09-27; chưa implement.**
  Mục tiêu: tìm/thêm/sửa/ngừng áp dụng/khôi phục từng quy tắc pháp chế,
  có lịch sử và bảo vệ thay đổi thủ công trước import.
  Branch: `feature/regulatory-manual-edit`, base `main@db26d40`.
  Người dùng đã chốt A–H và điều chỉnh D: import luôn giữ mục thủ công,
  không có tùy chọn ghi đè; preview tay chỉ trên UI, server kiểm tra quyền,
  CSRF, validation, expected revision và chống gửi lặp. Quyết định nằm trong
  `specs/regulatory-manual-edit/SPEC.md`. Hợp đồng kiểm chứng theo mục 11:
  `specs/regulatory-manual-edit/VERIFICATION.md`; kế hoạch migration/rollback:
  `specs/regulatory-manual-edit/MIGRATION.md`.
  Phạm vi được phép lượt này: chỉ cập nhật tài liệu trên branch hiện có;
  **không code, không tạo file migration, không SQL, không truy cập server**.
  V2 còn 20 claim (V1–V11, V13–V21; bỏ V12). Không bảng preview tay/bảng
  chi tiết import mới; dùng audit request_id chống gửi lặp và JSONB job/event
  hiện có cho danh sách xung đột đầy đủ. Không số đếm sản phẩm ảnh hưởng.
  Đã review tài liệu v2, có lượt review tài liệu độc lập; làm rõ không chia
  tùy ý các dòng cùng scope để chạy nhiều lần replace_scoped khi file lớn.
  Đây không phải verifier runtime. Bước kế tiếp: báo thay đổi và dừng chờ
  yêu cầu tiếp; không suy quyền implement từ việc duyệt spec.
  Chưa chạy test app/SQL, chưa có VERIFICATION_RESULT.md hoặc tính năng PASS.
- Các việc tùy chọn cũ (xóa file `heroku`, cân nhắc tắt legacy login,
  cập nhật tài liệu deploy) không thuộc phase này.

## Vấn đề đã biết

- `ENABLE_LEGACY_PASSWORD_LOGIN` (đăng nhập chỉ-mật-khẩu, không username)
  **đang bật trên production**; hiển thị dưới nút đăng nhập Google, thực tế
  chỉ admin dùng. Người dùng ghi nhận (2026-09-27): **nên tắt nếu xác nhận
  không còn cần thiết** (admin đã có đăng nhập Google). Đây là ý kiến/định
  hướng, chưa phải yêu cầu thực hiện ngay — tắt biến này là thay đổi cấu
  hình `.env` trên production (HIGH theo AGENTS.md/SECURITY.md), chỉ thực
  hiện khi người dùng yêu cầu rõ trong một task riêng. Xem SECURITY.md.
- Tài liệu vận hành hiện có (`HUONG_DAN_DEPLOY_VA_CAP_NHAT.md`) mô tả quy
  trình deploy đơn giản (git pull tại chỗ ở `/opt/search-tools-pg`, restart
  service) — **khác với thực tế vận hành gần đây** (xem ARCHITECTURE.md mục
  Deploy): mỗi release là một thư mục bất biến riêng
  `/opt/search-tools-pg-release-<timestamp>-<commit>-clean`, deploy qua 3
  script preflight/prepare/cutover, luôn có staging trước production. Tài
  liệu này chưa được cập nhật lại trong task hiện tại (ngoài phạm vi 5 file
  được giao viết lại) — cân nhắc cập nhật `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md`
  trong một task riêng để tránh agent tương lai làm theo quy trình cũ.
- Không có workflow CI trong repo tại thời điểm viết tài liệu này —
  `.github/` mới chỉ có `PULL_REQUEST_TEMPLATE.md` (từ Golden Template), chưa
  có GitHub Actions chạy test/lint tự động. Test hiện chạy thủ công:
  `PYTHONPATH=.:tests python -m unittest discover -s tests -v` (một số test
  cần PostgreSQL local, tự skip nếu không có) và các file `tests/*.js` (Node
  thuần, chạy từng file).
- Một số phase docs (`docs/phase6c2/`, `phase6c3/`...) ghi số liệu hiệu năng/
  benchmark tại thời điểm viết — không tự suy ra hiệu năng hiện tại còn
  đúng, đo lại nếu ra quyết định tối ưu mới.
- File `heroku` ở gốc repo là file rỗng (0 byte), tàn dư từ lần cấu hình
  Heroku cũ trước khi chuyển sang Vultr. Người dùng xác nhận (2026-09-27)
  **có thể xóa** — chưa xóa vì nằm ngoài phạm vi task viết tài liệu này; xóa
  trong một task riêng khi được yêu cầu.

## Môi trường

- **Local/dev**: PostgreSQL qua Docker Compose (service `db`, `docker-compose.yml`),
  `.venv` Python (`requirements.txt`), chạy `python search.py` (cổng mặc định
  theo `.env`, khuyến nghị 5001). Chi tiết: `HUONG_DAN_LOCAL.md`.
- **Production**: VPS Vultr, mỗi release là thư mục riêng
  `/opt/search-tools-pg-release-<timestamp>-<commit>-clean`, service systemd
  cho web (Gunicorn) + service riêng cho import worker, Nginx phía trước.
  Xem quy trình deploy thực tế (preflight/prepare/cutover, khác tài liệu
  `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` hiện có) ở ARCHITECTURE.md mục Deploy.
- **Staging**: hạ tầng riêng thật, tách biệt production — web
  `search-tools-staging.service`, thư mục `/srv/search-tools`, database
  `search_tools_staging`. Quy trình chuẩn: xong 1 phase → deploy staging →
  UAT → đạt mới triển khai production.

## Git

- Remote: xem `git remote -v` (không ghi cố định ở đây để tránh lỗi thời).
- Branch chuẩn: `main`. Mỗi task dùng branch riêng
  (`codex/<mô-tả>` là quy ước đặt tên đã dùng cho các phase trước; có thể
  tiếp tục quy ước này hoặc đổi theo hướng dẫn mới trong
  `docs/DEVELOPMENT_WORKFLOW.md`).
- Không xóa các branch backup/lịch sử khi chưa được yêu cầu rõ.

## Bước tiếp theo được khuyến nghị

1. Spec `regulatory-manual-edit` v2 đã cập nhật theo duyệt có điều chỉnh
   ngày 2026-09-27. Dừng chờ yêu cầu tiếp; chưa có quyền implement lượt này.
2. PR #21 đã merge Golden Development Template; không cần thực hiện lại.
3. (Tùy chọn, ngoài phạm vi task tài liệu, cần yêu cầu rõ trước khi làm)
   - Xóa file `heroku` rỗng ở gốc repo — đã xác nhận có thể xóa.
   - Cân nhắc tắt `ENABLE_LEGACY_PASSWORD_LOGIN` trên production nếu xác
     nhận không còn cần thiết — thay đổi cấu hình production, cần yêu cầu
     rõ và thực hiện theo đúng SECURITY.md.
   - Cập nhật `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` cho khớp quy trình release
     thực tế (immutable release + preflight/prepare/cutover, staging trước
     production) — tài liệu hiện tại mô tả cách làm cũ/đơn giản hơn.
4. Nếu tiếp tục phát triển tính năng: đọc `docs/DEVELOPMENT_WORKFLOW.md` để
   áp dụng đúng quy trình theo mức rủi ro (LOW/MEDIUM/HIGH), đặc biệt lưu ý
   HIGH chỉ thực hiện trong OpenCode theo AGENTS.md.
5. Cân nhắc thiết lập CI (chạy `python -m unittest discover -s tests`) nếu
   muốn có kiểm chứng tự động trước khi merge — hiện chưa có, chỉ chạy thủ
   công.

Khi có thay đổi nghiệp vụ/kỹ thuật mới, cập nhật mục "Việc đang mở" và
"Vấn đề đã biết" ở trên trước khi coi checkpoint này là lỗi thời.
