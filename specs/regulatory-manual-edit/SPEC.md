# Spec — Sửa và thêm quy tắc pháp chế thủ công

- Version: 2 — **CẬP NHẬT THEO DUYỆT CÓ ĐIỀU CHỈNH CỦA PO**, 2026-09-27.
- Risk: **HIGH** (ghi dữ liệu quyết định pháp chế, quyền ghi, import thay thế).
- Branch: `feature/regulatory-manual-edit`; base: `main@db26d40`.
- Lượt hiện tại chỉ viết spec. Chưa implement, chưa tạo/chạy migration,
  chưa chạy test ứng dụng, không truy cập staging/production.
- Hợp đồng kiểm chứng: [VERIFICATION.md](VERIFICATION.md), theo
  [template](../_TEMPLATE/VERIFICATION.md) và mục 11 của
  [quy trình](../../docs/DEVELOPMENT_WORKFLOW.md).
- Kế hoạch schema và rollback: [MIGRATION.md](MIGRATION.md).
- `VERIFICATION_RESULT.md` chưa tạo: chỉ verifier độc lập ghi evidence sau
  khi có implementation; không dùng tài liệu này như bằng chứng PASS.

## 1. Quyết định gốc của Product Owner — A–H

Quyết định hiện hành, không phải mô tả tính năng đã có. A/B/E/F/G giữ
nguyên văn; C/D/H dưới đây đã cập nhật theo điều chỉnh của PO ngày
**2026-09-27**, thay thế phần tương ứng của v1:

> A. Dùng quyền menu "Quy tắc quản lý" hiện có, không tách quyền sửa riêng.
>
> B. Có lịch sử cho thay đổi thủ công: người sửa, thời gian, nội dung trước/sau. Bắt buộc nhập lý do khi đổi tình trạng hoặc ngừng áp dụng. Xem được lịch sử trên từng quy tắc. Import vẫn dùng nhật ký tác vụ hiện có.
>
> C. Không xóa hẳn bằng thao tác thủ công hoặc đối với mục đã được bảo vệ.
> Chỉ ngừng áp dụng, có khôi phục. Mục thuần import vẫn theo hành vi
> replace_scoped hiện tại.
>
> D. Import luôn giữ nguyên mục thủ công (thêm tay, sửa tay, ngừng áp dụng)
> ở cả hai chế độ, kể cả mục vắng trong file. Bước xem trước phải liệt kê
> rõ các mục được giữ hoặc bị xung đột. BỎ tùy chọn "cho file ghi đè" khỏi
> bản đầu. Muốn lấy nội dung từ file cho mục thủ công thì sửa tay mục đó.
> Import không được ghi đè, xóa hay tự bật lại quy tắc đã ngừng áp dụng thủ công.
>
> E. Giữ cơ chế hiện tại. Trùng hoàn toàn thì dẫn tới mục có sẵn để sửa. Cùng CAS/mã khác tình trạng thì cảnh báo, vẫn cho thêm.
>
> F. Tìm trong giá trị của quy tắc (tên/CAS/mã), lọc theo tình trạng và trạng thái áp dụng, có phân trang. Tìm "quy tắc nào tác động tới sản phẩm X" để phase sau.
>
> G. Sửa được giá trị, tình trạng, ghi chú. Không sửa loại đối chiếu. Người sửa tự xác nhận sau khi xem trước/sau.
>
> H. Chưa làm số sản phẩm bị ảnh hưởng, chỉ hiện cảnh báo chung.

Điều chỉnh kỹ thuật được PO duyệt cùng ngày: giữ bảng khóa lịch sử; backfill
bảo vệ mọi rule inactive, không tạo lịch sử giả; bỏ bảng
`regulatory_rule_manual_previews`; xem trước tay trên giao diện, khi xác nhận
server kiểm tra quyền, CSRF, validation, expected revision và chống gửi lặp.
Chi tiết xung đột import chọn cách lưu đơn giản như mục 5.4, không bảng riêng.

## 2. Hiện trạng và phạm vi

Đã kiểm tra code tại base, không kiểm tra database đang chạy:

