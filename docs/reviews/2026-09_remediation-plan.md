# Kế hoạch khắc phục còn lại — ghi nhận 2026-09-30

PR1 đã xong; PR2–PR5 chưa thực hiện. Nội dung bên dưới chép nguyên văn các ràng buộc, quy tắc tiết kiệm token và kế hoạch còn lại từ yêu cầu của chủ dự án.

## Về người giao việc (quan trọng)
Tôi là chủ dự án, KHÔNG có chuyên môn kỹ thuật. Vì vậy:
- Anh TỰ QUYẾT mọi vấn đề kỹ thuật trong phạm vi các ràng buộc dưới đây. Không hỏi tôi chọn giữa các phương án kỹ thuật.
- CHỈ hỏi tôi khi: (a) có nguy cơ mất hoặc sai dữ liệu; (b) cần đụng vào hệ thống đang phục vụ người dùng thật; (c) cần quyết định về nghiệp vụ; (d) phát sinh công việc lớn ngoài kế hoạch.
- Khi hỏi: dùng tiếng Việt đời thường, không thuật ngữ. Nêu hậu quả của từng lựa chọn, nói rõ anh khuyên chọn gì và vì sao. Câu hỏi phải trả lời được bằng "đồng ý / không" hoặc chọn một phương án.
- Nếu tôi trả lời "đồng ý" mà không nói gì thêm, nghĩa là làm theo khuyến nghị của anh.

## Ràng buộc cứng
1. Nếu production chưa deploy: KHÔNG sửa, di chuyển hay chạy lại các script preflight/prepare/cutover và bundle đã được verifier duyệt. Không sửa hợp đồng claim, file VALIDATION hay báo cáo verifier trong docs/ops-deploy-regulatory-manual-edit/. Chúng là evidence đã khóa.
2. KHÔNG thao tác trên server staging hoặc production. KHÔNG deploy.
3. KHÔNG đưa secret, chuỗi kết nối, backup hay dữ liệu thật vào Git.
4. Đi theo quy tắc Git trong AGENTS.md: mỗi PR một branch riêng, chỉ add đúng file thuộc phạm vi, không dùng git add -A, không push thẳng main.
5. Nếu khi đối chiếu thấy bản rà soát sai ở điểm nào, tự đánh giá và chọn cách đúng, rồi ghi lại lý do trong báo cáo. Chỉ hỏi tôi nếu việc đó thuộc các trường hợp (a)–(d) ở trên.

## Quy tắc tiết kiệm token (bắt buộc)
- KHÔNG đọc lại toàn bộ 56 file. Dùng tham chiếu file và dòng trong bản rà soát. Mở file theo khoảng dòng, dùng grep để xác nhận.
- KHÔNG chạy lại bộ test của phase regulatory (30 + 10 + 60) trong đợt này.
- Các PR chỉ sửa tài liệu xếp mức rủi ro THẤP: không lập hợp đồng claim, không gọi verifier. Tự kiểm bằng grep theo tiêu chí hoàn thành.
- Vấn đề mới phát hiện ngoài phạm vi PR thì ghi vào mục "Việc mở" của PROJECT_STATE.md, không tự sửa.
- Báo cáo giữa chừng tối đa 3 dòng. Báo cáo cuối PR theo mẫu ở cuối prompt.
- Mỗi PR xong thì DỪNG, chờ tôi trả lời rồi mới sang PR kế tiếp.

═══════════════ ĐỢT A — LÀM NGAY ═══════════════

## PR2 — AGENTS.md, workflow và quyền verifier
Branch: docs/agent-workflow
1. AGENTS.md: thay yêu cầu "đọc sáu tài liệu nền" bằng ba chế độ.
   - PHIÊN MỚI: AGENTS.md, PROJECT_STATE.md, hồ sơ task. Tài liệu nền khác chỉ đọc khi task chạm tới chủ đề đó. Thêm bảng "chủ đề → file nguồn duy nhất".
   - TIẾP TỤC CÙNG TASK: mục "Tiếp nối nhanh", git status, diff kể từ checkpoint, hồ sơ task. Không đọc lại tài liệu nền.
   - THAY ĐỔI NHỎ: AGENTS.md và các file bị sửa.
