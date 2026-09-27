# .opencode/

Cấu hình agent cho OpenCode — harness chính của template (xem ARCHITECTURE.md).

Đã xác nhận với OpenCode 1.18.32 (09/2026): `opencode agent list` nhận diện đúng `primary (primary)` và `verifier (subagent)` từ `agent/*.md` — vị trí file và cú pháp frontmatter `description`/`mode` là đúng.

Chưa xác nhận riêng phần `permission:` (allow/ask/deny theo path) có được thực thi đúng như khai không — `agent list` chỉ xác nhận agent được nhận diện, không kiểm tra từng rule permission. Cách xác nhận thật là quan sát khi dùng task thật: verifier có bị chặn sửa file ngoài `specs/*/VERIFICATION_RESULT.md` không. Nếu phát hiện permission không hoạt động như mô tả, chạy `opencode agent --help` / đọc lại https://opencode.ai/docs/permissions/ để đối chiếu cú pháp đúng của bản đang cài, và sửa lại phần `permission:` trong hai file agent — phần system prompt (nội dung sau frontmatter) không phụ thuộc version, giữ nguyên.
