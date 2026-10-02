# Verification Result — deploy-regulatory-manual-edit-production (sửa nhận diện tiến trình exec, 2026-10-02)

Do verifier độc lập tạo, context sạch, không thấy lịch sử/kế hoạch của agent chính.
Đầu vào: hợp đồng khóa `VERIFICATION.md` (SHA256 `81ff2ae8…a33ab`, đã kiểm lại: khớp),
diff script so với `locked-2026-09-30/`, lệnh test, Observed/Expected/Classification của
lần thất bại production 2026-10-02 (PO chạy, chỉ đọc). Không SSH, không kết nối máy
chủ, không Docker; mọi kiểm chứng là local/mock với `/proc` giả và systemd giả.

Artifact được kiểm (SHA256 đã đối chiếu, khớp yêu cầu):
- `prepare_regulatory_staging.py` `b8e4ed37…68db`; `cutover_regulatory_staging.py` `dcc5e352…7f28`
- bản khóa trước: prepare `dd668fb1…840d`, cutover `24042403…91e8`

Phạm vi diff (tự `diff -u` và so AST): mỗi script đúng một hunk trong
`verify_production_entrypoint()`, thêm biến `worker_exec` (unit WORKER, đúng 2 token,
Python cùng realpath venv của `LIVE`, token 2 == chuỗi `str(LIVE/'scripts/import_worker.py')`)
và nối vào điều kiện chặn. Mọi định nghĩa top-level khác AST-identical với bản khóa
(kể cả `verify_entrypoint` staging, `unit`, `service_info`, `env_dsn`,
`activate_production_release`, `recover_production_failure`, khối main).

Lệnh test:
- Bộ của agent chính: `cd /Volumes/DATA/Development/_ops/search-tools && …/.venv/bin/python -B -m unittest test_production_adapter test_cutover_fingerprint` → `Ran 42 tests … OK`.
- Test độc lập mới: `cd /Volumes/DATA/Development/Search-tools && .venv/bin/python -B -m unittest -v tests.independent.test_production_launcher_exec_cmdline_adversarial` → `Ran 26 tests … OK`.
- Mutation check (bản sao script trong scratchpad, không sửa artifact): 5 đột biến
  đều bị test độc lập bắt: bỏ kiểm role (11 fail), so với release cũ thay vì `LIVE`
  hiện hành (4 fail), `endswith` thay so khớp tuyệt đối (21 fail), bỏ kiểm Python (11
  fail), `len>=2` (5 fail).

Fixture dùng đúng dạng đo trên production: LIVE `/opt/search-tools-pg-release-20260927T013108Z-6155f25-clean`
(dựng dưới thư mục tạm), venv dùng chung symlink tới `python3.10`; web ExecStart
`[LIVE/.venv/bin/python, -I, -B, LIVE-launchers-phase6d9/web.py]`, /proc
`[LIVE/.venv/bin/python, -m, gunicorn, --workers, 2, --timeout, 180, --graceful-timeout, 30, --bind, 127.0.0.1:5001, search:app]`;
worker ExecStart `[…, LIVE-launchers-phase6d9/worker.py]`, /proc `[LIVE/.venv/bin/python, LIVE/scripts/import_worker.py]`.

## Claim P1 — Profile và launcher đúng phạm vi

