**Tôi đã rà toàn bộ 56 file Markdown thuộc dự án, tổng 7.560 dòng**, gồm chín file anh chỉ rõ, hồ sơ các phase, spec, báo cáo kiểm chứng và cấu hình agent. Tôi trực tiếp đọc nhóm tài liệu nền; hai lượt rà soát chỉ đọc phụ trách hồ sơ phase cũ và phase hiện tại. Những điểm nghi ngờ đã được đối chiếu với Git, code và migrations.

**Kết luận: phần kéo dài đến từ cả lỗi tổ chức tài liệu, cách thiết kế quy trình triển khai và lỗi thực thi của AI.** Không thể giải thích đơn giản rằng “phase này HIGH nên phải lâu”. Tôi không có thống kê token thực tế để phân bổ theo phần trăm, nhưng có đủ bằng chứng xác định các nguồn gây làm lại.

## 1. Vì sao một phase lại kéo dài như vậy?

### A. Mỗi release đang bị biến thành một lần phát triển công cụ deploy

Đây là vấn đề nền lớn nhất.

[Hướng dẫn deploy, dòng 12–16](HUONG_DAN_DEPLOY_VA_CAP_NHAT.md#L12-L16) yêu cầu **mỗi release soạn riêng ba script**. Luồng ở dòng 39–45 còn chia thành bộ staging và bộ production.

Trong repository lại chưa có bộ preflight/prepare/cutover được quản lý phiên bản để tái sử dụng. Các script hiện tại nằm ngoài repo; một test nằm trong thư mục tạm.

Hệ quả thực tế ở phase này:
- Phải bổ sung cách chuyển code bằng bundle.
- Phải sửa nhận diện Python/worker.
- Phải sửa fingerprint cấu hình.
- Phải thích nghi staging cập nhật tại chỗ sang production dùng release riêng, launcher và drop-in.
- Phải viết và kiểm chứng chính các công cụ mới đó.

**Một phase nghiệp vụ đã phải gánh thêm việc xây dựng và sửa công cụ vận hành.** Nếu tiếp tục “soạn riêng từ đầu mỗi release”, chi phí này dễ lặp lại ở phase sau.

### B. Bộ nhớ dự án chứa quá nhiều trạng thái cũ và thông tin trái nhau

`PROJECT_STATE.md` hiện vừa là bảng trạng thái, vừa là nhật ký điều tra, vừa là lịch sử phê duyệt và danh sách việc cũ.

Ví dụ:
- Dòng 64–96: PR #22 đã merge, UAT staging đạt, adapter production đã qua verifier.
- Dòng 204–213 vẫn giữ mốc “chưa merge/deploy”.
- Dòng 226–234 vẫn nói hướng dẫn deploy chưa được cập nhật, dù tài liệu đó đã được viết lại trong PR #21.

Một số đoạn đã được đánh dấu lịch sử, nên **không phải mọi câu “chưa làm” đều là thông tin sai**. Nhưng đặt tất cả trong file bắt buộc đọc khiến mỗi phiên phải tái dựng lại thứ tự sự kiện.

**Lỗi nằm ở cách bảo trì bộ nhớ: thêm diễn biến mới nhưng không thu gọn trạng thái hiện hành và đóng các việc đã xong.**

### C. Có lỗi thật trong script do AI viết, gây thêm vòng sửa và kiểm tra

Hồ sơ ghi rõ:
- Gate worker từ chối đường dẫn venv/entrypoint hợp lệ.
- Fingerprint so cả PID và thời điểm chạy/dừng, khiến staging dừng **sau migration COMMIT** dù cấu hình không đổi.
- Kiểm tra JS ban đầu có thể báo PASS khi file trên đĩa và HTTP cùng đổi sang nội dung ngoài commit đích.

Nguồn: [VALIDATION vận hành](docs/ops-deploy-regulatory-manual-edit/VALIDATION.md#L106-L180), [lịch sử repair production](docs/ops-deploy-regulatory-manual-edit/VALIDATION.md#L285-L295).

**Đây là trách nhiệm chất lượng triển khai của AI.** Các lượt kiểm tra sau đó bắt được lỗi có ích, nhưng không nên dùng việc kiểm tra cần thiết để che mất nguyên nhân ban đầu là script chưa đúng.

### D. AI cũng đã làm lặp những việc chưa có lý do mới rõ ràng

Ngay lượt tiếp tục sau khi mở máy, tôi xác nhận hash hai script và bundle vẫn khớp bản đã được verifier xem xét, rồi vẫn chạy lại toàn bộ 30 + 10 + 60 kiểm tra.

Nếu chưa có thay đổi code, runtime hoặc điều kiện kiểm thử liên quan, cách hợp lý hơn là xác minh tính toàn vẹn rồi dùng lại evidence đã có. **Tôi đã mặc định kiểm tra lại quá rộng, thay vì nêu rõ điều gì mới cần chứng minh.**

Việc đọc lại nhiều tài liệu, báo cáo trạng thái dài và nhắc lại các điều kiện đã chốt cũng làm tăng token. Phần này cần sửa trong cách làm của AI, không chỉ sửa tài liệu.

---

## 2. Kết quả rà từng file anh yêu cầu

### `PROJECT_STATE.md` — cần ưu tiên chỉnh nhất

Các vấn đề:
- Trạng thái hiện hành bị lẫn với lịch sử dài.
- Có việc đã hoàn thành nhưng vẫn nằm trong “Vấn đề đã biết” và “Bước tiếp theo”.
- Chứa nhiều lần nhắc lại cùng test, quyền thực hiện và điều kiện dừng.
- Dẫn tới bộ nhớ vận hành ngoài repo, nhưng chưa có một nơi tiếp nối ngắn gọn, đầy đủ.

Quy trình vốn đã yêu cầu chuyển lịch sử dài sang archive tại [DEVELOPMENT_WORKFLOW.md:88](docs/DEVELOPMENT_WORKFLOW.md#L88). **AI chưa thực hiện tốt chính quy tắc này.**

**Hướng sửa:** giữ một trang trạng thái hiện hành; chuyển diễn biến cũ sang hồ sơ lịch sử, giữ liên kết và bằng chứng.

### `HUONG_DAN_DEPLOY_VA_CAP_NHAT.md` — đã cập nhật nhưng còn lỗi quan trọng

1. **Yêu cầu soạn lại script mỗi release:** nguồn phát sinh công việc đã nêu.
2. **Git không khớp quy tắc hiện hành:** dòng 63–109 hướng dẫn `git add -A`, push thẳng `main`, pull/rebase; trong khi AGENTS yêu cầu branch riêng và kiểm soát phạm vi commit.
3. **Tự mâu thuẫn về môi trường:** dòng 122 nói staging/production chỉ khác đường dẫn và service; dòng 212 lại ghi rõ một bên cập nhật tại chỗ, một bên release bất biến. Khác biệt này ảnh hưởng cách chuyển phiên bản và phục hồi, không chỉ là thay tham số.
4. **Khái quát rollback quá rộng:** dòng 182–187 cho rằng lỗi sau stop có thể tự về code cũ vì migration additive thường tương thích. Với migration033, worker cũ có thể làm mất cơ chế bảo vệ rule; runbook phase đã phải quy định khác.
5. Còn đường dẫn Mac cũ, giả định clone HTTPS và ví dụ đưa chuỗi kết nối vào đối số lệnh.

**Hướng sửa:** tài liệu chung chỉ mô tả nguyên tắc; profile môi trường và chiến lược migration/rollback phải được xác định rõ. Không coi “additive” là bằng chứng code cũ an toàn.

### `ARCHITECTURE.md` — phần mô tả ứng dụng hữu ích, phần vận hành lỗi thời

- [Dòng 188–191](ARCHITECTURE.md#L188-L191) nói hướng dẫn deploy vẫn là tài liệu cũ cần cập nhật, trong khi PR #21 đã cập nhật nó.
- Dòng 199–205 còn giả định HTTPS, mô tả prepare tạo checkpoint/backup và rollback code cũ chung chung.
- Dòng 250 vẫn mô tả feature ở branch chưa phát hành, thiếu phân biệt rõ đã merge, đã staging, chưa production.

**Hướng sửa:** giữ kiến trúc ổn định tại đây; trạng thái phát hành nằm ở `PROJECT_STATE.md`; chi tiết thao tác dẫn sang hướng dẫn deploy, tránh ba nơi cùng chép một quy trình.

### `SECURITY.md` — nguyên tắc bảo vệ đúng, mục Deploy tự mâu thuẫn

- Dòng 43–51 mô tả release bất biến và ba bước preflight/prepare/cutover.
- [Dòng 100–104](SECURITY.md#L100-L104) lại yêu cầu luồng `git pull → cài dependency → migration → restart`, đồng thời nói không được rút gọn.

AI đọc cả hai sẽ phải tự phân xử đâu là luồng thực tế. Đây là mâu thuẫn trong một tài liệu có tính bắt buộc.

**Hướng sửa:** SECURITY giữ ranh giới quyền, secret, dữ liệu và backup; không sao chép các lệnh deploy dễ lỗi thời.

### `HUONG_DAN_LOCAL.md` — không còn đủ để dựng bản hiện tại

Tôi đã đối chiếu với code:
- Hướng dẫn schema chủ yếu dừng ở migration002–006.
- Đăng nhập hiện tại dùng nhiều cột xuất hiện ở migration sau, như `auth_version`, `account_status`, `auth_provider` — [search.py:3050–3054](search.py#L3050-L3054).
- Hướng dẫn đăng nhập chỉ-mật-khẩu nhưng mẫu cấu hình không bật `ENABLE_LEGACY_PASSWORD_LOGIN`; code mặc định tắt.
- Vẫn hướng dẫn dùng `migrate_sqlite_to_postgres.py`, trong khi script đã ghi **deprecated** và từ chối DB có Brand Master — [script:5–12](scripts/migrate_sqlite_to_postgres.py#L5-L12).
- Còn đường dẫn cũ và yêu cầu gửi “toàn bộ” lỗi Terminal, chưa nhắc che dữ liệu nhạy cảm.

**Hệ quả:** làm đúng tài liệu vẫn có thể dựng thiếu môi trường, rồi mất thời gian debug lỗi nền không thuộc phase đang làm.

### `HUONG_DAN_CAP_NHAT_VA_RBAC.md` — tài liệu đời đầu chưa theo kịp code

Một lỗi đối chiếu được trực tiếp:
- Checklist yêu cầu schema + migration002 rồi chạy bootstrap admin.
- `bootstrap_admin.py` hiện ghi cột `is_super_admin`.
- Cột này chỉ được thêm ở migration030.

Nguồn: [checklist](HUONG_DAN_CAP_NHAT_VA_RBAC.md#L121-L133), [bootstrap](scripts/bootstrap_admin.py#L39-L44), [migration030](sql/migration_030_admin_menu_permissions.sql#L14-L26).

Ngoài ra:
- Mô tả thay dữ liệu theo brand chưa giải thích đầy đủ canonical brand và các nguồn brand lịch sử.
- Thiếu luồng `--dry-run`/`--upsert` hiện có.
- Còn hướng dẫn legacy login thiếu điều kiện bật.
- Quy định backup production dùng từ “nên”, không thống nhất với yêu cầu bắt buộc trong SECURITY.

**Hướng sửa:** xác định rõ tài liệu dùng cho phiên bản hiện hành; ưu tiên các luồng quản trị/import đã có thay vì để hướng dẫn cũ trông như quy trình chuẩn.

### `README.md` — đang dẫn người đọc vào bootstrap cũ

- Quick start chỉ tạo bảng products rồi chạy app.
- Nói “xem danh sách migration bên dưới” nhưng không có danh sách đó.
- Dẫn sang hai hướng dẫn local/RBAC đang thiếu các bước cần thiết.
- Thiếu CI là tình trạng thực, nhưng không phải bằng chứng rằng mọi lần tiếp tục đều cần chạy lại toàn bộ test.

**Hướng sửa:** README làm điểm vào ngắn gọn, dẫn tới một hướng dẫn bootstrap được xác minh; không duy trì thêm một bản hướng dẫn cài đặt thiếu bước.

### `AGENTS.md` — có quy tắc tốt nhưng chi phí nạp ngữ cảnh cao

[Dòng 10](AGENTS.md#L10) yêu cầu đọc sáu tài liệu nền. Hiện sáu file đó có **1.213 dòng**, chưa tính AGENTS và hồ sơ task.

Các điểm làm tăng chi phí:
- Chưa phân biệt phiên mới, tiếp tục cùng task và thay đổi nhỏ.
- Yêu cầu kiểm tra Git “sau mỗi bước nhỏ” dễ bị thực hiện máy móc.
- Quy tắc chung về `uv` chưa được giải thích rõ với project hiện dùng `.venv`/`requirements.txt`.
- Yêu cầu báo cáo, lưu trạng thái và quy trình đầy đủ dễ bị áp dụng lại cho từng lượt trao đổi.

Tuy vậy, AGENTS cũng yêu cầu sửa tối thiểu và dừng mở rộng khi đạt checkpoint. **Không thể đổ toàn bộ việc làm quá nhiều cho AGENTS; AI đã chưa vận dụng phần giới hạn phạm vi đủ tốt.**

### `CLAUDE.md` — không phải nguồn gây phình quy trình

File chỉ có hai dòng, chủ yếu import `AGENTS.md`. Không có bộ quy tắc dài thứ hai.

Nếu dùng Claude Code, quy định “HIGH chỉ làm trong OpenCode” được kế thừa từ AGENTS; đó là chính sách đã thiết lập, không phải lỗi riêng của CLAUDE.md.

---

## 3. Hai vấn đề bổ sung ngoài chín file

### Quy trình kiểm chứng chưa khớp hoàn toàn với cấu hình công cụ

- Workflow cho verifier viết test độc lập trong `tests/independent/`.
- Cấu hình [verifier](.opencode/agent/verifier.md#L4-L21) mặc định chỉ cho sửa báo cáo, đường dẫn test phải cấp riêng.
- Hồ sơ feature ghi nhận bị từ chối ghi test, nên phải chạy kiểm chứng trong bộ nhớ.

Điều này tạo công việc chuẩn bị lại và làm bằng chứng khó tái sử dụng. Đây là vấn đề tích hợp giữa template và công cụ, không phải lỗi nghiệp vụ regulatory.

### Quy trình có giới hạn repair, nhưng thiếu cơ chế dùng lại evidence rõ ràng

Hồ sơ hiện có ba hợp đồng: feature, staging và adapter production, tổng **33 claim**, cùng ít nhất **sáu lượt verifier được ghi nhận**, tính cả repair và polish.

Không có bằng chứng quy trình là vòng lặp vô hạn; đã có giới hạn hai repair. Nhưng chưa quy định rõ:
- Evidence nào còn hiệu lực khi artifact không đổi.
- Thay đổi nào thực sự cần mở lại kiểm chứng.
- Gate trước deploy khác gate xác nhận deploy thành công thế nào.
- Khi nào báo cáo cũ chỉ là lịch sử, không phải blocker hiện hành.

Đây là chỗ cần tinh giản, thay vì bỏ kiểm tra an toàn.

## 4. Phần nào thực sự cần giữ?

Phase này có migration, bảo vệ quyết định sửa tay trước import và tương tác với worker. Vì vậy:
- Backup trước migration.
- Đúng DB và đúng hai dịch vụ.
- Phân biệt trước/sau COMMIT.
- Kiểm tra tương thích PG14.
- Verifier độc lập bắt lỗi có hậu quả dữ liệu.
- Phê duyệt production.

…đều có cơ sở.

**Phần cần giảm là viết lại công cụ, đọc lại lịch sử, kiểm lại cùng artifact và hỏi lại quyết định đã chốt — không phải giảm bảo vệ dữ liệu.**

## 5. Kế hoạch khắc phục tối thiểu tôi đề xuất

1. **Làm sạch trạng thái và mâu thuẫn tài liệu trước.** Một trang trạng thái hiện hành; archive lịch sử; sửa các hướng dẫn deploy/rollback/Git trái nhau. Giữ nguyên hợp đồng đã khóa và evidence cũ.
2. **Chỉnh cách AI tiếp tục công việc.** Đọc checkpoint và diff trước; chỉ mở lại phần tài liệu hoặc test bị ảnh hưởng. Ghi phạm vi phê duyệt một lần, không mặc định hỏi lại mỗi lượt.
3. **Sửa hướng dẫn local/RBAC theo code hiện tại.** Chốt một đường bootstrap đáng tin cậy; bỏ hướng dẫn công cụ deprecated khỏi luồng thông thường.
4. **Lưu bền vững script và test không chứa secret.** Đưa bộ đã kiểm chứng vào quản lý phiên bản khi được duyệt; giữ credential, backup và dữ liệu ngoài Git. Tái sử dụng cho release sau thay vì soạn lại.
5. **Đồng bộ workflow với quyền verifier và cách dùng lại evidence.** Giữ tính độc lập; kiểm tra bổ sung phải gắn với thay đổi hoặc rủi ro mới cụ thể.

**Chốt lại:** vấn đề lớn nhất là dự án có quy trình kiểm chứng khá chặt nhưng nền triển khai và bộ nhớ tài liệu chưa đủ ổn định để vận hành quy trình đó hiệu quả. AI đã làm tăng chi phí bằng các lỗi script, cập nhật tài liệu kiểu nối thêm và kiểm tra lặp. Đây là những điểm tôi cần sửa trong cách làm, thay vì tiếp tục yêu cầu anh duyệt thêm nhiều vòng.

Lượt rà soát này **chỉ đọc và đối chiếu; chưa sửa file, chạy test hay thao tác server**.
