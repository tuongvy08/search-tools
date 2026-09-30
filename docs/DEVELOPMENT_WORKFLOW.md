# Quy trình phát triển phần mềm với AI

Đây là quy trình chung được [AGENTS.md](../AGENTS.md) dẫn tới khi task chạm quy trình/kiểm chứng. Áp dụng theo phạm vi thực tế; không tạo công việc hình thức cho thay đổi nhỏ.

## 1. Vai trò và quyền quyết định

Người dùng là Product Owner / Project Manager: nêu mục tiêu nghiệp vụ, chọn ưu tiên, kiểm tra kết quả và duyệt rủi ro quan trọng. AI chịu trách nhiệm đề xuất kỹ thuật có lý do, tự đọc/sửa file, chạy kiểm tra và giải thích kết quả. Không chuyển việc debug hoặc sửa code sang người dùng.

AI chủ động về kỹ thuật trong phạm vi được giao, không tự mở rộng nghiệp vụ hoặc chấp nhận rủi ro thay người dùng. Chỉ hỏi quyết định nghiệp vụ chưa rõ hoặc thông tin/quyền thực sự thiếu sau khi đã tự kiểm tra. Nếu quyền công cụ chặn thao tác, báo rõ việc nào chưa thực hiện; không tìm cách vượt giới hạn.

Ưu tiên lần lượt: đúng nghiệp vụ, an toàn dữ liệu, ổn định production, đơn giản, dễ bảo trì, dễ rollback, hiệu năng, rồi mới đến tính mới của công nghệ.

## 2. Understand — Hiểu yêu cầu

Xác định vấn đề nghiệp vụ, hành vi hiện tại, hành vi mong muốn, phạm vi ảnh hưởng và tiêu chí nghiệm thu quan sát được. Với yêu cầu chưa đủ rõ, làm phần kiểm tra hữu ích trước rồi hỏi đúng quyết định còn thiếu.

## 3. Inspect — Kiểm tra trước khi sửa

Chọn chế độ đọc và nguồn thông tin trong AGENTS.md, sau đó kiểm tra code, API, schema, lịch sử migration, `.env.example`, cấu hình Docker/deploy và tests có liên quan nếu tồn tại. Không đọc hoặc in secret chỉ để hiểu tên cấu hình. Không suy đoán kiến trúc khi có thể xác minh.

Kiểm tra đúng repository, branch, git status và thay đổi chưa commit. Giữ nguyên công việc của người khác. Xác định môi trường và dữ liệu bị tác động trước khi chạy lệnh có thể ghi dữ liệu; không mặc định kết nối database là local.

Với bug: tái hiện nếu có thể, thu thập log đã che dữ liệu nhạy cảm và xác định nguyên nhân. Không sửa thử theo phỏng đoán. Nếu chưa thể tái hiện, ghi rõ bằng chứng hiện có và giới hạn kết luận.

## 4. Plan — Thông báo kế hoạch

Trước thay đổi đáng kể, trình bày ngắn: mục tiêu, nguyên nhân hoặc kiến trúc liên quan, phần sẽ sửa, mức rủi ro và cách chứng minh kết quả. Nêu ranh giới phạm vi khi cần giúp người dùng ra quyết định.

- LOW: thay đổi chỉ trên tài liệu, text/UI nhỏ, validation đơn giản, bug cô lập, cập nhật test. AI có thể chủ động triển khai trong phạm vi đã giao; tài liệu thuần túy không cần hợp đồng claim hoặc verifier.
- MEDIUM: logic API, query, nghiệp vụ hoặc tích hợp. AI có thể triển khai với kiểm tra hồi quy phù hợp.
- HIGH: auth, permissions, payment, hạ tầng, triển khai, migration dữ liệu quan trọng, thay đổi production hoặc thao tác phá hủy. Chuẩn bị và kiểm chứng phương án trong môi trường an toàn khi được phép; cần phê duyệt rõ trước bước làm thay đổi bảo mật, dữ liệu quan trọng hoặc production. Trước khi implement, tạo `specs/<task>/VERIFICATION.md` theo mục 11.

Với task MEDIUM/HIGH, sau khi trình bày kế hoạch và trước khi implement, ghi ngắn mục tiêu, branch, phạm vi và bước kế tiếp vào mục "Việc đang mở" của PROJECT_STATE.md. Nếu phiên bị ngắt giữa chừng (hết hạn mức, lỗi phiên, đổi công cụ), phiên sau đọc mục này cùng `git status` và `git diff` để tiếp tục, không phải đoán.