- `admin_regulatory.py`, `templates/admin_regulatory.html`,
  `static/admin_regulatory.js`: tình trạng/màu/thứ tự và import; chưa có
  tìm/sửa từng quy tắc. `search.py` quick-rule/quick-rule-delete trả 410.
- `regulatory_import_jobs.py`: upsert theo tình trạng + trường + giá trị;
  replace_scoped xóa rule active vắng trong file theo trường + tình trạng.
  Hiện import đặt `is_active=true` khi trùng, chưa bảo vệ dữ liệu thủ công.
- `regulatory.py`: CAS/code khớp chính xác, tên khớp chứa chuỗi, không giới
  hạn brand; resolver theo ưu tiên tình trạng; ngoại lệ sản phẩm là cơ chế khác.
- Migrations 003/026: `regulatory_rules` có ID, status_id, trường, giá trị,
  note, is_active, timestamps; UNIQUE theo status_id + trường + upper(btrim(value)),
  **kể cả dòng inactive**. 026 có status/import events, chưa có lịch sử rule.
- Migrations 027/028 chỉ thêm màu; 030 và `admin_permissions.py` quản lý
  quyền menu `regulatory`. Không thêm menu/permission key trong phase này.

Trong phạm vi: bổ sung quản lý từng quy tắc, audit thủ công, điều chỉnh cả
preview/apply import và worker để tôn trọng bảo vệ thủ công, kiểm tra hồi quy.
Không thay resolver ưu tiên, chính sách ALLOW/BLOCK, ngoại lệ theo sản phẩm,
luồng import sản phẩm/tồn kho; không mở lại endpoint quick-rule cũ.

## 3. Quy ước v2 theo phê duyệt ngày 2026-09-27

Giữ số mục từ v1 để đối chiếu phê duyệt; không còn các lựa chọn ghi đè của v1.

1. **Import không có ngoại lệ ghi đè mục thủ công.** Muốn nhận nội dung file
   phải mở rule để sửa tay, qua cùng validation, lý do và lịch sử như bình thường.
2. Mục thủ công vắng trong file replace_scoped **luôn giữ nguyên**. Muốn
   loại bỏ hiệu lực phải ngừng áp dụng thủ công. Quy tắc thuần import chưa
   bảo vệ vẫn theo replace_scoped cũ (có thể xóa hẳn); C áp dụng cho thao
   tác thủ công và mọi mục đã được bảo vệ, gồm inactive cũ được backfill.
3. Khi sửa giá trị hoặc tình trạng, bảo vệ cả **khóa trước khi sửa** để file
   cũ không tạo lại bản quy tắc đã bị thay thế. Khóa này dẫn về cùng rule ID,
   không tham gia resolver. Không tự giải phóng khóa lịch sử trong phase này.
4. Mặc định danh sách hiện quy tắc đang áp dụng, 50 dòng/trang; cho chọn
   đang áp dụng / ngừng áp dụng / tất cả, thêm bộ lọc loại đối chiếu.
5. H: **không triển khai số đếm** trong bản đầu, chỉ cảnh báo chung (mục 9).
6. Dữ liệu trước phase chưa có dấu vết đủ để nhận biết mọi sửa tay: bảo vệ
   sẵn **mọi rule đang inactive** khi migrate, không tạo audit giả;
   rule active cũ mặc định chưa bảo vệ đến khi có thay đổi tay thật. Không suy đoán
   nguồn dữ liệu từ ghi chú hoặc thời gian cập nhật.

Các claim trong VERIFICATION.md v2 theo các quyết định hiện hành trên.
Phê duyệt spec không cấp quyền code trong lượt này; cập nhật xong phải dừng.

## 4. Luồng màn hình và validation

### 4.1 Tìm quy tắc (V2)

- Thêm khu vực danh sách vào module hiện tại, không yêu cầu mở Excel.
- Từ khóa tìm chứa chuỗi trong `match_value`, không phân biệt hoa/thường,
  giữ dấu tiếng Việt; `%`, `_`, dấu nháy được hiểu là ký tự thường, không là
  wildcard/SQL. Không tìm trong sản phẩm, ghi chú hoặc khóa lịch sử.
- Kết hợp bộ lọc tình trạng (ID ổn định), loại đối chiếu và trạng thái áp dụng.
  Từ khóa trống nghĩa là danh sách theo bộ lọc, không phải lỗi.
