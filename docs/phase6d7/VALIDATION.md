# Phase 6D7 — kiểm chứng local

Baseline `cd7ebb7e663c07ec47d9a12853ba23cce86982d9`, worktree
`/private/tmp/search-tools-phase6d7-search-stock-suggest`. Chỉ dữ liệu giả lập,
không SSH/DB thật; chưa commit/push/PR/deploy. Không sửa worktree 8fdb/6D6.

## Kết quả và thiết kế

- `/search` tìm thêm tên tồn đang active, đồng thời trả stock-only khi catalog
  cũng khớp. Literal LIKE cho tên tồn, VIEW_NAME gate. Giữ exact code/CAS cũ
  (kể cả quyền tìm CAS riêng), không đổi semantics query catalog cũ.
- Mở rộng một bulk `fetch_stock_options`, không sao chép resolver giá/compliance.
  CTE direct và attachment cùng SQL/snapshot view; dedup bằng Stock_Item_Id đã
  attach, không dedup theo giá trị bị redact. Badge phân biệt tên/code/CAS.
- Direct stock giới hạn1.000+sentinel; JSON `stock_truncated` và UI yêu cầu nhập
  cụ thể hơn, không cắt âm thầm. Giới hạn này không thay phần attachment cũ.
- `/search/suggestions` riêng, chỉ đọc; SEARCH auth/grant allowlist và no-store/private.
  3–500 ký tự trim/NFC; chỉ query field được VIEW, CAS cần cả VIEW_CAS/SEARCH_BY_CAS.
  Team active/brand filter trước mọi LIMIT, generic label/value chỉ từ field được
  phép. Không metadata tên/CAS ẩn, giá, compliance, total count hoặc cache dùng chung.
- Staged exact code/CAS -> prefix -> contains. Exact code/CAS dùng index007/008
  upper(trim) với predicate partial; prefix/contains rawILIKE tận dụng010.
  Không chạy exact-name common bitmap scan riêng; exact name được ưu tiên trong
  tập prefix đã giới hạn. Mỗi branch/source/field<=10; mỗi stage<=60 rows, trả<=10.
  Đây là gợi ý đại diện, không ranking toàn bộ hay hứa mọi unique suggestion.
- Deadline500ms cho tổng các stage DB, statement tối đa150ms (hoặc budget còn lại),
  lock100ms, connect_timeout1s. Deadline là budget candidate pipeline, không phải
  hard deadline toàn HTTP/middleware/auth/network. Timeout rollback connection
  sạch, dừng các stage tiếp và trả `degraded`/thông báo thử Search, không lộ SQL.
  LIMIT không tự bảo đảm ít scan: timeout là chốt chặn cho dữ liệu/planner khác.
- JS riêng: debounce300ms, AbortController và generation guard; clear/Escape/blur/
  outside/selection đều invalidate. Composition không phát search khi Enter xác
  nhận IME. Thay handler keypress cũ bằng một keydown handler, có Arrow/Enter,
  combobox/listbox/aria-activedescendant; label dùng textContent, không innerHTML.

## Tests

- Coordinator review fixes: selection now commits on completed click, not
  pointerdown; mousedown prevents focus loss without consuming touch-down/pan.
  First ArrowUp from no selection picks the last option. DOM regression covers
  ten options, touch down/move/cancel with no search, completed selection exactly
  once, non-primary click ignored, three-option ArrowUp/wrap/Enter.
- Coordinator rerun after fixes: 105 PostgreSQL-backed/focused tests passed on
  disposable localhost PostgreSQL14; all seven DOM harnesses passed. The static
  cache-version contract now explicitly includes both suggestion assets.
- Focused đầu:36/36 đạt; focused cuối:79/79 đạt,0skip,11,621s (`focused-tests.log`).
  Lần giữa phát hiện mock stock tuple cũ12cột; cập nhật thêm direct-match NULL
  cho projection13cột, không sửa assertion quyền ghi chú.
- Full suite chạy một lần: **1.100 test,1.097 pass,3 skip,0 lỗi**,82,189s
  (`full-tests.log`). Ba skip thuộc RealTemplateExportTests thiếu file ngoài repo
  QUOTE_TEMPLATE_FIXTURE (end-to-end template, namespaces/calcChain, source unchanged).
