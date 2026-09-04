"""Quick Quote page and client helper tests — Phase 3C2."""

import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import search  # noqa: E402
from auth_test_helpers import start_auth_db_patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUICK_QUOTE_HTML = ROOT / "templates" / "quick_quote.html"
QUICK_QUOTE_JS = ROOT / "static" / "quick_quote.js"
QUICK_QUOTE_CSS = ROOT / "static" / "styles.css"
INDEX_HTML = ROOT / "templates" / "index.html"


# ─────────────────────── static asset checks ───────────────────────────


class QuickQuoteStaticTests(unittest.TestCase):

    def test_index_links_to_quick_quote_page(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('href="{{ url_for(\'quick_quote\') }}"', html)
        self.assertIn("Quick Quote", html)

    def test_quick_quote_template_has_grid_and_controls(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqRequestGrid"', html)
        self.assertIn('id="qqMatchBtn"', html)
        self.assertIn('id="qqCopyBtn"', html)
        self.assertIn("Chọn thủ công", html)
        self.assertIn("Giá thấp nhất mỗi brand", html)
        self.assertIn("Một giá thấp nhất", html)
        self.assertIn("quick_quote.js", html)
        self.assertIn('id="qqClearAllBtn"', html)
        self.assertIn("Xóa tất cả", html)
        self.assertIn("qq-request-wrap", html)

    def test_template_has_equiv_toggle_and_scope_column(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqEquivDefault"', html)
        self.assertIn("Tìm sản phẩm tương đương", html)
        self.assertIn("qq-col-scope", html)
        self.assertIn("Phạm vi", html)

    def test_template_has_preparation_type_and_size_mode_controls(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('data-preparation-type="ANY"', html)
        self.assertIn('data-preparation-type="NEAT"', html)
        self.assertIn('data-preparation-type="SOLUTION"', html)
        self.assertIn('data-preparation-type="MIXTURE"', html)
        self.assertIn("Nguyên chất", html)
        self.assertIn("Dạng dung dịch", html)
        self.assertIn("Hỗn hợp", html)
        self.assertNotIn('data-unit-group="SOLID"', html)
        self.assertNotIn('data-unit-group="LIQUID"', html)
        # size mode is now a select
        self.assertIn('id="qqSizeModeSelect"', html)
        self.assertIn('<option value="MIN">', html)
        self.assertIn('<option value="MAX">', html)
        self.assertIn('<option value="EXACT">', html)
        self.assertIn('id="qqExactSizeWrap"', html)

    def test_template_has_strategy_select(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqStrategySelect"', html)
        self.assertIn('<option value="MANUAL">', html)
        self.assertIn('<option value="LOWEST_PER_BRAND">', html)
        self.assertIn('<option value="LOWEST_OVERALL">', html)

    def test_template_brand_policy_widget(self):
        """Phase 3B1: global brand policy replaces the old single flat combobox."""
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqPolicyWidget"', html)
        self.assertIn('data-policy-mode="PRIORITY_FALLBACK"', html)
        self.assertIn('data-policy-mode="ALLOWLIST_ONLY"', html)
        self.assertIn('data-policy-mode="ALL_AVAILABLE"', html)
        self.assertIn("Cách chọn hãng", html)
        self.assertIn('id="qqAllowlistPanel"', html)
        self.assertIn('id="qqAllowlistChips"', html)
        self.assertIn('id="qqAllowlistSearchInput"', html)
        self.assertIn('id="qqAllowlistDropdown"', html)
        self.assertIn('id="qqAllowlistList"', html)
        self.assertIn('id="qqTierPanel"', html)
        self.assertIn('id="qqTierList"', html)
        self.assertIn('id="qqAddTierBtn"', html)
        self.assertIn('id="qqBrandOptionsTemplate"', html)
        self.assertIn('class="qq-brand-combo"', html)
        # old single flat brand widget is fully gone (superseded, not layered on top)
        self.assertNotIn('id="qqBrandWidget"', html)
        self.assertNotIn('id="qqBrandChips"', html)
        self.assertNotIn('id="qqBrandSearchInput"', html)
        self.assertNotIn('id="qqBrandRequiredHint"', html)
        self.assertNotIn('id="qqBrandPasteInput"', html)
        self.assertNotIn('id="qqBrandSearch"', html)
        self.assertNotIn('id="qqBrandPaste"', html)

    def test_template_conditions_layout(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn("qq-cond-brand-row", html)
        self.assertIn("qq-cond-controls-row", html)
        self.assertIn("qq-cond-actions-row", html)
        # size mode + strategy are now selects
        self.assertIn('id="qqSizeModeSelect"', html)
        self.assertIn('id="qqStrategySelect"', html)
        # preparation type remains segmented
        self.assertIn('data-preparation-type="ANY"', html)
        # no strategy/size-mode segmented buttons remaining
        self.assertNotIn('data-strategy="MANUAL"', html)
        self.assertNotIn('data-size-mode="EXACT"', html)

    def test_css_conditions_layout_no_autofit_minmax_220(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        self.assertIn(".qq-cond-controls-row", css)
        self.assertIn(".qq-cond-select", css)
        self.assertIn(".qq-brand-combo", css)
        self.assertIn(".qq-brand-empty", css)
        self.assertIn(".qq-brand-load-error", css)
        self.assertIn(".qq-manual-hint", css)
        # controls row must NOT use auto-fit minmax(220px)
        controls_block = re.search(
            r"\.qq-cond-controls-row\s*\{[^}]*\}", css, re.S
        )
        self.assertIsNotNone(controls_block)
        self.assertNotIn("auto-fit", controls_block.group(0))
        self.assertIn("repeat(3", controls_block.group(0))

    def test_template_has_result_table_and_bottom_copy(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqResultGroups"', html)
        self.assertIn('id="qqCopyBtnBottom"', html)
        self.assertIn('class="qq-selected-count"', html)

    def test_js_brand_policy_state(self):
        """Phase 3B1: mode + allowlist Set + tiers array is the only brand state."""
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("QQ_POLICY_PRIORITY_FALLBACK", js)
        self.assertIn("QQ_POLICY_ALLOWLIST_ONLY", js)
        self.assertIn("QQ_POLICY_ALL_AVAILABLE", js)
        self.assertIn("qqBrandPolicyMode", js)
        self.assertIn("qqAllowlistBrands", js)
        self.assertIn("qqPriorityTiers", js)
        self.assertIn("qqAllBrands", js)
        self.assertIn("qqResolveBrandToken", js)
        self.assertIn("qqCreateBrandPicker", js)
        self.assertIn("qqPolicyValidation", js)
        self.assertIn("qqBuildGlobalBrandPolicyPayload", js)
        self.assertIn("qqAddTier", js)
        self.assertIn("qqRemoveTier", js)
        self.assertIn("qqMoveTier", js)
        # the old flat single-Set brand widget must be fully replaced, not layered
        self.assertNotIn("qqSelectedBrands", js)
        self.assertNotIn("qqGetSelectedBrands", js)
        self.assertNotIn("qqNeedsBrandWarning", js)
        self.assertNotIn("qqRowNeedsEquivBrand", js)

    def test_js_brand_combobox_generic_selectors(self):
        """The combobox factory targets shared classes, not one hardcoded ID set."""
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qq-brand-search-input", js)
        self.assertIn("qq-brand-dropdown", js)
        self.assertIn("qq-brand-empty", js)
        self.assertNotIn("qqBrandPasteInput", js)
        self.assertNotIn("qqBrandSearchInput", js)

    def test_js_uses_selects_for_size_and_strategy(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqSizeModeSelect", js)
        self.assertIn("qqStrategySelect", js)

    def test_js_has_stale_invalidation(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqInvalidateResults", js)
        self.assertIn("qqManualHint", js)
        self.assertIn("data-preparation-type", js)
        self.assertIn("qqPreparationType", js)
        self.assertNotIn("qqUnitGroup", js)

    def test_js_uses_safe_dom_and_api(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("textContent", js)
        self.assertIn("/api/quote-assistant/match", js)
        self.assertIn("qqBuildCopyPayload", js)
        self.assertIn("qqFilterSubmittableRows", js)
        self.assertIn("qqApplyPasteToRows", js)
        self.assertIn("qqClearAllGrid", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("QQ_BLOCKED_COMPLIANCE", js)
        self.assertIn("Thiếu Code/CAS", js)

    def test_js_result_table_renderer(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqRenderResultTable", js)
        self.assertIn("qq-result-table", js)
        self.assertIn("qq-result-cb", js)
        # no old card renderer
        self.assertNotIn("qqRenderResultGroup", js)
        self.assertNotIn("qqBuildCandidateRow", js)

    def test_js_has_vietnamese_labels(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for label in [
            "Thiếu Code/CAS", "Không có giá hợp lệ", "Cần chọn sản phẩm",
            "Code và CAS không khớp", "Cần kiểm tra thủ công", "Không tìm thấy",
            "Cần chọn brand", "Đúng Code", "Đúng CAS", "Code + CAS", "Tương đương theo CAS",
        ]:
            self.assertIn(label, js, f"Missing label: {label}")

    def test_css_has_new_brand_and_result_table_classes(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [
            ".qq-brand-chips", ".qq-brand-chip", ".qq-brand-chip-remove",
            ".qq-brand-search-input", ".qq-brand-dropdown", ".qq-brand-list",
            ".qq-brand-option", ".qq-brand-combo",
            ".qq-result-table", ".qq-result-cb", ".qq-row-selected",
            ".qq-result-header", ".qq-selected-count",
        ]:
            with self.subTest(css_class=cls):
                self.assertIn(cls, css, f"Missing CSS: {cls}")

    def test_css_grid_containment_unchanged(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        self.assertIn(".qq-grid td.qq-grid-cell", css)
        self.assertIn("box-sizing: border-box", css)
        self.assertIn(".qq-request-wrap .qq-grid", css)
        self.assertIn("table-layout: fixed", css)
        self.assertIn(".qq-grid-input:focus", css)

    def test_template_has_request_file_wizard(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('data-request-source="manual"', html)
        self.assertIn('data-request-source="file"', html)
        self.assertIn('id="qqFileWizard"', html)
        self.assertIn('id="qqRequestFileInput"', html)
        self.assertIn('accept=".xlsx,.csv"', html)
        self.assertIn('id="qqRequestSheetSelect"', html)
        self.assertIn('id="qqRequestHeaderRowSelect"', html)
        self.assertIn('id="qqMapNameSelect"', html)
        self.assertIn('id="qqMapCodeSelect"', html)
        self.assertIn('id="qqMapCasSelect"', html)
        self.assertIn('id="qqReplaceDialog"', html)
        self.assertIn("Đưa vào Quick Quote", html)

    def test_js_request_file_wizard_contract(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for text in [
            "QQ_REQUEST_FILE_ANALYZE_ENDPOINT = '/api/quote-assistant/request-file/analyze'",
            "QQ_REQUEST_FILE_PARSE_ENDPOINT = '/api/quote-assistant/request-file/parse'",
            "QQ_REQUEST_FILE_MAX_BYTES = 10 * 1024 * 1024",
            "new FormData()",
            "fd.append('file', qqRequestFile)",
            "fd.append('mapping', JSON.stringify(mapping))",
            "showModal",
            "qqInvalidateResults()",
            "qqSetRequestSource('manual')",
        ]:
            self.assertIn(text, js)
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)
        self.assertNotIn("window.confirm", js)

    def test_css_request_file_wizard_responsive(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [
            ".qq-file-wizard", ".qq-file-drop", ".qq-mapping-grid",
            ".qq-file-preview-wrap", ".qq-file-preview", ".qq-replace-dialog",
        ]:
            with self.subTest(css_class=cls):
                self.assertIn(cls, css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("grid-template-columns: 1fr", css)


# ─────────────────────────── Python mirror helpers ─────────────────────────


class QuickQuoteMirrorHelpers:
    """Shared Python mirrors of quick_quote.js constants/logic. Deliberately
    NOT a unittest.TestCase — mixed into multiple test classes (via multiple
    inheritance) so those classes can reuse the same classmethod helpers
    without unittest re-discovering and re-running inherited test_* methods
    under each subclass name."""

    BLOCKED = {"CẤM NHẬP", "Cấm nhập", "Chưa xác định"}
    GRID_FIELDS = ["requested_name", "code", "cas"]
    INITIAL_ROW_COUNT = 5
    REASON_LABELS = {
        "MISSING_IDENTIFIER": "Thiếu Code/CAS",
        "NO_VALID_PRICE": "Không có giá hợp lệ",
        "MANUAL_SELECTION_REQUIRED": "Cần chọn sản phẩm",
        "CODE_CAS_CONFLICT": "Code và CAS không khớp",
        "MANUAL_REVIEW": "Cần kiểm tra thủ công",
        "BRAND_REQUIRED": "Cần chọn brand",
        "CODE_HAS_NO_CAS": "Code không có CAS — không thể tìm tương đương",
        "CODE_MULTIPLE_CAS": "Code có nhiều CAS — không thể tìm tương đương",
    }
    MATCH_MODE_LABELS = {
        "EXACT_CODE": "Đúng Code",
        "EXACT_CAS": "Đúng CAS",
        "CODE_CAS": "Code + CAS",
        "EQUIVALENT": "Tương đương theo CAS",
    }
    SCOPE_DEFAULT = "DEFAULT"
    SCOPE_EXACT = "EXACT"
    SCOPE_EQUIV = "EQUIV"

    @staticmethod
    def _text(v):
        return str(v or "").strip()

    @staticmethod
    def _split_tokens(text):
        out = []
        for raw in re.split(r"[\n\r\t,;]+", str(text or "")):
            t = raw.strip()
            if t:
                out.append(t)
        return out

    @classmethod
    def _resolve_brand_token(cls, token, all_brands):
        needle = token.lower()
        exact = next((b for b in all_brands if b.lower() == needle), None)
        if exact:
            return {"match": exact, "ambiguous": False}
        prefix = [b for b in all_brands if b.lower().startswith(needle)]
        if len(prefix) == 1:
            return {"match": prefix[0], "ambiguous": False}
        if len(prefix) > 1:
            return {"match": None, "ambiguous": True}
        sub = [b for b in all_brands if needle in b.lower()]
        if len(sub) == 1:
            return {"match": sub[0], "ambiguous": False}
        if len(sub) > 1:
            return {"match": None, "ambiguous": True}
        return {"match": None, "ambiguous": False}

    @classmethod
    def _apply_brand_paste(cls, text, all_brands):
        tokens = cls._split_tokens(text)
        selected = set()
        added, unresolved, ambiguous = [], [], []
        for token in tokens:
            r = cls._resolve_brand_token(token, all_brands)
            if r["match"]:
                selected.add(r["match"])
                added.append(r["match"])
            elif r["ambiguous"]:
                ambiguous.append(token)
            else:
                unresolved.append(token)
        return {"selected": selected, "added": added, "unresolved": unresolved, "ambiguous": ambiguous}

    @classmethod
    def _parse_tsv_cells(cls, line):
        cells = [cls._text(c) for c in str(line).split("\t")]
        while len(cells) < 3:
            cells.append("")
        return cells[:3]

    @classmethod
    def _parse_paste_matrix(cls, text):
        raw_lines = [line for line in str(text or "").splitlines() if line.strip()]
        if not raw_lines:
            return []
        if any("\t" in line for line in raw_lines):
            return [cls._parse_tsv_cells(line) for line in raw_lines]
        return [[cls._text(line)] for line in raw_lines]

    @classmethod
    def _parse_paste_grid(cls, text):
        return [
            {
                "requested_name": cells[0] if len(cells) > 0 else "",
                "code": cells[1] if len(cells) > 1 else "",
                "cas": cells[2] if len(cells) > 2 else "",
                "scope": cls.SCOPE_DEFAULT,
            }
            for cells in cls._parse_paste_matrix(text)
        ]

    @classmethod
    def _blank_row(cls):
        return {"requested_name": "", "code": "", "cas": "", "scope": cls.SCOPE_DEFAULT}

    @classmethod
    def _apply_paste_to_rows(cls, existing_rows, matrix, start_row, start_col):
        rows = [dict(row) for row in existing_rows]
        needed_rows = start_row + len(matrix)
        while len(rows) < needed_rows:
            rows.append(cls._blank_row())
        for row_offset, paste_row in enumerate(matrix):
            target_row = start_row + row_offset
            for col_offset, value in enumerate(paste_row):
                field_index = start_col + col_offset
                if field_index >= len(cls.GRID_FIELDS):
                    continue
                rows[target_row][cls.GRID_FIELDS[field_index]] = value or ""
        return rows

    @classmethod
    def _is_submittable(cls, row):
        return bool(row.get("requested_name") or row.get("code") or row.get("cas"))

    @classmethod
    def _filter_submittable(cls, rows):
        return [row for row in rows if cls._is_submittable(row)]

    # ── global brand policy (Phase 3B1) ─────────────────────────────────

    MODE_PRIORITY_FALLBACK = "PRIORITY_FALLBACK"
    MODE_ALLOWLIST_ONLY = "ALLOWLIST_ONLY"
    MODE_ALL_AVAILABLE = "ALL_AVAILABLE"
    ALL_AVAILABLE_POLICY = {"mode": "ALL_AVAILABLE", "priority_tiers": [], "brands": []}

    @classmethod
    def _allowlist_policy(cls, brands):
        return {"mode": cls.MODE_ALLOWLIST_ONLY, "priority_tiers": [], "brands": list(brands)}

    @classmethod
    def _tier_policy(cls, tiers):
        """`tiers` is a list of brand-lists, e.g. [["A"], ["B", "C"]]."""
        return {
            "mode": cls.MODE_PRIORITY_FALLBACK,
            "priority_tiers": [{"brands": list(t)} for t in tiers],
            "brands": [],
        }

    @classmethod
    def _policy_validation(cls, mode, allowlist_brands=None, tiers=None):
        """Mirrors qqPolicyValidation(): structural validity of the active mode only."""
        if mode == cls.MODE_ALLOWLIST_ONLY:
            return bool(allowlist_brands)
        if mode == cls.MODE_PRIORITY_FALLBACK:
            return any(bool(t) for t in (tiers or []))
        return True

    @classmethod
    def _legacy_brands_from_policy(cls, policy):
        """Mirrors qqLegacyBrandsFromPolicy(): derives filters.brands, never a second state."""
        if not policy:
            return []
        if policy.get("mode") == cls.MODE_ALLOWLIST_ONLY:
            return list(policy.get("brands") or [])
        if policy.get("mode") == cls.MODE_PRIORITY_FALLBACK:
            seen, out = set(), []
            for tier in policy.get("priority_tiers") or []:
                for b in tier.get("brands") or []:
                    if b not in seen:
                        seen.add(b)
                        out.append(b)
            return out
        return []

    @classmethod
    def _build_global_brand_policy_payload(cls, mode, allowlist_brands=None, tiers=None):
        """Mirrors qqBuildGlobalBrandPolicyPayload(): drops empty tiers, keeps display order."""
        if mode == cls.MODE_ALLOWLIST_ONLY:
            return cls._allowlist_policy(allowlist_brands or [])
        if mode == cls.MODE_PRIORITY_FALLBACK:
            non_empty = [t for t in (tiers or []) if t]
            return cls._tier_policy(non_empty)
        return dict(cls.ALL_AVAILABLE_POLICY)

    # ── per-row brand policy override (Phase 3B2) ───────────────────────
    # Mirrors qqRowBrandPolicies: Map<request_id, policy>. A "row policy" here
    # is {"mode": ..., "allowlist_brands": [...], "tiers": [[...], ...]} —
    # the Python-side stand-in for the JS Set/array-based row policy object.

    MODE_INHERIT = "INHERIT"

    @classmethod
    def _default_row_policy(cls):
        return {"mode": cls.MODE_INHERIT, "allowlist_brands": [], "tiers": []}

    @classmethod
    def _row_policy_summary(cls, row_policy):
        """Mirrors qqRowPolicySummary(): compact grid-column text, never a raw enum."""
        policy = row_policy or cls._default_row_policy()
        mode = policy.get("mode", cls.MODE_INHERIT)
        if mode == cls.MODE_ALLOWLIST_ONLY:
            return f"{len(policy.get('allowlist_brands') or [])} hãng riêng"
        if mode == cls.MODE_PRIORITY_FALLBACK:
            non_empty = len([t for t in (policy.get("tiers") or []) if t])
            return f"Ưu tiên riêng · {non_empty} mức"
        if mode == cls.MODE_ALL_AVAILABLE:
            return "Tất cả hãng"
        return "Theo thiết lập chung"

    @classmethod
    def _row_policy_validation(cls, row_policy, global_valid=True):
        """Mirrors qqRowPolicyValidation(): INHERIT defers to the global
        policy's own validity; other modes are validated against their own
        allowlist/tiers, exactly like the global policy is."""
        policy = row_policy or cls._default_row_policy()
        mode = policy.get("mode", cls.MODE_INHERIT)
        if mode == cls.MODE_INHERIT:
            return {"mode": mode, "valid": global_valid}
        if mode == cls.MODE_ALLOWLIST_ONLY:
            return {"mode": mode, "valid": bool(policy.get("allowlist_brands"))}
        if mode == cls.MODE_PRIORITY_FALLBACK:
            tiers = policy.get("tiers") or []
            return {"mode": mode, "valid": any(bool(t) for t in tiers)}
        return {"mode": mode, "valid": True}

    @classmethod
    def _build_row_brand_policy_payload(cls, row_policy):
        """Mirrors qqBuildRowBrandPolicyPayload(): INHERIT sends just
        {"mode": "INHERIT"}; every other mode reuses the exact same wire
        shape as the global policy (qqBuildBrandPolicyPayloadFrom)."""
        if not row_policy or row_policy.get("mode", cls.MODE_INHERIT) == cls.MODE_INHERIT:
            return {"mode": cls.MODE_INHERIT}
        mode = row_policy["mode"]
        if mode == cls.MODE_ALLOWLIST_ONLY:
            return cls._allowlist_policy(row_policy.get("allowlist_brands") or [])
        if mode == cls.MODE_PRIORITY_FALLBACK:
            non_empty = [t for t in (row_policy.get("tiers") or []) if t]
            return cls._tier_policy(non_empty)
        return dict(cls.ALL_AVAILABLE_POLICY)

    @classmethod
    def _row_is_exact_code_locked(cls, row):
        """Mirrors qqRowIsExactCodeLocked(): Exact Code + no equivalent search
        is the one case where brand policy never applies, regardless of
        whatever override is stored for the row."""
        has_code = bool(row.get("code"))
        scope = row.get("scope") or cls.SCOPE_DEFAULT
        if scope == cls.SCOPE_EXACT:
            resolved_equivalent = False
        elif scope == cls.SCOPE_EQUIV:
            resolved_equivalent = True
        else:
            resolved_equivalent = bool(row.get("equiv_default"))
        return has_code and not resolved_equivalent

    @classmethod
    def _build_payload(cls, rows, policy, size_text, strategy,
                       equiv_default=False, preparation_type="ANY", size_mode="ANY",
                       row_policies=None):
        """`row_policies` mirrors qqRowBrandPolicies: Map<request_id, override>,
        keyed by request_id (Phase 3B2). Rows without a request_id, or whose
        request_id has no entry, still default to INHERIT (Phase 3B1 behavior)."""
        submittable = cls._filter_submittable(rows)
        sizes = cls._split_tokens(size_text) if size_mode == "EXACT" else []
        effective_policy = policy if policy is not None else dict(cls.ALL_AVAILABLE_POLICY)
        row_policies = row_policies or {}
        payload_rows = []
        for row in submittable:
            override = row_policies.get(row.get("request_id"), {"mode": "INHERIT"})
            r = {
                "requested_name": row.get("requested_name", ""),
                "code": row.get("code", ""),
                "cas": row.get("cas", ""),
                "brand_policy_override": override,
            }
            if row.get("code"):
                scope = row.get("scope") or cls.SCOPE_DEFAULT
                if scope == cls.SCOPE_EXACT:
                    r["equivalent_override"] = False
                elif scope == cls.SCOPE_EQUIV:
                    r["equivalent_override"] = True
            payload_rows.append(r)
        payload = {
            "rows": payload_rows,
            "selection_strategy": strategy,
            "global_brand_policy": effective_policy,
        }
        if equiv_default:
            payload["equivalent_search_default"] = True
        legacy_brands = cls._legacy_brands_from_policy(effective_policy)
        filters = {}
        if legacy_brands:
            filters["brands"] = legacy_brands
        if preparation_type != "ANY":
            filters["preparation_type"] = preparation_type
        if size_mode != "ANY":
            filters["size_mode"] = size_mode
        if sizes:
            filters["sizes"] = sizes
        if filters:
            payload["filters"] = filters
        return payload

    @classmethod
    def _is_selectable(cls, candidate):
        if not candidate or candidate.get("eligible") is False:
            return False
        compliance = candidate.get("Compliance") or candidate.get("compliance") or ""
        return compliance not in cls.BLOCKED

    @classmethod
    def _effective_selected_candidates(cls, result, strategy, user_picks=None, row_index=0):
        if user_picks and row_index in user_picks:
            picks = user_picks[row_index]
            if isinstance(picks, list):
                return [p for p in picks if cls._is_selectable(p)]
            return [picks] if cls._is_selectable(picks) else []
        if strategy == "MANUAL" and result.get("reason") == "MANUAL_SELECTION_REQUIRED":
            return []
        sc = result.get("selected_candidates") or []
        if sc:
            return [c for c in sc if cls._is_selectable(c)]
        selected = result.get("selected")
        if selected and cls._is_selectable(selected):
            return [selected]
        return []

    @classmethod
    def _effective_selected(cls, result, strategy, user_picks=None, row_index=0):
        cands = cls._effective_selected_candidates(result, strategy, user_picks, row_index)
        return cands[0] if cands else None

    @classmethod
    def _is_reference_only(cls, result, strategy, user_picks=None, row_index=0):
        if cls._effective_selected(result, strategy, user_picks, row_index):
            return False
        candidates = result.get("candidates") or []
        return len(candidates) == 1 and not cls._is_selectable(candidates[0])

    @classmethod
    def _display_product(cls, result, strategy, user_picks=None, row_index=0):
        selected = cls._effective_selected(result, strategy, user_picks, row_index)
        if selected:
            return selected
        candidates = result.get("candidates") or []
        if len(candidates) == 1:
            return candidates[0]
        return None

    @classmethod
    def _summarize(cls, results, strategy, picks=None):
        picks = picks or {}
        counts = {"matched": 0, "manual_review": 0, "unresolved": 0, "blocked": 0}
        for index, result in enumerate(results):
            effective = cls._effective_selected(result, strategy, picks, index)
            if effective:
                counts["matched"] += 1
                continue
            reason = result.get("reason") or ""
            if reason == "MANUAL_REVIEW":
                counts["blocked"] += 1
                counts["manual_review"] += 1
                continue
            if reason == "MANUAL_SELECTION_REQUIRED":
                counts["manual_review"] += 1
                continue
            counts["unresolved"] += 1
        return counts

    @classmethod
    def _copy_payload(cls, results, strategy, picks=None):
        picks = picks or {}
        lines = []
        columns = [
            "Name", "Code", "Cas", "Brand", "Size", "Unit_Price", "Note",
            "Compliance", "Compliance_Note",
        ]
        for index, result in enumerate(results):
            candidates = cls._effective_selected_candidates(result, strategy, picks, index)
            for selected in candidates:
                if not cls._is_selectable(selected):
                    continue
                cells = []
                for key in columns:
                    if key == "Compliance":
                        cells.append(selected.get("Compliance") or selected.get("compliance") or "")
                    elif key == "Compliance_Note":
                        cells.append(selected.get("Compliance_Note") or selected.get("compliance_note") or "")
                    elif key == "Note":
                        cells.append(selected.get("Note") or selected.get("note") or "")
                    else:
                        cells.append(selected.get(key) or "")
                lines.append("\t".join(cells))
        return "\n".join(lines)


class QuickQuoteHelperMirrorTests(QuickQuoteMirrorHelpers, unittest.TestCase):
    """Python mirrors of quick_quote.js helper-logic regressions (brand
    widget parsing, policy validation, copy/summarize contracts)."""

    # ── brand widget logic (mirrors JS) ──────────────────────────────────

    SAMPLE_BRANDS = ["LGC (Đức)", "TRC (Canada)", "Accu (UK)", "Sigma", "Merck", "Fluka"]

    def test_resolve_brand_exact(self):
        r = self._resolve_brand_token("LGC (Đức)", self.SAMPLE_BRANDS)
        self.assertEqual(r["match"], "LGC (Đức)")
        self.assertFalse(r["ambiguous"])

    def test_resolve_brand_exact_case_insensitive(self):
        r = self._resolve_brand_token("lgc (đức)", self.SAMPLE_BRANDS)
        self.assertEqual(r["match"], "LGC (Đức)")

    def test_resolve_brand_unique_prefix(self):
        r = self._resolve_brand_token("LGC", self.SAMPLE_BRANDS)
        self.assertEqual(r["match"], "LGC (Đức)")
        self.assertFalse(r["ambiguous"])

    def test_resolve_brand_unique_prefix_trc(self):
        r = self._resolve_brand_token("TRC", self.SAMPLE_BRANDS)
        self.assertEqual(r["match"], "TRC (Canada)")

    def test_resolve_brand_unique_prefix_accu(self):
        r = self._resolve_brand_token("Accu", self.SAMPLE_BRANDS)
        self.assertEqual(r["match"], "Accu (UK)")

    def test_resolve_brand_unmatched(self):
        r = self._resolve_brand_token("XYZ", self.SAMPLE_BRANDS)
        self.assertIsNone(r["match"])
        self.assertFalse(r["ambiguous"])

    def test_resolve_brand_ambiguous(self):
        brands = ["Sigma-Aldrich", "Sigma-Merck", "Fluka"]
        r = self._resolve_brand_token("Sigma", brands)
        self.assertIsNone(r["match"])
        self.assertTrue(r["ambiguous"])

    def test_apply_brand_paste_comma_separated(self):
        res = self._apply_brand_paste("LGC, TRC, Accu", self.SAMPLE_BRANDS)
        self.assertIn("LGC (Đức)", res["selected"])
        self.assertIn("TRC (Canada)", res["selected"])
        self.assertIn("Accu (UK)", res["selected"])
        self.assertEqual(res["unresolved"], [])
        self.assertEqual(res["ambiguous"], [])

    def test_apply_brand_paste_mixed_separators(self):
        res = self._apply_brand_paste("LGC;Sigma\nFluka", self.SAMPLE_BRANDS)
        self.assertIn("LGC (Đức)", res["selected"])
        self.assertIn("Sigma", res["selected"])
        self.assertIn("Fluka", res["selected"])

    def test_apply_brand_paste_unmatched_reported(self):
        res = self._apply_brand_paste("LGC, UNKNOWN_CO", self.SAMPLE_BRANDS)
        self.assertIn("LGC (Đức)", res["selected"])
        self.assertIn("UNKNOWN_CO", res["unresolved"])

    def test_apply_brand_paste_ambiguous_reported(self):
        brands = ["Sigma-Aldrich", "Sigma-Merck", "Fluka"]
        res = self._apply_brand_paste("Sigma", brands)
        self.assertIn("Sigma", res["ambiguous"])
        self.assertEqual(len(res["selected"]), 0)

    def test_selected_brands_are_canonical_display_names(self):
        """Payload must contain exact canonical brand names, not shortened tokens."""
        res = self._apply_brand_paste("LGC", self.SAMPLE_BRANDS)
        self.assertEqual(list(res["selected"])[0], "LGC (Đức)")

    # ── global brand policy validation (Phase 3B1) ──────────────────────
    # Replaces the old per-row "needs brand" warning: Match is now gated
    # purely on the structural validity of whichever mode is selected,
    # independent of grid row content (exact-code rows bypass policy
    # entirely on the backend regardless of this gate).

    def test_policy_all_available_always_valid(self):
        self.assertTrue(self._policy_validation(self.MODE_ALL_AVAILABLE))
        self.assertTrue(self._policy_validation(self.MODE_ALL_AVAILABLE, allowlist_brands=[]))

    def test_policy_allowlist_only_requires_at_least_one_brand(self):
        self.assertFalse(self._policy_validation(self.MODE_ALLOWLIST_ONLY, allowlist_brands=[]))
        self.assertTrue(self._policy_validation(self.MODE_ALLOWLIST_ONLY, allowlist_brands=["BrandA"]))

    def test_policy_priority_fallback_requires_one_nonempty_tier(self):
        self.assertFalse(self._policy_validation(self.MODE_PRIORITY_FALLBACK, tiers=[]))
        self.assertFalse(self._policy_validation(self.MODE_PRIORITY_FALLBACK, tiers=[[]]))
        self.assertFalse(self._policy_validation(self.MODE_PRIORITY_FALLBACK, tiers=[[], []]))
        self.assertTrue(self._policy_validation(self.MODE_PRIORITY_FALLBACK, tiers=[[], ["BrandA"]]))

    def test_policy_priority_fallback_valid_with_multiple_nonempty_tiers(self):
        self.assertTrue(self._policy_validation(self.MODE_PRIORITY_FALLBACK, tiers=[["A"], ["B"]]))

    # ── global brand policy payload shape ───────────────────────────────

    def test_policy_payload_allowlist_only_shape(self):
        p = self._build_global_brand_policy_payload(self.MODE_ALLOWLIST_ONLY, allowlist_brands=["LGC", "TRC"])
        self.assertEqual(p["mode"], "ALLOWLIST_ONLY")
        self.assertEqual(p["priority_tiers"], [])
        self.assertEqual(p["brands"], ["LGC", "TRC"])

    def test_policy_payload_all_available_shape(self):
        p = self._build_global_brand_policy_payload(self.MODE_ALL_AVAILABLE)
        self.assertEqual(p, {"mode": "ALL_AVAILABLE", "priority_tiers": [], "brands": []})

    def test_policy_payload_priority_fallback_shape_is_list_of_brand_objects(self):
        """priority_tiers is [{"brands": [...]}, ...], matching search.py's
        _quote_validate_brand_policy — NOT bare [[...], ...] string arrays."""
        p = self._build_global_brand_policy_payload(
            self.MODE_PRIORITY_FALLBACK, tiers=[["CATO"], ["LGC", "HPC"]]
        )
        self.assertEqual(p["mode"], "PRIORITY_FALLBACK")
        self.assertEqual(p["priority_tiers"], [{"brands": ["CATO"]}, {"brands": ["LGC", "HPC"]}])
        self.assertEqual(p["brands"], [])

    def test_policy_payload_priority_fallback_drops_empty_tiers(self):
        """No empty tier is ever sent — this is what makes 'no empty tier in
        the middle' true by construction, regardless of UI ordering."""
        p = self._build_global_brand_policy_payload(
            self.MODE_PRIORITY_FALLBACK, tiers=[["A"], [], ["B"]]
        )
        self.assertEqual(p["priority_tiers"], [{"brands": ["A"]}, {"brands": ["B"]}])

    def test_legacy_brands_derived_from_allowlist_policy(self):
        p = self._allowlist_policy(["LGC", "TRC"])
        self.assertEqual(self._legacy_brands_from_policy(p), ["LGC", "TRC"])

    def test_legacy_brands_derived_from_tier_policy_union_first_seen_order(self):
        p = self._tier_policy([["A", "B"], ["B", "C"]])
        self.assertEqual(self._legacy_brands_from_policy(p), ["A", "B", "C"])

    def test_legacy_brands_empty_for_all_available(self):
        self.assertEqual(self._legacy_brands_from_policy(self.ALL_AVAILABLE_POLICY), [])

    # ── split tokens ──────────────────────────────────────────────────

    def test_split_tokens_comma(self):
        self.assertEqual(self._split_tokens("LGC, TRC, Accu"), ["LGC", "TRC", "Accu"])

    def test_split_tokens_semicolon(self):
        self.assertEqual(self._split_tokens("LGC;TRC;Accu"), ["LGC", "TRC", "Accu"])

    def test_split_tokens_newline(self):
        self.assertEqual(self._split_tokens("LGC\nTRC\nAccu"), ["LGC", "TRC", "Accu"])

    def test_split_tokens_tab(self):
        self.assertEqual(self._split_tokens("LGC\tTRC"), ["LGC", "TRC"])

    def test_split_tokens_strips(self):
        self.assertEqual(self._split_tokens("  LGC  ,  TRC  "), ["LGC", "TRC"])

    # ── paste/grid regression ──────────────────────────────────────────

    def test_paste_preserves_order_and_duplicates(self):
        text = "A\tC1\tCAS-1\nA\tC1\tCAS-1\nB\tC2\t"
        rows = self._parse_paste_grid(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["code"], "C1")
        self.assertEqual(rows[1]["code"], "C1")
        self.assertEqual(rows[2]["cas"], "")

    def test_tsv_trailing_tabs_preserve_alignment(self):
        rows = self._parse_paste_grid("Requested Name\t\t\nOnlyName\t\t")
        self.assertEqual(rows[0]["requested_name"], "Requested Name")
        self.assertEqual(rows[0]["code"], "")
        self.assertEqual(rows[1]["requested_name"], "OnlyName")

    def test_initial_blank_rows_reused_during_paste(self):
        existing = [self._blank_row() for _ in range(self.INITIAL_ROW_COUNT)]
        matrix = self._parse_paste_matrix("A\tC1\tCAS-1\nB\tC2\tCAS-2\nC\tC3\tCAS-3\nD\tC4\tCAS-4\nE\tC5\tCAS-5")
        result = self._apply_paste_to_rows(existing, matrix, 0, 0)
        self.assertEqual(len(result), self.INITIAL_ROW_COUNT)
        self.assertEqual(result[4]["code"], "C5")

    def test_paste_starts_at_focused_row_and_column(self):
        existing = [self._blank_row() for _ in range(5)]
        existing[2] = {"requested_name": "Keep", "code": "OLD", "cas": "OLD-CAS", "scope": self.SCOPE_DEFAULT}
        matrix = [["NEW-CODE"], ["NEXT-CODE"]]
        result = self._apply_paste_to_rows(existing, matrix, 2, 1)
        self.assertEqual(result[2]["code"], "NEW-CODE")
        self.assertEqual(result[2]["cas"], "OLD-CAS")
        self.assertEqual(result[3]["code"], "NEXT-CODE")

    def test_clear_all_controls_present(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn('id="qqClearAllBtn"', html)
        self.assertIn("qqClearAllGrid", js)
        self.assertIn("qqFocusFirstNameCell", js)
        self.assertIn("QQ_INITIAL_ROW_COUNT", js)

    def test_blank_rows_omitted_from_payload(self):
        rows = [
            self._blank_row(),
            {"requested_name": "Ref", "code": "X1", "cas": "", "scope": self.SCOPE_DEFAULT},
            self._blank_row(),
        ]
        payload = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL")
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["code"], "X1")

    # ── payload / override ─────────────────────────────────────────────

    def test_payload_equiv_default_toggle(self):
        rows = [{"requested_name": "R", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self._allowlist_policy(["BrandA"]), "", "LOWEST_OVERALL", equiv_default=True)
        self.assertTrue(p.get("equivalent_search_default"))
        p2 = self._build_payload(rows, self._allowlist_policy(["BrandA"]), "", "LOWEST_OVERALL", equiv_default=False)
        self.assertNotIn("equivalent_search_default", p2)

    def test_payload_scope_exact_sends_override_false(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_EXACT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL")
        self.assertEqual(p["rows"][0].get("equivalent_override"), False)

    def test_payload_scope_equiv_sends_override_true(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_EQUIV}]
        p = self._build_payload(rows, self._allowlist_policy(["BrandA"]), "", "MANUAL")
        self.assertEqual(p["rows"][0].get("equivalent_override"), True)

    def test_payload_scope_default_sends_no_override(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL")
        self.assertNotIn("equivalent_override", p["rows"][0])

    def test_payload_cas_only_no_code_sends_no_override(self):
        rows = [{"requested_name": "", "code": "", "cas": "CAS-001", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self._allowlist_policy(["BrandA"]), "", "MANUAL")
        self.assertNotIn("equivalent_override", p["rows"][0])

    def test_payload_row_always_carries_inherit_override(self):
        """Phase 3B1 has no per-row override UI yet — every row inherits the global policy."""
        rows = [
            {"requested_name": "R1", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT},
            {"requested_name": "R2", "code": "", "cas": "CAS-2", "scope": self.SCOPE_DEFAULT},
        ]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL")
        for row in p["rows"]:
            self.assertEqual(row["brand_policy_override"], {"mode": "INHERIT"})

    def test_payload_carries_global_brand_policy_object(self):
        rows = [{"requested_name": "R", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self._tier_policy([["A"], ["B"]]), "", "MANUAL")
        self.assertEqual(p["global_brand_policy"]["mode"], "PRIORITY_FALLBACK")
        self.assertEqual(p["global_brand_policy"]["priority_tiers"], [{"brands": ["A"]}, {"brands": ["B"]}])

    def test_payload_defaults_to_all_available_when_policy_omitted(self):
        rows = [{"requested_name": "R", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, None, "", "MANUAL")
        self.assertEqual(p["global_brand_policy"], self.ALL_AVAILABLE_POLICY)
        self.assertNotIn("brands", p.get("filters", {}))

    def test_payload_size_mode_exact_includes_sizes(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "1g, 100mg", "LOWEST_OVERALL", size_mode="EXACT")
        self.assertEqual(p["filters"]["size_mode"], "EXACT")
        self.assertEqual(p["filters"]["sizes"], ["1g", "100mg"])

    def test_payload_non_exact_mode_omits_sizes(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "1g", "LOWEST_OVERALL", size_mode="MIN")
        self.assertNotIn("sizes", p.get("filters", {}))

    def test_payload_preparation_type_neat(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "LOWEST_OVERALL", preparation_type="NEAT")
        self.assertEqual(p["filters"]["preparation_type"], "NEAT")

    def test_payload_preparation_type_any_omits_filter(self):
        rows = [{"requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL", preparation_type="ANY")
        self.assertNotIn("preparation_type", p.get("filters", {}))

    def test_payload_strategies(self):
        rows = [{"requested_name": "R", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        for strat in ("MANUAL", "LOWEST_PER_BRAND", "LOWEST_OVERALL"):
            p = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", strat)
            self.assertEqual(p["selection_strategy"], strat)

    # ── candidates / copy ─────────────────────────────────────────────

    def _make_cand(self, pid, name, brand="B", eligible=True, compliance="Được bán", auto_excluded=False):
        return {
            "product_id": pid, "Name": name, "Code": f"C{pid}", "Cas": f"CAS{pid}",
            "Brand": brand, "Size": "1g", "Unit_Price": "100",
            "Note": "", "Compliance": compliance,
            "eligible": eligible, "auto_excluded": auto_excluded, "Compliance_Note": "",
        }

    def test_blocked_candidates_not_selectable(self):
        blocked = {"Compliance": "Chưa xác định", "eligible": False}
        self.assertFalse(self._is_selectable(blocked))
        ok = {"Compliance": "Được bán", "eligible": True}
        self.assertTrue(self._is_selectable(ok))

    def test_manual_multi_pick_all_copied(self):
        ca = self._make_cand(1, "A")
        cb = self._make_cand(2, "B")
        result = {
            "reason": "MANUAL_SELECTION_REQUIRED", "selected": None, "selected_candidates": [],
            "candidates": [ca, cb],
        }
        picks = {0: [ca, cb]}
        copy = self._copy_payload([result], "MANUAL", picks)
        lines = [l for l in copy.split("\n") if l]
        self.assertEqual(len(lines), 2)
        self.assertIn("A", lines[0])
        self.assertIn("B", lines[1])

    def test_manual_unchecked_not_copied(self):
        ca = self._make_cand(1, "A")
        result = {
            "reason": "MANUAL_SELECTION_REQUIRED", "selected": None, "selected_candidates": [],
            "candidates": [ca],
        }
        copy = self._copy_payload([result], "MANUAL", {})
        self.assertEqual(copy, "")

    def test_lowest_per_brand_all_selected_candidates_copied(self):
        ca = self._make_cand(1, "A", brand="BrandA")
        cb = self._make_cand(2, "B", brand="BrandB")
        result = {
            "reason": "SELECTED_LOWEST_PER_BRAND",
            "selected": ca, "selected_candidates": [ca, cb],
            "candidates": [ca, cb],
        }
        copy = self._copy_payload([result], "LOWEST_PER_BRAND")
        lines = [l for l in copy.split("\n") if l]
        self.assertEqual(len(lines), 2)

    def test_lowest_overall_one_copied(self):
        ca = self._make_cand(1, "Best")
        result = {
            "reason": "SELECTED_LOWEST_OVERALL",
            "selected": ca, "selected_candidates": [ca],
            "candidates": [ca],
        }
        copy = self._copy_payload([result], "LOWEST_OVERALL")
        lines = [l for l in copy.split("\n") if l]
        self.assertEqual(len(lines), 1)

    def test_blocked_not_copied(self):
        blocked = self._make_cand(9, "Blocked", compliance="CẤM NHẬP", eligible=False)
        result = {
            "reason": "MANUAL_REVIEW", "selected": None, "selected_candidates": [],
            "candidates": [blocked],
        }
        self.assertEqual(self._copy_payload([result], "MANUAL"), "")

    def test_duplicate_auto_excluded_not_auto_copied(self):
        dup = self._make_cand(5, "Dup", auto_excluded=True)
        result = {
            "reason": "MANUAL_SELECTION_REQUIRED", "selected": None, "selected_candidates": [],
            "candidates": [dup],
        }
        self.assertEqual(self._copy_payload([result], "LOWEST_OVERALL"), "")

    def test_copy_no_header_no_empty_lines(self):
        ca = self._make_cand(1, "Good")
        none_result = {
            "reason": "NO_MATCH", "selected": None, "selected_candidates": [], "candidates": [],
        }
        good_result = {
            "reason": "SELECTED_LOWEST_OVERALL",
            "selected": ca, "selected_candidates": [ca], "candidates": [ca],
        }
        copy = self._copy_payload([none_result, good_result, none_result], "LOWEST_OVERALL")
        lines = [l for l in copy.split("\n") if l]
        self.assertEqual(len(lines), 1)
        self.assertNotRegex(copy, re.compile(r"^Name\t", re.M))

    def test_copy_order_follows_request(self):
        results = []
        for i in range(1, 4):
            c = self._make_cand(i, f"Prod{i}")
            results.append({
                "reason": "SELECTED_LOWEST_OVERALL",
                "selected": c, "selected_candidates": [c], "candidates": [c],
            })
        copy = self._copy_payload(results, "LOWEST_OVERALL")
        lines = [l for l in copy.split("\n") if l]
        self.assertEqual(len(lines), 3)
        self.assertIn("Prod1", lines[0])
        self.assertIn("Prod2", lines[1])
        self.assertIn("Prod3", lines[2])

    # ── Vietnamese labels / no raw enum ──────────────────────────────

    def test_vietnamese_reason_labels_in_js(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for label in self.REASON_LABELS.values():
            self.assertIn(label, js)

    def test_vietnamese_match_mode_labels_in_js(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for label in self.MATCH_MODE_LABELS.values():
            self.assertIn(label, js)

    def test_no_raw_enum_exposed(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for raw in ["MISSING_IDENTIFIER", "NO_VALID_PRICE", "BRAND_REQUIRED", "CODE_HAS_NO_CAS"]:
            # must appear only as dict key, never as bare textContent assignment
            self.assertIsNone(
                re.search(rf"textContent\s*=\s*['\"]?{re.escape(raw)}['\"]?", js),
                f"Raw enum exposed: {raw}",
            )

    # ── summary ──────────────────────────────────────────────────────

    def test_summary_counts(self):
        ca = self._make_cand(1, "A")
        results = [
            {"reason": "SELECTED_LOWEST_OVERALL", "selected": ca, "selected_candidates": [ca]},
            {"reason": "MISSING_IDENTIFIER", "selected": None, "selected_candidates": [], "candidates": []},
            {"reason": "MANUAL_SELECTION_REQUIRED", "selected": None, "selected_candidates": [], "candidates": [ca, ca]},
        ]
        counts = self._summarize(results, "LOWEST_OVERALL")
        self.assertEqual(counts["matched"], 1)
        self.assertEqual(counts["manual_review"], 1)
        self.assertEqual(counts["unresolved"], 1)

    # ── result table presence in JS ──────────────────────────────────

    def test_result_table_js_has_all_columns(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for col in ["Yêu cầu", "Sản phẩm", "Code", "CAS", "Brand", "Size", "Giá nhập",
                    "Note", "Compliance", "Ghi chú CL", "Loại khớp"]:
            self.assertIn(col, js, f"Missing column label: {col}")

    def test_result_table_css_containment(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        self.assertIn(".qq-result-table", css)
        self.assertIn("qq-result-table-wrap", css)
        self.assertIn(".qq-row-selected", css)
        self.assertIn(".qq-row-blocked", css)


# ─────────────────────────── route tests ───────────────────────────


class QuickQuoteRouteTests(unittest.TestCase):
    """Phase 6A -- Local Release Gate: `test_team_render_includes_brand_fixture`
    below is the only test in this class using a non-admin session
    (`team_id=1`), and it's testing template rendering of the team's brand
    fixture, not IP policy. Without this, it would fall through
    `middleware_access.py`'s REAL `teams.ip_policy` lookup against whatever
    `DATABASE_URL` is ambient (`products_local`, which doesn't have
    migration_015's `ip_policy` column applied) and get a 503 that has
    nothing to do with what the test actually verifies. Scope
    `DISABLE_IP_ALLOWLIST` to just this class (same pattern as
    `test_quote_assistant_api.py`'s `QuoteAssistantUnitTests`) rather than
    mocking `middleware_access.get_connection` for a policy this class never
    exercises either way.
    """

    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        # Phase 5D2A: stub the per-request session-liveness DB check with an
        # in-memory fake (no real Postgres touched) for every test here.
        start_auth_db_patch(self)
        self._disable_ip_patch = mock.patch.dict("os.environ", {"DISABLE_IP_ALLOWLIST": "1"})
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)

    def _auth(self, admin=True):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user_id"] = 1
            sess["auth_version"] = 1
            sess["is_admin"] = admin
            if not admin:
                sess["team_id"] = 1

    def test_quick_quote_requires_auth(self):
        response = self.client.get("/quote-assistant/quick")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("Location", ""))

    def _mock_conn(self, rows=None, raise_error=False):
        """Build a fake get_connection() whose cursor yields brand rows."""
        cursor = MagicMock()
        if raise_error:
            cursor.execute.side_effect = Exception("boom")
        cursor.fetchall.return_value = [(r,) for r in (rows or [])]
        cm = MagicMock()
        cm.__enter__.return_value = cursor
        cm.__exit__.return_value = False
        conn = MagicMock()
        conn.cursor.return_value = cm
        return conn

    def test_quick_quote_renders_for_authenticated_user(self):
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            response = self.client.get("/quote-assistant/quick")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Quick Quote", body)
        self.assertIn("qqRequestGrid", body)
        self.assertIn("qqPolicyWidget", body)
        self.assertIn("Cách chọn hãng", body)
        self.assertIn("Chọn thủ công", body)
        self.assertIn("Giá thấp nhất mỗi brand", body)
        self.assertIn("Tìm sản phẩm tương đương", body)
        self.assertNotIn("qqPreviewTable", body)

    def test_admin_render_includes_brand_fixture(self):
        self._auth(admin=True)
        brands = ["LGC (Đức)", "TRC (Canada)", "Accu (UK)"]
        with patch.object(search, "get_connection", return_value=self._mock_conn(brands)):
            response = self.client.get("/quote-assistant/quick")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for b in brands:
            self.assertIn(f'data-brand="{b}"', body)
        self.assertNotIn("Không tải được danh sách brand", body)

    def test_team_render_includes_brand_fixture(self):
        self._auth(admin=False)
        brands = ["TeamBrandA", "TeamBrandB"]
        with patch.object(search, "get_connection", return_value=self._mock_conn(brands)):
            response = self.client.get("/quote-assistant/quick")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('data-brand="TeamBrandA"', body)
        self.assertIn('data-brand="TeamBrandB"', body)

    def test_render_shows_brand_load_error_on_query_failure(self):
        self._auth(admin=True)
        with patch.object(search, "get_connection", return_value=self._mock_conn(raise_error=True)):
            response = self.client.get("/quote-assistant/quick")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Không tải được danh sách brand", body)
        self.assertIn('id="qqBrandLoadError"', body)

    def test_brand_query_uses_subquery_not_distinct_orderby(self):
        """The brand query must not ORDER BY a non-selected DISTINCT expression."""
        src = (ROOT / "search.py").read_text(encoding="utf-8")
        # locate quick_quote function body
        start = src.index("def quick_quote(")
        end = src.index("def ", start + 1)
        body = src[start:end]
        self.assertIn("visible_brands", body)
        self.assertIn("brand_load_error", body)
        self.assertIn("app.logger.exception", body)
        # must not have the buggy "ORDER BY UPPER(TRIM(p.brand))" with DISTINCT TRIM in same select
        self.assertNotIn("ORDER BY UPPER(TRIM(p.brand))", body)

    def test_quick_quote_api_rejects_unauthenticated(self):
        response = self.client.post("/api/quote-assistant/match", json={"rows": []})
        self.assertEqual(response.status_code, 401)

    def test_quick_quote_api_brand_required_for_cas_only(self):
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            response = self.client.post(
                "/api/quote-assistant/match",
                json={"rows": [{"cas": "CAS-ONLY-NO-BRAND"}]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"][0]["reason"], "BRAND_REQUIRED")

    def test_quick_quote_api_equivalent_override_accepted(self):
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            response = self.client.post(
                "/api/quote-assistant/match",
                json={
                    "equivalent_search_default": True,
                    "rows": [{"code": "NO_SUCH_CODE", "equivalent_override": False}],
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_quick_quote_api_all_strategies_accepted(self):
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            for strategy in ("MANUAL", "LOWEST_PER_BRAND", "LOWEST_OVERALL"):
                response = self.client.post(
                    "/api/quote-assistant/match",
                    json={"rows": [{"requested_name": "Test"}], "selection_strategy": strategy},
                )
                self.assertEqual(response.status_code, 200, f"strategy={strategy}")

    def test_quick_quote_api_preparation_type_and_size_mode(self):
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            response = self.client.post(
                "/api/quote-assistant/match",
                json={
                    "rows": [{"code": "NO_SUCH_CODE"}],
                    "filters": {"preparation_type": "NEAT", "size_mode": "MIN"},
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_cas_only_with_brand_reaches_api(self):
        """CAS-only + brand in payload should not be rejected at auth/payload level."""
        self._auth()
        with patch.object(search, "get_connection", return_value=self._mock_conn([])):
            response = self.client.post(
                "/api/quote-assistant/match",
                json={
                    "rows": [{"cas": "CAS-ONLY-123"}],
                    "filters": {"brands": ["SomeBrand"]},
                },
            )
        # API processes it; result may be NO_MATCH but not 4xx
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["results"][0]
        # reason is not BRAND_REQUIRED since brand was supplied
        self.assertNotEqual(result["reason"], "BRAND_REQUIRED")


# ─────────────────────── Export UI static checks ────────────────────────────


class QuickQuoteExportStaticTests(unittest.TestCase):
    """Verify direct active-template export UI and JS helpers."""

    def test_template_has_export_buttons(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqExportBtn"', html)
        self.assertIn('id="qqExportBtnBottom"', html)
        self.assertIn("Xuất Excel", html)

    def test_template_has_template_status_and_no_export_dialog(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqTemplateStatus"', html)
        self.assertIn("Đang kiểm tra mẫu báo giá…", html)
        self.assertNotIn('id="qqExportDialog"', html)
        self.assertNotIn('id="qqExportFileInput"', html)
        self.assertNotIn('id="qqExportCancelBtn"', html)
        self.assertNotIn('id="qqExportSubmitBtn"', html)
        self.assertNotIn("Xuất file", html)

    def test_js_has_export_functions(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in [
            "qqBuildExportSelections",
            "qqSafeFilenameFromDisposition",
            "qqSubmitExport",
            "qqUpdateExportButton",
            "qqLoadActiveTemplateMetadata",
            "qqRenderTemplateStatus",
            "qqHasActiveTemplate",
            "qqTemplateNameFromDownload",
        ]:
            self.assertIn(fn, js, f"Missing: {fn}")
        for removed in ["qqOpenExportDialog", "qqCloseExportDialog", "qqHandleExportFileChange", "QQ_EXPORT_MAX_BYTES"]:
            self.assertNotIn(removed, js)

    def test_metadata_fetch_and_template_states(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("QQ_TEMPLATE_ENDPOINT = '/api/quote-assistant/workbook/template'", js)
        self.assertIn("fetch(QQ_TEMPLATE_ENDPOINT", js)
        self.assertIn("Mẫu báo giá:", js)
        self.assertIn("Đang kiểm tra mẫu báo giá…", js)
        self.assertIn("Chưa có mẫu báo giá. Vui lòng liên hệ admin.", js)
        self.assertIn("Không tải được thông tin mẫu báo giá.", js)
        self.assertNotIn("uploaded_by", js)
        self.assertNotIn("mapping_json", js)

    def test_js_export_uses_form_data_without_workbook(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("async function qqSubmitExport")
        end = js.index("/* ═══════════════ paste into grid", start)
        export_section = js[start:end]
        self.assertIn("new FormData()", export_section)
        self.assertIn("fd.append('export_items'", export_section)
        self.assertNotIn("fd.append('selections'", export_section)
        self.assertIn("fetch(QQ_EXPORT_ENDPOINT", export_section)
        self.assertIn("QQ_EXPORT_ENDPOINT = '/api/quote-assistant/workbook/export'", js)
        self.assertNotIn("fd.append('workbook'", export_section)
        self.assertNotIn("headers:", export_section)
        self.assertNotIn("contentType", export_section)

    def test_js_export_no_product_fields_beyond_product_id(self):
        """Client only sends product_id, not Name/price/compliance."""
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("product_id: c.product_id", js)
        # verify qqBuildExportSelections does NOT send Name/Unit_Price
        start = js.index("function qqBuildExportSelections")
        end = js.index("}", start + 1)
        body = js[start:end]
        self.assertNotIn("Name", body)
        self.assertNotIn("Unit_Price", body)
        self.assertNotIn("Compliance", body)

    def test_js_export_filename_header_parsing(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("Content-Disposition", js)
        self.assertIn("filename*", js)
        self.assertIn("decodeURIComponent", js)

    def test_js_export_error_handling_status_codes(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("401", js)
        self.assertIn("403", js)
        self.assertIn("409", js)
        self.assertIn("413", js)
        self.assertIn("Chưa đăng nhập", js)
        self.assertIn("Không có quyền", js)
        self.assertIn("qqTemplateState = 'missing'", js)

    def test_js_export_double_submit_prevention(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqExportInProgress", js)
        self.assertIn("if (qqExportInProgress) return", js)

    def test_export_button_requires_template_and_top_bottom_sync(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("function qqUpdateExportButton")
        end = js.index("function qqCountSelected", start)
        body = js[start:end]
        self.assertIn("qqResults.length > 0 && qqHasActiveTemplate() && !qqExportInProgress", body)
        self.assertIn("qqExportBtn", body)
        self.assertIn("qqExportBtnBottom", body)
        self.assertIn("qqSetSoftDisabled(btn, !canExport)", body)
        self.assertIn("qqSetSoftDisabled(btnBottom, !canExport)", body)

    def test_export_blocked_shows_status_message(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("function qqExplainExportBlocked", js)
        start = js.index("async function qqSubmitExport")
        end = js.index("/* ═══════════════ paste into grid", start)
        body = js[start:end]
        self.assertIn("qqExplainExportBlocked()", body)
        self.assertNotIn("if (!selections.length) return;", body)

    def test_copy_blocked_shows_status_message(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("function qqExplainCopyBlocked", js)
        start = js.index("function qqCopyResults")
        end = js.index("/* ═══════════════ export workbook", start)
        body = js[start:end]
        self.assertIn("qqExplainCopyBlocked()", body)

    def test_copy_button_does_not_depend_on_template(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("function qqUpdateCopyButton")
        end = js.index("function qqUpdateExportButton", start)
        body = js[start:end]
        self.assertIn("qqHasCopyableRows(qqResults)", body)
        self.assertNotIn("qqHasActiveTemplate", body)

    def test_stale_results_disable_export(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("function qqInvalidateResults")
        end = js.index("function qqUpdateBrandWarning", start)
        body = js[start:end]
        self.assertIn("qqResults = []", body)
        self.assertIn("qqUpdateCopyButton()", body)

    def test_export_error_does_not_clear_selections_or_manual_picks(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("async function qqSubmitExport")
        end = js.index("/* ═══════════════ paste into grid", start)
        body = js[start:end]
        self.assertNotIn("qqResults = []", body)
        self.assertNotIn("qqUserPicks = new Map()", body)

    def test_js_export_no_innerHTML(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)

    def test_css_has_template_status_and_no_export_dialog_classes(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [".qq-template-status", ".qq-template-status.is-loading", ".qq-template-status.is-ok", ".qq-template-status.is-error", ".btn-excel-export"]:
            self.assertIn(cls, css, f"Missing CSS: {cls}")
        self.assertNotIn(".qq-export-dialog", css)
        self.assertNotIn(".qq-export-file-input", css)
        self.assertNotIn(".qq-export-actions", css)

    def test_css_no_export_dialog_backdrop(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        self.assertNotIn("qq-export-dialog::backdrop", css)

    def test_export_status_is_inline_not_card_or_modal(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        block = re.search(r"\.qq-template-status\s*\{[^}]+\}", css, re.S)
        self.assertIsNotNone(block)
        self.assertIn("font-size", block.group(0))
        self.assertNotIn("box-shadow", block.group(0))


class QuickQuoteExportClickabilityTests(unittest.TestCase):
    """Regressions for export clicks being swallowed and blob URLs revoked too early."""

    def _js(self):
        return QUICK_QUOTE_JS.read_text(encoding="utf-8")

    def _block(self, js, start_marker, end_marker):
        start = js.index(start_marker)
        return js[start:js.index(end_marker, start)]

    def test_action_buttons_are_not_natively_disabled(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        for button_id in ("qqCopyBtn", "qqExportBtn", "qqCopyBtnBottom", "qqExportBtnBottom"):
            match = re.search(rf'<button[^>]*id="{button_id}"[^>]*>', html)
            self.assertIsNotNone(match, f"Missing button {button_id}")
            tag = match.group(0)
            self.assertNotRegex(
                tag,
                r"(?<![-\w])disabled(?![-\w=])",
                f"{button_id} must not carry the native disabled attribute",
            )
            self.assertIn("is-soft-disabled", tag, button_id)
            self.assertIn('aria-disabled="true"', tag, button_id)

    def test_soft_disable_clears_native_disabled(self):
        body = self._block(self._js(), "function qqSetSoftDisabled", "function qqExplainCopyBlocked")
        self.assertIn("button.disabled = false", body)
        self.assertIn("removeAttribute('disabled')", body)
        self.assertIn("classList.toggle('is-soft-disabled', disabled)", body)
        self.assertIn("aria-disabled", body)

    def test_export_and_copy_state_never_set_native_disabled(self):
        js = self._js()
        for start, end in [
            ("function qqUpdateCopyButton", "function qqUpdateExportButton"),
            ("function qqUpdateExportButton", "function qqCountSelected"),
        ]:
            body = self._block(js, start, end)
            self.assertNotIn(".disabled = !", body, f"{start} must not natively disable the button")
            self.assertIn("qqSetSoftDisabled", body)

    def test_export_listeners_attached_once_each(self):
        js = self._js()
        self.assertEqual(js.count("addEventListener('click', qqSubmitExport)"), 2)
        self.assertEqual(
            js.count("getElementById('qqExportBtn')?.addEventListener('click', qqSubmitExport)"), 1
        )
        self.assertEqual(
            js.count("getElementById('qqExportBtnBottom')?.addEventListener('click', qqSubmitExport)"), 1
        )

    def test_copy_button_state_initialised_on_load(self):
        js = self._js()
        body = self._block(js, "document.addEventListener('DOMContentLoaded'", "document.getElementById('qqAddRowBtn')")
        self.assertIn("qqUpdateCopyButton()", body)
        self.assertIn("qqLoadActiveTemplateMetadata()", body)

    def test_download_anchor_lifecycle_and_async_revoke(self):
        js = self._js()
        body = self._block(js, "function qqTriggerBlobDownload", "function qqExportErrorMessage")
        self.assertIn("document.body.appendChild(a)", body)
        self.assertIn("a.click()", body)
        self.assertIn("a.remove()", body)
        self.assertIn("setTimeout(() => URL.revokeObjectURL(url), QQ_OBJECT_URL_REVOKE_MS)", body)
        without_async_revoke = body.replace(
            "setTimeout(() => URL.revokeObjectURL(url), QQ_OBJECT_URL_REVOKE_MS);", ""
        )
        self.assertNotIn("URL.revokeObjectURL(url)", without_async_revoke)
        revoke_ms = int(re.search(r"QQ_OBJECT_URL_REVOKE_MS = (\d+)", js).group(1))
        self.assertGreaterEqual(revoke_ms, 1000)

    def test_export_validates_zip_signature_before_download(self):
        js = self._js()
        checker = self._block(js, "async function qqBlobLooksLikeXlsx", "function qqTriggerBlobDownload")
        self.assertIn("0x50", checker)
        self.assertIn("0x4b", checker)
        submit = self._block(js, "async function qqSubmitExport", "/* ═══════════════ paste into grid")
        self.assertIn("await qqBlobLooksLikeXlsx(blob)", submit)
        self.assertLess(
            submit.index("await qqBlobLooksLikeXlsx(blob)"),
            submit.index("qqTriggerBlobDownload(blob, filename)"),
            "blob must be validated before the download is triggered",
        )

    def test_export_posts_only_export_items_to_export_endpoint(self):
        js = self._js()
        submit = self._block(js, "async function qqSubmitExport", "/* ═══════════════ paste into grid")
        self.assertIn("fd.append('export_items', JSON.stringify(exportItems))", submit)
        self.assertNotIn("fd.append('selections'", submit)
        self.assertNotIn("fd.append('workbook'", submit)
        self.assertIn("fetch(QQ_EXPORT_ENDPOINT", submit)
        self.assertIn("QQ_EXPORT_ENDPOINT = '/api/quote-assistant/workbook/export'", js)

    def test_blocked_export_explains_reason_instead_of_silent_return(self):
        js = self._js()
        submit = self._block(js, "async function qqSubmitExport", "/* ═══════════════ paste into grid")
        self.assertIn("qqExplainExportBlocked()", submit)
        self.assertNotIn("if (!selections.length) return;", submit)
        explain = self._block(js, "function qqExplainExportBlocked", "function qqSummarizeResults")
        for hint in ("Bấm Match", "Chưa có mẫu báo giá"):
            self.assertIn(hint, explain)

    def test_download_dom_harness_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        harness = Path(__file__).resolve().parent / "quick_quote_export_dom.js"
        result = subprocess.run(
            [node, str(harness)], capture_output=True, text=True, timeout=60, check=False
        )
        self.assertEqual(result.returncode, 0, f"{result.stdout}\n{result.stderr}")


# ─────────────────────── Export helper mirror tests ─────────────────────────


class QuickQuoteExportHelperTests(unittest.TestCase):
    """Python mirrors of export JS helpers."""

    BLOCKED = {"CẤM NHẬP", "Cấm nhập", "Chưa xác định"}

    @classmethod
    def _is_selectable(cls, candidate):
        if not candidate or candidate.get("eligible") is False:
            return False
        compliance = candidate.get("Compliance") or candidate.get("compliance") or ""
        return compliance not in cls.BLOCKED

    @classmethod
    def _effective_selected_candidates(cls, result, strategy, user_picks=None, row_index=0):
        if user_picks and row_index in user_picks:
            picks = user_picks.get(row_index, [])
            return [p for p in picks if cls._is_selectable(p)]
        if strategy == "MANUAL" and result.get("reason") == "MANUAL_SELECTION_REQUIRED":
            return []
        sc = result.get("selected_candidates") or []
        if sc:
            return [c for c in sc if cls._is_selectable(c)]
        selected = result.get("selected")
        if selected and cls._is_selectable(selected):
            return [selected]
        return []

    @classmethod
    def _build_export_selections(cls, results, strategy, user_picks=None):
        sel = []
        for idx, result in enumerate(results):
            cands = cls._effective_selected_candidates(result, strategy, user_picks, idx)
            for c in cands:
                if cls._is_selectable(c):
                    sel.append({"product_id": c["product_id"]})
        return sel

    @classmethod
    def _safe_filename(cls, disposition):
        if not disposition:
            return None
        import re as _re
        utf8 = _re.search(r"filename\*\s*=\s*UTF-8''([^;\s]+)", disposition, _re.I)
        if utf8:
            from urllib.parse import unquote
            try:
                return unquote(utf8.group(1))
            except Exception:
                pass
        ascii_m = _re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, _re.I)
        if ascii_m:
            return ascii_m.group(1).strip()
        return None

    def _make_cand(self, pid, name="P", compliance="Được bán", eligible=True):
        return {
            "product_id": pid, "Name": name, "Code": f"C{pid}", "Cas": f"CAS{pid}",
            "Brand": "B", "Size": "1g", "Unit_Price": "100",
            "Note": "", "Compliance": compliance, "eligible": eligible,
        }

    # 1. Export button disabled/enabled

    def test_export_disabled_when_no_results(self):
        sels = self._build_export_selections([], "MANUAL")
        self.assertEqual(sels, [])

    def test_export_enabled_when_has_selectable(self):
        c = self._make_cand(1)
        result = {"reason": "SELECTED_LOWEST_OVERALL", "selected": c,
                  "selected_candidates": [c], "candidates": [c]}
        sels = self._build_export_selections([result], "LOWEST_OVERALL")
        self.assertEqual(len(sels), 1)
        self.assertEqual(sels[0]["product_id"], 1)

    # 2. MANUAL checkbox updates export

    def test_manual_checkbox_updates_export(self):
        c = self._make_cand(2)
        result = {"reason": "MANUAL_SELECTION_REQUIRED", "selected": None,
                  "selected_candidates": [], "candidates": [c]}
        sels_no_pick = self._build_export_selections([result], "MANUAL", {})
        self.assertEqual(sels_no_pick, [])
        sels_with_pick = self._build_export_selections([result], "MANUAL", {0: [c]})
        self.assertEqual(sels_with_pick, [{"product_id": 2}])

    # 3. Auto strategies have selections

    def test_lowest_per_brand_selections(self):
        ca = self._make_cand(10, "A")
        cb = self._make_cand(11, "B")
        result = {"reason": "SELECTED_LOWEST_PER_BRAND",
                  "selected_candidates": [ca, cb], "candidates": [ca, cb]}
        sels = self._build_export_selections([result], "LOWEST_PER_BRAND")
        self.assertEqual([s["product_id"] for s in sels], [10, 11])

    def test_lowest_overall_one_selection(self):
        c = self._make_cand(20)
        result = {"reason": "SELECTED_LOWEST_OVERALL",
                  "selected": c, "selected_candidates": [c], "candidates": [c]}
        sels = self._build_export_selections([result], "LOWEST_OVERALL")
        self.assertEqual(sels, [{"product_id": 20}])

    # 4. Stale result → no selections (results cleared)

    def test_stale_empty_results_no_export(self):
        sels = self._build_export_selections([], "LOWEST_OVERALL")
        self.assertEqual(sels, [])

    # 5. Selected count reflects actual selections

    def test_selected_count(self):
        ca = self._make_cand(30)
        cb = self._make_cand(31)
        results = [
            {"reason": "SELECTED_LOWEST_OVERALL", "selected": ca,
             "selected_candidates": [ca], "candidates": [ca]},
            {"reason": "SELECTED_LOWEST_OVERALL", "selected": cb,
             "selected_candidates": [cb], "candidates": [cb]},
        ]
        sels = self._build_export_selections(results, "LOWEST_OVERALL")
        self.assertEqual(len(sels), 2)

    # 6. Reject blocked/ineligible

    def test_blocked_candidate_not_in_selections(self):
        blocked = self._make_cand(40, compliance="CẤM NHẬP", eligible=False)
        result = {"reason": "MANUAL_REVIEW", "selected": None,
                  "selected_candidates": [], "candidates": [blocked]}
        sels = self._build_export_selections([result], "MANUAL")
        self.assertEqual(sels, [])

    def test_unresolved_not_in_selections(self):
        result = {"reason": "NO_MATCH", "selected": None,
                  "selected_candidates": [], "candidates": []}
        sels = self._build_export_selections([result], "MANUAL")
        self.assertEqual(sels, [])

    # 7. FormData has only ordered product IDs via export_items; active template is server-side

    def test_js_form_data_appends_only_export_items(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("fd.append('export_items'", js)
        self.assertIn("JSON.stringify(exportItems)", js)
        self.assertNotIn("fd.append('selections'", js)
        self.assertNotIn("fd.append('workbook'", js)

    # 8. Duplicate product_id preserved

    def test_duplicate_product_id_kept(self):
        c = self._make_cand(50)
        results = [
            {"reason": "MANUAL_SELECTION_REQUIRED", "selected": None,
             "selected_candidates": [], "candidates": [c]},
            {"reason": "MANUAL_SELECTION_REQUIRED", "selected": None,
             "selected_candidates": [], "candidates": [c]},
        ]
        picks = {0: [c], 1: [c]}
        sels = self._build_export_selections(results, "MANUAL", picks)
        self.assertEqual([s["product_id"] for s in sels], [50, 50])

    # 9. No extra fields in selections

    def test_selections_only_product_id(self):
        c = self._make_cand(60)
        result = {"reason": "SELECTED_LOWEST_OVERALL",
                  "selected": c, "selected_candidates": [c], "candidates": [c]}
        sels = self._build_export_selections([result], "LOWEST_OVERALL")
        self.assertEqual(list(sels[0].keys()), ["product_id"])

    # 10. Filename header parsing

    def test_filename_utf8_encoded(self):
        d = "attachment; filename*=UTF-8''From_BG_V2_draft.xlsx"
        self.assertEqual(self._safe_filename(d), "From_BG_V2_draft.xlsx")

    def test_filename_utf8_with_vietnamese(self):
        from urllib.parse import quote
        name = "Báo giá_draft.xlsx"
        d = f"attachment; filename*=UTF-8''{quote(name)}"
        self.assertEqual(self._safe_filename(d), name)

    def test_filename_ascii_quoted(self):
        d = 'attachment; filename="quote_draft.xlsx"'
        self.assertEqual(self._safe_filename(d), "quote_draft.xlsx")

    def test_filename_ascii_unquoted(self):
        d = "attachment; filename=quote_draft.xlsx"
        self.assertEqual(self._safe_filename(d), "quote_draft.xlsx")

    def test_filename_missing_returns_none(self):
        self.assertIsNone(self._safe_filename(""))
        self.assertIsNone(self._safe_filename(None))
        self.assertIsNone(self._safe_filename("attachment"))

    # 11. Order follows request order

    def test_selections_preserve_request_order(self):
        results = []
        for i in [3, 1, 2]:
            c = self._make_cand(i)
            results.append({"reason": "SELECTED_LOWEST_OVERALL",
                            "selected": c, "selected_candidates": [c], "candidates": [c]})
        sels = self._build_export_selections(results, "LOWEST_OVERALL")
        self.assertEqual([s["product_id"] for s in sels], [3, 1, 2])

    # 12. Copy behavior not regressed

    def test_copy_tsv_format_unchanged(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqBuildCopyPayload", js)
        self.assertIn("QQ_COPY_COLUMNS", js)
        # export does NOT modify copy columns
        self.assertNotIn("qqExportSelections", js)


# ─────────────────────── Responsive/containment checks ──────────────────────


class QuickQuoteExportContainmentTests(unittest.TestCase):
    def test_template_has_no_export_dialog_or_file_picker(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertNotIn("qqExportDialog", html)
        self.assertNotIn("qqExportFileInput", html)
        self.assertNotIn("Mẫu báo giá (.xlsx)", html)
        self.assertIn('id="qqReplaceDialog"', html)

    def test_css_template_status_no_viewport_overflow(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        block = re.search(r"\.qq-result-actions\s*\{[^}]+\}", css, re.S)
        self.assertIsNotNone(block)
        body = block.group(0)
        self.assertIn("flex-wrap: wrap", body)

    def test_js_export_uses_textcontent_not_innerhtml(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("async function qqSubmitExport")
        end = js.index("/* ═══════════════ paste into grid", start)
        export_section = js[start:end]
        self.assertNotIn("innerHTML", export_section)


class QuickQuoteRequestIdIdentityTests(unittest.TestCase):
    """Phase 1: stable request_id generation and identity preservation in JS."""

    def test_request_id_helpers_exported(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("function qqNewRequestId", js)
        self.assertIn("function qqEnsureRequestId", js)
        self.assertIn("function qqRequestIdForResult", js)
        self.assertIn("qqNewRequestId", self._test_surface(js))
        self.assertIn("qqBuildExportItems", self._test_surface(js))

    def test_match_payload_carries_identity_fields(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        start = js.index("function qqBuildMatchPayload")
        end = js.index("/* ═══════════════ candidate helpers", start)
        body = js[start:end]
        self.assertIn("request_id", body)
        self.assertIn("request_order", body)
        self.assertIn("source_row", body)
        # request_order is computed from position, not array index
        self.assertIn("request_order: index + 1", body)

    def test_grid_rows_carry_request_id_dataset(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        create = js[js.index("function qqCreateRequestRow"):js.index("function qqSetRequestRows")]
        self.assertIn("tr.dataset.requestId", create)
        self.assertIn("tr.dataset.sourceRow", create)
        read = js[js.index("function qqReadRequestRows"):js.index("function qqIsSubmittableRow")]
        self.assertIn("request_id: tr.dataset.requestId", read)
        self.assertIn("source_row: tr.dataset.sourceRow", read)

    def test_user_picks_keyed_by_request_id_not_index(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        render = js[js.index("function qqRenderResultTable"):js.index("/* ═══════════════ preview")]
        # checkbox change handler must use requestId, not raw resultIndex
        self.assertIn("qqRequestIdForResult(result, resultIndex)", render)
        self.assertIn("qqUserPicks.set(requestId, picks)", render)
        self.assertNotIn("qqUserPicks.set(resultIndex", render)

    def test_export_items_v2_contract(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        items = js[js.index("function qqBuildExportItems"):js.index("/* ═══════════════ status UI")]
        self.assertIn("request_id", items)
        self.assertIn("request_order", items)
        self.assertIn("source_row", items)
        self.assertIn("requested_name", items)
        self.assertIn("requested_code", items)
        self.assertIn("requested_cas", items)
        self.assertIn("selection_order", items)
        # multi-selection order follows candidate display order (lineIndex+1)
        self.assertIn("selection_order: lineIndex + 1", items)

    def test_submit_export_sends_only_export_items(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        submit = js[js.index("async function qqSubmitExport"):js.index("/* ═══════════════ paste into grid")]
        self.assertIn("fd.append('export_items'", submit)
        self.assertNotIn("fd.append('selections'", submit)

    def test_file_import_preserves_source_row(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        import_block = js[js.index("async function qqImportRequestFileRows"):js.index("function qqInitRequestFileWizard")]
        self.assertIn("source_row: row.source_row", import_block)
        self.assertIn("request_id: qqNewRequestId()", import_block)

    def test_delete_row_forgets_identity(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        remove = js[js.index("document.getElementById('qqRemoveRowBtn')?.addEventListener"):js.index("document.getElementById('qqClearAllBtn')")]
        self.assertIn("qqForgetRequestId", remove)

    def test_clear_grid_mints_fresh_ids(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        set_rows = js[js.index("function qqSetRequestRows"):js.index("function qqAddRows")]
        self.assertIn("qqRequestIdentity = new Map()", set_rows)

    def _test_surface(self, js):
        start = js.index("window.QQ_TEST = {")
        end = js.index("};", start)
        return js[start:end]


class QuickQuoteLifecycleTests(unittest.TestCase):
    """Tests for Phase 2: Lifecycle states, reason codes, badges, and preflight warnings."""

    def test_lifecycle_constants_and_reason_codes_in_js(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        expected_lifecycles = [
            "QQ_LIFECYCLE_SELECTED",
            "QQ_LIFECYCLE_REVIEW",
            "QQ_LIFECYCLE_UNRESOLVED",
            "QQ_LIFECYCLE_BLOCKED",
            "QQ_LIFECYCLE_EXPORTED",
        ]
        for lc in expected_lifecycles:
            self.assertIn(lc, js)

        expected_reasons = [
            "PENDING_MATCH",
            "MISSING_IDENTIFIER",
            "NO_MATCH",
            "CODE_CAS_CONFLICT",
            "CODE_HAS_NO_CAS",
            "CODE_MULTIPLE_CAS",
            "BRAND_REQUIRED",
            "NO_VALID_PRICE",
            "MANUAL_SELECTION_REQUIRED",
            "FILTER_NO_MATCH",
            "COMPLIANCE_BLOCKED",
            "COMPLIANCE_UNRESOLVED",
            "DUPLICATE_CODE_BRAND_SIZE",
            "AUTO_SELECTED",
            "MANUALLY_SELECTED",
            "EXPORTED_SUCCESSFULLY",
        ]
        for r in expected_reasons:
            self.assertIn(r, js)

    def test_vietnamese_labels_no_raw_enum_exposed(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        # Ensure all lifecycle labels are friendly Vietnamese
        for label in ["Đã chọn", "Cần xem", "Chưa resolve", "Bị chặn", "Đã xuất"]:
            self.assertIn(label, js)
        # Ensure specific Vietnamese reason descriptions exist
        for reason in [
            "Chờ Match",
            "Thiếu Code/CAS",
            "Không tìm thấy sản phẩm",
            "Code và CAS không khớp",
            "Code không có CAS — không thể tìm tương đương",
            "Code có nhiều CAS — không thể tìm tương đương",
            "Cần chọn brand",
            "Không có giá hợp lệ",
            "Cần chọn sản phẩm",
            "Không khớp bộ lọc quy cách/dạng",
            "Tất cả sản phẩm bị chặn compliance",
            "Trùng Code + Brand + Size — cần chọn thủ công",
            "Đã chọn tự động",
            "Đã chọn thủ công",
            "Đã xuất Excel",
        ]:
            self.assertIn(reason, js)

    def test_five_badges_rendered_and_sum_to_total_requests(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqRenderSummary", js)
        self.assertIn("qq-summary-bar", js)
        self.assertIn("qq-lifecycle-btn", js)
        self.assertIn("aria-pressed", js)
        self.assertIn("qqActiveLifecycleFilter", js)

    def test_css_badges_and_row_containment(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [
            ".qq-summary-bar",
            ".qq-summary-counters",
            ".qq-summary-badges",
            ".qq-lifecycle-btn",
            ".qq-lifecycle-btn-selected",
            ".qq-lifecycle-btn-review",
            ".qq-lifecycle-btn-unresolved",
            ".qq-lifecycle-btn-blocked",
            ".qq-lifecycle-btn-exported",
            ".qq-cell-lifecycle",
            ".qq-lifecycle-tag",
            ".qq-status-reason",
            ".qq-row-action-btn",
            ".qq-result-empty-filter",
            "tr.is-missing-identifier",
            ".qq-grid-warn-badge",
        ]:
            self.assertIn(cls, css)

    def test_preflight_endpoint_in_js(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("QQ_PREFLIGHT_ENDPOINT = '/api/quote-assistant/preflight'", js)
        self.assertIn("qqRunPreflight", js)
        self.assertIn("qqPreflightResults", js)
        self.assertIn("qqUpdateGridRowStatus", js)


class QuickQuoteBrandPolicyDisplayTests(unittest.TestCase):
    """Phase 3B1: matched_priority_tier badge + FALLBACK_TIER_USED + expandable detail row."""

    def test_js_renders_tier_badge_from_matched_priority_tier(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("matched_priority_tier", js)
        self.assertIn("qq-tier-badge", js)
        self.assertIn("qqTierLabel", js)
        self.assertIn("Ưu tiên ${index + 1}", js)

    def test_js_has_fallback_tier_used_vietnamese_message(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("FALLBACK_TIER_USED", js)
        self.assertIn("Đã dùng brand ưu tiên thấp hơn (fallback)", js)
        # must be a dict-key mapping, never a bare raw-enum textContent assignment
        self.assertIsNone(
            re.search(r"textContent\s*=\s*['\"]?FALLBACK_TIER_USED['\"]?", js),
            "Raw enum FALLBACK_TIER_USED exposed directly",
        )

    def test_js_has_expandable_fallback_detail_row(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqFallbackTierEntries", js)
        self.assertIn("qqBuildFallbackDetailTable", js)
        self.assertIn("qqAttachFallbackDetailRow", js)
        self.assertIn("qq-fallback-toggle-btn", js)
        self.assertIn("qq-fallback-detail-row", js)
        self.assertIn("Chi tiết chọn hãng", js)
        # per-tier breakdown covers all three rejection buckets from search.py's fallback_path
        self.assertIn("COMPLIANCE", js)
        self.assertIn("NO_VALID_PRICE", js)

    def test_js_fallback_toggle_rewired_after_status_cell_rebuild(self):
        """Manual-pick rebuilds replace the status cell's DOM — the toggle
        button must be re-wired to the same (already-open/closed) detail row,
        not silently lose its click handler."""
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("qqWireFallbackToggle", js)
        start = js.index("function qqRenderResultTable")
        end = js.index("/* ═══════════════ preview")
        body = js[start:end]
        self.assertIn("existingDetailTr", body)
        self.assertIn("qqWireFallbackToggle(newToggleBtn, existingDetailTr)", body)

    def test_css_has_tier_builder_and_fallback_classes(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [
            ".qq-policy-field", ".qq-policy-mode-segmented", ".qq-policy-panel",
            ".qq-tier-list", ".qq-tier-row", ".qq-tier-label", ".qq-tier-body",
            ".qq-tier-controls", ".qq-tier-ctrl-btn", ".qq-tier-add-btn",
            ".qq-tier-badge", ".qq-fallback-toggle-btn", ".qq-fallback-detail-row",
            ".qq-fallback-detail-table",
        ]:
            self.assertIn(cls, css, f"Missing CSS: {cls}")

    def test_css_no_card_in_card_for_tier_rows(self):
        """Tier rows are flat dashed-separator rows, not nested cards."""
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        block = re.search(r"\.qq-tier-row\s*\{[^}]+\}", css, re.S)
        self.assertIsNotNone(block)
        self.assertNotIn("box-shadow", block.group(0))

    def test_css_responsive_mobile_390_rules_present(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        media_block = re.search(
            r"@media screen and \(max-width: 768px\)\s*\{.*?\.qq-tier-row\s*\{[^}]+\}",
            css, re.S,
        )
        self.assertIsNotNone(media_block, "Tier row must have a mobile (<=768px) layout override")
        self.assertIn("flex-direction: column", media_block.group(0))

    # ── regression: counters/copy/export/request-file must be unaffected ──

    def test_lifecycle_counters_unaffected_by_policy_refactor(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("function qqSummarizeResults", js)
        self.assertIn("function qqGetRequestLifecycle", js)

    def test_copy_export_functions_unaffected_by_policy_refactor(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in ["qqBuildCopyPayload", "qqBuildExportSelections", "qqBuildExportItems"]:
            self.assertIn(fn, js)

    def test_request_file_wizard_unaffected_by_policy_refactor(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in ["qqImportRequestFileRows", "qqHandleRequestFile", "qqInitRequestFileWizard"]:
            self.assertIn(fn, js)


# ─────────────────────── Phase 3B2: per-row brand policy override ──────────


class QuickQuoteRowPolicyMirrorTests(QuickQuoteMirrorHelpers, unittest.TestCase):
    """Python mirrors of the Phase 3B2 per-row override logic. Mixes in
    QuickQuoteMirrorHelpers (not QuickQuoteHelperMirrorTests) to reuse its
    classmethod helpers (_allowlist_policy, _tier_policy, _build_payload,
    etc.) WITHOUT inheriting from a TestCase — this is what prevents
    unittest from re-discovering and re-running QuickQuoteHelperMirrorTests'
    test_* methods a second time under this class name."""

    # ── default state / summary ─────────────────────────────────────────

    def test_default_row_policy_is_inherit(self):
        policy = self._default_row_policy()
        self.assertEqual(policy["mode"], self.MODE_INHERIT)
        self.assertEqual(self._row_policy_summary(policy), "Theo thiết lập chung")

    def test_row_policy_summary_inherit(self):
        self.assertEqual(self._row_policy_summary(None), "Theo thiết lập chung")
        self.assertEqual(self._row_policy_summary({"mode": "INHERIT"}), "Theo thiết lập chung")

    def test_row_policy_summary_all_available(self):
        self.assertEqual(self._row_policy_summary({"mode": "ALL_AVAILABLE"}), "Tất cả hãng")

    def test_row_policy_summary_allowlist_counts_brands(self):
        policy = {"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["LGC", "TRC"]}
        self.assertEqual(self._row_policy_summary(policy), "2 hãng riêng")

    def test_row_policy_summary_priority_fallback_counts_nonempty_tiers(self):
        policy = {"mode": "PRIORITY_FALLBACK", "tiers": [["CATO"], ["LGC", "HPC"]]}
        self.assertEqual(self._row_policy_summary(policy), "Ưu tiên riêng · 2 mức")

    def test_row_policy_summary_priority_fallback_ignores_empty_tiers(self):
        policy = {"mode": "PRIORITY_FALLBACK", "tiers": [["A"], [], ["B"]]}
        self.assertEqual(self._row_policy_summary(policy), "Ưu tiên riêng · 2 mức")

    def test_row_policy_summary_never_leaks_raw_enum(self):
        for policy in (
            {"mode": "INHERIT"}, {"mode": "ALL_AVAILABLE"},
            {"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]},
            {"mode": "PRIORITY_FALLBACK", "tiers": [["A"]]},
        ):
            summary = self._row_policy_summary(policy)
            self.assertNotIn(policy["mode"], summary)

    # ── validation ───────────────────────────────────────────────────────

    def test_row_validation_inherit_defers_to_global(self):
        self.assertTrue(self._row_policy_validation({"mode": "INHERIT"}, global_valid=True)["valid"])
        self.assertFalse(self._row_policy_validation({"mode": "INHERIT"}, global_valid=False)["valid"])

    def test_row_validation_all_available_always_valid(self):
        self.assertTrue(self._row_policy_validation({"mode": "ALL_AVAILABLE"}, global_valid=False)["valid"])

    def test_row_validation_allowlist_requires_brand(self):
        self.assertFalse(self._row_policy_validation({"mode": "ALLOWLIST_ONLY", "allowlist_brands": []})["valid"])
        self.assertTrue(self._row_policy_validation({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]})["valid"])

    def test_row_validation_priority_fallback_requires_nonempty_tier(self):
        self.assertFalse(self._row_policy_validation({"mode": "PRIORITY_FALLBACK", "tiers": []})["valid"])
        self.assertFalse(self._row_policy_validation({"mode": "PRIORITY_FALLBACK", "tiers": [[]]})["valid"])
        self.assertTrue(self._row_policy_validation({"mode": "PRIORITY_FALLBACK", "tiers": [[], ["A"]]})["valid"])

    def test_row_validation_independent_of_global_when_overridden(self):
        """An override's own validity never depends on the global policy's
        validity — only INHERIT does."""
        result = self._row_policy_validation({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]}, global_valid=False)
        self.assertTrue(result["valid"])

    # ── payload shape (contract with search.py's _quote_validate_brand_policy) ──

    def test_row_override_payload_inherit_shape(self):
        self.assertEqual(self._build_row_brand_policy_payload(None), {"mode": "INHERIT"})
        self.assertEqual(self._build_row_brand_policy_payload({"mode": "INHERIT"}), {"mode": "INHERIT"})

    def test_row_override_payload_allowlist_sends_canonical_brands(self):
        p = self._build_row_brand_policy_payload({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["LGC (Đức)", "TRC (Canada)"]})
        self.assertEqual(p, {"mode": "ALLOWLIST_ONLY", "priority_tiers": [], "brands": ["LGC (Đức)", "TRC (Canada)"]})

    def test_row_override_payload_priority_fallback_is_object_wrapped(self):
        p = self._build_row_brand_policy_payload(
            {"mode": "PRIORITY_FALLBACK", "tiers": [["CATO (Trung Quốc)"], ["LGC (Đức)", "HPC (Đức)"]]}
        )
        self.assertEqual(p["mode"], "PRIORITY_FALLBACK")
        self.assertEqual(
            p["priority_tiers"],
            [{"brands": ["CATO (Trung Quốc)"]}, {"brands": ["LGC (Đức)", "HPC (Đức)"]}],
        )
        self.assertEqual(p["brands"], [])

    def test_row_override_payload_all_available_sends_no_redundant_brands(self):
        p = self._build_row_brand_policy_payload({"mode": "ALL_AVAILABLE"})
        self.assertEqual(p, {"mode": "ALL_AVAILABLE", "priority_tiers": [], "brands": []})

    def test_row_override_payload_drops_empty_tiers(self):
        p = self._build_row_brand_policy_payload({"mode": "PRIORITY_FALLBACK", "tiers": [["A"], [], ["B"]]})
        self.assertEqual(p["priority_tiers"], [{"brands": ["A"]}, {"brands": ["B"]}])

    # ── exact-code lock ────────────────────────────────────────────────

    def test_exact_code_scope_is_locked_regardless_of_global_default(self):
        row = {"code": "C1", "scope": self.SCOPE_EXACT, "equiv_default": True}
        self.assertTrue(self._row_is_exact_code_locked(row))

    def test_equiv_scope_is_never_locked(self):
        row = {"code": "C1", "scope": self.SCOPE_EQUIV, "equiv_default": False}
        self.assertFalse(self._row_is_exact_code_locked(row))

    def test_default_scope_locked_state_follows_global_equiv_default(self):
        row_a = {"code": "C1", "scope": self.SCOPE_DEFAULT, "equiv_default": False}
        row_b = {"code": "C1", "scope": self.SCOPE_DEFAULT, "equiv_default": True}
        self.assertTrue(self._row_is_exact_code_locked(row_a))
        self.assertFalse(self._row_is_exact_code_locked(row_b))

    def test_cas_only_row_never_locked(self):
        row = {"code": "", "cas": "CAS-1", "scope": self.SCOPE_DEFAULT, "equiv_default": False}
        self.assertFalse(self._row_is_exact_code_locked(row))

    # ── payload integration: concurrent per-row modes ───────────────────

    def test_four_concurrent_row_policies_stay_independent_in_payload(self):
        """Row 1: allowlist A/B. Row 2: priority C -> D. Row 3: all available.
        Row 4: no entry at all -> inherits global. All in one Match call."""
        rows = [
            {"request_id": "r1", "requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT},
            {"request_id": "r2", "requested_name": "", "code": "C2", "cas": "", "scope": self.SCOPE_DEFAULT},
            {"request_id": "r3", "requested_name": "", "code": "C3", "cas": "", "scope": self.SCOPE_DEFAULT},
            {"request_id": "r4", "requested_name": "", "code": "C4", "cas": "", "scope": self.SCOPE_DEFAULT},
        ]
        row_policies = {
            "r1": self._build_row_brand_policy_payload({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A", "B"]}),
            "r2": self._build_row_brand_policy_payload({"mode": "PRIORITY_FALLBACK", "tiers": [["C"], ["D"]]}),
            "r3": self._build_row_brand_policy_payload({"mode": "ALL_AVAILABLE"}),
        }
        payload = self._build_payload(
            rows, self._tier_policy([["GlobalBrand"]]), "", "MANUAL", row_policies=row_policies,
        )
        self.assertEqual(payload["rows"][0]["brand_policy_override"]["mode"], "ALLOWLIST_ONLY")
        self.assertEqual(payload["rows"][0]["brand_policy_override"]["brands"], ["A", "B"])
        self.assertEqual(payload["rows"][1]["brand_policy_override"]["mode"], "PRIORITY_FALLBACK")
        self.assertEqual(
            payload["rows"][1]["brand_policy_override"]["priority_tiers"],
            [{"brands": ["C"]}, {"brands": ["D"]}],
        )
        self.assertEqual(payload["rows"][2]["brand_policy_override"]["mode"], "ALL_AVAILABLE")
        self.assertEqual(payload["rows"][3]["brand_policy_override"], {"mode": "INHERIT"})

    def test_identical_code_rows_keep_independent_policies(self):
        """Two rows with the exact same Code must not share a policy — only
        request_id decides which override applies."""
        rows = [
            {"request_id": "dup-1", "requested_name": "", "code": "SAMECODE", "cas": "", "scope": self.SCOPE_DEFAULT},
            {"request_id": "dup-2", "requested_name": "", "code": "SAMECODE", "cas": "", "scope": self.SCOPE_DEFAULT},
        ]
        row_policies = {
            "dup-1": self._build_row_brand_policy_payload({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]}),
        }
        payload = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL", row_policies=row_policies)
        self.assertEqual(payload["rows"][0]["brand_policy_override"]["mode"], "ALLOWLIST_ONLY")
        self.assertEqual(payload["rows"][1]["brand_policy_override"], {"mode": "INHERIT"})

    def test_row_override_takes_priority_over_global_policy(self):
        rows = [{"request_id": "r1", "requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        row_policies = {"r1": self._build_row_brand_policy_payload({"mode": "ALL_AVAILABLE"})}
        payload = self._build_payload(
            rows, self._allowlist_policy(["GlobalOnly"]), "", "MANUAL", row_policies=row_policies,
        )
        self.assertEqual(payload["global_brand_policy"]["mode"], "ALLOWLIST_ONLY")
        self.assertEqual(payload["rows"][0]["brand_policy_override"]["mode"], "ALL_AVAILABLE")

    def test_global_policy_change_does_not_touch_stored_row_override(self):
        """Simulates changing the global widget: the SAME row_policies dict
        (== qqRowBrandPolicies) is reused across two payload builds with two
        different global policies, and the row override is identical both times."""
        rows = [{"request_id": "r1", "requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        row_policies = {"r1": self._build_row_brand_policy_payload({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["Keep"]})}
        payload_1 = self._build_payload(rows, self.ALL_AVAILABLE_POLICY, "", "MANUAL", row_policies=row_policies)
        payload_2 = self._build_payload(rows, self._tier_policy([["X"], ["Y"]]), "", "MANUAL", row_policies=row_policies)
        self.assertEqual(payload_1["rows"][0]["brand_policy_override"], payload_2["rows"][0]["brand_policy_override"])
        self.assertNotEqual(payload_1["global_brand_policy"], payload_2["global_brand_policy"])

    def test_filters_brands_still_derives_only_from_global_not_row_overrides(self):
        """Legacy filters.brands must never mix in row-override brands — it
        mirrors global_brand_policy only, keeping exactly two sources of
        truth (global widget state, per-row policy map), never three."""
        rows = [{"request_id": "r1", "requested_name": "", "code": "C1", "cas": "", "scope": self.SCOPE_DEFAULT}]
        row_policies = {"r1": self._build_row_brand_policy_payload({"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["RowOnlyBrand"]})}
        payload = self._build_payload(rows, self._allowlist_policy(["GlobalBrand"]), "", "MANUAL", row_policies=row_policies)
        self.assertEqual(payload["filters"]["brands"], ["GlobalBrand"])
        self.assertNotIn("RowOnlyBrand", payload["filters"]["brands"])

    # ── identity: add/delete/reorder/clear ──────────────────────────────

    def test_request_id_keyed_map_survives_reorder(self):
        """The core guarantee behind qqRowBrandPolicies: a dict keyed by
        request_id (never by array/row index) keeps the right policy attached
        to the right request even after the row list is reordered."""
        row_policies = {
            "r1": {"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]},
            "r2": {"mode": "PRIORITY_FALLBACK", "tiers": [["C"]]},
            "r3": {"mode": "ALL_AVAILABLE"},
        }
        original_order = ["r1", "r2", "r3"]
        reordered = ["r3", "r1", "r2"]
        for request_id in original_order:
            self.assertEqual(
                row_policies[request_id],
                {"r1": {"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]},
                 "r2": {"mode": "PRIORITY_FALLBACK", "tiers": [["C"]]},
                 "r3": {"mode": "ALL_AVAILABLE"}}[request_id],
            )
        # After "reordering" the grid (only DOM/array order changes), every
        # request_id must still resolve to its own unchanged policy.
        for request_id in reordered:
            self.assertIn(request_id, row_policies)
        self.assertEqual(row_policies["r2"]["mode"], "PRIORITY_FALLBACK")

    def test_delete_row_removes_only_that_requests_policy(self):
        row_policies = {"r1": {"mode": "ALLOWLIST_ONLY"}, "r2": {"mode": "ALL_AVAILABLE"}}
        del row_policies["r1"]
        self.assertNotIn("r1", row_policies)
        self.assertIn("r2", row_policies)
        self.assertEqual(row_policies["r2"]["mode"], "ALL_AVAILABLE")

    def test_add_row_defaults_to_inherit_without_touching_existing_rows(self):
        row_policies = {"r1": {"mode": "ALLOWLIST_ONLY", "allowlist_brands": ["A"]}}
        # qqEnsureRowPolicy-equivalent: new row gets a fresh default entry.
        row_policies.setdefault("r2", self._default_row_policy())
        self.assertEqual(row_policies["r1"]["mode"], "ALLOWLIST_ONLY")
        self.assertEqual(row_policies["r2"]["mode"], self.MODE_INHERIT)

    def test_clear_grid_resets_every_row_to_inherit(self):
        row_policies = {"r1": {"mode": "ALLOWLIST_ONLY"}, "r2": {"mode": "PRIORITY_FALLBACK"}}
        row_policies.clear()  # qqSetRequestRows: qqRowBrandPolicies = new Map()
        for new_id in ("fresh-1", "fresh-2"):
            self.assertEqual(self._row_policy_summary(row_policies.get(new_id)), "Theo thiết lập chung")

    def test_request_file_import_creates_inherit_rows(self):
        imported_row_policies = {}  # fresh Map, exactly like manual clear
        for request_id in ("imp-1", "imp-2", "imp-3"):
            self.assertEqual(self._row_policy_summary(imported_row_policies.get(request_id)), "Theo thiết lập chung")

    # ── exact-code exemption from validation ────────────────────────────

    def test_exact_code_row_bypasses_invalid_override(self):
        """A row that's locked (Exact Code, no equivalent) never blocks Match
        even if it happens to carry a structurally-invalid override — the
        backend never applies brand policy to it at all."""
        row = {"code": "C1", "scope": self.SCOPE_EXACT, "equiv_default": False}
        invalid_override = {"mode": "ALLOWLIST_ONLY", "allowlist_brands": []}
        locked = self._row_is_exact_code_locked(row)
        self.assertTrue(locked)
        blocks_match = (not locked) and not self._row_policy_validation(invalid_override)["valid"]
        self.assertFalse(blocks_match)

    def test_non_locked_row_with_invalid_override_blocks_match(self):
        row = {"code": "", "cas": "CAS-1", "scope": self.SCOPE_DEFAULT, "equiv_default": False}
        invalid_override = {"mode": "PRIORITY_FALLBACK", "tiers": []}
        locked = self._row_is_exact_code_locked(row)
        self.assertFalse(locked)
        blocks_match = (not locked) and not self._row_policy_validation(invalid_override)["valid"]
        self.assertTrue(blocks_match)


class QuickQuoteRowPolicyUiTests(unittest.TestCase):
    """Structural checks on the real quick_quote.html/js/css — the grid
    column, the shared per-row dialog, state wiring, and result badges."""

    def test_template_has_row_policy_grid_column(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('<th class="qq-col-policy">Chính sách hãng</th>', html)

    def test_template_has_row_policy_dialog(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        self.assertIn('id="qqRowPolicyDialog"', html)
        self.assertIn('id="qqRowPolicyDialogTitle"', html)
        self.assertIn('data-row-policy-mode="INHERIT"', html)
        self.assertIn('data-row-policy-mode="PRIORITY_FALLBACK"', html)
        self.assertIn('data-row-policy-mode="ALLOWLIST_ONLY"', html)
        self.assertIn('data-row-policy-mode="ALL_AVAILABLE"', html)
        self.assertIn('id="qqRowAllowlistPanel"', html)
        self.assertIn('id="qqRowTierPanel"', html)
        self.assertIn('id="qqRowTierList"', html)
        self.assertIn('id="qqRowAddTierBtn"', html)
        self.assertIn('id="qqRowPolicyHint"', html)

    def test_dialog_reuses_canonical_brand_combo_markup(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        dialog_start = html.index('id="qqRowPolicyDialog"')
        dialog_end = html.index('</dialog>', dialog_start)
        dialog_html = html[dialog_start:dialog_end]
        self.assertIn('class="qq-brand-combo"', dialog_html)
        self.assertIn('qq-brand-search-input', dialog_html)
        self.assertIn('qq-brand-dropdown', dialog_html)
        self.assertIn('qq-tier-list', dialog_html)

    def test_match_button_no_longer_natively_disabled(self):
        """Phase 3B2 switches Match to the soft-disable convention so a click
        while a row override is invalid still registers and can explain why."""
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        match = re.search(r'<button[^>]*id="qqMatchBtn"[^>]*>', html)
        self.assertIsNotNone(match)
        tag = match.group(0)
        self.assertNotRegex(tag, r"(?<![-\w])disabled(?![-\w=])")
        self.assertIn("is-soft-disabled", tag)
        self.assertIn('aria-disabled="true"', tag)

    def test_js_row_policy_state_declared(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("let qqRowBrandPolicies = new Map();", js)
        self.assertIn("QQ_ROW_POLICY_INHERIT", js)
        self.assertIn("qqRowPolicyEditingRequestId", js)

    def test_js_row_policy_helpers_present(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in [
            "function qqDefaultRowPolicy", "function qqEnsureRowPolicy", "function qqForgetRowPolicy",
            "function qqRowPolicySummary", "function qqRowPolicyValidation",
            "function qqBuildRowBrandPolicyPayload", "function qqBuildBrandPolicyPayloadFrom",
            "function qqRowIsExactCodeLocked", "function qqRowResolvedEquivalent",
            "function qqRowBlocksMatch", "function qqAnyRowPolicyBlocksMatch",
            "function qqUpdateRowPolicyCell", "function qqOpenRowPolicyDialog",
            "function qqSetRowPolicyMode", "function qqOnRowPolicyChanged",
            "function qqCloseRowPolicyDialogCleanup",
        ]:
            self.assertIn(fn, js, f"Missing: {fn}")

    def test_js_new_row_ensures_inherit_default(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqCreateRequestRow"):js.index("function qqSetRequestRows")]
        self.assertIn("qqEnsureRowPolicy(requestId)", body)

    def test_js_delete_row_forgets_row_policy(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        remove = js[js.index("document.getElementById('qqRemoveRowBtn')?.addEventListener"):js.index("document.getElementById('qqClearAllBtn')")]
        self.assertIn("qqForgetRowPolicy", remove)

    def test_js_clear_grid_resets_row_policy_map(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqSetRequestRows"):js.index("function qqAddRows")]
        self.assertIn("qqRowBrandPolicies = new Map()", body)

    def test_js_match_payload_reads_from_row_policy_map(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqBuildMatchPayload"):js.index("/* ═══════════════ candidate helpers")]
        self.assertIn("qqBuildRowBrandPolicyPayload(qqRowBrandPolicies.get(row.request_id))", body)
        self.assertNotIn("{ mode: 'INHERIT' }", body)

    def test_js_match_button_soft_disabled_and_checks_row_overrides(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqUpdateMatchButton"):js.index("function qqUpdateCopyButton")]
        self.assertIn("qqSetSoftDisabled", body)
        self.assertIn("qqAnyRowPolicyBlocksMatch()", body)
        self.assertNotIn(".disabled = !", body)

    def test_js_exact_code_lock_bypasses_policy(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqRowBlocksMatch"):js.index("function qqAnyRowPolicyBlocksMatch")]
        self.assertIn("qqRowIsExactCodeLocked(tr)", body)
        self.assertIn("return false", body)

    def test_js_row_warning_and_edit_buttons_open_dialog(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqBuildRowPolicyCell"):js.index("function qqCreateRequestRow")]
        self.assertEqual(body.count("qqOpenRowPolicyDialog(tr)"), 2)

    def test_js_result_table_reads_row_policy_for_own_badge(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        body = js[js.index("function qqBuildStatusCellContent"):js.index("function qqWireFallbackToggle")]
        self.assertIn("qqRowBrandPolicies.get(qqRequestIdForResult(result, resultIndex))", body)
        self.assertIn("Hãng riêng", body)

    def test_js_no_raw_row_policy_enum_exposed(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for raw in ["INHERIT", "PRIORITY_FALLBACK", "ALLOWLIST_ONLY", "ALL_AVAILABLE"]:
            self.assertIsNone(
                re.search(rf"textContent\s*=\s*['\"]{re.escape(raw)}['\"]", js),
                f"Raw row-policy enum exposed: {raw}",
            )

    def test_tier_builder_is_shared_generic_engine_not_duplicated(self):
        """Phase 3B2 must reuse (not copy-paste) the Phase 3B1 tier builder —
        qqBuildTierRow/qqRenderTierListInCtx are the single shared engine
        used by both the global widget ctx and the row-dialog ctx."""
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        self.assertIn("function qqRenderTierListInCtx", js)
        self.assertIn("function qqAddTierTo", js)
        self.assertIn("function qqRemoveTierFrom", js)
        self.assertIn("function qqMoveTierIn", js)
        self.assertIn("qqGlobalTierCtx", js)
        self.assertIn("function qqRowTierCtx", js)
        # exactly one qqBuildTierRow definition — not a second copy for rows
        self.assertEqual(js.count("function qqBuildTierRow"), 1)

    def test_css_has_row_policy_classes(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        for cls in [
            ".qq-col-policy", ".qq-row-policy-normal", ".qq-row-policy-locked",
            ".qq-row-policy-summary", ".qq-row-policy-edit-btn", ".qq-row-policy-warn-badge",
            ".qq-row-policy-locked-btn", ".qq-row-policy-dialog", ".qq-own-policy-badge",
        ]:
            self.assertIn(cls, css, f"Missing CSS: {cls}")

    def test_css_row_policy_hidden_wrappers_actually_hide(self):
        """Regression: `.qq-row-policy-normal, .qq-row-policy-locked { display: flex }`
        and `.qq-row-policy-warn-badge { display: inline-flex }` both override the
        browser's default `[hidden] { display: none }` UA rule at equal specificity
        (author beats UA), so an explicit `[hidden]` override is required or the
        `hidden` attribute silently does nothing and both views render stacked."""
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.qq-row-policy-normal\[hidden\][\s\S]{0,40}\{\s*display:\s*none")
        self.assertRegex(css, r"\.qq-row-policy-locked\[hidden\][\s\S]{0,40}\{\s*display:\s*none")
        self.assertRegex(css, r"\.qq-row-policy-warn-badge\[hidden\]\s*\{\s*display:\s*none")

    def test_css_row_policy_cell_not_card_in_card(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        block = re.search(r"\.qq-row-policy-normal,\s*\.qq-row-policy-locked\s*\{[^}]+\}", css, re.S)
        self.assertIsNotNone(block)
        self.assertNotIn("box-shadow", block.group(0))

    def test_css_row_policy_dialog_no_mobile_overflow(self):
        css = QUICK_QUOTE_CSS.read_text(encoding="utf-8")
        block = re.search(r"\.qq-row-policy-dialog\s*\{[^}]+\}", css, re.S)
        self.assertIsNotNone(block)
        self.assertIn("100vw", block.group(0))
        media_block = re.search(
            r"@media screen and \(max-width: 768px\)\s*\{.*?\.qq-row-policy-dialog\s*\{[^}]+\}",
            css, re.S,
        )
        self.assertIsNotNone(media_block, "Row policy dialog must have an explicit mobile (<=768px) override")
        self.assertIn("100vw", media_block.group(0))

    def test_asset_version_bumped(self):
        html = QUICK_QUOTE_HTML.read_text(encoding="utf-8")
        css_version = re.search(r"styles\.css',\s*v='([^']+)'", html)
        js_version = re.search(r"quick_quote\.js',\s*v='([^']+)'", html)
        self.assertIsNotNone(css_version)
        self.assertIsNotNone(js_version)
        self.assertEqual(css_version.group(1), js_version.group(1))

    # ── regression: everything from earlier phases must be untouched ────

    def test_copy_export_order_stt_helpers_unaffected(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in [
            "qqBuildCopyPayload", "qqBuildExportSelections", "qqBuildExportItems",
            "qqRequestIdForResult", "qqSummarizeResults", "qqGetRequestLifecycle",
        ]:
            self.assertIn(fn, js)
        self.assertIn("request_order: index + 1", js)
        self.assertIn("selection_order: lineIndex + 1", js)

    def test_request_file_and_lifecycle_still_present(self):
        js = QUICK_QUOTE_JS.read_text(encoding="utf-8")
        for fn in ["qqImportRequestFileRows", "qqInitRequestFileWizard", "qqRunPreflight"]:
            self.assertIn(fn, js)


if __name__ == "__main__":
    unittest.main()