Đổi framework, database, ORM, hệ thống auth, nền tảng deploy, API contract quan trọng hoặc refactor diện rộng cần phê duyệt trước khi triển khai thay đổi đó. Đề xuất phải nêu vấn đề, giải pháp, lợi ích, rủi ro, phạm vi ảnh hưởng và rollback. Không coi yêu cầu sửa bug là quyền đổi kiến trúc.

Phân loại theo hậu quả thực tế, không chỉ tên file. Migration local có thể kiểm tra trên database thử đã xác nhận; migration production vẫn thuộc vùng bảo vệ trong SECURITY.md.

## 5. Implement — Triển khai

Sau commit khởi tạo, tạo branch riêng cho mỗi task, ví dụ `docs/development-rules`, `feature/customer-block` hoặc `fix/search-timeout`; trước khi dùng branch hiện có phải kiểm tra nó thuộc cùng công việc. Không tự chuyển branch làm xáo trộn thay đổi có sẵn.

AI tự sửa file, cấu hình, migration và tests khi cần. Giữ framework, database, ORM, auth, deploy, naming, cấu trúc và API conventions hiện có. Chỉ thay đổi phần nhỏ nhất giải quyết đúng yêu cầu; ghi nhận vấn đề khác riêng.

Trước khi thêm dependency, kiểm tra chức năng tương đương đã có, khả năng bảo trì, an toàn và tương thích; ưu tiên thư viện chuẩn hoặc dependency hiện hữu. Không nâng major framework trong task sửa bug thông thường.

Không che bug bằng bắt lỗi quá rộng, hardcode, bỏ validation/security check hoặc tăng timeout thiếu căn cứ. Nếu cần workaround, ghi rõ giới hạn, rủi ro và hướng xử lý tiếp.

## 6. Verify — Kiểm chứng

Chọn kiểm tra theo thay đổi: unit/integration/regression tests, lint, formatter, type check, build, migration validation, API smoke test hoặc khởi động ứng dụng. Không chạy mọi loại kiểm tra chỉ để đủ danh sách.

Với bug, thêm hoặc chạy kiểm tra hồi quy chứng minh lỗi đã được xử lý khi phù hợp. Với tài liệu, kiểm tra nội dung, liên kết, tính nhất quán và diff là đủ; không tạo test ứng dụng giả.

Ghi rõ lệnh hoặc cách kiểm tra, môi trường, kết quả và phần chưa kiểm tra. Phân biệt PASS, FAIL, NOT RUN và N/A; không biến kiểm tra bị bỏ qua thành PASS. Review toàn bộ diff trước đề xuất commit, bao gồm file mới, debug code tạm và secret.

### Dùng lại evidence

Evidence đã có **chỉ còn hiệu lực khi đồng thời**: (1) SHA256 artifact đang dùng khớp artifact đã kiểm; (2) không đổi code, migration, cấu hình hoặc runtime **liên quan tới claim**; (3) cùng môi trường đích mà evidence đó đã chứng minh. Xác minh hash và diff/phạm vi thay đổi trước khi dùng lại; nếu thiếu bằng chứng về một điều kiện thì không tự coi là PASS. Mock/local kiểm adapter cho production không thay thế kiểm chứng trên production thật; staging không chứng minh cutover production thành công.

| Loại thay đổi | Claim cần mở lại |
| --- | --- |
| Chỉ tài liệu diễn giải, không đổi lệnh, artifact hoặc điều kiện vận hành | Không mở lại claim thực thi; kiểm nội dung, liên kết và tính nhất quán tài liệu. Nếu sửa runbook làm thay đổi lệnh/gate, đánh giá như thay đổi vận hành bên dưới. |
| Code ứng dụng hoặc migration | Các claim về hành vi/nghiệp vụ, schema, tương thích và rollback phụ thuộc phần đổi; không tự mở lại claim độc lập. |
| Script deploy, bundle hoặc lệnh/gate runbook | Các claim preflight/prepare/cutover, tính toàn vẹn artifact, thứ tự, phục hồi và hậu kiểm bị ảnh hưởng; hash mới phải được chứng minh, không dùng PASS của artifact cũ. |
| Cấu hình, dịch vụ, quyền, DB hoặc runtime liên quan | Các claim đọc/ghi/phân quyền, tương thích runtime, service/DB và phục hồi liên quan; preflight lại điều kiện đích. |
| Chuyển môi trường đích (local → staging → production) | Các claim phụ thuộc môi trường (DB, service, cấu hình, backup, quyền, smoke/UAT) phải xác minh tại đích mới. Evidence thuần tĩnh của artifact có thể hỗ trợ kế hoạch kiểm tra, nhưng không coi PASS ở đích cũ là PASS ở đích mới. |

