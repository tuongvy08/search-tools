# Verification Result — regulatory-cas-only

Do verifier độc lập tạo. Dữ liệu synthetic, DB tạm dùng một lần qua `scripts/test_regulatory_local.py`; không truy cập production/mạng/secret. Không có kết luận pháp lý về hóa chất.

## Lệnh đã chạy và số liệu

| Lệnh | Kết quả |
|---|---|
| `.venv/bin/python -B scripts/test_regulatory_local.py test_regulatory_cas_only -v` | 5 tests OK |
| cùng runner, 13 module hợp đồng (cas_only, status_cleanup, phase6d1_regulatory, manual_service/http/import/display, search_compliance_precedence, search_result_display, regulatory_status_validation, admin_nav_shell_ui, admin_brand_compliance, quote_assistant_api) | 157 tests OK |
| cùng runner, `independent.test_cas_verifier` (file mới `tests/independent/test_cas_verifier.py`) | 35 tests, **34 pass, 1 FAIL** (xem V3) |
| cùng runner, mở rộng: test_static_asset_versions, test_phase6c4_admin_ux, test_admin_menu_permissions, test_product_manual_compliance_import, test_admin_products_pg, test_quick_quote, test_regulatory_manual_migration | 312 tests; 6 ERROR đều ở `test_regulatory_manual_migration` do `RuntimeError: No psql runner available` (thiếu psql ở máy verifier — môi trường, không phải lỗi code; module này ngoài danh sách bắt buộc) |
| `node tests/regulatory_manual_dom_test.js` | PASS (cas/code/name fixture) |
| `node tests/admin_regulatory_dom_test.js` | chạy xong, không in lỗi |

Lần chạy đầu của test độc lập có 3 fail + 12 error do lỗi test của verifier (quyền team không hợp lệ, con trỏ RealDict, kỳ vọng ghi chú gộp sai, regex menu); đã sửa test, không sửa source. Kỳ vọng "ghi chú gộp" chỉ áp dụng trong tình trạng thắng (hành vi cũ, không đổi bởi diff).

## Claim V1

Test thực hiện: fixture tên dài chứa tên chất, mã trùng rule code, CAS lệch/trống/một phần; hai tình trạng cùng CAS; kiểm resolver SQL, `/search` (admin + team có quyền), `_quote_export_products`, `/check_cas_batch`, `/api/quote-assistant/match`.
Kết quả: PASS
Evidence: rule name/code BLOCK + CAS ALLOW → ALLOW, note `CAS-ALLOW`; xóa rule cũ, CAS BLOCK vẫn BLOCK. Hai tình trạng cùng CAS → tình trạng ưu tiên cao thắng, ghi chú của tình trạng thua không lọt vào. Search/export/Check CAS/Quick Quote: sản phẩm FAKE-1 (CAS `629-14-1`) và không CAS đều Compliance rỗng, không chứa `NAMEWRONG/CODEWRONG`; sản phẩm CAS đúng có note `REAL`.
Negative case đã thử: CAS `64-17-5; 50-00-0`, `64-17-5 (anhydrous)`, `4-17-5`, `64-17-50`, `64175`, trống, khoảng trắng, NULL, ký tự zero-width, CAS toàn-rộng `６４-１７-５`, tiền tố `x…`, rule CAS ngừng áp dụng, rule name active → đều không có status. `' 64-17-5 '` vẫn khớp. Team không có VIEW_COMPLIANCE không thấy note.
Residual risk: không kiểm tra export workbook thực (chỉ hàm dựng dữ liệu export); không đo hiệu năng truy vấn.

## Claim V2

Test thực hiện: ma trận hãng ON/OFF/không có setting/khác hoa-thường+khoảng trắng; gán riêng ALLOW vs CAS BLOCK; gán riêng BLOCK vs CAS ALLOW; liên kết hỏng.
Kết quả: PASS
Evidence: hãng ON + gán riêng ALLOW → `ALLOW/manual`; hãng OFF, hãng không có setting, hoặc không gán riêng → `BLOCK/automatic`, note `CAS-BLOCK`; hãng ON + gán riêng BLOCK + CAS ALLOW → BLOCK; sản phẩm không CAS + hãng ON + gán riêng BLOCK → `BLOCK/manual`. Liên kết hỏng (status_id NULL, trigger tắt tạm trong DB tạm) ở hãng ON → `source=error, BLOCK`, HTTP `/search` không trả `ALLOW`; chuyển hãng sang OFF mới rơi về CAS (đúng hợp đồng). Đọc qua search/check CAS không đổi bảng `products` và `brand_compliance_settings` (so sánh snapshot).
Negative case đã thử: hãng OFF có gán riêng ALLOW vẫn bị CAS BLOCK; liên kết hỏng không rơi về ALLOW.
Residual risk: không kiểm tra migration/dữ liệu thật về cài đặt hãng (ngoài phạm vi, không có dữ liệu).