- Phân trang server-side, 50 dòng/trang, thứ tự ID tăng dần; giữ bộ lọc khi
  chuyển trang. Trên dữ liệu không đổi không mất/trùng dòng giữa các trang.
  Nếu danh mục thay đổi giữa các request, tải lại danh sách; không hứa snapshot.
- Kết quả: ID, loại, giá trị, tình trạng, ghi chú, trạng thái áp dụng, dấu
  “Được bảo vệ khi import”, thao tác mở chi tiết/lịch sử. Chỉ ghi “đã thay
  đổi thủ công” khi có lịch sử tay; inactive cũ được bảo vệ không bị gán
  nguồn gốc sửa tay không có bằng chứng.
- Page không phải số hoặc <1, filter ID không hợp lệ: báo lỗi có kiểm soát;
  trang vượt cuối trả danh sách rỗng, không trả toàn bộ dữ liệu.

### 4.2 Thêm/sửa (V3–V5)

- Thêm: chọn CAS/code/name; nhập giá trị, chọn tình trạng có sẵn và ghi chú.
  Không tự tạo tình trạng mới từ form tay; vẫn dùng màn hình tình trạng hiện có.
- Sửa: ID rule không đổi; sửa giá trị, status_id, note. Loại đối chiếu chỉ
  đọc; backend từ chối request cố đổi, không chỉ khóa ô trên giao diện.
- Dùng validation chung: CAS đúng cấu trúc và checksum; code/name không
  trắng, tối đa 500 ký tự; CAS tối đa 32; ghi chú tối đa 4.000 ký tự;
  lý do tối đa 1.000, trim trước kiểm tra bắt buộc.
- So trùng theo cùng status_id + loại + upper(btrim(giá trị)), bao gồm
  inactive và khóa lịch sử được giữ. Chuẩn hóa NFC input; DB unique là
  nguồn quyết định cuối, không dựa duy nhất vào Python casefold khác SQL.
- “Trùng hoàn toàn” là trùng khóa, **kể cả ghi chú khác**: không upsert ngầm,
  hiển thị đường dẫn tới rule có sẵn để sửa hoặc khôi phục. Với khóa lịch
  sử: giải thích giá trị đã được sửa, dẫn về rule chủ sở hữu hiện tại.
  Ngoại lệ không phải chiếm khóa: chính rule đó được sửa A → B → A qua
  preview/confirm, giữ nguyên ID và lịch sử, kể cả khi đang inactive. Khi
  sửa về khóa cũ không tự đổi active; rule khác vẫn không được lấy khóa đó.
- Cùng CAS/code khác tình trạng: hiển thị các rule liên quan (phân biệt
  active/inactive), cảnh báo kết quả còn phụ thuộc ưu tiên; vẫn cho xác nhận.
- Xem trước → so sánh cũ/mới → xác nhận. Bắt buộc lý do khi status_id thay
  đổi; sửa note/value không bắt buộc lý do. Thêm mới không bắt buộc lý do.
  Xem trước trên UI từ dữ liệu đã tải và form hiện tại; sửa form sau bước
  này quay lại so sánh trước khi gửi. Không có preview record/token phía DB.
- Lưu không có khác biệt: thông báo không thay đổi, không tạo audit giả hoặc
  chuyển rule thuần import thành được bảo vệ chỉ vì mở rồi bấm lưu.

### 4.3 Ngừng áp dụng/khôi phục (V6)

- Không có thao tác xóa hẳn trong UI/API mới. Ngừng áp dụng đặt inactive,
  giữ ID, dữ liệu, lịch sử; bắt buộc lý do và xác nhận trước/sau.
- Khôi phục cần preview/confirm, giữ ID, bật active, vẫn được bảo vệ import;
  lý do tùy chọn. Hiển thị cảnh báo trùng CAS/code khác tình trạng như lúc thêm.
- Không cho form sửa thông thường lén đổi is_active; dùng hành động riêng.
- Bấm lại cùng hành động trên trạng thái đã đạt không tạo event trùng.

### 4.4 Lịch sử (V7)

