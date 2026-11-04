# Cross-vendor review — 2026-11-04

Claude, phiên chỉ đọc riêng sau verifier v2 PASS. Không kế thừa hội thoại hoặc suy luận agent triển khai.

**Kết luận: không có phát hiện chặn (blocking).** Đây không phải phê duyệt production. Tôi chỉ đọc mã và tài liệu, không chạy lệnh nào. Các kết luận dưới đây dựa trên đọc mã nguồn và đối chiếu bằng chứng trong `VERIFICATION_RESULT.md`. Tôi không tự chạy lại test nào.

## Những gì tôi đã kiểm tra độc lập

- **Resolver dùng chung** (`regulatory.py:260-326`):
  - Nhánh `matched` chỉ còn `match_field='cas'` và so khớp chính xác `upper(btrim(p.cas)) = upper(btrim(r.match_value))`. Không còn `strpos`, tên hay mã.
  - Thứ tự `manual_choice` → `broken_manual` → `automatic_choice` được giữ nguyên. Liên kết gán riêng hỏng ở hãng bật ưu tiên vẫn ra `error` / BLOCK.
  - Mọi điểm vào đều đi qua `product_resolver_lateral` hoặc `cas_resolver_lateral` (`search.py:2121`, `4878`, `5007`, `5065`, `5169`, `5458`, `6098`). Tôi không thấy SQL tự đối chiếu tên/mã nào còn sót.
- **Đường ghi quy tắc thủ công** (`regulatory_manual.py:113-160`): chỉ nhận `cas`. Quy tắc cũ tên/mã chỉ được `deactivate`; `restore` và `edit` bị từ chối. Lúc restore, giá trị CAS và nhãn trạng thái được kiểm tra lại.
- **Đường import:**
  - Parser, `build_plan` và `apply_plan` đều từ chối tên/mã.
  - `apply_plan` dựng lại plan từ file đã parse, nên preview cũ chứa tên/mã không qua được. `catalog_fingerprint` có thêm `matching_policy: cas-only-v1`, nên preview cũ bị coi là đã đổi.
  - `replace_scoped` có phạm vi `(match_field, status)` chỉ gồm `cas`, nên không xóa quy tắc tên/mã. Quy tắc được bảo vệ vẫn được giữ.
- **Đường ghi cũ** (`search.py:3216`, `3318`, `3747`): đã bị chặn/chết (preview trả redirect, apply trả redirect, quick-rule trả 410). Phần mã phía sau `return` vẫn còn nhưng không chạy được.
- **CAS Unicode:** `normalize_cas` chặn bằng `isascii()` (`regulatory.py:99`) và không chuyển đổi chữ số.
- **Dọn trạng thái sai** (`regulatory_status_cleanup.py`):
  - Hạn chế ở 1–20 cặp ID↔nhãn khớp chính xác `[0-9]{2,7}-[0-9]{2}-[0-9]`.
  - Liệt kê mọi FK trỏ tới `regulatory_statuses` từ `pg_constraint` (mọi schema), từ chối FK ghép, đếm tham chiếu từng bảng.
  - Thứ tự: advisory lock → `FOR UPDATE` → so khớp fingerprint → `DELETE ... RETURNING` kiểm tra đúng phạm vi → ghi audit. Tất cả nằm trong một giao dịch, lỗi audit thì rollback.
  - Các FK trong migration đều là NO ACTION/RESTRICT, không có CASCADE hay SET NULL. Vì vậy ngay cả khi có tranh chấp, FK vẫn chặn xóa chứ không xóa lan.
  - `regulatory_status_events.status_id` cho phép NULL nên dòng audit chèn được sau khi xóa.
  - CLI mặc định chỉ kiểm tra (read-only, `REPEATABLE READ`); chỉ ghi khi có `--apply` cùng fingerprint, actor và reason.

## Phát hiện (đều Low / Info, không chặn)

