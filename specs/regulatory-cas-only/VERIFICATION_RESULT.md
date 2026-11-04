# Verification Result — regulatory-cas-only (contract v2)

Do verifier độc lập tạo (context sạch, contract v2). Dữ liệu synthetic, DB tạm qua runner local. Không truy cập production/secret/mạng.

## Commands run (thực tế) và số test

Runner: `.venv/bin/python -B scripts/test_regulatory_local.py <module>` (mỗi lệnh riêng).

| Module | Ran | Kết quả |
|---|---|---|
| independent.test_cas_verifier (suite cũ, tái hiện) | 35 | OK |
| independent.test_cas_verifier_v2 (MỚI, file mới) | 12 | OK |
| test_regulatory_cas_only | 7 | OK |
| test_regulatory_status_cleanup | 6 | OK |
| test_phase6d1_regulatory | 25 | OK |
| test_regulatory_manual_service | 5 | OK |
| test_regulatory_manual_http | 5 | OK |
| test_regulatory_manual_import | 5 | OK |
| test_regulatory_manual_display | 3 | OK |
| test_search_compliance_precedence | 3 | OK |
| test_search_result_display | 18 | OK |
| test_regulatory_status_validation | 6 | OK |
| test_admin_nav_shell_ui | 22 | OK |
| test_admin_brand_compliance | 8 | OK |
| test_quote_assistant_api | 51 | OK |
| `node tests/regulatory_manual_dom_test.js` | 3 dòng PASS (cas/code/name labels) | exit 0 |
| `node tests/admin_regulatory_dom_test.js` | không in output | exit 0 (không có dòng PASS để trích) |

Không chạy migration/deploy legacy test (đúng chỉ dẫn). Lỗi lần đầu của v2 (ModuleNotFoundError do import sai đường dẫn trong test của chính verifier) đã sửa trong file test mới; không phải lỗi sản phẩm.

Lưu ý: test cũ `V3.test_unicode_digit_cas_is_rejected_or_ascii` chỉ assert "giá trị lưu là ASCII" nên yếu hơn contract v2; đã bổ sung test chặt hơn trong file v2 (đòi 400 + hướng dẫn + snapshot DB không đổi).

## Claim V1

Test thực hiện: suite cũ (6 test V1) + `test_unicode_product_cas_does_not_match_ascii_rule` mới; kiểm resolver SQL, Search (admin + staff có/không VIEW_COMPLIANCE), export quote, check_cas_batch, quote-assistant.
Kết quả: PASS
Evidence: tên dài chứa chất khác/mã trùng rule cũ BLOCK ưu tiên cao không thắng CAS ALLOW (ALLOW/'CAS-ALLOW'); xóa rule tên/mã rồi CAS BLOCK vẫn BLOCK; hai trạng thái cùng CAS chọn đúng ưu tiên, note đúng; CAS thiếu/trống/một phần/`64-17-5; 50-00-0`/fullwidth/Arabic-Indic/mixed không nhận tình trạng.
Negative case đã thử: sản phẩm tên "Diethyl"/mã FAKE-1/CAS khác hoặc NULL với rule tên+mã BLOCK → Compliance rỗng trên mọi bề mặt, không rò note; staff thiếu quyền không thấy note.
Residual risk: không có.

## Claim V2

Test thực hiện: ma trận hãng bật/tắt/không có setting/brand khác hoa-thường & khoảng trắng; liên kết gán riêng hỏng; read-only.
Kết quả: PASS
Evidence: hãng bật + gán riêng ALLOW → ALLOW/source manual; hãng tắt/không setting/không gán riêng → BLOCK/automatic với note CAS; gán riêng BLOCK + CAS ALLOW ở hãng bật → BLOCK; liên kết hỏng (trigger tắt tạm, status id NULL) ở hãng bật → source `error`, BLOCK, HTTP /search không trả ALLOW. products/brand_compliance_settings snapshot không đổi sau Search/check_cas_batch.
Negative case đã thử: hãng không ưu tiên có gán riêng ALLOW vẫn bị CAS BLOCK (đạt); liên kết hỏng + CAS ALLOW không rơi về ALLOW (đạt).
Residual risk: khả năng xuất file thực (xlsx export) kiểm qua `_quote_export_products` chứ không qua endpoint tải file đầy đủ.

