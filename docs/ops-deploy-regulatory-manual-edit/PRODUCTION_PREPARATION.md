# Phiếu chuẩn bị triển khai production — migration033

> **Đã thực hiện 2026-10-02 — THÀNH CÔNG.** Phiếu dưới đây là bản chuẩn bị lịch sử; kết quả, evidence và hai chỗ phải sửa script xem [hồ sơ release](PRODUCTION_RELEASE_2026-10-02.md).

Ngày 30/09/2026. **CHỈ CHUẨN BỊ — CHƯA ĐƯỢC PHÉP TRIỂN KHAI.**
Đưa bản đã thử đạt lên cho nhân viên thật; không làm PR3–PR5 hoặc đổi cách đăng nhập kèm theo.

## 1. Trình tự và thời gian dự kiến

Thời gian dưới đây là ước tính lập lịch, chưa đo trên production; chưa tính xử lý lỗi. Chỉ bắt đầu sau một lần anh phê duyệt rõ ràng ở mục 5; không chờ xác nhận giữa các bước trong script cutover.

1. **Kiểm tra trước khi làm: 3–5 phút.** Đúng máy, đúng dữ liệu, bản đang chạy vẫn là bản dự kiến, chưa có thay đổi 033, đủ đĩa và không còn việc nhập file đang chờ/chạy. Sai bất cứ điều kiện nào: dừng, đối chiếu; không sửa dữ liệu để ép qua kiểm tra.
2. **Chép gói và chuẩn bị thư mục bản mới: 2–5 phút.** Giữ nguyên bản đang phục vụ; kiểm tra đường dẫn khởi động mới. Chép lỗi hoặc chuẩn bị lỗi: không chuyển bản, giữ hiện trạng và dấu vết, không tự chạy lại/xóa để thử tiếp.
3. **Tạm dừng xử lý nhập file rồi dừng trang web; sao lưu: khoảng 1–3 phút.** Kiểm tra bản sao lưu có nội dung và đọc được danh sách. Sao lưu lỗi: không đổi dữ liệu; chỉ mở lại bản cũ nếu chứng minh dữ liệu chưa thay đổi và chưa chuyển bản. Đọc danh sách được chưa chứng minh đã phục hồi thử thành công.
4. **Cập nhật dữ liệu 033 và kiểm tra: khoảng 0,5–1 phút.** Nếu lỗi và chứng minh chưa ghi hoàn tất, không có giao dịch dở, chưa chuyển bản: có thể mở lại bản cũ có kiểm soát. Nếu đã ghi hoàn tất hoặc chưa rõ: giữ hai dịch vụ dừng, không chạy bản cũ; xử lý theo mục 3.
5. **Chuyển sang bản mới, mở dịch vụ, kiểm tra tự động: khoảng 0,5–1 phút.** Nếu đường dẫn, dữ liệu, trang đăng nhập hoặc tệp giao diện không đúng: giữ/dừng cả hai dịch vụ ghi dữ liệu, không quay bản cũ; ưu tiên sửa tiến tới.
6. **Kiểm tra riêng sau chuyển và anh mở ứng dụng kiểm tra: 3–5 phút.** Đăng nhập, tra cứu, mở Quản trị → Quy tắc quản lý và xem quy tắc/lịch sử có sẵn. Không tạo quy tắc giả hay nhập file mẫu vào dữ liệu thật. Lỗi: chưa thông báo hoàn tất; dừng ghi nếu chưa bảo đảm an toàn, đánh giá và xin duyệt cách phục hồi.

## 2. Nhân viên có thể bị gián đoạn bao lâu?

- Bước 1–2 vẫn phục vụ bình thường. Từ lúc ngừng ghi/upload đến khi anh kiểm tra xong, nhân viên không nhập file, sửa quy tắc hoặc xác nhận dữ liệu.
- Trang web dự kiến ngừng **2–5 phút**, nên đặt cửa sổ bảo trì **10–15 phút**. Lượng dữ liệu sao lưu và lỗi có thể kéo dài thời gian, vượt cửa sổ này. Dành khoảng **20–30 phút** cho toàn bộ buổi, có người theo dõi tới khi kiểm tra xong.

## 3. Khi nào sửa tiến tới, khi nào phục hồi từ sao lưu?

