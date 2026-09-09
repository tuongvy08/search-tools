# Phase 6C3 validation — 2026-09-09

**RELEASE HOLD — READY FOR CODE REVIEW.** Không deploy/migrate/mutate staging
hoặc production trước khi read-only staging preflight được duyệt.

## Phạm vi và cô lập

- Base `24ac6d4860e401efb2ba9c8e490ac204dad770dc`, branch
  `codex/phase6c3-product-management`, worktree riêng.
- Local PostgreSQL chỉ tạo database ngẫu nhiên có prefix
  `p6a_release_gate_pgtest_`. Full suite dùng ambient database name cố ý không
  tồn tại, nên entrypoint nào không tự chuyển sang DB test sẽ fail thay vì chạm
  `products_local`.
- Browser UAT dùng một DB giả riêng, sau đó server dừng và DB được drop. Kiểm tra
  cuối: `0` database test còn lại. Không truy cập VPS/staging/production.

## Kết quả

- Focused product-management cuối: **10/10 pass** trên PostgreSQL thật. Bao phủ keyset
  pagination/search/canonical filter, create/update, validation, optimistic
  revision, chỉnh note/price theo selected ID giữa same-code variants, từ chối
  identity collision/ambiguous create, admin/staff/CSRF, actor revoke sau khi chờ advisory lock, durable
  cross-worker preview, stale/replay/TTL, single/brand delete, audit, backup đầy
  đủ compliance/preparation/source, positive restore và fail-closed sau real
  Import Center quick upsert cùng logical identity; backup vẫn nguyên.
- Regression gần scope cuối: **51/51 pass** (`admin_products`, admin nav, Import
  Center, import concurrency). Lần chạy này diễn ra sau self-review sửa lifecycle
  và independent-review fixes.
- Migration 025 chạy **hai lần** bằng production-style
  `psql -X -v ON_ERROR_STOP=1 -f`, không `--single-transaction`: pass. Regression
  còn tạo deterministic invalid concurrent index, xác nhận migration fail closed,
  rồi explicit drop/rerun phục hồi index `(brand,id)` valid: pass.
- JavaScript syntax, Python compile, `git diff --check` và `bash -n` cho staging
  command block: pass.
- Full suite chạy đúng một lần với `DISABLE_IP_ALLOWLIST=1` và DB guard:
  **944 pass + 3 optional-fixture skips (947 total), 0 failure**, 62.893 giây.
  Các sửa sau full suite chỉ giới hạn ở lifecycle connection, identity/restore,
  migration 025 và runbook; focused regression liên quan đã chạy lại sau tất cả
  blocker fix. Không lặp full suite theo yêu cầu.

Ba skip là fixture tùy chọn có sẵn của quote template, không liên quan Phase 6C3.
Suite để lại một DB test 8.7 MB không có active connection từ test legacy; đã xác
minh đúng prefix, drop thủ công và kiểm tra lại còn `0` DB test.

## Browser gate

- 1440×900: 50 rows, document width 1440, table không tràn viewport, admin nav
  đánh dấu đúng trang.
- 390×844: filter xếp một cột, action rõ, body width 390; bảng giữ vùng scroll
  ngang riêng (`clientWidth 358`, `scrollWidth 972`) và document x-axis bị
  containment; form detail 14 controls, nav active đúng.
- Delete preview: canonical brand TRC đếm 21 rows trên fixture, phrase xác nhận
  `XOA 21`, apply disabled trước khi nhập đúng phrase. Không bấm apply trong
  browser UAT.

Ảnh:

- `phase6c3-products-desktop-1440.png`
- `phase6c3-products-mobile-390.png`
- `phase6c3-product-detail-mobile-390.png`
- `phase6c3-products-delete-preview.png`

## Ghi chú thiết kế/review

- List dùng signed keyset cursor theo ID và index `(brand,id)`; không dùng OFFSET.
  Search text tiếp tục tận dụng trigram indexes hiện có cho name/code/CAS.
- Mutation luôn lấy product advisory lock trước câu SQL đầu tiên trong transaction,
  rồi recheck `ACTIVE`/admin/`auth_version`. Update so `xmin`; delete preview nằm
  trong PostgreSQL, TTL 5 phút, actor/auth-bound, one-time và fingerprint toàn row.
- Apply re-fingerprint, lock/copy rows database-side, so fingerprint bản copy rồi
  xóa exact IDs. Không xóa/sửa Brand Master, aliases, team grants, regulatory rules
  hay import history. Audit chỉ lưu actor/action/scope/count/changed-field names,
  không lưu secret/DSN.
- Form giữ đúng manual compliance/preparation semantics; blank manual status bỏ
  override để resolver tự động tiếp tục hoạt động. Không tự tạo brand, đổi currency,
  dedup policy hay regulatory rules từ trang này.
- Migration kiểm tra `pg_index` trước/sau concurrent build. Index cùng tên invalid
  hoặc sai definition không bị `IF NOT EXISTS` che khuất; operator phải inspect và
  recovery tường minh. Runbook dùng `psql --dbname="$LIVE_DATABASE_URL"`, xác minh
  database thực của PID web/worker và không in DSN.