Chỉ kiểm lại các claim bị ảnh hưởng theo hợp đồng đã khóa, trừ khi hợp đồng HIGH được đổi theo mục 11 (khi đó phải chạy lại toàn bộ theo quy định). **Gate trước deploy** chứng minh điều kiện và artifact sẵn sàng, không chứng minh đã deploy; **xác nhận sau deploy** kiểm commit/schema/service/đăng nhập/UAT thật tại đích, chỉ được ghi PASS sau khi quan sát. Báo cáo cũ bị báo cáo mới thay thế phải gắn nhãn **LỊCH SỬ**, không coi FAIL cũ là blocker hiện hành; lưu nguyên evidence cũ, không sửa hồi tố.

Không áp dụng hồi tố để mở lại hợp đồng phase regulatory-manual-edit hiện tại. Quy tắc dùng lại evidence áp dụng cho lần deploy production sắp tới: xác nhận đúng SHA256 script/bundle và điều kiện đích trước khi quyết định dùng lại phần kiểm local; production vẫn cần phê duyệt, backup và kiểm chứng sau triển khai riêng.

## 7. Report — Báo cáo và nghiệm thu

Báo ngắn bằng tiếng Việt: đã thay đổi gì, file chính, đã kiểm tra gì, kết quả, rủi ro còn lại và trạng thái.

- READY FOR USER TEST: kiểm chứng kỹ thuật cần thiết đã đạt, chờ người dùng nghiệm thu. Với task HIGH, chỉ báo trạng thái này sau khi qua verifier độc lập và checklist 5 câu ở mục 11.
- PARTIALLY COMPLETE: còn việc hoặc kiểm chứng cần làm; nêu rõ phần thiếu.
- BLOCKED: không thể tiếp tục vì thiếu quyền, thông tin hoặc điều kiện bên ngoài; nêu cách gỡ chặn.
- DONE: đáp ứng tiêu chí hoàn thành và phần nghiệm thu đã thống nhất cho task.

Không báo DONE khi test liên quan fail hoặc thiếu kiểm chứng bắt buộc. Nếu lỗi nền đã tồn tại, tách rõ với thay đổi hiện tại và nêu ảnh hưởng đến kết luận.

Checklist UAT phải đơn giản: mở màn hình nào, thao tác gì, kết quả nhìn thấy là gì. Với CLI, tài liệu hoặc tác vụ không có UI, đưa cách kiểm tra đầu ra dễ hiểu. Không yêu cầu người dùng đọc code để nghiệm thu.

## 8. Git, PR và CI

Mỗi commit tương ứng một thay đổi logic, thông điệp rõ nghĩa như `fix: prevent duplicate order items`; chỉ commit sau khi người dùng đồng ý cho thay đổi đang xét.

Luồng dự kiến khi project đã có remote: branch riêng → kiểm tra local và review diff → commit được duyệt → push/PR được phép → CI/review áp dụng → merge được phép. UAT thực hiện trên môi trường thử phù hợp trước hoặc sau merge theo pipeline project; phải hoàn tất trước phát hành production khi release yêu cầu UAT.

Private GitHub repository, PR, GitHub Actions và bảo vệ main là chuẩn thiết lập dự kiến cho project có remote. Khi cấu hình, kiểm tra khả năng thực tế của repository/tài khoản và xác nhận quy tắc có hiệu lực: yêu cầu PR, status checks phù hợp, chặn force push/xóa main. Không tuyên bố bảo vệ đã bật khi chỉ có tài liệu mô tả.

Không merge nếu gate bắt buộc fail hoặc chưa chạy. Repo chưa có CI phải ghi rõ thiếu CI và kế hoạch thiết lập, không báo CI PASS. Push, tạo PR, merge hoặc deploy chỉ thực hiện trong phạm vi người dùng đã cho phép; quyền commit không tự bao gồm các bước đó.

## 9. Release và dữ liệu

Luồng release dự kiến: main đã qua kiểm tra → staging nếu project có → UAT → phê duyệt phát hành → backup nếu có dữ liệu bị ảnh hưởng → deploy production → smoke test → ghi nhận release. Quy trình cụ thể, môi trường và rollback phải được xác định trước khi dùng.

Đọc [SECURITY.md](../SECURITY.md) trước mọi thao tác liên quan dữ liệu, secret hoặc production. Mặc định không thao tác production. Git lưu lịch sử mã nguồn, không khôi phục thay cho backup database hay trạng thái dịch vụ.