Test thực hiện: ma trận `verify_entrypoint()` cho cả prepare và cutover (profile
`--production`), trước chuyển bản (LIVE = release cũ) và sau chuyển bản (cutover,
LIVE = release mới, ExecStart = `<mới>-launchers-rme`); so sánh khác biệt với bản khóa
trên cùng tập input; kiểm profile staging (không flag) cho kết quả giống hệt bản khóa;
bộ test agent (`test_profiles_and_unknown_flag`, `test_launchers_correct_role_realpath_and_negative_cases`).
Kết quả: PASS
Evidence (Observed): dạng thật web (gunicorn) và worker (exec) được chấp nhận ở cả hai
script; dạng launcher ngay sau start được chấp nhận; sau chuyển bản chỉ
`[<mới>/.venv/bin/python, <mới>/scripts/import_worker.py]` hoặc launcher `-rme` được nhận.
Test vi phân: mọi input bản khóa chấp nhận vẫn được chấp nhận; input mới được chấp nhận
duy nhất là dạng worker 2 token với Python realpath venv và script đúng `LIVE/scripts/import_worker.py`
trên unit WORKER. Profile staging: 2 script × 2 unit × 2 ExecStart × 5 cmdline cho kết
quả gate giống hệt bản khóa.
Expected: tiến trình production hợp lệ được nhận diện; tiến trình lạ/sai release bị chặn;
staging không đổi; S1 vẫn là rủi ro đã chấp nhận.
Negative case đã thử (tất cả bị chặn `PRODUCTION_LAUNCHER_CMDLINE`, cả prepare và cutover):
script release mới trước chuyển (Python cũ hoặc mới), script khác cùng release
(`evil.py`, `import_worker.py` ở gốc release, `.pyc`, dấu `/` cuối), Python realpath khác
(`python3.12`), Python tương đối (`.venv/bin/python`, `python3`), script tương đối
(`scripts/…`, `./scripts/…`), `..`, `//`, thư mục release qua symlink, file script qua
symlink, đối số thừa (`--once`), `-I -B script`, `-u script`, `-c`, `-m module`, dạng
gunicorn trên unit worker, launcher web trên unit worker, launcher `-rme` trước chuyển,
đảo thứ tự, chỉ python, chỉ script, cmdline rỗng, tiêu đề tiến trình systemd trước exec
`(python)`. Unit web mang dạng worker (exec hoặc launcher) và script Python khác: bị chặn.
Sau chuyển bản: script release cũ (Python mới hoặc cũ), launcher cũ còn chạy, Python khác,
đối số thừa, dạng worker trên unit web: bị chặn; ExecStart còn trỏ launcher cũ: bị chặn.
Residual risk:
- Python được nhận theo realpath như hợp đồng: `/usr/bin/python3.10` hay `/usr/bin/python3`
  (cùng realpath) ở argv[0] được chấp nhận. Kiểm dựa trên `/proc/<pid>/cmdline`, không
  đối chiếu `/proc/<pid>/exe`; argv[0] do tiến trình tự khai (giới hạn đã có từ bản khóa).
- Token rỗng trong cmdline bị lọc trước khi so (hành vi có từ bản khóa), nên
  `[python, LIVE/scripts/import_worker.py, '']` được chấp nhận như 2 token. Không phải lỗ
  hổng đáng kể, nhưng là điểm chấp nhận nằm ngoài dạng đã đo.
- Web vẫn dựa fallback S1 (`search:app` đúng một token): không thay đổi, PO đã chấp nhận.
- Nội dung thật của launcher `phase6d9` không có trong evidence: việc launcher `-rme` sau
  khi thay path sẽ exec ra đúng `<mới>/scripts/import_worker.py` (dạng tuyệt đối, cùng
  cấu trúc) là suy luận từ dạng đã đo, chưa được chứng minh trên server.

## Claim P2 — Prepare bất biến và giữ nguyên venv

Test thực hiện: chạy khối main thật của prepare `--production` qua các gate chỉ đọc với
`/proc` giả (cwd/cmdline/environ), systemctl/git giả, bundle cố ý vắng để dừng ngay sau
gate chỉ đọc; tái hiện với bản khóa; bộ test agent (git fixture thật từ BASE 6155f25 +
bundle, venv, checkpoint, không ghi systemd).
Kết quả: PASS
Evidence (Observed): bản khóa trên fixture thật in đúng
`PREPARE STOP at read-only gates gate=PRODUCTION_LAUNCHER_CMDLINE` (tái hiện lỗi production).
Bản hiện tại đi qua toàn bộ gate chỉ đọc (dừng ở `verify transferred bundle` do fixture),
`launchers` = `{web: LIVE-launchers-phase6d9/web.py, worker: …/worker.py}`. Không tạo
candidate, checkpoint, thư mục `-launchers-rme`; không gọi lệnh ngoài systemctl show/git
rev-parse/status. 42 test agent PASS, gồm `test_real_git_prepare_from_production_baseline_bundle`.
Expected: prepare nhận tiến trình production hợp lệ, không ghi gì khi gate fail.
Negative case đã thử: worker chạy script release mới, script khác, Python khác, `-c`,
dạng web trên worker, dạng worker trên unit web → `PREPARE STOP at read-only gates gate=PRODUCTION_LAUNCHER_CMDLINE`,
không có artifact nào được tạo.
Residual risk: phần ghi của prepare (clone/fetch/venv/owner) không đổi so với bản khóa
(AST-identical); chỉ được kiểm hồi quy bằng test agent, không có kiểm chứng trên server.