- **Chưa ghi hoàn tất 033, và đã chứng minh chắc chắn chưa đổi dữ liệu/chưa chuyển bản:** quay lại hai dịch vụ cũ; thường không cần phục hồi sao lưu. Nếu kết nối bị ngắt hoặc chưa rõ trạng thái thì không áp dụng cách này, không tự chạy lại toàn bộ.
- **Đã ghi hoàn tất 033, dù chưa dùng tính năng mới:** ưu tiên **sửa tiến tới** — giữ dữ liệu mới, sửa bản mới rồi kiểm tra và xin duyệt mở lại. 033 đã bảo vệ các quy tắc ngừng áp dụng; bộ xử lý cũ không hiểu cơ chế này. Không chạy bản cũ, không chạy lệnh gỡ 033 dành cho local/test.
- **Dữ liệu bị hỏng không thể sửa an toàn, hoặc quyết định bắt buộc trở về dữ liệu trước 033:** cần **phục hồi từ sao lưu**, không chỉ quay mã nguồn. Phục hồi vào một cơ sở dữ liệu **mới**, đối chiếu, xin anh duyệt riêng việc chuyển sang đó và chấp nhận mất thay đổi sau lúc sao lưu. Không phục hồi đè dữ liệu đang dùng; giữ dữ liệu, bản sao lưu và dấu vết cũ để đối chiếu.
- **Không chắc đã ghi hoàn tất hay không, hoặc chưa xác nhận dịch vụ đã dừng:** kiểm tra đúng dữ liệu và tiến trình trước; không tự coi là đã dừng/an toàn, không vội phục hồi. Nếu báo `HOLD_STOP_UNCONFIRMED`, người vận hành phải xác minh và xử lý việc dừng.

### Xác nhận chỉ đọc: sao lưu lỗi có chặn đổi dữ liệu không?

**Có. Đọc script hiện có xác nhận: lỗi tạo/kiểm tra sao lưu chặn chạy migration033 và chuyển phiên bản.** Không chạy script để đưa ra kết luận này. Các điều kiện dừng tự động:

- Trước sao lưu: đường dẫn backup không hợp lệ/đã có dump hoặc dấu vết cutover; thiếu đĩa (buffer ít nhất 5 GiB hoặc 4 lần kích thước DB, lấy mức lớn hơn); phiên bản `pg_dump`/`pg_restore` không đúng PG14. Các kiểm tra đích, bản mã, cấu trúc DB, dịch vụ và hàng đợi cũng phải đạt; không xác nhận được worker/web đã dừng hoặc xuất hiện job mới thì không đi tiếp.
- `pg_dump` lỗi, không gọi được hoặc quá 30 phút → `BACKUP_PG_DUMP`; file dump thiếu, không phải file thường, là liên kết hoặc rỗng → `BACKUP_ARCHIVE_FILE`.
- `pg_restore --list` lỗi, không gọi được hoặc quá 2 phút → `BACKUP_PG_RESTORE_LIST`; danh sách có ít hơn 10 dòng → `BACKUP_TOC_NOT_EMPTY`. Lỗi đọc metadata hoặc ghi mốc hoàn tất sao lưu cũng chặn bước kế tiếp.
- Chỉ sau mọi kiểm tra trên mới tới `psql` chạy 033. Khi lỗi, script đi vào nhánh phục hồi rồi thoát với mã lỗi; **không chạy tiếp 033, không tự thử lại**. Có thể tự mở lại dịch vụ cũ nếu chưa COMMIT và chưa lắp đường dẫn khởi động mới; nếu không xác nhận được phục hồi thì cố dừng cả hai dịch vụ và báo người vận hành.

Căn cứ: `cutover_regulatory_staging.py` ngoài Git, dòng 103–112 (lệnh lỗi/timeout), 539–567 (phục hồi), 570–694 (kiểm tra → sao lưu → 033), 760–777 (thoát lỗi); [runbook production](PRODUCTION_RUNBOOK.md), mục “Nếu lỗi / thời gian dừng / nghiệm thu”. Đây là đảm bảo **script không áp dụng 033 khi kiểm tra sao lưu thất bại**, không phải chứng minh archive phục hồi đầy đủ hoặc cấm mọi thay đổi nghiệp vụ sau khi dịch vụ cũ mở lại. Mất điện/cưỡng bức kết thúc có thể bỏ qua xử lý phục hồi; phải kiểm tra trạng thái thật, không tự chạy lại.

