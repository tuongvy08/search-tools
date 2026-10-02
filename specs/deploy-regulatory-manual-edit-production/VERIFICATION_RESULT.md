# Verification Result — deploy-regulatory-manual-edit-production (lần 3: dung sai `?? .venv` của release cũ)

Do verifier độc lập tạo (context sạch), không do agent chính viết. Chỉ chạy local: Python/mock,
Git thật trong thư mục tạm; không SSH, không kết nối server, không commit/push. Không dùng
PROJECT_STATE.md, comment trong code hay các file `VERIFICATION_RESULT_*.md` khác làm bằng chứng.

## Đầu vào đã khóa (SHA256 xác nhận khớp)

| File | SHA256 |
| --- | --- |
| `VERIFICATION.md` (hợp đồng P1–P6) | `81ff2ae8c08fec25d0b70c79029f2cea5da7ba16d4835ade991633599e2a33ab` |
| `_ops/search-tools/prepare_regulatory_staging.py` | `afc6e63ea37c655d9f743b8e6b957f67bd48da6f592ca0b1e3f0c853e8405d50` |
| `_ops/search-tools/cutover_regulatory_staging.py` | `422274129971a01ec3bb0a25adcb27e2712e48ab3f036fafd27d1c20d694b194` |
| `_ops/search-tools/test_production_adapter.py` | `51c6b91fbb1dab13d3e1f39fa614f9b3a43a9728abbae3ae85a4f04f3fd55a6b` |
| `locked-2026-09-30/prepare…` / `cutover…` | `dd668fb1…` / `24042403…` |

Test độc lập mới: `tests/independent/test_production_live_venv_status_realgit.py` (14 test, Git thật).

## Lệnh đã chạy và kết quả

```
cd _ops/search-tools && .venv/bin/python -B -m unittest test_production_adapter test_cutover_fingerprint
  -> Ran 45 tests ... OK
.venv/bin/python -B -m unittest discover -s tests/independent -p test_production_live_venv_status_realgit.py
  -> Ran 14 tests ... OK            (Python 3.9.6; lặp lại với Homebrew Python 3.12: OK)
.venv/bin/python -B -m unittest discover -s tests/independent -p test_production_launcher_exec_cmdline_adversarial.py
  -> Ran 26 tests, FAILED (failures=6)
```

6 lỗi của bộ độc lập cũ đều nằm trong 2 test ghim phiên bản
(`test_hashes_of_reviewed_artifacts_and_locked_contract`, `test_diff_against_locked_only_adds_worker_exec_condition`):
chúng ghim hash b8e4…/dcc5… và tập dòng diff của lần sửa trước, nên fail đúng như dự kiến khi artifact
được thay. Toàn bộ 20 test hành vi còn lại (nhận diện worker exec, staging giống bản khóa, cutover E2E
qua /proc giả) PASS → không hồi quy hành vi. Phần ghim phiên bản/diff đã được thay bằng
`ArtifactsAndDiffScope` trong file test mới (hash mới + phạm vi diff mới).

Kiểm tra độ nhạy (mutation, chạy ad hoc trong scratchpad, không sửa artifact): 4 bản đột biến đều bị
test mới bắt — (M1) gate chấp nhận mọi status; (M2) bỏ kiểm symlink→thư mục; (M3) cho staging dung sai
`?? .venv`; (M4) bọc thêm kiểm `LIVE_TARGET_CLEAN` sau chuyển bản.

## Thay đổi so với bản khóa (diff thực tế)

Ngoài phần nhận diện worker exec (đã qua verifier trước, không đổi), mỗi script thêm đúng một hàm
`live_checkout_status_clean(status)`: rỗng → chấp nhận; nếu không phải `PRODUCTION` hoặc status khác
đúng chuỗi `?? .venv` → từ chối; còn lại chỉ chấp nhận khi `LIVE/.venv` là symlink và
`resolve(strict=True)` là thư mục (bắt `OSError`/`RuntimeError` → từ chối). Hàm được dùng ở đúng 3 chỗ:

