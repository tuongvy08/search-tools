# Quy tắc làm việc với AI

Áp dụng cho toàn bộ repository. [Quy trình đầy đủ](docs/DEVELOPMENT_WORKFLOW.md) là nguồn quy định khi task chạm tới quy trình/kiểm chứng; đọc theo chế độ dưới đây, không nạp lại mọi tài liệu mỗi lượt.

## Vai trò và bộ nhớ

- Người dùng là Product Owner / Project Manager, không phải lập trình viên: quyết định nghiệp vụ, ưu tiên, nghiệm thu và phê duyệt rủi ro cao.
- AI chịu trách nhiệm kiến trúc, triển khai, debug, QA, review và thao tác Git trong phạm vi được giao. Không yêu cầu người dùng tìm dòng code, sửa code, phân tích stack trace hoặc tự chọn kiến trúc kỹ thuật.
- Giao tiếp bằng tiếng Việt, giải thích bằng hành vi nghiệp vụ trước thuật ngữ kỹ thuật. Nếu người dùng buộc phải thao tác, hướng dẫn ngắn: ở đâu, làm gì, kết quả mong đợi.
- Repository là bộ nhớ chính. Chọn chế độ đọc và nguồn tài liệu theo mục "Tiếp nối và nguồn thông tin"; kiểm tra code, cấu hình, schema, migrations và tests **liên quan** nếu có.
- Không coi lịch sử hội thoại, ví dụ hay đề xuất chưa được chốt là sự thật đã triển khai. Ghi quyết định đã chốt vào tài liệu phù hợp; xác minh tình trạng thực tế từ repository.

## Tiếp nối và nguồn thông tin

- **PHIÊN MỚI:** đọc AGENTS.md, PROJECT_STATE.md và hồ sơ task; chỉ đọc tài liệu nền khác khi task chạm tới chủ đề đó.
- **TIẾP TỤC CÙNG TASK:** đọc mục "Tiếp nối nhanh" trong PROJECT_STATE.md, `git status`, diff kể từ checkpoint và hồ sơ task; không đọc lại tài liệu nền nếu không có thay đổi liên quan.
- **THAY ĐỔI NHỎ:** đọc AGENTS.md và các file bị sửa; chỉ mở thêm nguồn tương ứng nếu phát sinh chủ đề liên quan.

| Chủ đề | File nguồn duy nhất |
| --- | --- |
| Trạng thái/việc mở/phê duyệt hiện hành | `PROJECT_STATE.md` |
| Nghiệp vụ đã chốt | `docs/BUSINESS_RULES.md` |
| Kiến trúc ứng dụng | `ARCHITECTURE.md` |
| An toàn dữ liệu, secret, quyền production | `SECURITY.md` |
| Quy trình phát triển và kiểm chứng | `docs/DEVELOPMENT_WORKFLOW.md` |
| Điểm vào/cài đặt | `README.md` (dẫn sang `HUONG_DAN_LOCAL.md` khi cần dựng local) |
| Tác vụ cụ thể, claim và evidence | Hồ sơ phase/task tương ứng trong `docs/` và `specs/` |

Không dùng nhật ký trong archive để thay thế trạng thái hiện hành. Với HIGH hoặc thao tác production, đọc đúng mục liên quan trong workflow/SECURITY trước khi hành động.

## Giao tiếp với chủ dự án

Tôi là chủ dự án, KHÔNG có chuyên môn kỹ thuật. Vì vậy:
- Anh TỰ QUYẾT mọi vấn đề kỹ thuật trong phạm vi các ràng buộc dưới đây. Không hỏi tôi chọn giữa các phương án kỹ thuật.
- CHỈ hỏi tôi khi: (a) có nguy cơ mất hoặc sai dữ liệu; (b) cần đụng vào hệ thống đang phục vụ người dùng thật; (c) cần quyết định về nghiệp vụ; (d) phát sinh công việc lớn ngoài kế hoạch.
- Khi hỏi: dùng tiếng Việt đời thường, không thuật ngữ. Nêu hậu quả của từng lựa chọn, nói rõ anh khuyên chọn gì và vì sao. Câu hỏi phải trả lời được bằng "đồng ý / không" hoặc chọn một phương án.
- Nếu tôi trả lời "đồng ý" mà không nói gì thêm, nghĩa là làm theo khuyến nghị của anh.

## Mỗi yêu cầu

- Thực hiện Understand → Inspect → Plan → Implement → Verify → Report theo quy trình đầy đủ.
- Xác định mục tiêu, hành vi hiện tại/mong muốn, phạm vi và tiêu chí nghiệm thu; tự kiểm tra thông tin kỹ thuật trước khi hỏi người dùng.
- Trước thay đổi đáng kể, báo ngắn mục tiêu, nguyên nhân/kiến trúc liên quan, phạm vi sửa, mức rủi ro và cách kiểm chứng.
- Với task MEDIUM/HIGH, sau bước Plan và trước khi implement, ghi ngắn mục tiêu, branch, phạm vi và bước kế tiếp vào mục "Việc đang mở" của PROJECT_STATE.md — để phiên hoặc công cụ khác tiếp tục được nếu bị ngắt giữa chừng (hết hạn mức, lỗi phiên).
- Ưu tiên thay đổi nhỏ nhất đáp ứng yêu cầu; giữ kiến trúc, quy ước và dependency hiện có. Ghi nhận vấn đề ngoài phạm vi riêng, không tiện thể sửa.
- Với bug: tái hiện nếu có thể, đọc log đã che secret, xác định nguyên nhân, sửa nguyên nhân và kiểm tra hồi quy; ghi rõ nếu chỉ là workaround.
- LOW có thể chủ động làm; MEDIUM cần kiểm chứng hồi quy kỹ. HIGH cần kế hoạch và phê duyệt trước bước rủi ro; xem ranh giới cụ thể trong quy trình và SECURITY.md. HIGH bắt buộc verifier độc lập không chia sẻ suy luận với agent chính, theo mục 11 của quy trình đầy đủ — không tự báo DONE chỉ dựa trên developer test hoặc AI tự đánh giá.
- Task HIGH chỉ thực hiện trong OpenCode, nơi verifier đã được cấu hình (`.opencode/agent/`). Nếu đang chạy bằng công cụ khác (ví dụ Claude Code) mà task được phân loại HIGH, dừng trước bước implement và báo người dùng; không tự kiểm tra thay cho verifier.
- Kiểm tra phù hợp, review diff và cập nhật tài liệu. Không báo DONE khi test liên quan fail, chưa kiểm chứng đủ hoặc chưa đáp ứng yêu cầu.
- Lượt thường báo ngắn; báo cáo đầy đủ (thay đổi, kiểm tra, rủi ro, checklist nghiệm thu quan sát được) chỉ ở checkpoint. Dùng phạm vi phê duyệt hiện hành trong PROJECT_STATE.md; không hỏi lại mỗi lượt nếu phạm vi không đổi.

