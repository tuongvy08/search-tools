# Lịch sử trạng thái project — lưu 2026-09-30, phase regulatory-manual-edit / migration033

Nội dung bên dưới là bản nguyên văn của `PROJECT_STATE.md` trước khi thu gọn; các mốc cũ không phải trạng thái hiện hành.

# Trạng thái project

Cập nhật: 2026-09-29 (PO xác nhận UAT staging đạt; adapter production đạt
verifier P1–P6 local/mock, chờ PO duyệt; chưa chạy production).

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
Codex trước đây cho project này, lưu **cục bộ ngoài Git trên máy Product
Owner**; không giả định file này còn tồn tại trên máy khác. File đó ghi lại toàn
bộ lịch sử preflight/prepare/cutover từng phase, quyết định nghiệp vụ chi
tiết (đối chiếu với `SPEC_REGULATORY_STOCK.md` cùng thư mục — đã dùng để bổ
sung docs/BUSINESS_RULES.md) và mô hình deploy thực tế (xem ARCHITECTURE.md
mục Deploy). Theo lần cập nhật cuối cùng ghi nhận trong file đó (cũng ngày
2026-09-27), cutover production cho Phase 6D8+6D9 mới ở trạng thái "PREPARED,
AUTHORIZED, sẵn sàng cho người dùng chạy" — chưa có output xác nhận đã chạy
xong tại thời điểm đó; xác nhận của người dùng trong phiên tài liệu này (sau
đó cùng ngày) rằng production đã chạy bản mới nhất được hiểu là cutover đó
đã hoàn tất sau thời điểm file kia được ghi. Không tự suy ra thêm chi tiết
vận hành (tên release cụ thể...) ngoài hai nguồn này; đọc lại file đó
hoặc hỏi người dùng nếu cần mốc thời gian chính xác hơn.