| Script | Gate | Qua hàm mới? | Đối tượng |
| --- | --- | --- | --- |
| prepare | `LIVE_CHECKOUT_CLEAN` (read-only gate) | Có | release cũ |
| prepare | `PRODUCTION_OLD_RELEASE_UNCHANGED` (cuối prepare) | Có | release cũ |
| prepare | `CANDIDATE_CLEAN`, `PRODUCTION_RELEASE_CLEAN` | Không (vẫn yêu cầu rỗng) | release mới |
| cutover | `LIVE_CHECKOUT_CLEAN` (trước khi dừng dịch vụ) | Có | release cũ |
| cutover | `CANDIDATE_CLEAN`, `LIVE_TARGET_CLEAN` (sau chuyển bản) | Không (vẫn yêu cầu rỗng) | release mới |

Đã kiểm bằng AST (`test_only_old_release_status_checks_are_relaxed`) và bằng diff dòng
(`test_textual_diff_vs_locked_is_worker_exec_plus_status_helper_only`).

## Ma trận Git thật (Observed)

Repo tạm = checkout thật commit `6155f25` (lấy từ repo local; `.gitignore` có `.venv/`, `venv/`), tên thư mục
giống LIVE production, `.venv` là symlink tới venv dùng chung; chạy thật
`git status --porcelain --untracked-files=all` qua chính hàm `checked()` (có `.strip()`) rồi đưa vào
`live_checkout_status_clean` của cả prepare và cutover; config Git toàn cục bị cô lập
(`GIT_CONFIG_GLOBAL=/dev/null`).

| Trạng thái checkout | Output git (raw) | Gate mới |
| --- | --- | --- |
| Production thật: `.venv` symlink → venv dùng chung | `?? .venv\n` (check-ignore rc=1) | CHẤP NHẬN |
| + file lạ / file lạ trong thư mục con / file staged | thêm dòng | TỪ CHỐI |
| + sửa file tracked / xóa file tracked / sửa `.gitignore` | thêm ` M`/` D` | TỪ CHỐI |
| + symlink lồng `scripts/.venv` | thêm `?? scripts/.venv` | TỪ CHỐI |
| + file `.venv ` (khoảng trắng cuối), `.venv\r`, tên có xuống dòng `x\n?? .venv`, `.venv_` | git quote tên: `?? ".venv "`, `?? ".venv\r"`, `?? "x\n?? .venv"`… | TỪ CHỐI |
| `.venv` là file thường | `?? .venv` | TỪ CHỐI (không phải symlink) |
| `.venv` symlink → file | `?? .venv` | TỪ CHỐI |
| `.venv` symlink hỏng | `?? .venv` | TỪ CHỐI |
| `.venv` symlink vòng (tự trỏ / hai bước) | `?? .venv` | TỪ CHỐI (RuntimeError được bắt; 3.9 và 3.12) |
| `.venv` là thư mục thật | rỗng (bị `.venv/` ignore) | CHẤP NHẬN ở gate này — xem P2 |
| `.venv` symlink → venv KHÁC / → `venv/` bị ignore trong LIVE / → `/` | `?? .venv` | CHẤP NHẬN — xem P2 (SPEC_AMBIGUOUS) |

Vi phân với bản khóa: mọi trạng thái bản khóa chấp nhận (status rỗng) vẫn được chấp nhận; trạng thái
mới được chấp nhận chỉ có dạng `status == '?? .venv'` và `LIVE/.venv` là symlink tới một thư mục.
Profile staging: với mọi trạng thái, kết quả = `status == ''` (giống hệt bản khóa).

## Claim P1 — Profile và launcher đúng phạm vi