### Hồ sơ thử phục hồi và đề xuất riêng — CHƯA THỰC HIỆN

Hồ sơ staging chỉ có backup **37,3 MB** và kiểm tra danh sách đạt; lần “phục hồi” trước là mở lại **dịch vụ bản mới**, không phục hồi dữ liệu từ backup. **(Đã lỗi thời từ 2026-10-02: xem mục “Kết quả thử phục hồi staging” ngay dưới; staging nay ĐÃ phục hồi archive thật vào DB tạm.)** ~~Chưa có bằng chứng staging đã phục hồi archive thật vào DB tạm.~~ Thử `pg_dump`/restore thật trên DB tạm PostgreSQL 14 **local** đã có, nhưng không chứng minh backup staging/server phục hồi được. Căn cứ: [VALIDATION](VALIDATION.md), dòng 10–28, 62–64, 132–159; [STAGING_RUNBOOK](STAGING_RUNBOOK.md), dòng 387–397, 450–454.

**Khuyến nghị thử riêng, khoảng 15–30 phút** (có thể lâu hơn tùy dung lượng/quyền/đĩa): người vận hành xác minh đích staging, kiểm tra metadata archive hiện có và dung lượng; tạo một DB tạm mới, không có web/worker trỏ vào; dùng client phù hợp để phục hồi archive vào đó, kiểm tra kết quả, bảng/ràng buộc và số liệu so với mốc trước sao lưu nếu có. Nếu thiếu mốc đối chiếu, ghi rõ giới hạn, không báo dữ liệu khớp đầy đủ. Không đổi DB ứng dụng, không ghi vào DB staging đang dùng, không chạy migration/cutover, không đụng production, không đưa backup/secret vào Git. Giữ nguyên archive; chỉ dọn đúng DB tạm sau xác nhận riêng. Việc này cần phê duyệt và kế hoạch kiểm chứng riêng trước thực hiện.

**Câu xin phép riêng:** “Tôi khuyên thử phục hồi bản sao lưu staging vào một nơi tạm riêng, khoảng 15–30 phút, để kiểm tra khả năng lấy lại dữ liệu mà không đụng hệ thống thật. Anh có đồng ý cho chuẩn bị và thực hiện lần thử này không?” Chờ anh đồng ý mới làm; (đã được PO phê duyệt và thực hiện trên staging, kết quả bên dưới), không tự thêm thành bước bắt buộc của release.

### Kết quả thử phục hồi staging (2026-10-02) — chỉ staging, production NOT RUN

PO tự chạy từng lệnh; hồ sơ lệnh/evidence: [OPERATIONS](../../specs/staging-restore-rehearsal/OPERATIONS.md), kiểm chứng: [VERIFICATION](../../specs/staging-restore-rehearsal/VERIFICATION.md) và [kết quả verifier](../../specs/staging-restore-rehearsal/VERIFICATION_RESULT.md).