## 10. Bộ nhớ, reviewer và checkpoint

Ghi nghiệp vụ đã chốt vào BUSINESS_RULES; kiến trúc vào ARCHITECTURE; trạng thái thực tế, việc dở dang, lỗi đã biết và bước tiếp theo vào PROJECT_STATE. Chỉ thêm tài liệu database/deploy, quyết định trong `docs/decisions/`, hồ sơ task trong `docs/tasks/` hoặc CHANGELOG khi có nội dung thực tế cần lưu. Khi PROJECT_STATE.md quá dài, chuyển phần lịch sử đã hoàn tất (không còn là trạng thái hiện tại) sang `docs/template-validation/` hoặc archive tương ứng của project — không xóa thông tin, chỉ đổi vị trí để giảm phần mỗi task mới phải đọc.

Một agent chính chịu trách nhiệm triển khai. Có thể cân nhắc reviewer độc lập cho auth, permissions, payment, migration lớn, security, architecture, infrastructure, concurrency và dữ liệu quan trọng; không bắt buộc cho thay đổi nhỏ. Với task HIGH, reviewer độc lập (verifier) là bắt buộc theo mục 11, không phải tùy chọn. Reviewer/verifier đọc repo/PR/diff và đưa nhận xét, không mặc định trở thành người sửa thứ hai.

Task đạt checkpoint khi: yêu cầu được đáp ứng; phần triển khai cần thiết đã có; kiểm tra liên quan pass; không phát hiện hồi quy nghiêm trọng; không còn debug code tạm hoặc secret; diff được review; tài liệu được cập nhật khi cần. Báo "Đây là checkpoint ổn định. Có thể commit tại đây." nếu chưa commit; dừng mở rộng task khi mục tiêu đã đạt.

## 11. HIGH-risk verification gate

Áp dụng cho mọi task HIGH theo mục 4. Mục tiêu: ngăn tình trạng AI tự đề bài rồi tự chấm — agent triển khai và agent xác minh không được chia sẻ cùng suy luận.

### Hợp đồng kiểm chứng trước khi code

Trước khi implement, agent chính tạo `specs/<task>/VERIFICATION.md` từ `specs/_TEMPLATE/VERIFICATION.md`, liệt kê từng claim nghiệp vụ cần đảm bảo, oracle khách quan để biết đúng/sai, loại test và ít nhất một failure case không phải happy path. File này sau khi implement bắt đầu là hợp đồng đã khóa: agent chính không được tự sửa để làm test dễ pass hơn. Cần đổi claim hoặc oracle phải ghi lý do vào mục "Change request log" trong chính file, xin phê duyệt (người dùng cho thay đổi yêu cầu; verifier độc lập đủ nếu chỉ làm rõ oracle), tăng version, và chạy lại toàn bộ verification từ đầu.

### Triển khai

Agent chính implement và viết developer test như bình thường theo mục 5–6. Developer test hữu ích nhưng không đủ để task HIGH đạt DONE.

### Verifier độc lập — không thấy suy luận của agent chính

Sau khi implement, một subagent verifier chạy trong context riêng, không kế thừa lịch sử hội thoại của agent chính (xem `.opencode/agent/verifier.md`). Câu lệnh gọi verifier chỉ được chứa đúng các phần sau, không thêm trường tự do:

- Yêu cầu/tiêu chí nghiệm thu liên quan, trích từ spec gốc, không diễn giải lại.
- Đường dẫn `specs/<task>/VERIFICATION.md` đã khóa.
- Diff code hoặc git range để verifier tự đọc.
- Lệnh chạy test/khởi động ứng dụng cần thiết.
- `VERIFICATION_RESULT.md` của lần fail trước nếu có — chỉ phần Observed/Expected/Reproduction/Classification, không có phần agent chính tự giải thích cách sửa.

Không đưa cho verifier: lịch sử hội thoại, kế hoạch sửa lỗi, giải thích hoặc kết luận "root cause" của agent chính, tóm tắt kiểu "đã sửa bằng cách...". Verifier tự hình thành nhận định từ code và spec, không phải từ cách agent chính nghĩ.

Verifier có quyền đọc toàn bộ source (read-only), bị cấm sửa application code, cấm truy cập production, cấm git push; chỉ được viết vào `specs/<task>/VERIFICATION_RESULT.md` và test độc lập của riêng nó nếu cần (`tests/independent/`). Với mỗi claim, verifier phải thử ít nhất một negative/adversarial case — một input hoặc trạng thái được nghĩ ra để cố làm implementation sai — không chỉ đọc lại test agent chính đã viết.