- Trang chi tiết có lịch sử phân trang, mới nhất trước (thời gian rồi event ID).
  Mỗi sự kiện tay: tài khoản thật + tên hiển thị tại thời điểm đó, thời gian
  có múi giờ, hành động, lý do, trước/sau (giá trị, status ID và nhãn snapshot,
  note, active, bảo vệ và version). Tạo mới có before rỗng.
- Đổi nhãn tình trạng sau này không được viết lại snapshot lịch sử.
- Ghi rule, khóa bảo vệ và audit trong cùng giao dịch; audit lỗi thì không lưu.
- Import không tạo event giả “sửa tay”; tiếp tục nhật ký job với danh sách
  giữ/xung đột. Lịch sử tay không được xóa khi job hết hạn/dọn upload.

## 5. Bảo vệ import — trọng tâm (V8–V11, V13, V14)

### 5.1 Nhận diện và đời sống bảo vệ

- Khóa hiện tại K = (status_id ổn định, match_field, giá trị chuẩn hóa theo
  unique DB). Khác tình trạng là khác khóa; không biến CAS thành unique toàn cục.
- Lần tạo/sửa/ngừng/khôi phục thủ công thành công đầu tiên bật bảo vệ. Những
  thay đổi hiển thị tình trạng/màu không tự bật bảo vệ cho tất cả quy tắc.
- Khi đổi giá trị/status, giữ các khóa đã dùng của rule (khóa lịch sử) bên
  cạnh khóa hiện tại. Không có rule thứ hai được chiếm cùng khóa đã giữ.
- Bảo vệ không hết hạn khi upload/job bị dọn, user bị khóa, web/worker restart
  hoặc chạy lại import. Khôi phục tay không bỏ bảo vệ.
- Khóa lịch sử không phải quy tắc active: tìm kiếm và resolver không được
  trả nó thành quy tắc thứ hai hoặc tiếp tục áp dụng giá trị cũ.

### 5.2 Luôn bảo vệ: cả upsert lẫn replace_scoped

- File trùng khóa hiện tại của rule được bảo vệ: giữ toàn bộ nội dung và
  active hiện có. Khác note/active hoặc dữ liệu đề xuất → ghi rõ xung đột;
  giống nhau → ghi rõ giữ nguyên thủ công. Không cập nhật timestamps/version
  của rule giữ nguyên chỉ để đánh dấu “đã đi qua import”.
- File trùng khóa lịch sử: không thêm lại rule cũ; hiển thị xung đột “khóa
  trước khi sửa”, dẫn về rule hiện tại, luôn bỏ qua dòng đó khi áp dụng.
- replace_scoped: rule được bảo vệ vắng trong file nhưng thuộc phạm vi
  trường+tình trạng của file vẫn giữ, không xóa và không ngừng áp dụng.
- Inactive thủ công: luôn giữ inactive; import không được bật lại. Phải đọc
  cả active/inactive khi lập kế hoạch, không tái sử dụng nguyên bộ lọc cũ.
- Dòng thuần import không xung đột với bảo vệ vẫn theo hành vi hiện có:
  upsert giữ dòng ngoài file; replace_scoped chỉ xóa active ngoài file trong
  đúng cặp trường+tình trạng; phạm vi khác không đổi; file rỗng bị từ chối.
- Không mở rộng phạm vi replace theo khóa lịch sử. Một dòng bị bỏ qua do
  bảo vệ không có nghĩa bỏ qua việc xem trước các dòng khác cùng phạm vi.

### 5.3 Không có luồng ghi đè từ file

- Không UI/API/metadata job để chọn bỏ bảo vệ hoặc cho ghi đè. Super Admin
  cũng không có ngoại lệ import. Cờ giả như `force`/`overwrite` không cấp
  quyền; các tham số điều khiển ghi đè ngoài hợp đồng bị từ chối có kiểm soát.
- Import confirm vẫn ràng buộc file hash, mode, fingerprint và plan digest
  như luồng hiện có; đổi file/mode/catalog phải preview lại. Không chuyển
  sang preview UI-only cho import — thay đổi đó chỉ áp dụng thao tác tay.