## Claim V3

Test thực hiện: 17 cặp (match_field, match_value) xấu qua `/rules/check` và `/rules/save` (JSON và form), edit/restore/activate rule cũ, key thừa; upload 10 loại file xấu qua worker; build/apply plan bị giả mạo; endpoint legacy.
Kết quả: **FAIL (một điểm, xem dưới)**; phần còn lại PASS
Evidence: `name`, `code`, `NAME`, `Cas ` (có khoảng trắng), None, rỗng, list, dict, giá trị không phải CAS, sai checksum, nhiều CAS, xuống dòng, quá dài → 400 (không 5xx), snapshot rules/statuses/events/manual_events/manual_keys không đổi. File header Tên/Code/tên, thêm cột Code, 1 dòng CAS sai trong file có dòng đúng, công thức, tình trạng là CAS, tình trạng trống → job `failed`, không preview, DB không đổi. Job cũ chứa file tên đã upload → worker `failed`; gọi apply trên job đó không ghi gì. `apply_plan` với `contract_version=1`, fingerprint/digest giả hoặc thiếu, hay rows tên/mã → `ImportProblem`; `_prepared` bị chèn rule name bị bỏ qua (apply tính lại kế hoạch), 0 rule non-CAS. `/admin/imports/quick-rule` bị chặn, `/admin/imports/preview` dataset regulatory_rules bị chặn (403), template name/code 404, `/templates/CAS` 404. CAS hợp lệ vẫn upload→preview và create/edit được.
Negative case đã thử: tất cả trên.
Phát hiện (FAIL): `match_value` CAS dùng chữ số toàn-rộng/Unicode, ví dụ `６４-１７-５`, được `/rules/check` và `/rules/save` chấp nhận (200/200) và lưu nguyên dạng vào `regulatory_rules.match_value`; rule này không bao giờ khớp CAS ASCII của sản phẩm nên là rule chết âm thầm. Nguyên nhân quan sát: `_CAS_RE` dùng `\d` (khớp Unicode) trong `regulatory.normalize_cas`, hàm không nằm trong diff → có khả năng đã có từ trước, nhưng hợp đồng V3 yêu cầu "CAS sai phải từ chối".
Classification: SPEC_AMBIGUOUS (hành vi tồn tại trước thay đổi; hợp đồng không nêu rõ chữ số không phải ASCII). Chủ sở hữu quyết định có thuộc phạm vi hay không.
Reproduction: chạy `independent.test_cas_verifier.V3.test_unicode_digit_cas_is_rejected_or_ascii`; hoặc POST `/admin/regulatory/rules/save` JSON `match_field=cas, match_value="６４-１７-５"` với CSRF hợp lệ.
Residual risk: chưa thử file `.xlsx` chứa chữ số toàn-rộng (cùng hàm nên dự kiến cùng hành vi); chưa thử tải đồng thời hàng loạt.

## Claim V4

Test thực hiện: fixture có rule name/code (active và ngừng), rule CAS được bảo vệ, rule CAS vắng trong file, rule CAS khác tình trạng; import upsert và replace_scoped qua HTTP+worker; UI chi tiết/danh sách; audit failure; replay.
Kết quả: PASS
Evidence: sau cả hai chế độ import, các hàng rule name/code (active/ngừng), rule bảo vệ và rule khác tình trạng giống hệt trước (so sánh nguyên hàng, gồm revision/updated_at); rule CAS mới được thêm. UI chi tiết và danh sách hiển thị "Không dùng đối chiếu"; không có option `restore`/`edit` cho rule cũ. Với rule cũ: deactivate OK (200); sau đó restore/edit/create → 400 và vẫn ngừng; revision cũ khi ngừng → 409. Trigger chặn insert audit → mutation CAS rollback, snapshot không đổi. Replay cùng request_id → `replayed`, 1 hàng; cùng request_id đổi sang field `name` → ManualProblem.
Negative case đã thử: file replace_scoped CAS vắng rule tên/mã và rule CAS bảo vệ → không bị xóa; restore/sửa rule cũ bị từ chối.
Residual risk: test song song hai request thật chỉ dựa vào test hiện hành (đã pass trong 157); chưa tự thiết kế thêm stress concurrency.

## Claim V5