Bối cảnh lịch sử xa hơn (không còn là rủi ro hiện tại, giữ lại để tránh nhầm
lẫn khi đọc phase docs cũ): `docs/phase6c3/OPERATIONS.md` (Phase 6C3, PR #6)
ghi **"PRODUCTION HOLD"** tại thời điểm đó và production khi đó đang ở
commit `24ac6d4` (PR #5) trên database tên có "rollback"
(`searchtools_pg_r1_rollback_20260906_153842`), tức từng có một khoảng trễ
giữa `main` và production quanh 2026-09-06 — khoảng trễ đó đã được thu hẹp
nhiều lần qua từng phase kể từ đó.

Golden Development Template đã merge qua PR #21 (`db26d40`); tính năng
regulatory-manual-edit đã merge qua PR #22 (`main@2ec9b6c`). Task vận hành
đang ở branch `ops/deploy-regulatory-manual-edit`, tạo từ `2ec9b6c`.

## Việc đang mở

### Trạng thái hiện tại — adapter production đã kiểm chứng, chờ PO duyệt

- PO đã xác nhận cơ chế production: release immutable, `.venv` dùng chung,
  launcher riêng, một drop-in mỗi unit chỉ đổi WD/ExecStart. PO chấp nhận
  S1 là rủi ro đã biết, không sửa. Được phép **hiệu chỉnh local hai script
  hiện có**, chưa được chạy server. Hợp đồng production adapter đã tạo
  trước sửa tại `specs/deploy-regulatory-manual-edit-production/VERIFICATION.md`.
  Đã hoàn thiện profile `--production`: prepare release sạch + copy launcher
  chỉ replace path + stage đúng hai drop-in; cutover sau COMMIT mới cài
  drop-in/reload/verify WD+ExecStart/start. Giữ EnvironmentFile, shared venv
  và release cũ. Recovery phân biệt chưa COMMIT/đã COMMIT/chưa rõ.
- **Kiểm chứng:** 30/30 adapter tests (gồm Git/bundle fixture thật local),
  10/10 fingerprint tests, 60/60 staging entrypoint checks; verifier độc lập
  **P1–P6 PASS local/mock**. Lượt đầu P6 FAIL: JS/file cùng drift sau start
  vẫn PASS; đã tái hiện bằng test đỏ, sửa và verifier chạy lại toàn bộ đạt.
  `attempt=1/2`, `same_claim_repeat_fail=false`, `next_action=HUMAN_REVIEW_WAIT_PO_APPROVAL`.
  Hợp đồng kiểm chứng không đổi; báo cáo trong spec production, evidence
  và human-review checklist trong `docs/ops-deploy-regulatory-manual-edit/VALIDATION.md`.
- Bundle production riêng ngoài Git: `_ops/search-tools/search-tools-regulatory-6155f25-to-2ec9b6c.bundle`,
  prerequisite duy nhất BASE6155f25; verify và prepare bằng Git fixture đạt.
  Runbook production đã cập nhật một trang/năm thao tác do PO tự chạy, **không
  phải quyền thực thi**. Script ops/test/bundle vẫn ngoài Git. Không sửa app/
  migration/feature tests, không SSH/scp/systemctl/DB server, không commit/push.
- **Bước tiếp theo:** PO review 5 câu và residual risk trước phê duyệt [GHI].
  Đặc biệt: S1 accepted nhưng chưa sửa; worker có thể nhận job ngay sau start
  trước postflight, cần cửa sổ không ghi/upload/queue0; approval staging cho
  rủi ro startup không tự áp dụng production. Không tự chạy lại staging.

### Mốc trước adapter production — giữ lịch sử, không dùng làm trạng thái mới

- PO xác nhận UAT staging **đạt**. Đã ghi ngắn vào VALIDATION.md và soạn
  `docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RUNBOOK.md` tối đa một
  trang, chưa chạy server. Preflight dùng lại được; prepare/cutover vẫn là
  staging in-place, không thể chỉ thay tham số để chuyển release immutable.
  Draft đánh dấu các lệnh ghi **chưa kích hoạt**; cần hiệu chỉnh tối thiểu
  hai script hiện có và chốt S1 trước production. Không tự sửa guard/script
  production trong lượt chỉ soạn tài liệu, không commit/push.

- PO báo backup staging OK **37.3 MB**, `pg_restore --list` PASS; migration
  033 **COMMIT**, live code đã chuyển `2ec9b6c`. Cutover dừng ở
  `ENVIRONMENT_SOURCE_UNCHANGED` trước start do fingerprint chứa trường
  runtime trong output ExecStart, không phải cấu hình thay đổi.
- PO đã duyệt và thực hiện phục hồi: inspect thấy `code_switched`,
  `migration_committed=True`, live target/backup/schema đúng; start lại cả
  hai service **bản mới**. Postflight theo PO: web/worker active, commit
  `2ec9b6c`, cùng DB `search_tools_staging`, 030–033 có, job 0, login HTTP 200.
- Lượt sửa trước chỉ sửa fingerprint/test trên Mac, rà các so sánh khác và ghi
  sự cố/runbook. **Không chạy lại prepare/cutover/migration/cleanup**, không
  lệnh server; UAT staging sau đó đã được PO xác nhận đạt.
  Milestone có thể còn `code_switched` sau phục hồi thủ công; không sửa nó
  thành PASS hoặc xóa artifact chỉ để chạy lại script.
- Fingerprint đã sửa chỉ lấy cấu hình ổn định của ExecStart cùng các nguồn
  environment/unit và metadata; **9/9 tests fingerprint + 60 gate checks
  local PASS**, tái hiện được FAIL của logic cũ trước sửa. Đã rà mọi so sánh
  khác; không thấy cùng loại lỗi runtime, không nới gate. Verifier xác nhận
  correction fingerprint local đạt, nhưng phát hiện riêng **S1 FAIL**:
  guard web chấp nhận script khác khi `search:app` chỉ là đối số phụ.
  Không tự sửa ngoài scope/yêu cầu trước đó giữ web; không suy staging
  thực tế sai. Tại mốc đó chờ PO chốt S1; `next_action=ESCALATE_USER`,
  attempt ops verifier=0/2, chưa repair S1. Chưa chạy lại script staging,
  không commit/push; production hiện chỉ có draft chưa cho phép bước ghi.

### Lịch sử chuẩn bị — không dùng như bước phải chạy lại

- PO báo prepare staging dừng ở `read-only gates`: worker dùng Python venv
  versioned và entrypoint tuyệt đối; web đã đạt, Git baseline sạch, chưa có
  candidate/checkpoint, bundle đã có trên staging. Đây là kết quả PO cung
  cấp, không phải agent chạy server. Đã sửa hai script để so realpath
  worker Python với `.venv/bin/python`, nhận entrypoint relative/absolute,
  in tên gate an toàn. Chạy lại theo runbook bỏ bước 4–5, bắt đầu bước 6;
  không cleanup/scp lại, không chạy server trong lượt sửa này. Cú pháp và
  30 mô phỏng gate local mỗi script (60 tổng) PASS; review tĩnh scope đạt.
  Helper mới áp dụng cả kiểm tra worker trước và sau start; web giữ nguyên.

- Nhánh `ops/deploy-regulatory-manual-edit` ở `main@2ec9b6c`; working tree
  **chưa commit** gồm cập nhật file này, bản nháp
  `docs/ops-deploy-regulatory-manual-edit/` và hợp đồng HIGH tại
  `specs/deploy-regulatory-manual-edit-staging/VERIFICATION.md`. Không reset,
  xóa hoặc tự sửa hợp đồng sau khi bắt đầu thực thi runbook.
- PO đã cung cấp preflight hai máy riêng (chi tiết bên dưới). PG14.24 local
  tạm đã đạt **24/24 test phase + 81/81 hồi quy + 2 DOM, 0 skip**;
  container tạm đã dừng/tự xóa, không chạm DB local gốc hoặc UAT.
- Đã **soạn nhưng CHƯA CHẠY** runbook STAGING: `STAGING_RUNBOOK.md` trong
  thư mục docs trên. Script để PO review nằm ngoài repo ở
  `/Volumes/DATA/Development/_ops/search-tools/`: `prepare_regulatory_staging.py`,
  `cutover_regulatory_staging.py`, `inspect_regulatory_staging_after_interruption.py`;
  `preflight_readonly.py` của PO giữ nguyên. Chỉ kiểm tra cú pháp script và
  `git diff --check`; **không SSH, backup, stop service, migrate, deploy**.
- **PO đã chấp nhận residual risk worker nhận job ngay khi start CHỈ cho
  STAGING**, với điều kiện queue = 0 và không ai upload trong cửa sổ deploy.
  Giữ gate kiểm tra DB/config/queue; quyết định không áp dụng production.
- Đã sửa trước thực thi: prepare dùng bundle do Mac tạo/verify, scp qua
  alias staging thay HTTPS (server không có credential); cleanup chỉ đúng
  candidate/checkpoint/bundle khi prepare dừng, không xóa backup/milestone.
  UAT staging chỉ rule giả `UAT-STG-` và upsert; không replace_scoped, sau
  UAT ngừng áp dụng rule giả. Không SSH/scp hoặc lệnh nào trên server lượt này.
- Đã tạo bundle `search-tools-regulatory-a8b4cf4-to-2ec9b6c.bundle` và file
  `uat-stg-regulatory-upsert-2ec9b6c.xlsx` trong `_ops/search-tools/` ngoài repo.
  Bundle verify + fetch thử từ repo tạm chỉ có baseline đạt; workbook đúng
  ba mã giả/ba cột/một sheet, không công thức. Cleanup script chỉ đúng ba
  artifact task, chặn nếu cutover đã bắt đầu. Syntax/static review đạt;
  runbook đã có mục đầu “Anh cần chạy gì”. Không sửa app/test/SQL hoặc
  hợp đồng kiểm chứng; dừng chờ PO đọc bản cập nhật, chưa thực thi staging.
- **Bước kế tiếp:** đọc `git status`/diff cùng checkpoint này, PO review
  runbook/script cập nhật, chạy lại preflight chỉ đọc ngay
  trước mọi bước ghi; chỉ sau phê duyệt riêng mới cân nhắc Prepare/Cutover
  staging. Chưa lập/chạy production runbook. Hai việc sửa tài liệu cuối
  task được ghi bên dưới, chưa làm.

- **HIGH — ops/deploy-regulatory-manual-edit — PREFLIGHT CHỈ ĐỌC, chưa deploy.**
  Nhánh tài liệu riêng tạo từ `main@2ec9b6c` (PR #22 đã merge theo Git).
  PO đã cung cấp kết quả preflight 28/09: staging trên máy riêng, web/worker
  active user deploy cwd `/srv/search-tools`, commit `a8b4cf4` (tree giống
  `6155f25`); DB staging ~1171 MB PostgreSQL 16.15; 030–032 có, 033 chưa;
  không có job queued/running; đĩa trống ~56.7 GB. Production máy khác,
  commit `6155f25`, PostgreSQL 14.24, DB production đang chạy phải giữ nguyên
  tên; 030–032 có, 033 chưa; job 0, ~103.3 GB trống. Không đụng service
  Dify/n8n hoặc service Search-tools cũ ngoài phạm vi. Agent không SSH/server.
  Lượt này: test phase/hồi quy trên PostgreSQL 14 **local container tạm riêng**
  (không chạm DB local gốc hoặc `search_tools_uat`); soạn runbook STAGING
  có [CHỈ ĐỌC]/[GHI], backup/kiểm tra restore list/gate/rollback/UAT.
  PG14.24 local đã kiểm tra: 24/24 phase + 81/81 hồi quy + 2 DOM PASS,
  0 skip; container riêng bind loopback đã dừng và tự xóa (--rm). Runner
  test-scoped ngoài repo, không đổi app/tests/SQL hay DB của người dùng.
  Runbook review draft: `docs/ops-deploy-regulatory-manual-edit/STAGING_RUNBOOK.md`;
  ba script prepare/cutover/inspect gián đoạn để PO review nằm ngoài repo ở
  `_ops/search-tools/`, preflight_readonly.py của PO giữ nguyên. Evidence và
  giới hạn: `docs/ops-deploy-regulatory-manual-edit/VALIDATION.md`.
  **Không chạy bất kỳ bước [GHI] staging**, chưa soạn lệnh production;
  đang chờ PO review/gate, xin phép riêng trước cutover.
  **Ghi nhận sửa cuối task, chưa sửa:** hướng dẫn deploy còn đường dẫn Mac
  cũ; PROJECT_STATE mục Vấn đề đã biết còn nói cập nhật hướng dẫn deploy là
  việc chưa làm dù PR #21 đã hoàn tất. Không tiện thể sửa trong lượt này.

- `feature/regulatory-manual-edit`: code hoàn tất, hợp đồng spec v2 khóa ở
  commit `ee2ee5a`; verifier độc lập **PASS 20/20 claim** sau 1 repair.
- PO đã nghiệm thu local **8/8 chức năng + 4/4 hiển thị**. Bộ test phase
  cuối: **24 tính năng + 81 hồi quy + 2 bộ DOM PASS**, không skip.
- Migration 033 và rollback có guard đã kiểm tra trên database test tạm;
  chưa chạy trên staging/production. Chi tiết evidence, lịch sử repair và
  UAT: `docs/regulatory-manual-edit/VALIDATION.md` và `UAT.md`.
- Bước kế tiếp: review PR → được phép merge → lập task HIGH riêng để deploy
  staging rồi production sau preflight/backup/UAT và phê duyệt tương ứng.
  **Chưa merge/deploy hoặc thao tác database/hạ tầng server.**
- Phase sau đề xuất (chưa triển khai): **Xuất danh mục quy tắc ra Excel**;
  cần chốt phạm vi xuất. Phase sau đề xuất: **Thiết kế lại giao diện quản trị**.

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

1. PO review adapter/runbook production đã qua verifier local P1–P6, phê
   duyệt rủi ro và cửa sổ [GHI] trước tự chạy. Feature PR #22 đã merge và UAT
   staging đã đạt, không lặp lại các bước này. Hai đề xuất phase sau giữ ở
   "Việc đang mở", chưa triển khai. Không suy quyền deploy từ quyền sửa local.
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
