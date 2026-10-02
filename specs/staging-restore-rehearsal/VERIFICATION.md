# Verification Contract — staging-restore-rehearsal v1

Tạo trước implement, ngày 2026-09-30, branch `ops/staging-restore-rehearsal`. Sau khi bắt đầu chuẩn bị lệnh thực thi, hợp đồng này khóa theo workflow mục 11. Không sửa các hợp đồng/artifact/evidence của phase deploy đã khóa.

## Yêu cầu gốc đã được PO chốt

- “CHỈ trên staging. Tuyệt đối không đụng production.”
- “Lấy lại vào một cơ sở dữ liệu TẠM mới, đặt tên rõ ràng (ví dụ có chữ restore_test và ngày). KHÔNG ghi đè cơ sở dữ liệu staging đang dùng. KHÔNG dừng dịch vụ staging.”
- “Dùng bản sao lưu staging đã có, không tạo bản sao lưu mới.”
- “Kiểm tra dung lượng đĩa trước khi bắt đầu.”
- “Xong thì xóa cơ sở dữ liệu tạm, và hỏi tôi trước khi xóa.”
- “Đưa cho tôi TỪNG lệnh một, giải thích bằng lời đời thường lệnh đó làm gì và có thay đổi gì không. Tôi sẽ tự chạy và dán kết quả. Không dùng mật khẩu hay chuỗi kết nối trực tiếp trong lệnh.”
- “Nếu kết quả bất thường ở bất kỳ bước nào thì dừng, báo tôi bằng lời dễ hiểu, không tự thử lại.”
- “Số lượng dữ liệu trong bản sao lưu có thể khác staging hiện tại vì staging đã được dùng sau lúc sao lưu. Chỉ dừng và báo tôi nếu chênh lệch lớn bất thường hoặc có bảng chính bị trống hay thiếu. Chênh lệch nhỏ mà giải thích được thì ghi lại và đi tiếp.”
- “Tiêu chí đạt của bài thử: lấy lại xong không báo lỗi, các bảng chính đều có và có dữ liệu, tình trạng đúng như trước 033. Kiểm tra riêng, chỉ đọc, rằng staging đang dùng đã có 033.”
- “Bắt đầu bằng các lệnh chỉ đọc và kiểm tra dung lượng đĩa.”

## Các giai đoạn kiểm chứng

Task tương tác nhiều lượt: S0 kiểm tra chỉ đọc (V1); S1 chuẩn bị/restore (V2–V3); S2 đối chiếu (V4–V5); S3 PO duyệt xóa và ghi kết quả (V6). Verifier có thể review từng giai đoạn đã chuẩn bị, nhưng các giai đoạn chưa chuẩn bị/thực thi phải ghi NOT RUN/PENDING, không coi là PASS hoặc DONE toàn task. Lệnh ghi chỉ được phát sau review độc lập phần an toàn liên quan; evidence server do PO cung cấp, verifier không tự truy cập server.

## Claim V1 — Bước đầu chỉ kiểm staging, không ghi hoặc lộ secret

Oracle: lệnh gửi PO chỉ dùng alias `staging`, hai unit staging hiện có và thư mục backup staging; nội dung script đã đọc/pin hash chỉ đọc trạng thái, metadata và SELECT, PGOPTIONS read-only. Không gọi backup, restore dữ liệu, migration hoặc start/stop/reload dịch vụ, không đọc `.env`; không in DSN/password. PO cung cấp output để xác nhận đúng `search_tools_staging`, web/worker cùng DB, active, 033 có, PG/client và free space. Output “completed” tự nó không chứng minh đúng đích hoặc đủ đĩa cho restore. Cần kiểm filesystem data directory trước bước tạo DB, không chỉ filesystem backup/code.

Test type: static review + negative tests local/mock không kết nối DB/server + manual evidence PO.

Failure case cần thử: web/worker khác DB, mất PID, lệnh/query lỗi hoặc cấu hình có secret giả phải dừng/in lỗi an toàn, không gọi lệnh ghi; output sai DB/033 thiếu không được phát bước ghi. Alias/server mapping phải được đối chiếu ở bước chỉ đọc trước ghi; không tự suy trạng thái từ Git.

## Claim V2 — Chỉ tạo DB tạm mới và chỉ restore archive staging có sẵn vào đó

Oracle: xác minh đúng PostgreSQL staging/đích local của dịch vụ và data filesystem bằng output chỉ đọc; buffer đĩa trước create/restore được đánh giá từ free bytes và kích thước DB nguồn, không chỉ kích thước dump nén. Tên DB tạm chứa `restore_test` và ngày, khác các DB ứng dụng; xác nhận chưa tồn tại, không có service trỏ vào. Archive dự kiến `/srv/backups/search-tools/regulatory-manual-edit-2ec9b6c-prechange.dump`: file thường, không symlink, đọc được, metadata/hash/TOC được ghi trước và giữ nguyên sau. Restore không dùng --create/--clean hoặc chuyển sang DB ứng dụng; đích explicit DB tạm trên cùng staging, không dùng DSN/password trong argv. Không tạo dump mới, không stop/restart/service config hoặc ghi DB staging đang dùng. Không tạo/xóa role hay extension ở DB khác để ép restore đạt.