2. Thay yêu cầu "kiểm tra Git sau mỗi bước nhỏ" bằng: kiểm tra trước khi commit, và trước/sau mỗi thao tác có tác dụng phụ.
3. Quy tắc uv: tự xác định theo thực tế repo (.venv + requirements.txt hay uv), ghi rõ vào AGENTS.md.
4. Thêm mục "Giao tiếp với chủ dự án" vào AGENTS.md, chép nguyên các quy tắc trong phần "Về người giao việc" của prompt này, để các phiên sau cũng tuân theo.
5. Báo cáo: lượt thường báo ngắn, báo cáo đầy đủ chỉ ở checkpoint. Phạm vi phê duyệt lấy từ PROJECT_STATE.md, không hỏi lại mỗi lượt trừ khi phạm vi thay đổi.
6. DEVELOPMENT_WORKFLOW.md: thêm mục "Dùng lại evidence".
   - Evidence còn hiệu lực khi đủ ba điều kiện: sha256 artifact khớp; không đổi code, migration, cấu hình hoặc runtime liên quan; cùng môi trường đích.
   - Thêm bảng "loại thay đổi → claim cần mở lại". Chỉ kiểm lại các claim bị ảnh hưởng.
   - Phân biệt rõ gate trước deploy với bước xác nhận sau deploy.
   - Báo cáo đã bị bản mới thay thế thì đánh dấu LỊCH SỬ, không coi là blocker.
   - Thay đổi chỉ trên tài liệu là rủi ro THẤP, không cần verifier.