### Khi verifier FAIL

Mỗi lần FAIL, verifier phân loại theo đúng một trong bốn loại và ghi vào `VERIFICATION_RESULT.md`:

- IMPLEMENTATION_FAIL hoặc REGRESSION_FAIL: lỗi code, agent chính được sửa (repair).
- SPEC_AMBIGUOUS: yêu cầu chưa rõ, không phải lỗi code — dừng, hỏi người dùng ngay, không cho agent chính tự chọn cách hiểu.
- VERIFICATION_CONTRACT_PROBLEM: oracle hoặc claim trong VERIFICATION.md không thể chứng minh được — dừng, xử lý theo "Thay đổi hợp đồng" ở trên, không cho agent chính tự sửa test để dễ pass.

Với IMPLEMENTATION_FAIL hoặc REGRESSION_FAIL, ngân sách sửa tính theo task, không theo từng test: tối đa 2 lần repair cho mỗi task (`max_auto_repairs: 2`). Ngoài ra, nếu cùng một claim FAIL hai lần liên tiếp thì escalate người dùng ngay, dù chưa dùng hết 2 lần repair — xác suất nguyên nhân không còn là lỗi code nhỏ đã tăng đáng kể (có thể là sai kiến trúc, spec thiếu, oracle sai, hoặc vấn đề môi trường). Quy tắc quyết định:

- Verifier PASS toàn bộ claim → Human review.
- FAIL, classification SPEC_AMBIGUOUS hoặc VERIFICATION_CONTRACT_PROBLEM → Escalate người dùng ngay, không tính vào ngân sách repair.
- FAIL, classification IMPLEMENTATION_FAIL/REGRESSION_FAIL, claim đó fail lần đầu → agent chính repair, `attempt += 1`; nếu `attempt` vượt 2 → escalate.
- Cùng một claim FAIL hai lần liên tiếp → escalate người dùng ngay, không chờ hết ngân sách.
- Sau repair, verifier phát hiện lỗi ở một claim khác (không phải claim vừa sửa) → tính là lần fail đầu của claim đó, áp dụng lại quy tắc trên cho claim này.

Agent chính — không phải verifier — đọc và cập nhật khối "Task status" (attempt, same_claim_repeat_fail, next_action) trong `VERIFICATION_RESULT.md` trước và sau mỗi lần gọi verifier, vì verifier chạy với context sạch mỗi lần và không tự nhớ số lần đã fail. Verifier chỉ ghi Claim/Result/Classification cho lần chạy hiện tại, không tự đếm hoặc sửa attempt.

Sau mỗi lần repair, verifier chạy lại với context sạch như lần đầu — chỉ thêm phần Observed/Expected/Reproduction/Classification của lần fail trước, không thêm lý do agent chính đã sửa thế nào. Verifier không được yêu cầu đề xuất giải pháp cụ thể (ví dụ "sửa dòng X bằng mutex Y") — chỉ cung cấp evidence và classification, để verifier không trở thành một agent code thứ hai.

### Human review

Khi verifier PASS toàn bộ claim, agent chính trình bày theo đúng cấu trúc Claim → Test → Result → Independent verifier → Residual risk cho từng claim, không yêu cầu người dùng đọc code. Người dùng tự trả lời 5 câu sau trước khi approve, không chỉ kiểm tra đủ mục:

1. Claim này đúng là điều cần hệ thống đảm bảo không?
2. Nếu implementation sai, test này có thật sự FAIL không, hay chỉ là test tautological?
3. Evidence là kết quả quan sát được (output/log/database), hay chỉ là AI nói "PASS"?
4. Có tình huống nghiệp vụ quan trọng nào chưa được test?
5. Residual risk còn lại có chấp nhận được không?

Với task HIGH nhạy cảm — database/migration, dữ liệu production, phân quyền — nên có thêm một lượt review độc lập bằng model của nhà cung cấp khác với agent chính (ví dụ người dùng gửi link PR hoặc branch cho Claude), sau khi verifier PASS và trước khi approve merge. Lý do: agent chính và verifier hiện cùng dòng model nên có thể cùng bỏ sót một chỗ. Lượt review này đối chiếu PR với `VERIFICATION.md`, không thay thế verifier hay 5 câu hỏi trên.

Chỉ sau khi người dùng approve, task mới được báo READY FOR USER TEST hoặc DONE theo mục 7.