## Claim V3

Test thực hiện: file mới `tests/independent/test_cas_verifier_v2.py` + suite cũ V3.
Kết quả: PASS
Evidence (v2, mỗi dạng thử qua check/save JSON + form):
- Giá trị CAS bị từ chối (HTTP 400 cho check và save JSON; form save 400/415; snapshot rules/statuses/events/keys trước = sau): `６４-１７-５`, `٦٤-١٧-٥`, trộn `６４-١٧-5`, `64-١٧-5`, Devanagari `६४-१७-५`, siêu chỉ số `6⁴-17-5`, gạch U+2010, en dash, dấu trừ U+2212, gạch fullwidth, zero-width space cuối/giữa, chuỗi nhiều dòng trộn. Thông điệp lỗi có "0-9" (hướng dẫn nhập lại) ở cả hàm và body HTTP cho các dạng chữ số.
- Sửa (edit) sang CAS Unicode → 400, bản ghi gốc nguyên vẹn.
- Khôi phục (restore) quy tắc đã lưu sẵn CAS Unicode (chèn trực tiếp mô phỏng dữ liệu cũ) → 400 qua HTTP và ManualProblem ở service, DB không đổi; edit giữ nguyên giá trị Unicode → 400; deactivate vẫn được; dữ liệu cũ KHÔNG bị tự sửa (còn nguyên `٦٤-١٧-٥`); edit nhập lại ASCII đúng → 200.
- Workbook: 5 dạng Unicode lẫn 1 dòng hợp lệ → job `failed`, preview_ready=false, snapshot DB không đổi (từ chối cả file); replace_scoped với CAS Unicode không xóa quy tắc CAS có sẵn.
- Plan: `build_plan` (upsert và replace_scoped) và `apply_plan` từ chối hàng Unicode kể cả khi dùng plan hợp lệ tạo từ hàng ASCII và khi trộn hàng tốt+xấu; DB rỗng sau thử.
- Apply HTTP với fingerprint giả trên job file Unicode: DB không đổi.
- Hồi quy ASCII: ` 64-17-5 ` check 200; `7732-18-5` lưu ASCII; ghi chú/tên tình trạng có chữ số Unicode (không phải CAS) vẫn được chấp nhận; import CAS đúng + tình trạng tự đặt hoàn tất (`completed`).
- Suite cũ: JSON match_field name/code/None/list/biến thể, file Tên/Code/lowercase header/CAS+Code, tên trong cột CAS, CAS trong cột tình trạng, công thức `=1+1`, plan sửa contract_version/fingerprint/digest, `_prepared` giả, endpoint cũ quick-rule/imports preview, template name/code 404 → đều từ chối, không ghi.
Negative case đã thử: xem Evidence (đa số là adversarial).
Residual risk: (1) chưa thử riêng NBSP/khoảng trắng Unicode bao quanh CAS ASCII — code `clean_text` có `.strip()` trước kiểm ASCII nên có thể chấp nhận và lưu bản đã strip dạng ASCII (không phải chuyển đổi chữ số; tôi coi là chấp nhận được nhưng chưa chứng minh bằng test). (2) Thông điệp lỗi cho nhóm ký tự không-ASCII không phải chữ số (gạch, zero-width) cũng dùng chung thông điệp "chỉ dùng 0-9 và -" — hợp lý, không xung đột contract. (3) Hiển thị thông điệp ở DOM thật cần browser (main agent). (4) Không thể quan sát lỗi dòng import trên UI job detail ở mức DOM; kiểm bằng trạng thái job.

## Claim V4

Test thực hiện: suite cũ V4 + test v2 replace_scoped/restore.
Kết quả: PASS
Evidence: replace_scoped và upsert qua HTTP + worker + apply: quy tắc tên/mã cũ (active/inactive), quy tắc CAS được bảo vệ, quy tắc khác tình trạng không bị đổi (so sánh bản ghi trước/sau bằng nhau); rule CAS mới được thêm. UI quy tắc cũ có "Không dùng đối chiếu", không có option restore/edit; deactivate rule cũ 200, sau đó restore/edit/create 400 và vẫn inactive; revision cũ → 409. Audit trigger lỗi → rollback cả rule lẫn key; replay trả replayed, double-submit một dòng; cùng request_id đổi nội dung sang name bị ManualProblem.
Negative case đã thử: replace_scoped file CAS vắng quy tắc tên/mã/CAS bảo vệ; restore rule tên/mã cũ; restore rule CAS Unicode cũ (đều đạt).
Residual risk: concurrency chỉ kiểm bằng test hiện hành và một test double-submit; không tạo thêm race harness mới.