Test thực hiện: `inspect_statuses`/`cleanup_statuses` và CLI trên DB tạm; snapshot md5 từng bảng public trước/sau.
Kết quả: PASS
Evidence: dry-run không ghi bảng nào; apply thành công chỉ đổi `regulatory_statuses` và `regulatory_status_events` (event `removed_invalid_labels`, before_json giữ nhãn gốc, actor); replay cùng fingerprint → `CleanupProblem`. Mỗi loại tham chiếu (rules, products.manual_compliance_status, product_deleted_rows, manual_keys, status_events) chặn xóa và snapshot toàn bảng không đổi. Sau dry-run đổi priority/export_policy/thêm event/thêm rule → apply bị từ chối, không đổi. Tham chiếu cạnh tranh chưa commit → `LockNotAvailable`, status còn nguyên. Lô 2 status trong đó 1 có tham chiếu → không xóa cả hai (atomic).
Negative case đã thử: nhãn thật `CẤM NHẬP`, `64-17-5` gán cho status thường, nhãn có khoảng trắng/xuống dòng thừa, chữ số toàn-rộng, chuỗi kiểu SQL injection, ID/nhãn bị hoán đổi, ID 0/âm/chuỗi, nhãn None, 21 mục → đều bị từ chối, không đổi DB. CLI: thiếu `--fingerprint/--actor/--reason` khi `--apply`, ID trùng, tham số sai, không tham số → exit khác 0; fingerprint sai → exit khác 0, status còn.
Residual risk: chưa chạy trên production (đúng hợp đồng); tham chiếu không qua khóa ngoại (ví dụ cột text `manual_compliance` không có status_id) không được tính theo thiết kế "qua khóa ngoại" — không kiểm chứng ý nghĩa nghiệp vụ.

## Claim V6

Test thực hiện: menu render với 9 đường dẫn gần giống; thêm/đổi tên tình trạng qua HTTP và import; staff/CSRF/menu grant; tham số thừa.
Kết quả: PASS
Evidence: `/admin/regulatory`, `/admin/regulatory/`, `/rules`, `/jobs/x/protection` → trigger và link có `is-active` + `aria-current`; `/admin/regulatory-other`, `/admin/regulatoryx/rules`, `/admin/regulatory_rules`, `/admin/Regulatory/rules`, `/regulatory/rules` → không đánh dấu. Thêm tình trạng `50-00-0`, ` 50-00-0 `, `64-17-5`, `1563-66-2`, rỗng, >120 ký tự → không tạo; `Danh mục tự đặt`, `50-00-0 Formaldehyde`, `Nhóm 3`, `50 - 00 - 0` → tạo. Đổi tên sang CAS bị từ chối; revision sai bị từ chối; đổi tên hợp lệ OK. Import với tình trạng mới là CAS → job failed, DB không đổi; tên tự đặt → completed. Staff (team, không admin) gọi save/check/statuses/upload → 302/401/403, không tạo job/rule; GET trang → bị chặn. Admin thiếu CSRF hoặc CSRF sai → 400; thiếu menu grant `regulatory` → chặn. Payload check có khóa thừa → 400; upload có `match_field` thừa → không tạo job.
Negative case đã thử: tất cả trên.
Residual risk: nhãn dùng gạch ngang Unicode (ví dụ `50–00–0`) không được xem là CAS đơn lẻ (không thử/không nằm trong hợp đồng). Giao diện trình duyệt (bố cục, tương tác thật) KHÔNG được kiểm chứng ở đây — main agent làm UAT. Chỉ có kiểm HTML/DOM/HTTP.

## Residual risks tổng hợp / không truy cập được

- Không có trình duyệt: không có kết luận hình ảnh/UAT.
- `test_regulatory_manual_migration` không chạy được (thiếu psql runner) → migration không được xác minh độc lập; hợp đồng nói không cần migration cho resolver.
- Các mẫu DOM test cũ vẫn có fixture code/name (chỉ test JS, không phải đường nhập thực).
- Đã đọc diff; không so sánh với bản baseline Git (không có repo) nên "đã có từ trước" của lỗi chữ số Unicode suy từ việc `normalize_cas` không nằm trong diff.

## Task status
attempt: 0 / max_auto_repairs: 2
same_claim_repeat_fail: none
next_action: ESCALATE_USER

## Verdict

V1, V2, V4, V5, V6: PASS. V3: PASS ngoại trừ một phát hiện SPEC_AMBIGUOUS (CAS chữ số Unicode toàn-rộng được chấp nhận và lưu; có khả năng đã có từ trước). Không phát hiện REGRESSION_FAIL hay IMPLEMENTATION_FAIL trong phạm vi đã thử. Cần chủ sở hữu quyết định về phát hiện V3 và UAT trình duyệt.