1. **Low – nhãn chữ số Unicode: công cụ dọn và UI không khớp.** `regulatory_status_cleanup.py:23` dùng `[0-9]`, còn `normalize_status_label` (`regulatory.py:83`, `_CAS_RE` dùng `\d`) coi nhãn chữ số Unicode là CAS-like. UI gắn cờ "không hợp lệ" nhưng công cụ dọn từ chối. Điều kiện tái hiện: trạng thái có nhãn `６４-１７-５`. Kết quả là dữ liệu không mất, chỉ không dọn được.
2. **Low – CLI dọn không có `lock_timeout`/`statement_timeout`** (`scripts/cleanup_invalid_regulatory_statuses.py:32`). `FOR UPDATE` trên `regulatory_statuses` có thể chờ vô hạn nếu giao dịch khác đang giữ `FOR KEY SHARE`, trong khi advisory lock đã bị giữ. Đây là rủi ro về tính sẵn sàng, không phải mất dữ liệu.
3. **Low – `build_plan` kiểm tra nhưng không dùng giá trị đã chuẩn hóa** (`regulatory_import_jobs.py:300`, `328`). `normalize_match_value` được gọi mà kết quả bị bỏ. Đường parser thật luôn truyền giá trị đã chuẩn hóa nên an toàn. Nhưng nếu một caller trực tiếp truyền `" 64-17-5"`, nó vẫn qua kiểm tra và được ghi nguyên văn, dẫn tới so sánh `old['match_value'] != row['match_value']` luôn "cập nhật".
4. **Info – cleanup chỉ kiểm tham chiếu qua FK.** Văn bản nhãn không có ID (ví dụ `products.manual_compliance` hoặc hàng `product_deleted_rows` cũ có `status_id` NULL) không được tính. Nếu xóa trạng thái đó, resolver sẽ fail-closed (`error`/BLOCK) và restore bị từ chối. Ngược lại, trạng thái tạo qua UI luôn có event `created` nên không bao giờ dọn được; hành vi này an toàn nhưng cần biết.
5. **Info – ràng buộc CAS-only chỉ nằm ở tầng ứng dụng.** Không có CHECK hay trigger ở DB. Script SQL trực tiếp vẫn chèn được quy tắc tên/mã (chúng không có tác dụng đối chiếu), và dữ liệu cũ chữ số Unicode không bị sửa, đúng theo hợp đồng.
6. **Info – thay đổi ngoài phạm vi trong template.** `admin_regulatory_protection.html`, `admin_regulatory_rule.html` và `admin_regulatory_rules.html` thêm `<link>` tới `cdnjs.cloudflare.com` (font-awesome, `crossorigin`). Đây là phụ thuộc CDN bên ngoài trong trang admin, chưa có SRI. Cần xác nhận CSP/chính sách chấp nhận.
7. **Info – docstring hỏng** ở `regulatory.py:268-269` (câu "Note ordering... deterministic" bị cắt). Chỉ là lỗi trình bày.

## Rủi ro còn lại

- **Phạm vi chặn nhập giảm có chủ đích:**
  - Sản phẩm thiếu CAS hoặc có CAS sai định dạng (ví dụ gạch Unicode, chữ số fullwidth) sẽ không còn bị quy tắc tên/mã BLOCK cũ bắt.
  - Hợp đồng không có bước di chuyển quy tắc tên/mã sang CAS.
  - Đây là hệ quả của quyết định nghiệp vụ, nhưng nên có người chủ nghiệp vụ xác nhận trước khi phát hành.
- **Chưa xác minh được:**
  - UAT trên trình duyệt (hiển thị, CSS, FA CDN, thông điệp lỗi).
  - `admin_regulatory_dom_test.js` thoát exit 0 nhưng không in gì, nên không có bằng chứng kèm theo.
  - Hành vi trên production.
  - Cleanup với batch lớn hoặc có tải đồng thời thật.
  - Xuất file xlsx đầy đủ qua endpoint tải về, mới kiểm qua `_quote_export_products`.
- **Concurrency của đường import/quy tắc thủ công** chỉ dựa vào test hiện có; báo cáo xác nhận không có race harness mới.
- **Chưa đánh giá** quyền, CSRF và scope team/brand ở mức mã ngoài các điểm đã đọc. Tôi chỉ đối chiếu với bằng chứng của báo cáo.