## Claim P3 — Copy launcher chỉ đổi path, không lộ secret qua diff

Test thực hiện: so AST xác nhận `copy_production_launchers`, `finish_production_prepare`,
`dropin_content` không đổi; chạy lại test agent (copy đúng bytes thay path, mode/owner,
diff che context, từ chối symlink/non-Python/thiếu role/không có path cũ).
Kết quả: PASS (hồi quy)
Evidence (Observed): AST-identical với bản khóa; `test_copy_*`, `test_prepare_only_stages_named_dropins_and_preserves_shared_venv` PASS.
Expected: không đổi hành vi copy.
Negative case đã thử: thay đổi không chạm vùng này. Đột biến trong `verify_production_entrypoint`
không ảnh hưởng nhánh copy (đã chạy).
Residual risk: phép thay `old_release→new_release` là thay chuỗi thô; vì tên thư mục
launcher cũ bắt đầu bằng tên release cũ, nếu nội dung launcher tham chiếu chính thư mục
launcher của nó thì sẽ bị đổi thành `<mới>-launchers-phase6d9` (không tồn tại). Không
xác minh được vì không có nội dung launcher thật; đây là hành vi có từ bản khóa.

## Claim P4 — Thứ tự cutover và chuyển release có kiểm soát

Test thực hiện: chạy khối main thật của cutover `--production` với `unit()`, `env_dsn()`,
`stray_worker_count()`, `production_checkpoint()`, `activate_production_release()`, `milestone()`
thật trên `/proc` giả và systemd giả (drop-in ghi vào thư mục tạm); chỉ giả psql/pg_dump/git/HTTP
và fingerprint nguồn môi trường.
Kết quả: PASS
Evidence (Observed): với dạng exec thật trước dừng và sau khởi động, trace đúng
`stop:worker → stop:web → pg_dump → pg_restore → sql → reload → start:web,worker`, in
`PRODUCTION TECHNICAL CUTOVER PASS`, `LIVE` cuối = release mới. Dạng launcher ngay sau
start, và hỗn hợp (web exec + worker launcher), cũng PASS cùng trace. Bản khóa trên cùng
fixture dừng ở `read-only preflight gate=PRODUCTION_LAUNCHER_CMDLINE` trước mọi lệnh ghi.
Test agent về thứ tự/drop-in/reload/WD PASS.
Expected: không đổi thứ tự cutover; tiến trình hợp lệ được nhận ở kiểm trước dừng và sau start.
Negative case đã thử: trước dừng, worker chạy script release mới, cmdline không đọc được
(`WORKER_CMDLINE_READ`), cwd release khác (`WORKER_CWD_MATCH`) → dừng ở preflight, trace
rỗng, hai service vẫn chạy, không drop-in nào được lắp.
Residual risk: systemd thật, `daemon-reload` thật, và kiểu unit (`Type=`) chưa được kiểm.

## Claim P5 — Phục hồi phân biệt COMMIT và trạng thái chưa rõ