- Muốn dùng note/tình trạng/giá trị từ file: người có quyền sửa tay, nhập
  lý do khi đổi tình trạng, xác nhận và lưu audit. Rule vẫn được bảo vệ.

### 5.4 Preview có thể kiểm tra đầy đủ

- Tổng thêm/cập nhật/giữ-bỏ-qua đếm **dòng file**, tổng xóa đếm **rule ID**
  bị xóa thật; nhãn UI phải ghi rõ đơn vị. Tổng ba nhóm dòng bằng số dòng
  file hợp lệ; không cộng số rule vắng file được giữ vào tổng dòng file.
  Tổng “xóa” không tính rule thủ công. Giữ xác nhận số xóa của replace_scoped.
- Danh sách riêng, phân trang, **xem được toàn bộ chứ không chỉ 20 mẫu**:
  rule được giữ; xung đột khóa hiện tại; xung đột khóa cũ; mục vắng trong file
  được giữ; inactive không khôi phục.
- Mỗi mục: số dòng file nếu có, rule ID/đường dẫn, giá trị/tình trạng hiện tại
  và đề xuất, nguyên nhân, quyết định cuối. Phân loại mỗi dòng/quy tắc một
  lần trong đúng đơn vị của tổng hành động. Có tổng riêng theo rule ID:
  “mục được bảo vệ giữ nguyên”. File có khóa cũ + khóa hiện tại của R với
  note khác: 2 dòng giữ/bỏ qua, 1 rule giữ nguyên, 0 cập nhật R; vẫn hiện
  cả hai lý do xung đột. Rule vắng file được giữ chỉ đếm ở tổng rule.
- Chỉ liệt kê rule liên quan file/phạm vi; không đưa mọi rule thủ công ngoài
  phạm vi thành “xung đột”. Không có xung đột thì hiện trạng thái rỗng rõ ràng.
- **Lưu đơn giản, không thêm bảng chi tiết:** dùng JSONB `preview` sẵn có
  trong `regulatory_import_jobs`, thêm `contract_version=2` và
  `protection_details` đầy đủ, thứ tự ổn định. Lưu snapshot cùng kế hoạch
  trong `regulatory_import_events.detail` tại preview_completed/apply_completed
  theo cơ chế event hiện có; không tính lại chi tiết từ rule sống khi xem job cũ.
- Trang chi tiết phân trang 50 mục; response list/status chỉ summary, không
  đính toàn bộ JSON. Lấy trang từ JSON ở server; không tải hết xuống browser.
  Sau cleanup xóa `job.preview`, xem snapshot từ event hoàn tất tương ứng
  trong thời hạn giữ nhật ký; không xóa audit/khóa tay theo cleanup này.
- Giới hạn an toàn phần `protection_details`: 8 MiB JSON UTF-8 compact,
  không escape ký tự Unicode thành ASCII, không vượt rồi cắt bớt. Nếu vượt:
  preview thất bại, preview_ready=false, không cho apply và không ghi rule.
  Với upsert có thể chia theo dòng; với replace_scoped chỉ được chia thành
  các **scope trường+tình trạng không giao nhau**, mỗi scope phải còn đầy đủ
  dữ liệu dự định thay thế. Không hướng dẫn chia tùy ý một scope thành nhiều
  lần replace (lần sau sẽ xóa dòng thuần import của lần trước). Nếu một scope
  vẫn vượt giới hạn, dừng và báo chưa hỗ trợ kích thước này; không tự đổi
  mode hoặc áp dụng một phần. Ngưỡng bảo vệ trường hợp số mục tăng bất thường;
  mọi preview thành công đều xem được toàn bộ. Không dùng giới hạn sample 20.

## 6. Giao dịch, quyền và cạnh tranh (V1, V15–V17)

- Mọi route mới đăng ký quyền `regulatory`, cùng backend guard và CSRF hiện
  có; admin menu khác/staff/legacy session không có user ID không được ghi.
- Preview tay chỉ là bước giao diện; server **không tin** validation hay
  before/actor do client gửi. Khi confirm, đọc DB hiện tại và kiểm tra lại
  quyền/CSRF/field/giá trị/status/lý do/trùng khóa. Không yêu cầu chứng minh
  người dùng đã xem UI preview: request trực tiếp hợp lệ vẫn cùng contract.