- 7 DOM harness pass: search_suggestions, stock_notes, license_results,
  quick_quote_export, admin_ux, admin_regulatory, stock_quick_edit.
  JS syntax và git diff --check pass.
- 9 PG tests mới: tên tồn/mixed catalog/dedup ID/badge/giá tách biệt, active-only,
  Unicode/wildcard, hidden name, cap/truncation, auth401/SEARCH403, field/team
  trướcLIMIT, CAS2grants, bounds<=10/no heavy resolver/no-store, textXSS,
  timeout khóa products thoát<1s và connection SELECT1 được sau rollback;
  tổng deadline không chạy stage tiếp, permission refresh và team stock-only.
- DOM harness mô phỏng response trả ngược thứ tự, response vẫn về sau abort,
  clear/Escape/blur/outside/dismiss, XSS label, IME và bàn phím/chọn một lần.

## Benchmark 1 triệu dòng (không phải production SLO)

`scripts/benchmark_search_suggestions.py` tự tạo/drop DB pgtest trên localhost.
Python3.12/PostgreSQL14 Docker tmpfs, index007/008/010 + brand/(brand,id)/identity
tương đương metadata production do người dùng cung cấp. Không thêm index mới.
1.000.000 products,874 active +8.740 historical stock; ANALYZE local, warm samples.

12 cặp query/scope, mỗi cặp5mẫu; median service (không gồm connect/auth/HTTP):

- Exact `CAT-1000000`: admin21,32ms / team0,1%22,78ms.
- Prefix `CAT-0999`:27,81 /45,13ms.
- Common `Chemical`:11,84 /16,41ms.
- Rare contains `needle`:20,09 /21,62ms (team không có kết quả).
- Missing `missingneedlexyz`:22,56 /22,98ms.
- CAS `50-00-0`:39,70 /51,37ms.
- 20 calls với4reader đồng thời:12,02–26,50ms; cả60serial+20concurrent không degraded.

EXPLAIN ANALYZE BUFFERS từngstage trong `benchmark-1m.json`: rare/missing/prefix
dùng products raw GIN, exact code/CAS dùng upper_trim btree; common name có seq
scan early-stop với LIMIT. Team chọn lọc được áp trướcLIMIT. Không tuyên bố mọi
truy vấn đều index scan; planner có thể chọn scan khác theo stats/distribution.
Timeout test kiểm chứng cả trường hợp thiếu/không dùng index và lock contention.

Synthetic heap109.232.128bytes (~104MiB), indexes274.726.912bytes (~262MiB), khác
production heap388MB/index605MB. Rows local ngắn và stats mới, không so trực tiếp
thành SLO. Metadata production xác nhận010 valid/ready không chứng minh plan thực.
**Không có migration033**: chưa có evidence cần thêm btree prefix hay duplicateGIN.

## Browser QA

Skill Browser kiểm thử app thật localhost5517 trên DB giả lập, login bình thường;
desktop1440x1000, mobile390x844, trả viewport về mặc định sau QA.

- Gõ Dung dịch ->4gợi ý cảstock/catalog; ArrowDown×2+Enter -> stock-only HI70004P,
  đúng badge Khớp tên tồn/qty7/noteKhoA, quoteprice trống và chọn xuất bị khóa.
- Search Dung dịch ->3dòng:1catalog gắn stockHI70024P +2stock-only; không dòng
  HI70024P thêm lần2. Không đổi compliance/export eligibility.
- Mobile chọn HI70024P theo tên ->1stock-only đúng dữ liệu; no-match đóng list,
  Escape và outside click đặt aria-expanded=false. IME/late-response qua DOM harness.
- Mobile list width341px, left16/right359 trong viewport390px; document375px,
  không tràn ngang trang. Bảng kết quả tiếp tục dùng scroll wrapper cũ.
- QA account không có menu admin quote nên panel phạm vi xuất hiện thông báo403
  “Không tải được phạm vi xuất báo giá”; fixture-only, không sửa permission để che lỗi.

Ảnh local: `desktop-suggestions.png`, `desktop-mixed-results.png`,
`mobile-suggestions.png`. Không commit ảnh/log/benchmark JSON/stderr; source,
test, benchmark script và docs hướng dẫn gọn dành review/release.
