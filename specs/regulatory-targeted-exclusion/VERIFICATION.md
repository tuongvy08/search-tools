# Verification Contract v1 — regulatory-targeted-exclusion

**ĐÃ THAY THẾ, KHÔNG TRIỂN KHAI:** PO làm rõ tự động chỉ đối chiếu CAS,
giữ gán riêng theo hãng ưu tiên. Xem `../regulatory-cas-only/VERIFICATION.md`.

Ngày 2026-11-02. **DỰ THẢO CHỜ PO DUYỆT, CHƯA IMPLEMENT.**
Phạm vi HIGH: metadata quy tắc/đối chiếu và phương án dọn tình trạng sai.
Khóa hợp đồng khi bắt đầu implementation sau phê duyệt; verifier độc lập
không nhận lịch sử hội thoại hoặc giải thích cách triển khai của agent chính.

## Claim V1 — Loại trừ đúng một quy tắc tên

Một CAS được loại trừ chỉ mất tác động của quy tắc tên được chỉ định. Các
quy tắc khác và ngoại lệ sản phẩm hiện hành vẫn được xét theo ưu tiên cũ.

Oracle: PostgreSQL với dữ liệu giả gồm hai quy tắc tên cùng khớp, một quy
tắc CAS BLOCK, một ngoại lệ sản phẩm. So sánh `status_id`, `source`, policy
và note trước/sau; chỉ phần đóng góp của quy tắc bị loại trừ được phép mất.

Test type: integration PostgreSQL, HTTP search/quick quote/export liên quan.

Failure case cần thử: quy tắc tên bị loại trừ là ALLOW nhưng quy tắc CAS
còn lại BLOCK; kết quả bắt buộc vẫn BLOCK. CAS chỉ gần giống không bị loại.

## Claim V2 — Giữ hành vi cho sản phẩm ngoài phạm vi

Sản phẩm CAS khác, CAS trống, dung dịch và sản phẩm chỉ trùng tên vẫn nhận
kết quả cũ nếu không thuộc danh sách loại trừ của quy tắc đó.

Oracle: tập fixture nhiều sản phẩm và so sánh toàn bộ kết quả resolver
trước/sau thêm ngoại lệ, gồm các ghi chú được gộp từ nhiều quy tắc.

Test type: integration PostgreSQL và regression các entrypoint dùng resolver.

Failure case cần thử: hai quy tắc khác nhau cùng có tên; sửa ngoại lệ một
quy tắc không làm mất tác động quy tắc kia. Không loại CAS trống vì rỗng list.

## Claim V3 — Chỉnh ngoại lệ có kiểm soát và lịch sử

Thêm/sửa/xóa ngoại lệ theo CAS chỉ qua quyền regulatory hiện hành, xem
trước/xác nhận, lý do bắt buộc và revision đúng; không mất cập nhật đồng thời.

Oracle: trạng thái HTTP và snapshot DB trước/sau, số audit events và revision;
request lặp không tạo thêm thay đổi. CAS sai checksum bị từ chối.

Test type: HTTP/integration/concurrency/UAT.

Failure case cần thử: thiếu CSRF, quyền bị thu hồi giữa preview/confirm,
revision cũ, thiếu lý do, cùng request_id nhưng payload khác; không có ghi.

## Claim V4 — Import bảo toàn ngoại lệ thủ công

Import upsert và replace_scoped không ghi đè, xóa, bật lại hoặc tạo lại
khóa lịch sử của quy tắc có ngoại lệ đã chỉnh tay; preview cũ bị từ chối
nếu ngoại lệ thay đổi trước apply.

Oracle: snapshot quy tắc/ngoại lệ/manual keys/audit trước/sau hai loại import,
fingerprint và kết quả apply khi xen một lần sửa ngoại lệ.

Test type: integration PostgreSQL, concurrency.

Failure case cần thử: file cũ vắng quy tắc được bảo vệ hoặc giữ khóa cũ sau
đổi tên; ngoại lệ vẫn còn và importer không tạo bản trùng né bảo vệ.

## Claim V5 — Migration không tự đổi phân loại

Migration thêm metadata với danh sách ngoại lệ rỗng, không tự thêm ngoại lệ
hoặc thay đổi status/policy/kết quả phân loại. Chạy lại không mất dữ liệu.

Oracle: schema/snapshot dữ liệu và resolver trước/sau/lần chạy lại trên DB
tạm; rollback diễn tập theo kế hoạch riêng, không phục hồi đè dữ liệu mới.

Test type: migration rehearsal PostgreSQL, negative transaction test.

Failure case cần thử: chạy lại sau đã thêm ngoại lệ; ngoại lệ/audit phải giữ
nguyên. Lỗi giữa migration không để schema nửa chừng.

## Claim V6 — Dọn tình trạng chỉ trong phạm vi được duyệt

Không xóa tình trạng có tham chiếu còn sống hoặc lịch sử, không đổi nhãn
hoặc policy để né kiểm tra. Chỉ đúng định danh/nhãn đã đối chiếu và được
duyệt mới có thể được dọn; dữ liệu khác giữ nguyên.

Oracle: preflight counts và snapshot toàn bộ các bảng tham chiếu theo schema
thật, giao dịch khóa miền và kiểm lại ngay trước ghi. Thử chỉ trên DB giả.

Test type: integration PostgreSQL và kiểm chứng độc lập script nếu được tạo.

Failure case cần thử: số quy tắc bằng 0 nhưng có sản phẩm, bản xóa phục hồi,
manual key hoặc status event tham chiếu; phải từ chối. Định danh giữ nguyên
nhưng nhãn đã đổi sau preview cũng phải từ chối.

## Ngoài phạm vi

- Không tự xác định chất thuộc danh mục pháp luật nào.
- Không đổi toàn bộ quy tắc tên thành so khớp chính xác hoặc CAS-only.
- Không tự bật lại quy tắc Được bán hoặc sửa tên sản phẩm.
- Không truy cập production qua verifier. Không suy quyền commit/push/deploy
  từ việc PO cho làm local hoặc đăng nhập web.

## Thay đổi hợp đồng

Sau khi implement bắt đầu, thay đổi claim/oracle phải ghi lý do, xin duyệt
theo workflow, tăng version và chạy lại toàn bộ verification.

### Change request log

Chưa có.