- Edit/deactivate/restore bắt buộc `expected_revision` của rule đang mở;
  thiếu/sai định dạng bị từ chối, khác DB trả 409 và yêu cầu tải lại. Không
  dùng timestamp client làm phiên bản. Create chưa có revision; dùng unique
  khóa và chống gửi lặp. Revision của một ID không phải chữ ký gắn UI với ID.
- **Chống gửi lặp không cần bảng preview:** mỗi lần xác nhận dùng request_id
  UUID; giữ cùng ID khi retry do mất mạng/bấm hai lần. Audit sự kiện thành
  công lưu request_id UNIQUE và digest payload chuẩn hóa (action, rule ID,
  expected_revision nếu có, dữ liệu và reason), gắn actor từ session.
  Sau kiểm tra quyền/CSRF hiện tại, replay cùng actor + payload trả kết quả
  đã thực hiện (không ghi lại); cùng request_id nhưng khác actor/payload
  trả 409, không lộ dữ liệu của người khác. UUID không phải quyền truy cập.
  Audit và thay đổi commit cùng nhau nên rollback không chiếm request_id.
  No-op không tạo event; repeat no-op không có tác dụng ghi. UI disable nút
  chỉ hỗ trợ trải nghiệm, không phải biện pháp chống trùng duy nhất.
- Dùng regulatory advisory lock chung; sau đó kiểm tra actor/quyền sống,
  khóa actor và rule trước commit. Import/worker phải dùng cùng thứ tự khóa.
- Mọi writer làm thay đổi rule phải tăng revision (kể cả đồng bộ nhãn/ưu
  tiên tình trạng vào rule). Import fingerprint gồm revision/bảo vệ/khóa
  lịch sử. Rule đổi sau khi mở form → từ chối ghi mới và yêu cầu tải lại;
  import catalog đổi sau preview → từ chối apply, yêu cầu preview lại.
  Thay đổi trạng thái/ưu tiên ở rule khác không tự làm form tay stale;
  server vẫn xác thực danh mục tình trạng mới nhất lúc confirm. Không có
  cam kết khóa snapshot cả danh mục trong preview tay ở v2.
- Hai người thêm cùng khóa hoặc chiếm khóa lịch sử: chỉ một thành công,
  người còn lại nhận thông báo trùng/stale có kiểm soát; không HTTP 500 thô.
- Gửi lại request_id đã thành công không thực hiện lại, không nhân đôi audit.
  Lỗi giữa chừng rollback cả rule, lịch sử, bảo vệ và thay đổi job. Hai
  request_id khác nhau thêm cùng khóa vẫn chỉ một rule; request sau báo trùng.

## 7. Migration và tương thích

Cần migration bổ sung, dự kiến số **033** (kiểm tra lại số trước khi viết).
Không viết SQL trong lượt này. Chi tiết ở [MIGRATION.md](MIGRATION.md):
bảo vệ/revision trên rule, khóa bảo vệ lịch sử, audit tay có chống gửi lặp.
Không có bảng preview tay hoặc bảng chi tiết import; dùng JSONB sẵn có.
Không đổi permission key, products,
chính sách tình trạng hoặc giá trị nghiệp vụ cũ; chỉ thêm metadata mặc định
và backfill bảo vệ inactive cũ đã duyệt tại mục 3.6.

**Không chạy worker cũ với dữ liệu đã bắt đầu sửa tay**: worker cũ không hiểu
bảo vệ và có thể xóa/ghi đè. Web và worker cần cùng phiên bản hỗ trợ schema.
Rollback code đơn thuần không hoàn tác dữ liệu; có quy trình riêng trong
MIGRATION.md. Mọi chạy thử migration/rollback chỉ trên database local thử
được xác minh, không phải dữ liệu local thật và không staging/production.

## 8. Kế hoạch triển khai sau duyệt (chưa được thực hiện)

1. Chờ yêu cầu implement riêng; dùng spec v2 và khóa VERIFICATION.md trước code.
2. Migration additive; tests PostgreSQL thật trên DB tạm local; thử lặp và
   rollback. Bảo vệ schema/worker trước khi mở thao tác tay.
