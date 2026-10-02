# Checkpoint tạm nghỉ — 2026-10-01

> **ĐÃ LỖI THỜI từ 2026-10-02:** PO tiếp tục bằng Claude Code (Hướng A), V1/V2 đã PASS local/mock, hash archive đã sửa. Các hash/claim FAIL bên dưới là lịch sử; trạng thái hiện hành xem `PROJECT_STATE.md` mục Tiếp nối nhanh.

PO yêu cầu lưu checkpoint để quay lại sau. **PAUSED/BLOCKED, không DONE/READY.** Không tiếp tục sửa/thao tác khi chưa có chỉ đạo tiếp. Không có bước ghi trên staging được phát; chưa có DB tạm do task tạo, không có gì cần cleanup. Production NOT RUN.

## Git và file local

- Repository `/Volumes/DATA/Development/Search-tools`; branch `ops/staging-restore-rehearsal`, HEAD/main local `207cd11` (PR25 đã merge).
- Chưa commit/push/PR task rehearsal. `PROJECT_STATE.md` modified; task specs, hai script mới, tests developer/independent vẫn untracked nhưng đã ghi ra ổ đĩa. Không discard/restore/reset hoặc đổi branch làm mất nội dung.
- `opencode.json` là file có sẵn từ việc ngoài phạm vi, không đọc/sửa/stage.
- Mở `PROJECT_STATE.md` mục Tiếp nối nhanh → `git status`/`git diff` → hồ sơ task này. Có file untracked nên không chỉ đọc tracked diff; đọc trực tiếp các file mới liên quan.

## Evidence staging do PO tự chạy — không rerun tự động

Các lệnh/kết quả S0.1–S0.7 được ghi ở `OPERATIONS.md`. Agent không tự SSH/DB.

- Archive có sẵn `/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump`: 37.333.943 byte, mtime `2026-09-29 08:03:10.692114220+00`, SHA256 `cc021ca2848d75b98ec103726d9d42e1cabb3e39df36aa6d03e04b9789b7473f`; TOC đúng staging/PG16.15, 376 entries, bốn bảng chính có TABLE/TABLE DATA. Được tạo **trước033**; TOC không chứng minh restore/counts backup.
- Cảnh báo locale ở S0.3 đã được báo PO; PO đồng ý dùng LC_ALL=C/LANG=C chỉ từng process kế tiếp, không sửa server config và không rerun bước đã xong.
- S0.4: hai unit staging active/deploy/cwd `/srv/search-tools`, commit `2ec9b6c940c89802cce9fc77150e4f2b8dcfff64`, cùng runtime DB `search_tools_staging`, host127.0.0.1/port5432. Script có khả năng che warning được verifier phát hiện về sau; không suy mọi subprocess thật không warning từ marker.
- S0.5/S0.7: peer PG16.15, data directory `/var/lib/postgresql/16/main`, read-only on; S0.7 UTC `2026-10-01 06:45:30.598206+00`, PID TCP và postmaster3993835 khớp. 030–032 và chín cờ033 đều true. UTF8/libc (`c`), collate/ctype en_US.UTF-8, pg_default.
- Source counts hiện tại: products1262173, stock_items2295, rules6003, statuses6, inactive3. Đây **không** phải counts trước dump. Chênh nhỏ có giải thích với DB phục hồi được ghi/đi tiếp; thiếu/rỗng/bất thường lớn dừng báo PO.
- Source size1228176407 byte. Data filesystem available60691943424 byte tại S0.6, vượt max(5GiB,4×source)=5368709120. Snapshot cũ không chứng minh đủ đĩa khi quay lại; cập nhật gate trước bước ghi.

## Blocker và hợp đồng đã khóa

- Hợp đồng `VERIFICATION.md` v1 SHA256 `0a06c96414b9f09bb4b1d780f1eed187349a01844636f83311834574a857cc79`; không tự sửa oracle/claim để pass.
- Báo cáo hiện hành `VERIFICATION_RESULT.md`: **V1/V2 IMPLEMENTATION_FAIL**, chỉ local/mock, không khẳng định sự cố trên server.
- V1: script identity S0.4 bỏ/che warning stdout/stderr rồi báo đạt. V2: script create chấp nhận warning cùng dòng listener và vẫn CREATE/metadata write trong mock.
- Các ca warning version/create/ACL cũ đã PASS; không gọi lỗi cũ là blocker hiện hành. Không dùng developer13/13 hoặc bộ độc lập cũ15/15 PASS thay kết quả mới FAIL.
- Test tái hiện hiện hành `tests/independent/test_staging_restore_additional_output_adversarial.py`; report có Observed/Expected/Reproduction/Classification để gọi verifier context sạch. Chỉ đưa đúng template workflow mục11, không chia sẻ reasoning/cách sửa.
- attempt4 / max_auto_repairs2; same_claim_repeat_fail=V2. Repair3/4 được PO chỉ đạo sau escalation, không reset ngân sách. **Chưa có chỉ đạo repair5**, yêu cầu lưu checkpoint không phải quyền sửa tiếp.

## Artifact task và phạm vi còn lại

- Create script FAIL `scripts/staging_restore_create_temp.py`: SHA256 `56e057535f1a1730d94f18c931367ec99c0fe2ceb4bbf498535d3f25ea42fc16`.
- Identity script `scripts/staging_restore_identity_readonly.py`: `1d3ab0f853046fd3ecf546e11bc4a819bf2c9dddc256e4a353807b12faebc489`.
- SOURCE_CHECK.sql: `05a868759de8611451bd3aec060fc2b6d70bce31f5ed54943a41fe9b394f2951`.
- Tên DB tạm chỉ dự kiến `search_tools_restore_test_20261001_064530`; lệnh create chưa đưa PO, không chạy lệnh có sẵn trong bản draft bị BLOCKED.
- Restore/đối chiếu DB tạm/drop chưa chuẩn bị hoặc chạy. Thời gian restore thực tế chưa có. Không thêm033 vào DB phục hồi; expected đúng snapshot trước033, kiểm live033 riêng bằng chỉ đọc.
- Giữ nguyên preflight/prepare/cutover/bundle cũ, specs/evidence deploy đã khóa. Task chỉ staging, không prod; không backup mới, không ghi đè DB ứng dụng, không dừng dịch vụ; xóa DB tạm phải hỏi PO riêng nếu sau này đã tạo.

## Bước đầu khi quay lại

1. Đọc checkpoint/state/diff, không tự chạy lại lệnh server hoặc sửa code.
2. Xác nhận PO muốn tiếp tục và cho phép xử lý blocker V1/V2 trên **task scripts**, không artifact deploy đã khóa; giữ hợp đồng, budget và evidence fail.
3. Chỉ review/test các claim/artifact bị ảnh hưởng rồi verifier độc lập. Nếu còn FAIL dừng theo workflow, không hạ gate.
4. Chỉ sau safety gate đạt mới xem xét một lệnh ghi staging; cập nhật runtime/instance/archive/hash/disk/collision vì đã nghỉ. PO tự chạy từng lệnh, không có quyền SSH ngầm cho agent.
5. Sau restore thực/no errors và đối chiếu đạt, hỏi riêng trước xóa; sau hoàn tất cập nhật phiếu/state thời gian thực tế và PR riêng như đã giao. Hiện **chưa tới checkpoint hoàn tất để commit/PR**.