Test thực hiện: bộ agent (45) + bộ độc lập cũ phần hành vi (20) + test mới: diff/AST phạm vi, ma trận
staging, E2E prepare đọc /proc giả với dạng exec thật (web gunicorn, worker `[LIVE/.venv/bin/python, LIVE/scripts/import_worker.py]`).
Kết quả: PASS
Evidence (Observed): `test_production_adapter` 45/45 OK; 20/20 test hành vi của bộ độc lập cũ OK
(gồm ma trận worker exec ~28 dạng sai bị từ chối, vi phân với bản khóa, staging giống bản khóa).
Profile staging của `live_checkout_status_clean` trả về đúng `status == ''` trên toàn bộ ma trận Git thật.
E2E: prepare hiện tại qua read-only gates với hai dạng exec thật + `?? .venv`.
Expected: không flag → staging, hành vi cũ; `--production` → đúng unit/user/DB/PG14; launcher/exec đúng role và release.
Negative case đã thử: đột biến M3 (staging dung sai `?? .venv`) bị bắt; các dạng worker sai (release khác,
script khác, python khác, `-c`, dạng web trên worker…) vẫn `PRODUCTION_LAUNCHER_CMDLINE`.
Residual risk: S1 (fallback web) vẫn là rủi ro đã được PO chấp nhận, không được sửa (đúng hợp đồng).

## Claim P2 — Prepare bất biến và giữ nguyên venv

Test thực hiện: E2E prepare main thật với Git thật (clone từ LIVE giả, fetch bundle thật
`search-tools-regulatory-6155f25-to-2ec9b6c.bundle`, checkout `2ec9b6c`), LIVE ở trạng thái production thật
(`?? .venv` + worker exec); chỉ `sudo` bị bỏ, systemctl/`/proc` giả.
Kết quả: PASS
Evidence (Observed): `PREPARE complete`; candidate HEAD = `2ec9b6c9…`; `git status` candidate = rỗng (nhờ
`/.venv` trong `.git/info/exclude` của candidate); `git status` LIVE trước/sau đều `?? .venv\n`, HEAD và
đích symlink không đổi; candidate `.venv` resolve = venv dùng chung; không tạo `SYSTEMD_ROOT`; không có lệnh
systemctl nào ngoài `show`.
Bản khóa với cùng LIVE (dùng dạng launcher để vượt gate cmdline) tái hiện đúng lỗi production lần 2:
`PREPARE STOP at read-only gates gate=LIVE_CHECKOUT_CLEAN`, không tạo candidate.
Expected: release cũ hợp lệ (chỉ symlink venv chưa ignore) qua kiểm sạch; mọi thay đổi khác bị chặn; không ghi đè.
Negative case đã thử: file lạ / sửa / xóa file tracked / symlink `.venv` hỏng trong LIVE → dừng ở read-only gates,
không tạo candidate/checkpoint. File lạ xuất hiện trong LIVE giữa chừng (sau clone) → `PRODUCTION_OLD_RELEASE_UNCHANGED`,
không có `prepared.json`. `.venv` là thư mục thật → status rỗng nên QUA gate đọc, bị chặn sau đó tại
`SHARED_VENV_SOURCE` (sau khi đã tạo thư mục candidate; không checkpoint, không systemd, LIVE nguyên) — hành vi
giống hệt bản khóa, không phải hồi quy.
Classification (quan sát, không làm FAIL claim): SPEC_AMBIGUOUS — `.venv` symlink trỏ tới BẤT KỲ thư mục nào
(venv của ứng dụng khác, `venv/` bị ignore nằm trong LIVE, `/`) đều qua gate status. Trong luồng đầy đủ, nếu thư
mục đó có `bin/python` resolve được thì prepare PASS và release mới thừa hưởng đúng đích đó
(`test_prepare_accepts_venv_retargeted_to_another_venv`: PASS với venv khác). Không có gì neo `.venv` vào "venv
dùng chung" cụ thể: so sánh realpath python giữa ExecStart, `/proc/<pid>/cmdline` và `LIVE/.venv/bin/python`
là so một đường dẫn với chính nó (cùng chuỗi `LIVE/.venv/bin/python`). Hợp đồng P2 chỉ yêu cầu "cùng đích cũ"
(thỏa mãn) và không ghi giá trị đích mong đợi, nên verifier không thể coi đây là vi phạm.
Reproduction: trong test mới, kịch bản `OBSERVED-ACCEPT: .venv symlink to ANOTHER venv dir` /
`… to "/"` / `… to ignored venv/ dir inside LIVE` (ma trận) và `test_prepare_accepts_venv_retargeted_to_another_venv` (E2E).
Residual risk: (1) đích symlink không được neo (trên); (2) file bị `.gitignore` bỏ qua (vd `venv/…`, `.env`)
không bao giờ hiện trong `git status` — có từ bản khóa; (3) `.venv` thật bị chặn muộn (sau khi tạo candidate);
(4) Git production (Ubuntu, phiên bản không xác nhận) chưa được chạy; local là Git 2.50.1. Việc quote tên
có khoảng trắng chỉ quan trọng khi `.venv` thật đồng thời bị ẩn — lỗi kép, không thấy đường khai thác.