- **Kết luận:** bản sao lưu staging có sẵn (`regulatory-manual-edit-2ec9b6c-prechange.dump`, 37.333.943 byte, tạo trước migration 033) **phục hồi được** vào một cơ sở dữ liệu tạm mới: không báo lỗi, bốn bảng chính đều có dữ liệu, tình trạng đúng như **trước 033** (kiểm theo hình dạng lẫn theo tên). Staging đang dùng được kiểm riêng, chỉ đọc: **có** 033. DB staging đang dùng, dịch vụ và file sao lưu không bị thay đổi; DB tạm đã xóa sau khi PO đồng ý riêng.
- **Thời gian thực tế (UTC 2026-10-02):** tạo DB tạm trống 0,523 giây (04:08:27); **khôi phục dữ liệu 94,052 giây** (05:05:07 → 05:06:41) cho DB nguồn ≈1,23 GB (DB tạm sau restore ≈705 MB); xóa DB tạm 0,565 giây (05:42:45), giải phóng ≈705 MB. Số này chỉ là thời gian của máy chủ staging lúc đó; không dùng làm dự báo cho production (khác phiên bản PostgreSQL, dung lượng và tải).
- **Số liệu bốn bảng chính (DB phục hồi so với staging 01/10 06:45 UTC):** products 1.262.173 = 1.262.173; stock_items 2.295 = 2.295; regulatory_statuses 6 = 6; regulatory_rules 6.000 so với 6.003 (+3), rule inactive 0 so với 3 (+3). **Giải thích bằng dữ liệu:** 3 quy tắc chênh đều tạo 08:12–08:16 UTC ngày 29/09, tức 9–14 phút sau lúc sao lưu (08:03), cả 3 đang ngừng dùng; DB phục hồi không có bản ghi nào tạo/sửa sau lúc sao lưu. Chênh lệch nhỏ, có giải thích, đúng tiêu chí PO đã chốt. Chỉ so số đếm, không chứng minh từng dòng giống hệt.
- **Phát hiện khi chuẩn bị (đã xử lý, giữ làm bài học):** (1) bản chép hash ban đầu thiếu một ký tự; (2) một tùy chọn không có trong công cụ tạo DB bản 16 làm lệnh tạo lần đầu bị từ chối ngay phía máy khách, DB không được tạo — kiểm chỉ đọc xác nhận không có gì để dọn; (3) các kiểm tra giả lập đơn thuần không bắt được lỗi dạng này nên verifier phải chạy công cụ PostgreSQL 16 thật trên dữ liệu giả.
- **Giới hạn (không suy ra điều chưa chứng minh):** đây là staging PostgreSQL 16.15, dữ liệu staging; **không** chứng minh production (PostgreSQL 14.24) hay backup production phục hồi được; không chứng minh từng dòng dữ liệu giống hệt; thời gian không dự báo được cho production. Các kiểm tra verifier là PASS local/mock và PostgreSQL 16 thật dùng một lần với dữ liệu giả, chấp nhận hai điểm còn lại đã báo PO: kiểm “DB tạm trống” chưa được verifier context sạch chứng nhận lại sau lần sửa cuối, và test phụ cũ còn một số test đếm sai/lỗi thời (không ảnh hưởng script).


## 4. Anh cần chuẩn bị/xác nhận gì trước?