7. .opencode/agent/verifier.md: cấp quyền ghi tests/independent/** cho khớp với workflow. Vẫn cấm sửa source và tài liệu ngoài báo cáo.
8. Không áp dụng hồi tố để mở lại hợp đồng của phase hiện tại. Quy tắc dùng lại evidence ĐƯỢC áp dụng cho lần deploy production sắp tới.
Tiêu chí xong: grep không còn yêu cầu "đọc sáu tài liệu" hay "sau mỗi bước nhỏ". Workflow và verifier.md nhất quán về đường dẫn test.
→ DỪNG sau PR2. Nếu production chưa deploy, cuối báo cáo giải thích cho tôi bằng lời dễ hiểu:
   - việc đưa bản mới lên cho người dùng thật gồm những gì;
   - rủi ro chính là gì;
   - tôi cần đồng ý điều gì để bắt đầu.
Deploy dùng đúng script và bundle có hash trong PROJECT_STATE.md.

═══════════════ ĐỢT B — CHỈ LÀM SAU KHI PRODUCTION DEPLOY THÀNH CÔNG ═══════════════
(Trước khi bắt đầu, xác nhận PROJECT_STATE.md đã ghi production deploy thành công. Nếu chưa thì dừng và báo tôi bằng một câu dễ hiểu.)

## PR3 — Đưa bộ script deploy vào repo
Branch: ops/deploy-scripts
1. Commit 1: sao chép NGUYÊN VĂN bản đã chạy production vào ops/deploy/, ghi sha256 để chứng minh khớp. Quét secret (password, postgres://, token, key, IP nội bộ). Secret phải đọc từ biến môi trường hoặc file ngoài repo. Thêm .gitignore cho backup và credential.
2. Commit 2: tách khác biệt môi trường ra ops/deploy/profiles/staging.env.example và production.env.example (không chứa secret). Các khác biệt gồm: cập nhật tại chỗ hay release bất biến, launcher, drop-in, đường dẫn, tên service.
3. Chuyển test đang nằm ở thư mục tạm vào tests/ops/ và chạy một lần.
4. Nếu việc tham số hoá làm thay đổi logic, chỉ gọi verifier cho các claim bị ảnh hưởng, theo bảng ở PR2. Giữ thay đổi ở mức tối thiểu.
Tiêu chí xong: release sau chỉ cần chọn profile và ghi chú migration riêng, không phải viết script mới.

## PR4 — Tài liệu deploy, security, architecture
Branch: docs/deploy-single-source
1. HUONG_DAN_DEPLOY_VA_CAP_NHAT.md:
   - Bỏ yêu cầu "soạn riêng ba script mỗi release", thay bằng dùng ops/deploy/ cùng một mẫu "ghi chú release" (migration, lớp rollback, kiểm tra riêng).
   - Sửa luồng Git (dòng 63–109) cho khớp AGENTS.
   - Thay câu ở dòng 122 bằng bảng khác biệt staging/production.
   - Rollback (dòng 182–187): bỏ phần khái quát "additive nên an toàn". Mỗi release phải khai báo một trong ba lớp: (1) code cũ tương thích; (2) chỉ forward-fix, không quay code cũ; (3) phải restore backup. Lấy migration033 làm ví dụ, theo runbook phase.
   - Bỏ đường dẫn Mac, bỏ giả định clone HTTPS. Chuỗi kết nối lấy qua env hoặc .pgpass, không truyền qua đối số lệnh.
2. SECURITY.md: giữ phần quyền, secret, dữ liệu, backup. Thay dòng 100–104 bằng một liên kết sang hướng dẫn deploy. Backup trước migration production là BẮT BUỘC.
3. ARCHITECTURE.md: dòng 188–191 và 199–205 thu về một đoạn tham chiếu. Trạng thái phát hành ở dòng 250 chuyển sang PROJECT_STATE.md.
Tiêu chí xong: grep "git add -A", "git pull", "soạn riêng", "/Users/" trong tài liệu hiện hành không còn kết quả lỗi thời. Mỗi quy trình deploy chỉ được mô tả ở một nơi.

## PR5 — Hướng dẫn local, RBAC, README
Branch: docs/bootstrap
1. Chốt MỘT đường bootstrap và kiểm chứng thật trên một DB PostgreSQL local dùng xong bỏ: schema → toàn bộ migration theo thứ tự (hoặc runner hiện có) → bootstrap_admin.py → đăng nhập thành công. Ghi các lệnh đã chạy (không có secret) vào mô tả PR.
2. HUONG_DAN_LOCAL.md:
   - Cập nhật migration đầy đủ, không dừng ở 002–006.
   - Thêm ENABLE_LEGACY_PASSWORD_LOGIN vào mẫu cấu hình kèm điều kiện bật.
   - Đưa migrate_sqlite_to_postgres.py ra khỏi luồng chính, ghi rõ là deprecated.
   - Sửa đường dẫn cũ.
   - Nhắc che dữ liệu nhạy cảm khi gửi log lỗi.
3. HUONG_DAN_CAP_NHAT_VA_RBAC.md:
   - Sửa checklist (dòng 121–133) để bootstrap chạy sau khi đủ migration, tối thiểu migration030.
   - Bổ sung --dry-run / --upsert.
   - Giải thích ngắn canonical brand theo code hiện tại.
   - Đổi "nên backup" thành "bắt buộc backup" cho production.
   - Ghi rõ tài liệu áp dụng cho phiên bản nào.
4. README.md: làm điểm vào ngắn gọn. Bỏ quick start chỉ tạo bảng products và câu "xem danh sách migration bên dưới". Dẫn tới hướng dẫn bootstrap vừa được kiểm chứng.
Tiêu chí xong: người mới làm đúng README → HUONG_DAN_LOCAL là dựng được bản hiện tại và đăng nhập được.

## Mẫu báo cáo cuối mỗi PR
Phần 1 — Cho chủ dự án (tối đa 5 dòng, không thuật ngữ):
- Bước này đã làm được gì, giúp dự án nhanh hơn hoặc an toàn hơn thế nào.
- Hệ thống đang chạy có bị ảnh hưởng không.
- Tôi cần làm gì: thường chỉ là trả lời "đồng ý, làm tiếp".
Phần 2 — Kỹ thuật (tối đa 10 dòng, để lưu hồ sơ):
- PR / branch / file đã sửa; thay đổi chính; cách đã kiểm (grep, hash, test); việc mở mới phát sinh.