## Claim P3 — Copy launcher chỉ đổi path, không lộ secret qua diff

Test thực hiện: bộ agent (copy bytes/permission/redact, symlink/non-Python/thiếu role/không tham chiếu) + E2E
prepare mới với launcher chứa chuỗi canary giả.
Kết quả: PASS
Evidence (Observed): test agent OK; E2E prepare: output không chứa canary, launcher mới tạo ở `<new>-launchers-rme`,
checkpoint drop-in chỉ ở thư mục checkpoint, `SYSTEMD_ROOT` không tồn tại sau prepare. Thay đổi lần này không chạm
`copy_production_launchers`/`dropin_content` (diff xác nhận).
Expected: như hợp đồng P3.
Negative case đã thử: canary trong launcher; launcher symlink/.env/thiếu worker (bộ agent) → dừng, không in secret.
Residual risk: không có thêm so với lần trước.

## Claim P4 — Thứ tự cutover và chuyển release có kiểm soát

Test thực hiện: E2E cutover main thật, nối tiếp từ checkpoint do prepare thật tạo; Git thật cho mọi lệnh
`as_deploy`/`git show`; `/proc` giả (worker exec, web gunicorn) trước và sau chuyển; systemctl/pg_dump/psql/HTTP giả.
Kết quả: PASS
Evidence (Observed): `PRODUCTION TECHNICAL CUTOVER PASS`; trace =
`stop:worker, stop:web, pg_dump, pg_restore, sql, reload, start:web,worker`; LIVE cuối = release mới. Thứ tự lệnh
`git status` thật: candidate (`CANDIDATE_CLEAN`, rỗng) → release cũ (`?? .venv`, được chấp nhận) → release mới sau chuyển
(`LIVE_TARGET_CLEAN`, rỗng, vẫn kiểm nghiêm). Kiểm `LIVE_CHECKOUT_CLEAN` nằm trước lệnh `systemctl stop` đầu tiên (AST + trace).
Expected: release cũ hợp lệ qua kiểm trước khi dừng dịch vụ; kiểm release mới không bị nới.
Negative case đã thử (sau một prepare PASS, rồi làm bẩn LIVE): file lạ, sửa/xóa file tracked, symlink lồng
`scripts/.venv` → `LIVE_CHECKOUT_CLEAN`; `.venv` đổi đích sang venv khác hoặc thành thư mục thật →
`PRODUCTION_SHARED_VENV`; `.venv` thành file thường → `PRODUCTION_SHARED_VENV`; symlink hỏng → dừng ở
read-only preflight với `UNEXPECTED_READ_ONLY_PREFLIGHT` (ngoại lệ từ `resolve(strict=True)` trong kiểm checkpoint,
vẫn an toàn nhưng không có tên gate riêng). Mọi trường hợp: không có
start/stop, không milestone, không drop-in. Bản khóa với `?? .venv` → `LIVE_CHECKOUT_CLEAN` (tái hiện).
Đột biến M4 (nới `LIVE_TARGET_CLEAN`) bị bắt.
Residual risk: fingerprint môi trường, listener, queue/PG vẫn giả lập (không đổi trong lần sửa này).