Test type: local/mock adversarial command/target review + manual evidence PO.

Failure case cần thử: DB tạm đã có, sai tên/sai DB server, symlink/mất archive, disk thiếu, quyền thiếu; dừng không overwrite/rerun hoặc fallback sang DB khác. Nếu restore lỗi không tự cleanup hay retry.

## Claim V3 — Restore hoàn tất thật, không lấy đọc TOC thay cho restore

Oracle: PO gửi command/exit status, mốc thời gian UTC bắt đầu/kết thúc, restore success/no errors và kiểm tra DB tạm độc lập. Dùng pg_restore phù hợp phiên bản, exit-on-error và kiểm exit code; lỗi không báo PASS. Không xuất nội dung dữ liệu hay credential vào Git/log công khai.

Test type: local/mock failure/exit-code review + integration thực do PO chạy trên staging.

Failure case cần thử: archive hỏng, lệnh restore nonzero/mất SSH/timeout/một phần restore; giữ DB tạm và archive, dừng hỏi, không báo hoàn tất từ TOC hoặc số bảng tạo được.

## Claim V4 — Dữ liệu chính hiện diện; đối chiếu đúng thời điểm

Oracle: SELECT counts tách biệt cho `products`, `stock_items`, `regulatory_rules`, `regulatory_statuses` trên DB tạm; cả bốn bảng tồn tại và count >0. Ghi counts inactive của rules và footprint 030–032 hỗ trợ nhận diện snapshot. Nếu còn số liệu trước dump, dùng làm mốc đối chiếu chính; counts staging hiện tại chỉ tham khảo và gắn thời điểm vì có hoạt động sau backup. Không đòi count hiện tại bằng dump. Chênh lệch được ghi rõ, chỉ đi tiếp nếu nhỏ và có giải thích được PO xác nhận; trường hợp không đủ chứng cứ phân loại hoặc chênh lệch lớn bất thường thì báo PO, không tự coi là PASS. Không in row data. Snapshot counts chỉ hỗ trợ kiểm tồn tại/dữ liệu, không chứng minh toàn bộ từng dòng giống hệt.

Test type: read-only SQL review + manual UAT trên evidence PO.

Failure case cần thử: một bảng thiếu/rỗng, count chênh lớn chưa giải thích, không có snapshot cũ; thiếu snapshot không được bịa mốc hoặc dùng staging hiện tại như ảnh trước dump.

## Claim V5 — DB tạm trước 033; staging đang dùng có 033

Oracle: trên DB tạm, các footprint riêng của 033 đều vắng: hai bảng manual_keys/manual_events; hai cột revision/manual_protected; trigger zz_regulatory_rule_revision và function update_regulatory_rule_revision; các index riêng 033. DB staging đang dùng kiểm riêng bằng SELECT có footprint đầy đủ, không chỉ một marker. Không chạy 033 để ép trạng thái DB tạm. Giữ dịch vụ staging active; không sửa archive hoặc DB staging đang dùng.

Test type: read-only schema SQL review + manual evidence PO.

Failure case cần thử: DB tạm có partial/full 033 hoặc staging thiếu/partial 033; báo dừng, không chạy migration để làm đẹp kết quả.

## Claim V6 — Chỉ xóa đúng DB tạm sau PO đồng ý riêng; lưu kết quả đúng phạm vi

Oracle: ghi nguyên câu hỏi/xác nhận xóa riêng và tên DB tạm trong evidence; trước xóa kiểm lại identity, không có dịch vụ/connection ứng dụng trỏ vào, không dùng FORCE hoặc terminate backend tự động. Sau xóa SELECT xác nhận DB tạm không còn và dịch vụ staging vẫn active, archive không đổi. Chưa được duyệt xóa => chưa phát lệnh xóa. Ghi kết quả/thời gian thực tế vào PROJECT_STATE và phiếu chuẩn bị; PR riêng chỉ file liên quan/evidence không secret. PASS local hoặc review an toàn không thay kết quả restore staging thực; production luôn NOT RUN.

Test type: negative command review + manual confirmation/evidence PO + documentation diff review.

Failure case cần thử: chưa có duyệt xóa, tên DB không khớp, DB có kết nối/service trỏ vào; không xóa/force/terminate, dừng báo. Thời gian không có evidence => ghi chưa biết, không dùng ước tính thành thực tế.

## Ngoài phạm vi

Không truy cập/triển khai production; không tạo backup mới, chạy migration, đổi DB ứng dụng/dịch vụ/cấu hình; không sửa artifact deploy đã khóa; không coi bài thử này chứng minh mọi backup production phục hồi được hoặc mọi dòng dữ liệu giống hệt. Agent chỉ chạy kiểm chứng an toàn local, PO tự thực hiện từng lệnh staging. Không tự chọn cách xử lý dữ liệu nếu PO cần quyết định.

## Thay đổi hợp đồng

Không tự sửa sau khi implement bắt đầu. Thay đổi claim/oracle theo workflow mục 11: ghi lý do, xin phê duyệt đúng thẩm quyền, tăng version và chạy lại verification toàn bộ; không tính là repair.

### Change request log

Chưa có thay đổi sau khi khóa. Điều chỉnh mục 5 và ngoại lệ chênh lệch counts của PO đã có trước khi tạo v1.