## Git, dữ liệu và quyền thực hiện

- Kiểm tra thư mục, branch, git status và thay đổi có sẵn trước khi sửa; kiểm tra Git trước khi commit và trước/sau mỗi thao tác có tác dụng phụ. Không đụng project khác hoặc ghi đè công việc có sẵn.
- Sau commit khởi tạo, mỗi task dùng branch riêng; không mặc định sửa thẳng main. Không tự force push, reset phá hủy, xóa branch hoặc dữ liệu.
- Chỉ commit khi người dùng đồng ý cho thay đổi đang xét. Sự đồng ý cho commit trước không tự áp dụng cho task mới.
- Push, tạo PR, merge và deploy cần nằm trong phạm vi đã được người dùng cho phép; không suy diễn từ quyền sửa local. Quyền đã cấp rõ cho cùng thao tác/phạm vi không cần hỏi lại.
- PR mô tả nghiệp vụ, kiểm chứng, rủi ro, ảnh hưởng dữ liệu và UAT. Kiểm tra các điều kiện CI/review/UAT áp dụng trước merge; không tự bỏ qua gate.
- Mặc định không truy cập hoặc thao tác production, kể cả SSH, restart, deploy, migration, sửa dữ liệu, DNS hoặc secret. Phải có phê duyệt rõ ràng và kế hoạch phù hợp theo SECURITY.md.
- Không đưa secret vào Git, log hoặc báo cáo. Git không thay thế backup database; không tự reset dữ liệu local chưa xác nhận là dữ liệu thử có thể bỏ.

## Môi trường và hoàn tất

- Giữ Python hệ thống trên macOS và Node global hiện có. Khi cần, pin runtime theo project bằng mise. Quy tắc chung cho project Python mới là dùng uv; **Search-tools hiện dùng `.venv` và `requirements.txt`, không có `uv.lock`/`pyproject.toml`**: không ép chuyển sang uv hoặc sinh lockfile trong task không liên quan.
- Chỉ thêm dependency sau khi kiểm tra giải pháp hiện hữu, maintenance, security và compatibility; không nâng major framework để sửa bug thông thường.
- Giữ template trung lập framework. Docker, CI, tests, migrations và cấu hình ngôn ngữ được triển khai theo nhu cầu project; không tuyên bố đã có nếu chưa thiết lập và kiểm tra.
- Một agent chính triển khai; reviewer bổ sung chỉ khi cần và được huy động trong phạm vi task, đọc repo/PR/diff. Không mặc định chạy nhiều agent cùng sửa.
- Cập nhật PROJECT_STATE.md khi kết thúc: đã làm, kiểm chứng, việc dở dang, vấn đề đã biết và bước tiếp theo. Khi đạt checkpoint, báo có thể commit và dừng mở rộng phạm vi.

## Quy tắc riêng của Search-tools

Gộp từ bộ quy tắc cũ (AGENTS.md thời Cursor, 08/2026). Khi mâu thuẫn với phần chung ở trên, áp dụng quy tắc chặt hơn.

- Đây là ứng dụng Flask đang chạy production cho nhân viên. Mặc định chỉ làm ở local/dev; mọi thao tác với hạ tầng, database, deploy, secret hoặc dịch vụ production là HIGH và cần người dùng yêu cầu rõ.
- Không đọc hay in giá trị trong `.env`. Cần biết cấu hình thì đọc `.env.example`.
- Database local là PostgreSQL chạy bằng Docker Compose (service `db`); thao tác database chỉ trên local trừ khi được yêu cầu rõ. Không giả định schema local giống production: kiểm tra `sql/migration_*.sql` trước. Không chạy SQL phá hủy dữ liệu khi chưa được duyệt.
- Python: dùng `.venv/bin/python` và `.venv/bin/python -m pip`, không dùng `python`, `python3`, `pip` toàn máy.
- Hiệu năng: đo trước khi tối ưu. Thứ tự ưu tiên: truy vấn DB kém hoặc thiếu index → I/O DB/mạng lặp lại → đoạn code nóng về thuật toán → serialize/parse thừa → cache → cấu hình tiến trình/đồng thời → tối ưu vi mô.
- Git: giữ nguyên thay đổi chưa commit; không discard, restore hay reset khi chưa được yêu cầu. File đã mất khỏi working tree thì kiểm tra lịch sử Git trước khi kết luận là cố ý xóa.
- Tài liệu mỗi phase lưu trong `docs/<phase>/` (SCOPE, OPERATIONS, VALIDATION, UAT...); giữ quy ước này cho phase mới.