## Claim P5 — Phục hồi phân biệt COMMIT và trạng thái chưa rõ

Test thực hiện: bộ agent (fault injection trước/sau SQL, mất response, copy drop-in thứ hai, start lỗi, HOLD stop
lỗi) + bộ độc lập cũ (HOLD sau start với tiến trình sai) + E2E mới (lỗi status release cũ phải dừng trước mọi hành động).
Kết quả: PASS
Evidence (Observed): 45/45 và 20/20 hành vi OK; E2E mới: mọi lỗi checkout release cũ dừng ở read-only preflight,
`services_stopped` chưa bật nên không có recovery/start nào, dịch vụ cũ vẫn chạy.
Expected: như hợp đồng P5; thay đổi lần này không chạm `recover_production_failure`.
Negative case đã thử: như trên; không có lệnh `start` của release cũ khi chưa dừng gì.
Residual risk: không có thêm.

## Claim P6 — Postflight và bảo mật

Test thực hiện: bộ agent (SHA/DSN/HTTP/hash sai, exception chứa secret) + E2E mới với DSN canary trong
`/proc/<pid>/environ` và launcher.
Kết quả: PASS
Evidence (Observed): E2E PASS kiểm lại tiến trình/cwd/commit target bằng Git thật, JS hash từ `git show` thật khớp
asset; output prepare/cutover không chứa canary; tên gate cố định (`LIVE_CHECKOUT_CLEAN`, …), không in output
`git status` hay đường dẫn đích symlink.
Expected: như hợp đồng P6.
Negative case đã thử: canary trong DSN/launcher; lỗi được báo bằng tên gate an toàn.
Residual risk: không có thêm.

## Ghi chú về chất lượng test của agent chính (không phải FAIL)

- `test_real_git_prepare_from_production_baseline_bundle` vẫn ghi `/.venv` vào `.git/info/exclude` của release
  CŨ trước khi chạy, tức là che đúng trạng thái production đã gây lỗi lần 2; luồng prepare/cutover với
  `?? .venv` thật chỉ được agent chính kiểm bằng `git` giả trả chuỗi cố định.
- Ca `venv is a real directory` của agent đưa vào `'?? .venv'`, nhưng Git thật không bao giờ in dòng đó cho
  thư mục `.venv/` thật (bị ignore → rỗng). Ca này không chứng minh việc chặn thư mục thật; việc chặn thực tế đến
  từ `SHARED_VENV_SOURCE` (prepare) và `PRODUCTION_SHARED_VENV` (cutover) — đã kiểm bằng test mới.

## Kết luận

P1 PASS · P2 PASS (kèm SPEC_AMBIGUOUS: đích symlink `.venv` không được neo vào venv dùng chung cụ thể) ·
P3 PASS · P4 PASS · P5 PASS · P6 PASS. Không thấy IMPLEMENTATION_FAIL hay REGRESSION_FAIL. PASS local không thay
evidence production và không phải phê duyệt chạy [GHI].

## Task status

attempt: 3 (sửa thêm kiểm `git status` release cũ sau khi production lộ `?? .venv` 2026-10-02)
same_claim_repeat_fail: none (P1–P6 PASS; P2 kèm SPEC_AMBIGUOUS về đích symlink .venv, chấp nhận vì release mới dùng đúng đích venv mà production đang chạy)
next_action: PO_RUNS_PREPARE_THEN_CUTOVER_AFTER_STAFF_NOTICE
