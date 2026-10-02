# Hồ sơ release production — migration033 / `2ec9b6c` — 2026-10-02

**Kết quả: THÀNH CÔNG.** Production (`ssh python`) chạy bản `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, migration 033 đã COMMIT, PO kiểm tra ứng dụng đạt và cho nhân viên dùng lại. PO tự chạy mọi lệnh trên Mac; agent không truy cập production. Mọi thời gian theo UTC (giờ VN = UTC+7).

## Phê duyệt

- 06:33 — PO nói “đồng ý bắt đầu” (bộ artifact đã khóa 2026-09-30).
- Sau khi production lộ hai giả định sai trong script (xem dưới), PO nói “đồng ý sửa script triển khai”, cấp quyền (chế độ Manual), rồi nói lại “đồng ý bắt đầu” cho artifact đã sửa (~06:57).
- Trước cutover: PO xác nhận “đã báo nhân viên” ngừng nhập/sửa/xác nhận.
- Sau cutover: PO kiểm tra ứng dụng (đăng nhập, Quy tắc quản lý 5831 quy tắc, mở quy tắc #6 + lịch sử, trang thêm thủ công — không tạo dữ liệu) và xác nhận “đã kiểm tra ổn”.

## Artifact đã chạy (ngoài Git, `/Volumes/DATA/Development/_ops/search-tools/`)

| File | SHA256 | Ghi chú |
| --- | --- | --- |
| `preflight_readonly.py` | `6453e9010056aa46ca52567320705d589a2a772b1e23dd90e37d208fea941430` | Chỉ đọc, như 28/09 |
| `search-tools-regulatory-6155f25-to-2ec9b6c.bundle` | `3c9f83a474b3c891a81d95a90eccf573be0faf52f024803073154c339e91cddb` | Không đổi |
| `prepare_regulatory_staging.py` | `afc6e63ea37c655d9f743b8e6b957f67bd48da6f592ca0b1e3f0c853e8405d50` | Đã sửa 2 điểm, verifier P1–P6 PASS |
| `cutover_regulatory_staging.py` | `422274129971a01ec3bb0a25adcb27e2712e48ab3f036fafd27d1c20d694b194` | Đã sửa 2 điểm, verifier P1–P6 PASS |
| `test_production_adapter.py` | `51c6b91fbb1dab13d3e1f39fa614f9b3a43a9728abbae3ae85a4f04f3fd55a6b` | 45/45 OK |

Bản khóa gốc (prepare `dd668fb1…840d`, cutover `24042403…91e8`) giữ nguyên trong `locked-2026-09-30/`. Báo cáo verifier: [hiện hành](../../specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT.md), [lần 2](../../specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT_2026-10-02_attempt2_worker_exec_PASS.md), [P1–P6 bản khóa](../../specs/deploy-regulatory-manual-edit-production/VERIFICATION_RESULT_2026-09-30_P1-P6_locked.md).

## Hai lỗi lộ ra trên production (đã dừng an toàn ở kiểm tra chỉ đọc, không ghi gì)

1. **`PRODUCTION_LAUNCHER_CMDLINE`** — launcher exec sang chương trình thật; worker chạy là `[<release>/.venv/bin/python, <release>/scripts/import_worker.py]`, script khóa chỉ chấp nhận dạng launcher. Chẩn đoán: `scripts/production_diagnose_launcher_readonly.py`. Sửa: chấp nhận thêm đúng dạng đó (cả prepare và cutover, cả trước và sau chuyển bản).
2. **`LIVE_CHECKOUT_CLEAN`** — `git status` release cũ chỉ có `?? .venv` vì `.venv` là symlink tới venv dùng chung (`.gitignore` `.venv/` không khớp symlink). Chẩn đoán: `scripts/production_diagnose_checkout_readonly.py`. Sửa: chấp nhận đúng một mục đó khi `.venv` là symlink tới thư mục thật; kiểm release mới/sau chuyển bản vẫn đòi sạch.

Bài học: bộ kiểm P1–P6 trước đó chỉ local/mock, giả lập `/proc` và `git status` theo giả định — xem memory “verify with real tools”. Script triển khai lần sau phải được thử với dữ liệu đo thật (chỉ đọc) trước khi khóa.

## Evidence từng bước

1. **Preflight (06:35):** web `search-tools-pg.service` + worker active/running, user `searchtools-pg`, cwd release `…20260927T013108Z-6155f25-clean`, commit `6155f25…`; cùng DB `searchtools_pg_r1_rollback_20260906_153842` 1098 MB, PG 14.24; 030/031/032 = t, 033 = f; queue 0; free 100.7 GiB.
2. **scp bundle:** 155 KB, 100%, vào `/tmp`.
3. **Prepare (~08:05, lần chạy thứ 3):** gates PASS, candidate PASS `2ec9b6c…` requirements unchanged; release mới `/opt/search-tools-pg-release-20261002T080450Z-2ec9b6c-clean`; diff launcher chỉ đổi đường dẫn release (web.py dòng 2/6/7, worker.py dòng 2/6×2/7); checkpoint `/var/backups/search-tools-production/regulatory-manual-edit-2ec9b6c/prepared.json`.
4. **Cutover:** gate PASS, counts products|stock|rules|inactive|statuses = 1106803|874|5831|0|7, port 5001; dừng worker rồi web (queue 0); backup `/var/backups/search-tools-production/regulatory-manual-edit-2ec9b6c/prechange.dump` 32.077.412 byte, `pg_restore --list` PASS (chưa phải bằng chứng phục hồi); migration 033 COMMIT, schema/backfill/counts PASS; live release `2ec9b6c`; web + worker cùng DB, schema 033; `PRODUCTION TECHNICAL CUTOVER PASS … login=200, asset hash match, business counts preserved`. Gián đoạn thực tế: từ dừng worker tới khởi động lại (vài phút, chưa có mốc giây chính xác trong output).
5. **Postflight (chỉ đọc):** web pid 1954953 + worker pid 1954954 active, cwd release mới, commit `2ec9b6c`; cùng DB; 030–033 = t; queue 0; free 100.6 GiB; backup mới nhất 08:09:10Z.
6. **UAT PO:** đạt. Góp ý: giao diện trang quản trị quy tắc xấu → phase riêng.

## Còn lại / giới hạn

- Sau 033 không quay về bản cũ; lỗi phát sinh → sửa tiến tới; phục hồi từ backup cần PO duyệt riêng.
- Release cũ `…6155f25-clean` và launcher `…-launchers-phase6d9` giữ nguyên trên máy (không xóa). File bundle trong `/tmp` vô hại.
- Rủi ro S1 (nhận diện web qua fallback `search:app`) vẫn là rủi ro đã chấp nhận, không phải đã sửa.
- Đợt B (PR3–PR5) nay đủ điều kiện bắt đầu: PR3 lưu script đã chạy vào Git.