## Claim V5

Test thực hiện: suite cũ V5 (cleanup công cụ) + test_regulatory_status_cleanup (6).
Kết quả: PASS
Evidence: nhãn hợp lệ/biến thể (khoảng trắng, xuống dòng, CAS fullwidth, chuỗi SQL) không thể xóa; cặp id↔nhãn hoán đổi, 21 id, id 0/âm/chuỗi/None bị từ chối; mỗi loại tham chiếu (rules, products, product_deleted_rows, manual_keys, status_events) chặn xóa và hash md5 mọi bảng public không đổi; thay đổi sau dry-run (policy, priority, event, rule mới) → CleanupProblem, không xóa; reference cạnh tranh ở giao dịch khác → LockNotAvailable, status còn; apply thành công chỉ đổi regulatory_statuses + regulatory_status_events, có event `removed_invalid_labels` kèm before_json, actor; replay bị từ chối; CLI thiếu cờ/fingerprint sai/id lặp đều lỗi.
Negative case đã thử: xem Evidence.
Residual risk: chưa chạy trên dữ liệu production (đúng phạm vi); batch lớn hơn mô hình fixture chưa thử.

## Claim V6

Test thực hiện: suite cũ V6, test_admin_nav_shell_ui, test_regulatory_status_validation, DOM node tests.
Kết quả: PASS (phần không cần browser)
Evidence: thêm/đổi tên/import tình trạng bằng CAS (`50-00-0`, có khoảng trắng, `1563-66-2`) bị từ chối và DB không đổi; tên tự đặt (`Danh mục tự đặt`, `50-00-0 Formaldehyde`, `50 - 00 - 0`) được thêm; rename revision cũ không đổi; staff có/không quyền: mutation/check/statuses/upload → 302/401/403, không tạo job, DB không đổi; admin thiếu CSRF/sai CSRF → 400; admin bị gỡ grant menu regulatory → 302/403; menu: `/admin/regulatory/…` sub-path active; `/admin/regulatory-other`, `/admin/regulatoryx/rules`, `/admin/regulatory_rules`, `/admin/Regulatory/rules`, `/regulatory/rules` không active; trang con render shared nav; form tạo không có option name/code; trang chính không link template code/name.
Negative case đã thử: xem Evidence (staff mutation, thiếu CSRF, CAS sai ô tình trạng, đường dẫn gần giống).
Residual risk: UAT browser/visual do main agent. Không truy cập được browser trong phiên này nên không tuyên bố pass phần hiển thị thật (CSS/FA CDN trong template mới, thông điệp lỗi trực quan). `admin_regulatory_dom_test.js` không in gì (exit 0) — không có bằng chứng số liệu đi kèm.

## Phát hiện lần trước (CAS fullwidth)

Observed trước: `６４-１７-５` được check/save (200/200) và lưu nguyên văn. Expected v2: từ chối + hướng dẫn nhập lại. Observed lần này: check/save JSON 400, form 400/415, không ghi gì; Arabic-Indic, mixed, Devanagari cũng bị chặn; workbook/restore/stale plan chặn. → Đã đạt contract v2. Không còn finding mở.

## Residual risks tổng

- Không thể xác minh: browser/visual; hành vi production; node `admin_regulatory_dom_test.js` không có output.
- Chưa thử NBSP bao quanh (xem V3).
- Không có kết luận pháp lý về hóa chất.

## Verdict

Tất cả 6 claim PASS ở mức HTTP/DOM-script/PostgreSQL local; không IMPLEMENTATION_FAIL, REGRESSION_FAIL, SPEC_AMBIGUOUS hay VERIFICATION_CONTRACT_PROBLEM mới. Còn lại: browser UAT do main agent.

## Task status

attempt: 0 / max_auto_repairs: 2
same_claim_repeat_fail: none
next_action: HUMAN_REVIEW