- Chọn giờ ít người dùng; thông báo nhân viên khoảng bảo trì 10–15 phút, yêu cầu ngừng nhập/sửa/xác nhận và chờ thông báo mở lại; nếu kéo dài thì cập nhật thông báo.
- Chỉ định người chạy lệnh có quyền máy chủ (**mặc định anh tự chạy như các lần trước**) và người kiểm tra ứng dụng; có người theo dõi để báo/xử lý nếu lỗi.
- Xem năm câu duyệt và rủi ro trong [VALIDATION](VALIDATION.md#human-review--claim--test--result--independent-verifier--residual-risk): giới hạn nhận diện tiến trình web S1 vẫn chưa sửa; bộ xử lý nhập file có thể nhận việc trước khi kiểm tra cuối xong; bản sao lưu mới chỉ được kiểm tra danh sách, chưa có bằng chứng phục hồi đầy đủ production.
- Chỉ bắt đầu khi có phê duyệt riêng, không còn việc nhập file chờ/chạy, xác nhận ngừng ghi/upload, hash gói/script khớp bản duyệt và điều kiện máy chủ hiện tại phù hợp. Kết quả local/staging không thay kiểm tra trên production.
- **Quyết định PO:** bỏ hai điểm chờ giữa chừng, thay bằng một lần phê duyệt trước bắt đầu để giữ nguyên script và artifact đã khóa. Các điều kiện dừng tự động vẫn giữ nguyên; không bỏ kiểm tra hay cố chạy tiếp khi lỗi.

## 5. Một lần phê duyệt trước bắt đầu; ai chạy lệnh?

**Tôi không có quyền chạy lệnh trên production, kể cả lệnh chỉ đọc.** Anh chạy từ Terminal trên Mac theo từng lệnh tôi đưa và gửi kết quả đã che thông tin nhạy cảm; hoặc anh chỉ định người vận hành có quyền. Tôi đối chiếu kết quả và chỉ đưa bước tiếp theo khi đủ điều kiện.

**Câu xin phê duyệt, chỉ dùng khi anh đã chọn thời điểm:**

> “Vào thời điểm anh chọn, anh hoặc người vận hành sẽ chạy bộ lệnh đã duyệt để kiểm tra, chuẩn bị bản mới, tạm dừng dịch vụ, sao lưu, cập nhật dữ liệu rồi chuyển bản mới. Nhân viên dự kiến không truy cập được 2–5 phút; xin dành cửa sổ 10–15 phút, có thể lâu hơn nếu lỗi. Nếu tạo hoặc kiểm tra sao lưu lỗi, script không chạy cập nhật dữ liệu; nhưng kiểm tra danh sách chưa bảo đảm phục hồi đầy đủ. Rủi ro chính là lỗi sau cập nhật có thể kéo dài gián đoạn, giới hạn nhận diện tiến trình web vẫn còn, và bộ xử lý nhập file có thể nhận việc trước khi kiểm tra cuối xong. Sau khi dữ liệu đã cập nhật, không tự quay về bản cũ; ưu tiên sửa bản mới, phục hồi sao lưu cần duyệt riêng và có thể mất thay đổi sau sao lưu. Nhân viên phải ngừng nhập/sửa/xác nhận trong cửa sổ này. Anh có đồng ý bắt đầu theo kế hoạch này không?”

Chỉ câu trả lời rõ **“đồng ý bắt đầu”** tại thời điểm đã chọn mới cấp phép cho lần thực thi này. Không có xác nhận giữa chừng trong cutover; vẫn kiểm kết quả từng lệnh, đúng đường dẫn sau Prepare và dừng khi lỗi theo runbook. Nếu điều kiện/artifact khác bản duyệt, dừng đánh giá lại; phê duyệt không bao gồm tự rerun, sửa artifact, đổi DB hay phục hồi ngoài kế hoạch.

Các lệnh hiện có trong [runbook](PRODUCTION_RUNBOOK.md) dưới đây **chỉ để biết trước, không chạy trong đợt chuẩn bị**:

```bash
OPS_DIR='/Volumes/DATA/Development/_ops/search-tools'
PF='sudo -n python3 -u - search-tools-pg.service search-tools-import-worker.service /var/backups/search-tools-production'
# Kiểm tra trước, chỉ đọc (phải kiểm đúng bản preflight riêng trước khi dùng).
ssh python "$PF" < "$OPS_DIR/preflight_readonly.py"
# Chép đúng gói đã duyệt, rồi chuẩn bị bản mới; chỉ sau phê duyệt ghi.
scp -p "$OPS_DIR/search-tools-regulatory-6155f25-to-2ec9b6c.bundle" python:/tmp/search-tools-regulatory-6155f25-to-2ec9b6c.bundle
ssh python 'sudo -n python3 -u - --production' < "$OPS_DIR/prepare_regulatory_staging.py"
# Chỉ khi Prepare/đường dẫn đạt và cửa sổ đã duyệt: cutover chạy liền, tự dừng khi lỗi.
ssh python 'sudo -n python3 -u - --production' < "$OPS_DIR/cutover_regulatory_staging.py"
# Kiểm tra sau chuyển, chỉ khi dịch vụ đã mở lại; không dùng khi đang dừng.
ssh python "$PF" < "$OPS_DIR/preflight_readonly.py"
```

Giữ nguyên script, bundle và evidence đã khóa; các lệnh trên chỉ chép lại runbook, **không phải quyền chạy ngay**. Chưa có thời điểm và câu “đồng ý bắt đầu”, nên hiện vẫn dừng ở chuẩn bị.

## 6. Dấu hiệu triển khai thành công

- Cả trang web và bộ xử lý nhập file thực sự chạy bản **`2ec9b6c`**, cùng đúng dữ liệu production; thay đổi 033 và dữ liệu bảo vệ đúng, số lượng dữ liệu nghiệp vụ không lệch.
- Kiểm tra tự động báo `PRODUCTION TECHNICAL CUTOVER PASS`, trang đăng nhập đáp ứng và tệp giao diện đúng bản; kiểm tra riêng sau chuyển không phát hiện sai đích/cấu trúc/dịch vụ.
- Anh đăng nhập thật, tra cứu bình thường, mở danh sách quy tắc và lịch sử được; không chỉ thấy trang đăng nhập là đủ.
- Lưu bằng chứng kiểm tra và thông tin bản sao lưu không chứa secret; ghi nhận phát hành, rồi mới thông báo nhân viên dùng lại. **Hiện chưa có các bằng chứng này, vẫn coi production chưa triển khai.**