3. Service quản lý rule + audit/chống gửi lặp + confirm từ UI preview, tận dụng validation,
   resolver và quyền có sẵn; không thêm dependency/framework.
4. Điều chỉnh import preview/apply, job metadata, worker và fingerprint;
   triển khai cả bảo vệ khóa hiện tại và lịch sử trước khi cho sửa khóa.
5. UI tìm/sửa/thêm/lịch sử/ngừng/khôi phục và preview xung đột import.
6. Developer tests, hồi quy, review diff; verifier độc lập theo mục 11,
   tối đa 2 repair, cùng claim fail hai lần thì escalate. Không chia sẻ
   reasoning của agent chính với verifier.
7. Human review theo Claim → Test → Result → Independent verifier →
   Residual risk và 5 câu hỏi của quy trình. Chưa có quyền commit/push/deploy.

## 9. H — đánh giá số sản phẩm ảnh hưởng

Đánh giá tĩnh: CAS/code có index chuẩn hóa (migrations 007/008); name dùng
khớp chứa chuỗi. Quy mô products theo PROJECT_STATE/ARCHITECTURE khoảng
1,1 triệu dòng là thông tin lịch sử, không phải benchmark hiện tại.
Số sản phẩm khớp rule không bằng số sản phẩm **thay đổi kết quả pháp chế**:
còn ưu tiên, nhiều rule và ngoại lệ thủ công theo sản phẩm.

PO đã duyệt ngày 2026-09-27: **chưa làm số đếm trong bản đầu**. Chỉ hiện
cảnh báo quy tắc áp dụng toàn danh mục, tên có thể khớp nhiều sản phẩm,
trạng thái cuối còn phụ thuộc quy tắc khác và ngoại lệ sản phẩm. Không có
query đếm, benchmark, cache hay index products cho tính năng này trong phase.
Muốn thêm số đếm sau này phải là yêu cầu riêng, không tự bật lại phần tùy chọn v1.

## 10. Nghiệm thu người dùng sau triển khai

- Admin có quyền mở Quy tắc quản lý, tìm một phần CAS/mã/tên, kết hợp bộ
  lọc và chuyển trang; không thấy tìm theo tên sản phẩm ngoài giá trị rule.
- Thêm rule, xem trước, lưu; thêm lại khóa đó được dẫn về mục có sẵn.
- Đổi tình trạng không có lý do bị chặn; có lý do và xác nhận thì lịch sử
  thể hiện đúng cũ/mới và người thực hiện. Loại đối chiếu không đổi được.
- Ngừng rồi tìm trong danh sách ngừng áp dụng; khôi phục đúng ID, lịch sử còn.
- Import file xung đột bằng cả hai mode: preview liệt kê đầy đủ, luôn
  giữ tay; mục inactive không bật lại, mục ngoài file trong scope không mất.
- Thử file còn khóa cũ sau khi sửa CAS/status: không có quy tắc cũ tái xuất hiện.
- Không có tùy chọn ghi đè từ file; muốn lấy nội dung file phải sửa tay,
  lưu lịch sử và bảo vệ tiếp. Hai cửa sổ cùng sửa: cửa sổ cũ bị yêu cầu tải
  lại, không mất dữ liệu. Bấm xác nhận hai lần chỉ tạo một rule/event.

## 11. Trạng thái tại lượt spec

Đã cập nhật v2 theo điều chỉnh PO ngày 2026-09-27; chưa thử schema, chưa có
code/test mới, chưa có verifier result. Lượt này chỉ tài liệu, xong phải dừng.

## 12. Change log

- 2026-09-27, v1 → v2, theo phê duyệt/điều chỉnh của PO trước implementation:
  thay D bằng bảo vệ tuyệt đối trước import, bỏ ghi đè; làm rõ phạm vi C;
  duyệt khóa lịch sử và bảo vệ inactive cũ; H chỉ cảnh báo. Bỏ preview DB tay,
  chuyển UI preview + expected revision + request_id trên audit. Chi tiết
  import dùng JSONB hiện có, không bảng riêng. Hợp đồng v2 bỏ V12, sửa V15
  và các claim liên quan; còn 20 claim, giữ ID cũ để đối chiếu.