Test thực hiện: chạy end-to-end, sau COMMIT và start, cấy tiến trình không hợp lệ hoặc
trạng thái sai; bộ test agent về lỗi trước/sau SQL.
Kết quả: PASS
Evidence (Observed): sau start, worker chạy script release cũ, Python khác realpath,
unit web mang dạng worker, tiêu đề systemd trước exec `(python)`, cwd release cũ
(`WORKER_CWD_MATCH`), user sai (`WORKER_SERVICE_USER`), thiếu DSN
(`LIVE_PROCESS_CONNECTION_PRESENT`) → mọi trường hợp in `HOLD:`, trace kết thúc bằng
`stop:worker, stop:web`, cả hai unit dừng, không `old services restarted`, không PASS.
Test agent (rollback xác minh → start bản cũ; mất response/active/unknown → HOLD;
`HOLD_STOP_UNCONFIRMED`) PASS. `recover_production_failure` AST-identical với bản khóa.
Expected: không đổi logic phục hồi/HOLD.
Negative case đã thử: như trên.
Residual risk (quan trọng cho PO): sau `systemctl start` không có chờ/thử lại trước khi
`unit()` đọc `/proc`, trước `listening_ports()` và HTTP `/login` (timeout 12s). Nếu đúng
lúc đọc tiến trình còn ở giai đoạn systemd chưa exec, gunicorn master chưa bind cổng,
hoặc worker app nạp quá 12s, script sẽ HOLD sau COMMIT (an toàn dữ liệu, nhưng web/worker
dừng cho tới khi PO xử lý). Rủi ro thời gian này có từ bản khóa, không do thay đổi này
gây ra; chỉ dạng launcher và dạng exec được chấp nhận, đúng Expected.

## Claim P6 — Postflight và bảo mật

Test thực hiện: postflight thật (`unit()` lần hai, `env_dsn()` hai tiến trình, git target,
schema, listener, login, JS hash, counts) trong end-to-end; DSN giả chứa canary mật khẩu;
xác minh output không chứa canary trong mọi kịch bản; kiểm runbook.
Kết quả: PASS
Evidence (Observed): kịch bản hợp lệ PASS postflight với tiến trình mới dạng exec và dạng
launcher; mọi kịch bản lỗi chỉ in tên gate cố định, không in canary/DSN/cmdline. Các kiểm tra
khác trong `unit()` vẫn đúng với tiến trình đã exec: `User` lấy từ cấu hình unit (không đổi
qua exec), cwd được exec giữ nguyên (evidence production: worker đã vượt `WORKER_CWD_MATCH`
và chỉ dừng ở gate cmdline; web vượt toàn bộ gate), MainPID giữ nguyên qua exec.
`env_dsn()` đọc `/proc/<pid>/environ` của ảnh tiến trình sau exec: khi thiếu `DATABASE_URL`
thì trước dừng → dừng preflight không ghi gì; sau start → HOLD. Runbook
`docs/ops-deploy-regulatory-manual-edit/PRODUCTION_RUNBOOK.md` 48 dòng, 5 lệnh, ghi “CHƯA
CHẠY production”, không bị thay đổi này chạm. `preflight_readonly.py` (bước 1/5) không kiểm cmdline.
Expected: postflight nhận tiến trình mới hợp lệ, không lộ secret.
Negative case đã thử: tiến trình exec với environ đã lọc (không DSN) trước dừng và sau start;
user sai; cwd cũ; dạng worker trên unit web.
Residual risk: chẩn đoán production chưa đọc environ, nên chưa chứng minh tiến trình đã exec
trên server có `DATABASE_URL` trong `/proc/<pid>/environ` (phụ thuộc launcher dùng
`execv`/kế thừa hay `execve` với env lọc). Nếu không có, cutover dừng ở preflight trước
mọi lệnh ghi (fail-closed); sau start, launcher mới là bản copy cùng logic nên kỳ vọng cùng
hành vi. Gunicorn có `setproctitle` có thể ghi đè vùng environ; cmdline đo được là đối số
thật nên hiện không có dấu hiệu `setproctitle`.

## Kết luận

P1 PASS, P2 PASS, P3 PASS, P4 PASS, P5 PASS, P6 PASS (local/mock; server NOT RUN).
Không phát hiện IMPLEMENTATION_FAIL / REGRESSION_FAIL. PASS local không thay evidence
production hay phê duyệt chạy [GHI].

Test độc lập mới: `tests/independent/test_production_launcher_exec_cmdline_adversarial.py`.

## Task status

attempt: 2 (sửa sau khi production lộ lỗi nhận diện worker 2026-10-02; PO đồng ý sửa script triển khai)
same_claim_repeat_fail: none (P1–P6 PASS local/mock với cmdline thật đo trên production)
next_action: PO_REAPPROVE_START_WITH_CHANGED_ARTIFACTS (cần “đồng ý bắt đầu” mới + báo nhân viên trước cutover)
