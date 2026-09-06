/* global $ */

const QQ_MAX_ROWS = 2000;
const QQ_AJAX_TIMEOUT_MS = 180000;
const QQ_BLOCKED_COMPLIANCE = new Set(['CẤM NHẬP', 'Cấm nhập', 'Chưa xác định']);

const QQ_LIFECYCLE_SELECTED = 'SELECTED';
const QQ_LIFECYCLE_REVIEW = 'REVIEW';
const QQ_LIFECYCLE_UNRESOLVED = 'UNRESOLVED';
const QQ_LIFECYCLE_BLOCKED = 'BLOCKED';
const QQ_LIFECYCLE_EXPORTED = 'EXPORTED';

const QQ_LIFECYCLE_LABELS = {
    SELECTED: 'Đã chọn',
    REVIEW: 'Cần xem',
    UNRESOLVED: 'Chưa resolve',
    BLOCKED: 'Bị chặn',
    EXPORTED: 'Đã xuất',
};

const QQ_REASON_CODE_LABELS = {
    PENDING_MATCH: 'Chờ Match',
    MISSING_IDENTIFIER: 'Thiếu Code/CAS',
    NO_MATCH: 'Không tìm thấy sản phẩm',
    CODE_CAS_CONFLICT: 'Code và CAS không khớp',
    CODE_HAS_NO_CAS: 'Code không có CAS — không thể tìm tương đương',
    CODE_MULTIPLE_CAS: 'Code có nhiều CAS — không thể tìm tương đương',
    CODE_HAS_PLACEHOLDER_CAS: 'Code chỉ có CAS placeholder — không thể tìm tương đương',
    BRAND_REQUIRED: 'Cần chọn brand',
    NO_VALID_PRICE: 'Không có giá hợp lệ',
    MANUAL_SELECTION_REQUIRED: 'Cần chọn sản phẩm',
    CANDIDATE_LIMIT_EXCEEDED: 'Quá nhiều kết quả — vui lòng thu hẹp tìm kiếm',
    FILTER_NO_MATCH: 'Không khớp bộ lọc quy cách/dạng',
    COMPLIANCE_BLOCKED: 'Tất cả sản phẩm bị chặn compliance',
    COMPLIANCE_UNRESOLVED: 'Compliance chưa xác định',
    DUPLICATE_CODE_BRAND_SIZE: 'Trùng Code + Brand + Size — cần chọn thủ công',
    AUTO_SELECTED: 'Đã chọn tự động',
    MANUALLY_SELECTED: 'Đã chọn thủ công',
    EXPORTED_SUCCESSFULLY: 'Đã xuất Excel',
};

const QQ_REASON_LABELS = {
    MISSING_IDENTIFIER: 'Thiếu Code/CAS',
    NO_VALID_PRICE: 'Không có giá hợp lệ',
    MANUAL_SELECTION_REQUIRED: 'Cần chọn sản phẩm',
    CANDIDATE_LIMIT_EXCEEDED: 'Quá nhiều kết quả — vui lòng thu hẹp tìm kiếm',
    CODE_CAS_CONFLICT: 'Code và CAS không khớp',
    MANUAL_REVIEW: 'Cần kiểm tra thủ công',
    NO_MATCH: 'Không tìm thấy',
    SELECTED_SINGLE_CANDIDATE: 'Đã chọn tự động',
    SELECTED_LOWEST_UNIT_PRICE: 'Giá thấp nhất',
    SELECTED_LOWEST_OVERALL: 'Giá thấp nhất',
    SELECTED_LOWEST_PER_BRAND: 'Giá thấp nhất mỗi brand',
    BRAND_REQUIRED: 'Cần chọn brand',
    CODE_HAS_NO_CAS: 'Code không có CAS — không thể tìm tương đương',
    CODE_MULTIPLE_CAS: 'Code có nhiều CAS — không thể tìm tương đương',
    CODE_HAS_PLACEHOLDER_CAS: 'Code chỉ có CAS placeholder — không thể tìm tương đương',
};
const QQ_MATCH_MODE_LABELS = {
    EXACT_CODE: 'Đúng Code',
    EXACT_CAS: 'Đúng CAS',
    CODE_CAS: 'Code + CAS',
    EQUIVALENT: 'Tương đương theo CAS',
};
const QQ_WARNING_LABELS = {
    DUPLICATE_CODE_BRAND_SIZE: 'Trùng Code + Brand + Size — cần chọn thủ công',
    CODE_MULTIPLE_CAS: 'Code có nhiều CAS',
    SIZE_NOT_PARSED: 'Quy cách không parse được',
    SIZE_OVERFLOW: 'Quy cách vượt giới hạn',
    FALLBACK_TIER_USED: 'Đã dùng brand ưu tiên thấp hơn (fallback)',
};
const QQ_FALLBACK_REJECT_LABELS = {
    COMPLIANCE: 'Compliance',
    FILTER: 'Bộ lọc',
    NO_VALID_PRICE: 'Không có giá',
};
const QQ_COPY_COLUMNS = [
    'Name', 'Code', 'Cas', 'Brand', 'Size', 'Unit_Price', 'Note', 'Compliance', 'Compliance_Note',
];
const QQ_GRID_FIELDS = ['requested_name', 'code', 'cas'];
const QQ_INITIAL_ROW_COUNT = 5;
const QQ_SCOPE_DEFAULT = 'DEFAULT';
const QQ_SCOPE_EXACT = 'EXACT';
const QQ_SCOPE_EQUIV = 'EQUIV';
const QQ_TEMPLATE_ENDPOINT = '/api/quote-assistant/workbook/template';
const QQ_REQUEST_FILE_ANALYZE_ENDPOINT = '/api/quote-assistant/request-file/analyze';
const QQ_REQUEST_FILE_PARSE_ENDPOINT = '/api/quote-assistant/request-file/parse';
const QQ_REQUEST_FILE_MAX_BYTES = 10 * 1024 * 1024;
const QQ_EXPORT_ENDPOINT = '/api/quote-assistant/workbook/export';
const QQ_PREFLIGHT_ENDPOINT = '/api/quote-assistant/preflight';
const QQ_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
/* Revoking the blob URL in the same tick can cancel the download in some browsers. */
const QQ_OBJECT_URL_REVOKE_MS = 1000;

/* ── Stable request identity ── */
/*
 * Each grid row carries an immutable request_id for the session. Adding or
 * deleting rows never reuses or shifts another row's id; request_order is
 * recomputed from grid position. crypto.randomUUID() is preferred; a
 * monotonic counter fallback keeps tests and old runtimes working.
 */
let qqRequestIdCounter = 0;

function qqNewRequestId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }
    qqRequestIdCounter += 1;
    return `qq-legacy-${qqRequestIdCounter}`;
}

/*
 * Map<request_id, {requested_name, code, cas, scope, source_row}>.
 * The grid DOM stays the source of truth for current values; this map only
 * preserves identity + source_row across add/delete/paste operations.
 */
let qqRequestIdentity = new Map();

/*
 * Map<resultIndex, request_id> for legacy backends that don't echo request_id.
 * Lets us key qqUserPicks by request_id even when the backend omits identity.
 */
let qqLegacyResultIds = new Map();

/* ── Single source of truth for brand policy (Phase 3B1) ── */
const QQ_POLICY_PRIORITY_FALLBACK = 'PRIORITY_FALLBACK';
const QQ_POLICY_ALLOWLIST_ONLY = 'ALLOWLIST_ONLY';
const QQ_POLICY_ALL_AVAILABLE = 'ALL_AVAILABLE';

/*
 * qqBrandPolicyMode + qqAllowlistBrands + qqPriorityTiers together are the
 * ONLY source of truth for brand selection. No other DOM control reads or
 * writes brand state independently — this avoids the two-state drift a
 * single flat "selected brands" Set would be prone to once multiple pickers
 * (allowlist + per-tier) exist side by side.
 */
let qqBrandPolicyMode = QQ_POLICY_ALL_AVAILABLE;
let qqAllowlistBrands = new Set();
/* Array<{ id: string, brands: Set<string> }>, in priority order (index 0 = highest). */
let qqPriorityTiers = [];
let qqTierIdCounter = 0;
let qqAllBrands = [];               // canonical brand list, cloned from the shared <template>
/* Map<pickerKey, controller> — tracks live comboboxes so they can be torn down cleanly. */
let qqBrandPickers = new Map();

/* ── Per-row brand policy override (Phase 3B2) ──
 * qqRowBrandPolicies is a second, independent source of truth from the
 * global policy above — keyed by request_id (never by row index), so
 * add/delete/paste never transfers a policy to the wrong request, and two
 * rows with identical Code/CAS still carry unrelated policy objects.
 * Each entry has its OWN allowlistBrands Set / tiers array — nothing here
 * is ever the same object reference as the global qqAllowlistBrands /
 * qqPriorityTiers, so editing a row can never mutate the global policy
 * (or another row's policy) and vice versa. */
const QQ_ROW_POLICY_INHERIT = 'INHERIT';
let qqRowBrandPolicies = new Map();
/* request_id of the row currently open in the shared row-policy dialog. */
let qqRowPolicyEditingRequestId = null;
/* Picker keys created for the CURRENT dialog session, torn down before the
 * dialog is reused for a different row (the dialog DOM nodes are reused,
 * so stale per-tier picker listeners must not accumulate). */
let qqRowDialogPickerKeys = [];

let qqStrategy = 'MANUAL';
let qqPreparationType = 'ANY';
let qqSizeMode = 'ANY';
let qqResults = [];
/* Map<requestId, candidate[]> for MANUAL multi-picks */
let qqUserPicks = new Map();
let qqExportedRequestIds = new Set();
let qqActiveLifecycleFilter = null;
let qqPreflightResults = new Map();
let qqActiveTemplate = null;
let qqTemplateState = 'loading';
let qqExportInProgress = false;
let qqMatchInProgress = false;
let qqRequestSource = 'manual';
let qqRequestFile = null;
let qqRequestAnalyze = null;
let qqRequestAnalyzeInProgress = false;
let qqRequestParseInProgress = false;
let qqRequestAnalyzeSeq = 0;

/* ═══════════════ pure helpers ═══════════════ */

function qqText(value) {
    return String(value ?? '').trim();
}

function qqFormatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${n} B`;
}

function qqExcelSafeCell(value) {
    const s = String(value ?? '').replace(/\r?\n/g, ' ').trim();
    if (/^[=+\-@]/.test(s)) return `'${s}`;
    return s;
}

function qqSplitTokens(text) {
    /* Split on comma, semicolon, tab, newline */
    const out = [];
    for (const raw of String(text || '').split(/[\n\r\t,;]+/)) {
        const t = raw.trim();
        if (t) out.push(t);
    }
    return out;
}

/* Keep old name as alias used by tests */
const qqSplitList = qqSplitTokens;

function qqParseTsvCells(line) {
    const cells = String(line).split('\t').map(qqText);
    while (cells.length < 3) cells.push('');
    return cells.slice(0, 3);
}

function qqParsePasteMatrix(text) {
    const rawLines = String(text || '').split(/\r?\n/).filter((line) => line.trim() !== '');
    if (!rawLines.length) return [];
    const hasTabs = rawLines.some((line) => line.includes('\t'));
    if (hasTabs) return rawLines.map(qqParseTsvCells);
    return rawLines.map((line) => [qqText(line)]);
}

function qqParsePasteGrid(text) {
    return qqParsePasteMatrix(text).map((cells) => ({
        requested_name: cells[0] || '',
        code: cells[1] || '',
        cas: cells[2] || '',
        scope: QQ_SCOPE_DEFAULT,
    }));
}

/* alias kept for test surface */
const qqParsePasteLines = qqParsePasteGrid;

function qqBlankRow() {
    return { requested_name: '', code: '', cas: '', scope: QQ_SCOPE_DEFAULT };
}

/** Ensure a row dict has a stable request_id; never reuse across rows. */
function qqEnsureRequestId(row) {
    const r = row || {};
    if (!r.request_id || qqRequestIdentity.has(r.request_id)) {
        /* collision (e.g. duplicate row pasted): mint a fresh id */
        r.request_id = qqNewRequestId();
    }
    qqRequestIdentity.set(r.request_id, {
        requested_name: r.requested_name || '',
        code: r.code || '',
        cas: r.cas || '',
        scope: r.scope || QQ_SCOPE_DEFAULT,
        source_row: r.source_row == null ? null : Number(r.source_row),
    });
    return r;
}

/** Forget identity for rows no longer present (used after delete/clear). */
function qqForgetRequestId(requestId) {
    if (requestId) qqRequestIdentity.delete(requestId);
}

function qqApplyPasteToRows(existingRows, matrix, startRow, startCol) {
    const rows = existingRows.map((row) => ({ ...row }));
    const neededRows = startRow + matrix.length;
    while (rows.length < neededRows) rows.push(qqBlankRow());
    matrix.forEach((pasteRow, rowOffset) => {
        const targetRow = startRow + rowOffset;
        pasteRow.forEach((value, colOffset) => {
            const fi = startCol + colOffset;
            if (fi >= QQ_GRID_FIELDS.length) return;
            rows[targetRow][QQ_GRID_FIELDS[fi]] = value || '';
        });
    });
    return rows.slice(0, QQ_MAX_ROWS);
}

function qqGetPasteAnchor(activeElement) {
    const tr = activeElement?.closest('#qqRequestBody tr');
    const body = document.getElementById('qqRequestBody');
    if (!tr || !body) return { rowIndex: 0, colIndex: 0 };
    const rowIndex = Array.from(body.querySelectorAll('tr')).indexOf(tr);
    const input = activeElement?.closest('input.qq-grid-input');
    const inputs = tr.querySelectorAll('input.qq-grid-input');
    let colIndex = 0;
    if (input) {
        colIndex = Array.from(inputs).indexOf(input);
        if (colIndex < 0) colIndex = 0;
    }
    return { rowIndex: Math.max(0, rowIndex), colIndex: Math.max(0, colIndex) };
}

/* ═══════════════ grid read ═══════════════ */

function qqReadRequestRows() {
    const rows = [];
    document.querySelectorAll('#qqRequestBody tr').forEach((tr) => {
        rows.push({
            request_id: tr.dataset.requestId || qqNewRequestId(),
            requested_name: qqText(tr.querySelector('.qq-input-name')?.value),
            code: qqText(tr.querySelector('.qq-input-code')?.value),
            cas: qqText(tr.querySelector('.qq-input-cas')?.value),
            scope: tr.querySelector('.qq-input-scope')?.value || QQ_SCOPE_DEFAULT,
            source_row: tr.dataset.sourceRow == null ? null : Number(tr.dataset.sourceRow),
        });
    });
    return rows;
}

function qqIsSubmittableRow(row) {
    return Boolean(row.requested_name || row.code || row.cas);
}

function qqFilterSubmittableRows(rows) {
    return rows.filter(qqIsSubmittableRow);
}

function qqHasMatchableRows(rows) {
    return qqFilterSubmittableRows(rows).length > 0;
}

/* ═══════════════ brand policy — structural validation ═══════════════ */

/**
 * Structural validity of the CURRENTLY SELECTED global mode. This is the
 * only brand-related gate on Match in Phase 3B1 (no more per-row broad-match
 * detection): PRIORITY_FALLBACK needs >=1 tier with >=1 brand, ALLOWLIST_ONLY
 * needs >=1 brand, ALL_AVAILABLE has no requirement at all.
 */
function qqPolicyValidation() {
    if (qqBrandPolicyMode === QQ_POLICY_ALLOWLIST_ONLY) {
        return { mode: QQ_POLICY_ALLOWLIST_ONLY, valid: qqAllowlistBrands.size > 0 };
    }
    if (qqBrandPolicyMode === QQ_POLICY_PRIORITY_FALLBACK) {
        const validTierCount = qqPriorityTiers.filter((t) => t.brands.size > 0).length;
        return { mode: QQ_POLICY_PRIORITY_FALLBACK, valid: validTierCount > 0 };
    }
    return { mode: QQ_POLICY_ALL_AVAILABLE, valid: true };
}

/* ═══════════════ generic brand combobox picker ═══════════════ */

/**
 * Attempt to resolve a pasted token to a canonical brand name.
 * Priority: 1) exact (case-insensitive), 2) unique prefix/substring.
 * Returns { match: string|null, ambiguous: boolean }.
 */
function qqResolveBrandToken(token) {
    const needle = token.toLowerCase();
    /* 1. exact */
    const exact = qqAllBrands.find((b) => b.toLowerCase() === needle);
    if (exact) return { match: exact, ambiguous: false };
    /* 2. prefix (startsWith) */
    const prefixMatches = qqAllBrands.filter((b) => b.toLowerCase().startsWith(needle));
    if (prefixMatches.length === 1) return { match: prefixMatches[0], ambiguous: false };
    if (prefixMatches.length > 1) return { match: null, ambiguous: true };
    /* 3. substring */
    const subMatches = qqAllBrands.filter((b) => b.toLowerCase().includes(needle));
    if (subMatches.length === 1) return { match: subMatches[0], ambiguous: false };
    if (subMatches.length > 1) return { match: null, ambiguous: true };
    return { match: null, ambiguous: false };
}

/**
 * One controller per combobox instance: the ALLOWLIST_ONLY picker, or one per
 * priority tier. Each instance mutates the Set<string> the caller supplies
 * directly — that Set IS the state, so there is nothing else to keep in sync.
 * `root` is the DOM node containing `.qq-brand-chips` / `.qq-brand-combo`
 * (cloned from the shared #qqBrandOptionsTemplate for its option list).
 */
function qqCreateBrandPicker({ key, root, brands, onChange }) {
    if (!root) return null;
    const searchInput = root.querySelector('.qq-brand-search-input');
    const dropdown = root.querySelector('.qq-brand-dropdown');
    const listEl = root.querySelector('.qq-brand-list');
    const emptyEl = root.querySelector('.qq-brand-empty');
    const chipsEl = root.querySelector('.qq-brand-chips');
    const feedbackEl = root.querySelector('.qq-brand-paste-feedback');

    if (listEl && !listEl.querySelector('.qq-brand-option')) {
        const tpl = document.getElementById('qqBrandOptionsTemplate');
        if (tpl && tpl.content) listEl.appendChild(tpl.content.cloneNode(true));
    }

    function renderChips() {
        if (!chipsEl) return;
        chipsEl.replaceChildren();
        brands.forEach((name) => {
            const chip = document.createElement('span');
            chip.className = 'qq-brand-chip';
            const label = document.createElement('span');
            label.textContent = name;
            chip.appendChild(label);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'qq-brand-chip-remove';
            btn.setAttribute('aria-label', `Bỏ chọn ${name}`);
            btn.textContent = '×';
            btn.addEventListener('click', () => remove(name));
            chip.appendChild(btn);
            chipsEl.appendChild(chip);
        });
    }

    function updateList(filterText) {
        const needle = qqText(filterText ?? '').toLowerCase();
        let visibleCount = 0;
        listEl?.querySelectorAll('.qq-brand-option').forEach((item) => {
            const name = item.dataset.brand || '';
            const visible = !needle || name.toLowerCase().includes(needle);
            item.hidden = !visible;
            if (visible) visibleCount += 1;
            item.classList.toggle('is-selected', brands.has(name));
        });
        if (emptyEl) emptyEl.hidden = visibleCount !== 0;
    }

    function open() { if (dropdown) dropdown.hidden = false; }
    function close() {
        if (dropdown) dropdown.hidden = true;
        if (searchInput) searchInput.value = '';
        updateList('');
    }

    function add(name) {
        if (!name || !qqAllBrands.includes(name)) return;
        brands.add(name);
        renderChips();
        updateList();
        onChange();
    }

    function remove(name) {
        brands.delete(name);
        renderChips();
        updateList();
        onChange();
    }

    function toggle(name) {
        if (brands.has(name)) remove(name);
        else add(name);
    }

    /** Returns { added, unresolved, ambiguous } for tests. */
    function applyPaste(text) {
        const tokens = qqSplitTokens(text);
        if (!tokens.length) return { added: [], unresolved: [], ambiguous: [] };
        const added = [];
        const unresolved = [];
        const ambiguous = [];
        tokens.forEach((token) => {
            const { match, ambiguous: isAmbiguous } = qqResolveBrandToken(token);
            if (match) {
                brands.add(match);
                added.push(match);
            } else if (isAmbiguous) {
                ambiguous.push(token);
            } else {
                unresolved.push(token);
            }
        });
        renderChips();
        updateList();
        onChange();
        const issues = [];
        if (unresolved.length) issues.push(`Không nhận ra: ${unresolved.join(', ')}`);
        if (ambiguous.length) issues.push(`Mơ hồ (nhiều brand): ${ambiguous.join(', ')}`);
        if (feedbackEl) {
            feedbackEl.textContent = issues.join(' | ');
            feedbackEl.hidden = issues.length === 0;
        }
        return { added, unresolved, ambiguous };
    }

    const onFocusOpen = () => open();
    const onInput = () => {
        const val = searchInput.value;
        if (/[,;\t\n]/.test(val)) {
            applyPaste(val);
            searchInput.value = '';
            updateList('');
            return;
        }
        open();
        updateList(val);
    };
    const onKeydown = (e) => {
        if (e.key === 'Escape') { close(); return; }
        if (e.key === 'Enter') {
            e.preventDefault();
            const firstVisible = listEl?.querySelector('.qq-brand-option:not([hidden])');
            if (firstVisible) {
                toggle(firstVisible.dataset.brand);
                searchInput.value = '';
                updateList('');
            }
        }
    };
    const onListClick = (e) => {
        const item = e.target.closest('.qq-brand-option');
        if (!item) return;
        toggle(item.dataset.brand);
        searchInput.value = '';
        updateList('');
        searchInput.focus();
    };
    const onPaste = (e) => {
        const text = e.clipboardData?.getData('text/plain') || '';
        if (/[,;\t\n]/.test(text)) {
            e.preventDefault();
            applyPaste(text);
            searchInput.value = '';
            updateList('');
        }
    };
    const onOutsideClick = (e) => {
        if (!root.contains(e.target)) close();
    };

    searchInput?.addEventListener('focus', onFocusOpen);
    searchInput?.addEventListener('click', onFocusOpen);
    searchInput?.addEventListener('input', onInput);
    searchInput?.addEventListener('keydown', onKeydown);
    searchInput?.addEventListener('paste', onPaste);
    listEl?.addEventListener('click', onListClick);
    document.addEventListener('mousedown', onOutsideClick);

    function destroy() {
        searchInput?.removeEventListener('focus', onFocusOpen);
        searchInput?.removeEventListener('click', onFocusOpen);
        searchInput?.removeEventListener('input', onInput);
        searchInput?.removeEventListener('keydown', onKeydown);
        searchInput?.removeEventListener('paste', onPaste);
        listEl?.removeEventListener('click', onListClick);
        document.removeEventListener('mousedown', onOutsideClick);
        qqBrandPickers.delete(key);
    }

    renderChips();
    updateList('');

    const controller = { key, brands, add, remove, toggle, applyPaste, renderChips, updateList, destroy };
    qqBrandPickers.set(key, controller);
    return controller;
}

function qqDestroyBrandPicker(key) {
    qqBrandPickers.get(key)?.destroy();
}

/* ═══════════════ global brand policy payload ═══════════════ */

/**
 * Build the wire shape for `global_brand_policy` from the live picker state.
 * Always includes all three keys (mode/priority_tiers/brands) regardless of
 * mode, per the P3B1 contract. Empty tiers are dropped — this is what makes
 * "no empty tier in the middle" trivially true, since only non-empty tiers
 * are ever sent, in their current display order.
 *
 * Note: `priority_tiers` here is `[{brands:[...]}, ...]` (array of objects),
 * not bare `[[...], ...]` string arrays — the P3A backend
 * (_quote_validate_brand_policy in search.py) requires each tier to be an
 * object with a `brands` key. See final report for this contract note.
 */
/** Shared by qqBuildGlobalBrandPolicyPayload and qqBuildRowBrandPolicyPayload
 * so global and per-row overrides can never drift into different wire shapes. */
function qqBuildBrandPolicyPayloadFrom(mode, allowlistBrands, tiers) {
    if (mode === QQ_POLICY_ALLOWLIST_ONLY) {
        return {
            mode: QQ_POLICY_ALLOWLIST_ONLY,
            priority_tiers: [],
            brands: Array.from(allowlistBrands || []),
        };
    }
    if (mode === QQ_POLICY_PRIORITY_FALLBACK) {
        const cleanTiers = (tiers || [])
            .map((t) => ({ brands: Array.from(t.brands) }))
            .filter((t) => t.brands.length > 0);
        return { mode: QQ_POLICY_PRIORITY_FALLBACK, priority_tiers: cleanTiers, brands: [] };
    }
    return { mode: QQ_POLICY_ALL_AVAILABLE, priority_tiers: [], brands: [] };
}

function qqBuildGlobalBrandPolicyPayload() {
    return qqBuildBrandPolicyPayloadFrom(qqBrandPolicyMode, qqAllowlistBrands, qqPriorityTiers);
}

/**
 * Per-row override payload (Phase 3B2). INHERIT sends just `{mode:
 * 'INHERIT'}` — the backend then falls back to global_brand_policy for that
 * row. Any other mode reuses the exact same shape as the global policy
 * (qqBuildBrandPolicyPayloadFrom), so ALLOWLIST_ONLY/PRIORITY_FALLBACK/
 * ALL_AVAILABLE never diverge between "global" and "row override" wire
 * formats.
 */
function qqBuildRowBrandPolicyPayload(rowPolicy) {
    if (!rowPolicy || rowPolicy.mode === QQ_ROW_POLICY_INHERIT) {
        return { mode: QQ_ROW_POLICY_INHERIT };
    }
    return qqBuildBrandPolicyPayloadFrom(rowPolicy.mode, rowPolicy.allowlistBrands, rowPolicy.tiers);
}

/* ── Row policy state helpers (Phase 3B2) ──
 * qqRowBrandPolicies is declared near the global policy state above. These
 * helpers are the only code that reads/writes it, mirroring how
 * qqCreateBrandPicker is the only code that mutates a brands Set. */

function qqDefaultRowPolicy() {
    return { mode: QQ_ROW_POLICY_INHERIT, allowlistBrands: new Set(), tiers: [] };
}

/** Get-or-create a row's policy entry. New rows always start at INHERIT. */
function qqEnsureRowPolicy(requestId) {
    if (!requestId) return qqDefaultRowPolicy();
    if (!qqRowBrandPolicies.has(requestId)) {
        qqRowBrandPolicies.set(requestId, qqDefaultRowPolicy());
    }
    return qqRowBrandPolicies.get(requestId);
}

function qqForgetRowPolicy(requestId) {
    if (requestId) qqRowBrandPolicies.delete(requestId);
}

/** Compact grid-column summary text — never a raw enum, matches the spec's
 * four example strings exactly. */
function qqRowPolicySummary(rowPolicy) {
    const policy = rowPolicy || qqDefaultRowPolicy();
    if (policy.mode === QQ_POLICY_ALLOWLIST_ONLY) {
        return `${policy.allowlistBrands.size} hãng riêng`;
    }
    if (policy.mode === QQ_POLICY_PRIORITY_FALLBACK) {
        const nonEmptyTiers = policy.tiers.filter((t) => t.brands.size > 0).length;
        return `Ưu tiên riêng · ${nonEmptyTiers} mức`;
    }
    if (policy.mode === QQ_POLICY_ALL_AVAILABLE) {
        return 'Tất cả hãng';
    }
    return 'Theo thiết lập chung';
}

/**
 * Structural validity of a row's OWN override, mirroring qqPolicyValidation.
 * INHERIT rows are only as valid as the current global policy (no separate
 * row-level requirement); ALL_AVAILABLE is always valid; ALLOWLIST_ONLY
 * needs >=1 brand; PRIORITY_FALLBACK needs >=1 non-empty tier.
 */
function qqRowPolicyValidation(rowPolicy) {
    const policy = rowPolicy || qqDefaultRowPolicy();
    if (policy.mode === QQ_ROW_POLICY_INHERIT) {
        return { mode: QQ_ROW_POLICY_INHERIT, valid: qqPolicyValidation().valid };
    }
    if (policy.mode === QQ_POLICY_ALLOWLIST_ONLY) {
        return { mode: QQ_POLICY_ALLOWLIST_ONLY, valid: policy.allowlistBrands.size > 0 };
    }
    if (policy.mode === QQ_POLICY_PRIORITY_FALLBACK) {
        const validTierCount = policy.tiers.filter((t) => t.brands.size > 0).length;
        return { mode: QQ_POLICY_PRIORITY_FALLBACK, valid: validTierCount > 0 };
    }
    return { mode: QQ_POLICY_ALL_AVAILABLE, valid: true };
}

/**
 * Legacy `filters.brands` mirror, derived from the policy so there is never a
 * second brand state to drift from the first. ALLOWLIST_ONLY -> its brands;
 * PRIORITY_FALLBACK -> union of all tier brands (first-seen order);
 * ALL_AVAILABLE -> [] (no restriction).
 */
function qqLegacyBrandsFromPolicy(policy) {
    if (!policy) return [];
    if (policy.mode === QQ_POLICY_ALLOWLIST_ONLY) return Array.from(policy.brands || []);
    if (policy.mode === QQ_POLICY_PRIORITY_FALLBACK) {
        const seen = new Set();
        const out = [];
        (policy.priority_tiers || []).forEach((tier) => {
            (tier.brands || []).forEach((b) => {
                if (!seen.has(b)) {
                    seen.add(b);
                    out.push(b);
                }
            });
        });
        return out;
    }
    return [];
}

/* ═══════════════ payload builder ═══════════════ */

function qqBuildMatchPayload(rows, policy, sizeText, strategy, equivDefault) {
    const submittable = qqFilterSubmittableRows(rows);
    const sizes = qqSplitTokens(sizeText);
    const effectivePolicy = policy || { mode: QQ_POLICY_ALL_AVAILABLE, priority_tiers: [], brands: [] };

    const payloadRows = submittable.map((row, index) => {
        const r = {
            request_id: row.request_id,
            request_order: index + 1,
            source_row: row.source_row == null ? null : row.source_row,
            requested_name: row.requested_name,
            code: row.code,
            cas: row.cas,
            /* Phase 3B2: each row carries its OWN override object, sourced
             * from qqRowBrandPolicies (never from the global policy state). */
            brand_policy_override: qqBuildRowBrandPolicyPayload(qqRowBrandPolicies.get(row.request_id)),
        };
        if (row.code) {
            const scope = row.scope || QQ_SCOPE_DEFAULT;
            if (scope === QQ_SCOPE_EXACT) r.equivalent_override = false;
            else if (scope === QQ_SCOPE_EQUIV) r.equivalent_override = true;
        }
        return r;
    });

    const payload = { rows: payloadRows, selection_strategy: strategy };
    if (equivDefault) payload.equivalent_search_default = true;
    payload.global_brand_policy = effectivePolicy;

    const legacyBrands = qqLegacyBrandsFromPolicy(effectivePolicy);
    const filters = {};
    if (legacyBrands.length) filters.brands = legacyBrands;
    if (qqPreparationType !== 'ANY') filters.preparation_type = qqPreparationType;
    if (qqSizeMode !== 'ANY') filters.size_mode = qqSizeMode;
    if (sizes.length && qqSizeMode === 'EXACT') filters.sizes = sizes;
    if (Object.keys(filters).length) payload.filters = filters;

    return payload;
}

/* ═══════════════ candidate helpers ═══════════════ */

function qqIsSelectableCandidate(candidate) {
    if (!candidate || candidate.eligible === false) return false;
    const compliance = candidate.Compliance || candidate.compliance || '';
    return !QQ_BLOCKED_COMPLIANCE.has(compliance);
}

/** Resolve the request_id for a result, preferring backend-provided identity. */
function qqRequestIdForResult(result, fallbackIndex) {
    if (result && result.request_id) return result.request_id;
    /* Legacy backend without request_id: synthesize a stable per-session key. */
    if (!qqLegacyResultIds.has(fallbackIndex)) {
        qqLegacyResultIds.set(fallbackIndex, qqNewRequestId());
    }
    return qqLegacyResultIds.get(fallbackIndex);
}

function qqEffectiveSelectedCandidates(result, rowIndex) {
    const requestId = qqRequestIdForResult(result, rowIndex);
    if (qqUserPicks.has(requestId)) {
        const picks = qqUserPicks.get(requestId) || [];
        return picks.filter(qqIsSelectableCandidate);
    }
    if (qqStrategy === 'MANUAL' && result.reason === 'MANUAL_SELECTION_REQUIRED') return [];
    const sc = result.selected_candidates || [];
    if (sc.length) return sc.filter(qqIsSelectableCandidate);
    if (result.selected && qqIsSelectableCandidate(result.selected)) return [result.selected];
    return [];
}

function qqEffectiveSelected(result, rowIndex) {
    const cands = qqEffectiveSelectedCandidates(result, rowIndex);
    return cands.length ? cands[0] : null;
}

function qqIsReferenceOnly(result, rowIndex) {
    if (qqEffectiveSelected(result, rowIndex)) return false;
    const candidates = result.candidates || [];
    return candidates.length === 1 && !qqIsSelectableCandidate(candidates[0]);
}

function qqDisplayProduct(result, rowIndex) {
    const sel = qqEffectiveSelected(result, rowIndex);
    if (sel) return sel;
    const candidates = result.candidates || [];
    return candidates.length === 1 ? candidates[0] : null;
}

function qqFormatStatus(result) {
    if (!result) return '—';
    const reasonCode = result.reason_code || result.reason || '';
    return QQ_REASON_CODE_LABELS[reasonCode] || QQ_REASON_LABELS[reasonCode] || reasonCode || '—';
}

function qqFormatLifecycle(lifecycle) {
    return QQ_LIFECYCLE_LABELS[lifecycle] || lifecycle || '';
}

function qqFormatReasonCode(code) {
    return QQ_REASON_CODE_LABELS[code] || QQ_REASON_LABELS[code] || code || '';
}

function qqFormatMatchMode(mode) {
    return QQ_MATCH_MODE_LABELS[mode] || mode || '';
}

function qqFormatWarning(w) {
    return QQ_WARNING_LABELS[w] || w;
}

/**
 * Soft-disable action buttons so clicks still fire and we can show why export/copy is blocked.
 * The native disabled attribute must stay off, otherwise the browser swallows the click.
 */
function qqSetSoftDisabled(button, disabled) {
    if (!button) return;
    button.disabled = false;
    button.removeAttribute('disabled');
    button.classList.toggle('is-soft-disabled', disabled);
    button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
}

function qqExplainCopyBlocked() {
    if (!qqResults.length) {
        qqSetStatus('Bấm Match trước khi copy kết quả.', 'error');
        return;
    }
    if (qqStrategy === 'MANUAL') {
        qqSetStatus('Chọn sản phẩm ở cột checkbox trong bảng kết quả trước khi copy.', 'error');
        return;
    }
    qqSetStatus('Không có dòng eligible để copy (compliance hoặc chưa có giá).', 'error');
}

function qqExplainExportBlocked() {
    if (!qqResults.length) {
        qqSetStatus('Bấm Match trước khi xuất Excel.', 'error');
        return;
    }
    if (!qqHasActiveTemplate()) {
        qqSetStatus('Chưa có mẫu báo giá. Vui lòng liên hệ admin.', 'error');
        return;
    }
    qqSetStatus('Không có yêu cầu nào để xuất Excel.', 'error');
}

function qqGetRequestLifecycle(result, resultIndex) {
    if (!result) {
        return { lifecycle: QQ_LIFECYCLE_UNRESOLVED, reason_code: 'NO_MATCH' };
    }
    const requestId = qqRequestIdForResult(result, resultIndex);
    if (qqExportedRequestIds.has(requestId)) {
        return {
            lifecycle: QQ_LIFECYCLE_EXPORTED,
            reason_code: 'EXPORTED_SUCCESSFULLY',
        };
    }

    const candidates = result.candidates || [];
    const isManual = qqStrategy === 'MANUAL';

    if (isManual) {
        const picks = qqUserPicks.get(requestId) || [];
        const eligiblePicks = picks.filter(qqIsSelectableCandidate);
        if (eligiblePicks.length > 0) {
            return {
                lifecycle: QQ_LIFECYCLE_SELECTED,
                reason_code: 'MANUALLY_SELECTED',
            };
        }
        if (candidates.length > 0) {
            const hasEligible = candidates.some(qqIsSelectableCandidate);
            if (!hasEligible) {
                const allBlocked = candidates.every((c) =>
                    c.ineligible_reason === 'COMPLIANCE_BLOCKED' || QQ_BLOCKED_COMPLIANCE.has(c.Compliance || c.compliance)
                );
                if (allBlocked) {
                    return {
                        lifecycle: QQ_LIFECYCLE_BLOCKED,
                        reason_code: 'COMPLIANCE_BLOCKED',
                    };
                }
                const allNoPrice = candidates.every((c) => (c.Unit_Price_Value || 0) <= 0);
                if (allNoPrice) {
                    return {
                        lifecycle: QQ_LIFECYCLE_REVIEW,
                        reason_code: 'NO_VALID_PRICE',
                    };
                }
                return {
                    lifecycle: QQ_LIFECYCLE_BLOCKED,
                    reason_code: 'COMPLIANCE_BLOCKED',
                };
            }
            if (candidates.some((c) => c.auto_excluded)) {
                return {
                    lifecycle: QQ_LIFECYCLE_REVIEW,
                    reason_code: 'DUPLICATE_CODE_BRAND_SIZE',
                };
            }
            return {
                lifecycle: QQ_LIFECYCLE_REVIEW,
                reason_code: 'MANUAL_SELECTION_REQUIRED',
            };
        }
        return {
            lifecycle: result.lifecycle || QQ_LIFECYCLE_UNRESOLVED,
            reason_code: result.reason_code || result.reason || 'NO_MATCH',
        };
    }

    // Auto strategy
    const selected = qqEffectiveSelectedCandidates(result, resultIndex);
    const eligibleSelected = selected.filter(qqIsSelectableCandidate);
    if (eligibleSelected.length > 0) {
        return {
            lifecycle: QQ_LIFECYCLE_SELECTED,
            reason_code: 'AUTO_SELECTED',
        };
    }

    if (candidates.length > 0) {
        const hasEligible = candidates.some(qqIsSelectableCandidate);
        if (!hasEligible) {
            const allBlocked = candidates.every((c) =>
                c.ineligible_reason === 'COMPLIANCE_BLOCKED' || QQ_BLOCKED_COMPLIANCE.has(c.Compliance || c.compliance)
            );
            if (allBlocked) {
                return {
                    lifecycle: QQ_LIFECYCLE_BLOCKED,
                    reason_code: 'COMPLIANCE_BLOCKED',
                };
            }
            return {
                lifecycle: QQ_LIFECYCLE_REVIEW,
                reason_code: 'NO_VALID_PRICE',
            };
        }
        if (candidates.some((c) => c.auto_excluded)) {
            return {
                lifecycle: QQ_LIFECYCLE_REVIEW,
                reason_code: 'DUPLICATE_CODE_BRAND_SIZE',
            };
        }
        return {
            lifecycle: QQ_LIFECYCLE_REVIEW,
            reason_code: 'MANUAL_SELECTION_REQUIRED',
        };
    }

    return {
        lifecycle: result.lifecycle || QQ_LIFECYCLE_UNRESOLVED,
        reason_code: result.reason_code || result.reason || 'NO_MATCH',
    };
}

function qqSummarizeResults(results) {
    const counts = {
        selected: 0,
        review: 0,
        unresolved: 0,
        blocked: 0,
        exported: 0,
        total_requests: results.length,
        total_selected_lines: 0,
        matched: 0,
        manual_review: 0,
    };

    results.forEach((result, index) => {
        const info = qqGetRequestLifecycle(result, index);
        if (info.lifecycle === QQ_LIFECYCLE_EXPORTED) {
            counts.exported++;
        } else if (info.lifecycle === QQ_LIFECYCLE_SELECTED) {
            counts.selected++;
        } else if (info.lifecycle === QQ_LIFECYCLE_REVIEW) {
            counts.review++;
        } else if (info.lifecycle === QQ_LIFECYCLE_BLOCKED) {
            counts.blocked++;
        } else {
            counts.unresolved++;
        }

        const selectedCands = qqEffectiveSelectedCandidates(result, index).filter(qqIsSelectableCandidate);
        counts.total_selected_lines += selectedCands.length;
    });

    counts.matched = counts.selected + counts.exported;
    counts.manual_review = counts.review;
    return counts;
}

function qqBuildCopyPayload(results) {
    const lines = [];
    results.forEach((result, index) => {
        const cands = qqEffectiveSelectedCandidates(result, index);
        cands.forEach((c) => {
            if (!qqIsSelectableCandidate(c)) return;
            const cells = QQ_COPY_COLUMNS.map((key) => {
                if (key === 'Compliance') return qqExcelSafeCell(c.Compliance || c.compliance || '');
                if (key === 'Compliance_Note') return qqExcelSafeCell(c.Compliance_Note || c.compliance_note || '');
                if (key === 'Note') return qqExcelSafeCell(c.Note || c.note || '');
                return qqExcelSafeCell(c[key] || '');
            });
            lines.push(cells.join('\t'));
        });
    });
    return lines.join('\n');
}

function qqHasCopyableRows(results) {
    return results.some((result, index) =>
        qqEffectiveSelectedCandidates(result, index).some(qqIsSelectableCandidate)
    );
}

/**
 * Build the ordered selections array for the export endpoint (legacy contract).
 * Returns [{ product_id }] preserving order and duplicates.
 */
function qqBuildExportSelections(results) {
    const selections = [];
    results.forEach((result, index) => {
        const cands = qqEffectiveSelectedCandidates(result, index);
        cands.forEach((c) => {
            if (!qqIsSelectableCandidate(c)) return;
            selections.push({ product_id: c.product_id });
        });
    });
    return selections;
}

/**
 * Build export_items v2 keyed by request_id. Each item carries identity and an
 * ordered lines array; multi-selection order follows candidate display order,
 * not click order. Requests with no selection are kept (not dropped) so the
 * export preserves 100% of request_order; they carry a `placeholder`
 * classification/reason instead of `lines` so the backend can render a
 * placeholder line at the correct position (UNRESOLVED/BLOCKED/REVIEW).
 */
function qqBuildExportItems(results) {
    const items = [];
    results.forEach((result, index) => {
        const requestId = qqRequestIdForResult(result, index);
        const cands = qqEffectiveSelectedCandidates(result, index).filter(qqIsSelectableCandidate);
        const lines = cands.map((c, lineIndex) => ({
            product_id: c.product_id,
            selection_order: lineIndex + 1,
        }));
        const item = {
            request_id: requestId,
            request_order: result.request_order || (index + 1),
            source_row: result.source_row == null ? null : result.source_row,
            requested_name: result.requested_name || '',
            requested_code: result.requested_code || result.code || '',
            requested_cas: result.requested_cas || result.cas || '',
            lines,
        };
        if (!lines.length) {
            item.placeholder = qqBuildExportPlaceholder(result, index);
        }
        items.push(item);
    });
    return items;
}

/**
 * Classification/reason for a request with no selection, sent to the backend
 * so it can write the correct placeholder note text (column N) at export
 * time without re-running Match server-side.
 */
function qqBuildExportPlaceholder(result, index) {
    const info = qqGetRequestLifecycle(result, index);
    let classification = QQ_LIFECYCLE_UNRESOLVED;
    if (info.lifecycle === QQ_LIFECYCLE_BLOCKED) classification = QQ_LIFECYCLE_BLOCKED;
    else if (info.lifecycle === QQ_LIFECYCLE_REVIEW) classification = QQ_LIFECYCLE_REVIEW;
    return {
        classification,
        reason_code: info.reason_code || null,
    };
}

/* ═══════════════ status UI ═══════════════ */

function qqSetStatus(message, kind) {
    const el = document.getElementById('qqStatus');
    if (!el) return;
    el.classList.remove('is-loading', 'is-error', 'is-success');
    if (!message) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false;
    if (kind === 'loading') {
        el.classList.add('is-loading');
        el.replaceChildren();
        const spinner = document.createElement('span');
        spinner.className = 'status-spinner';
        el.appendChild(spinner);
        el.appendChild(document.createTextNode(` ${message}`));
    } else {
        el.textContent = message;
        if (kind === 'error') el.classList.add('is-error');
        if (kind === 'success') el.classList.add('is-success');
    }
}

function qqFormatAjaxError(xhr, fallback) {
    const status = xhr && xhr.status;
    if (status === 401) return 'Chưa đăng nhập.';
    if (status === 403) return 'Không có quyền hoặc chưa gán team.';
    if (status === 413) return 'Dữ liệu quá lớn hoặc file quá 10MB.';
    if (status >= 500) return 'Server đang lỗi, vui lòng thử lại.';
    let body = '';
    try { const json = xhr.responseJSON; if (json && json.error) body = String(json.error); } catch (_e) { /* ignore */ }
    if (!body && xhr.responseText) body = String(xhr.responseText).trim().slice(0, 280);
    return body || fallback;
}

function qqSetTemplateStatus(message, kind) {
    document.querySelectorAll('.qq-template-status').forEach((el) => {
        el.classList.remove('is-loading', 'is-ok', 'is-error');
        if (kind === 'loading') el.classList.add('is-loading');
        if (kind === 'ok') el.classList.add('is-ok');
        if (kind === 'error') el.classList.add('is-error');
        el.textContent = message;
    });
}

function qqRenderTemplateStatus() {
    if (qqTemplateState === 'loading') {
        qqSetTemplateStatus('Đang kiểm tra mẫu báo giá…', 'loading');
        return;
    }
    if (qqTemplateState === 'ready' && qqActiveTemplate) {
        qqSetTemplateStatus(`Mẫu báo giá: ${qqActiveTemplate.filename}`, 'ok');
        return;
    }
    if (qqTemplateState === 'missing') {
        qqSetTemplateStatus('Chưa có mẫu báo giá. Vui lòng liên hệ admin.', 'error');
        return;
    }
    qqSetTemplateStatus('Không tải được thông tin mẫu báo giá.', 'error');
}

function qqHasActiveTemplate() {
    return qqTemplateState === 'ready' && Boolean(qqActiveTemplate && qqActiveTemplate.id);
}

async function qqLoadActiveTemplateMetadata() {
    qqTemplateState = 'loading';
    qqActiveTemplate = null;
    qqRenderTemplateStatus();
    qqUpdateExportButton();
    try {
        const response = await fetch(QQ_TEMPLATE_ENDPOINT, { credentials: 'same-origin' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 409) {
                qqTemplateState = 'missing';
            } else {
                qqTemplateState = 'error';
            }
            qqActiveTemplate = null;
            qqRenderTemplateStatus();
            qqUpdateExportButton();
            return;
        }
        qqActiveTemplate = data && data.template ? data.template : null;
        qqTemplateState = qqActiveTemplate ? 'ready' : 'missing';
        qqRenderTemplateStatus();
        qqUpdateExportButton();
    } catch (_err) {
        qqActiveTemplate = null;
        qqTemplateState = 'error';
        qqRenderTemplateStatus();
        qqUpdateExportButton();
    }
}

/* ═══════════════ button state ═══════════════ */

function qqUpdateMatchButton() {
    const rows = qqReadRequestRows();
    const btn = document.getElementById('qqMatchBtn');
    if (!btn) return;
    const policyOk = qqPolicyValidation().valid;
    /* Soft-disabled (never natively `.disabled`) so a click while a row's
     * OWN override is invalid still registers and can explain which row —
     * same convention as Copy/Export. */
    const canMatch = qqHasMatchableRows(rows)
        && policyOk
        && !qqAnyRowPolicyBlocksMatch()
        && !qqRequestParseInProgress
        && !qqMatchInProgress;
    qqSetSoftDisabled(btn, !canMatch);
}

function qqUpdateCopyButton() {
    const btn = document.getElementById('qqCopyBtn');
    const btnBottom = document.getElementById('qqCopyBtnBottom');
    const canCopy = qqHasCopyableRows(qqResults);
    const sel = qqCountSelected();
    qqSetSoftDisabled(btn, !canCopy);
    qqSetSoftDisabled(btnBottom, !canCopy);
    /* update selected count label */
    document.querySelectorAll('.qq-selected-count').forEach((el) => {
        el.textContent = canCopy ? `${sel} dòng được chọn` : '';
    });
    qqUpdateExportButton();
}

function qqUpdateExportButton() {
    /* Export never requires a selection: it exports selected/unresolved/
     * blocked/review requests alike, so only Match-complete + template gate it. */
    const canExport = qqResults.length > 0 && qqHasActiveTemplate() && !qqExportInProgress;
    const btn = document.getElementById('qqExportBtn');
    const btnBottom = document.getElementById('qqExportBtnBottom');
    qqSetSoftDisabled(btn, !canExport);
    qqSetSoftDisabled(btnBottom, !canExport);
}

function qqCountSelected() {
    return qqResults.reduce((acc, result, index) =>
        acc + qqEffectiveSelectedCandidates(result, index).filter(qqIsSelectableCandidate).length, 0);
}

/**
 * Any change to inputs/filters/strategy invalidates existing results:
 * clear results, hide the preview section, disable Copy, require re-Match.
 */
function qqInvalidateResults() {
    if (!qqResults.length) return;
    qqResults = [];
    qqUserPicks = new Map();
    qqLegacyResultIds = new Map();
    qqExportedRequestIds = new Set();
    qqActiveLifecycleFilter = null;
    const section = document.getElementById('qqPreviewSection');
    if (section) section.hidden = true;
    document.getElementById('qqResultGroups')?.replaceChildren();
    document.getElementById('qqSummary')?.replaceChildren();
    const manualHint = document.getElementById('qqManualHint');
    if (manualHint) manualHint.hidden = true;
    qqUpdateCopyButton();
    qqSetStatus('Điều kiện đã thay đổi — bấm Match lại để cập nhật kết quả.', 'error');
}

/**
 * Kept as the single entry point every grid/filter/brand-policy change calls:
 * re-renders the policy validation hints, refreshes Match, and invalidates
 * any stale results. (Name kept from Phase 3C2 — it now covers the whole
 * brand-policy widget, not just a single brand chip warning.)
 */
function qqUpdateBrandWarning() {
    qqRenderPolicyHints();
    qqUpdateMatchButton();
    qqInvalidateResults();
}

/* ═══════════════ grid ═══════════════ */

function qqUpdateGridRowStatus() {
    const rows = document.querySelectorAll('#qqRequestBody tr');
    rows.forEach((tr) => {
        const codeInput = tr.querySelector('.qq-input-code');
        const casInput = tr.querySelector('.qq-input-cas');
        const nameInput = tr.querySelector('.qq-input-name');
        const hasCode = Boolean(qqText(codeInput?.value));
        const hasCas = Boolean(qqText(casInput?.value));
        const hasName = Boolean(qqText(nameInput?.value));
        const idxTd = tr.querySelector('.qq-col-idx');

        if (!hasCode && !hasCas && hasName) {
            tr.classList.add('is-missing-identifier');
            tr.classList.remove('is-pending-match');
            if (idxTd && !idxTd.querySelector('.qq-grid-warn-badge')) {
                const warn = document.createElement('span');
                warn.className = 'qq-grid-warn-badge';
                warn.textContent = '!';
                warn.title = 'Thiếu Code/CAS (Chưa resolve)';
                idxTd.appendChild(warn);
            }
        } else {
            tr.classList.remove('is-missing-identifier');
            idxTd?.querySelector('.qq-grid-warn-badge')?.remove();
            if (hasCode || hasCas) {
                tr.classList.add('is-pending-match');
            } else {
                tr.classList.remove('is-pending-match');
            }
        }
    });
}

async function qqRunPreflight(rows) {
    if (!rows || !rows.length) return;
    const submittable = qqFilterSubmittableRows(rows);
    if (!submittable.length) return;

    submittable.forEach((row) => {
        const hasCode = Boolean(qqText(row.code));
        const hasCas = Boolean(qqText(row.cas));
        if (!hasCode && !hasCas) {
            qqPreflightResults.set(row.request_id, {
                request_id: row.request_id,
                preflight_status: 'MISSING_IDENTIFIER',
                lifecycle: QQ_LIFECYCLE_UNRESOLVED,
                reason_code: 'MISSING_IDENTIFIER',
                match_count: 0,
            });
        } else {
            qqPreflightResults.set(row.request_id, {
                request_id: row.request_id,
                preflight_status: 'NEEDS_MATCH',
                lifecycle: QQ_LIFECYCLE_REVIEW,
                reason_code: 'PENDING_MATCH',
                match_count: 0,
            });
        }
    });
    qqUpdateGridRowStatus();

    const lookupRows = submittable.filter((r) => Boolean(qqText(r.code)) || Boolean(qqText(r.cas)));
    if (!lookupRows.length) return;

    try {
        const response = await fetch(QQ_PREFLIGHT_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                rows: lookupRows.map((r, idx) => ({
                    request_id: r.request_id,
                    request_order: idx + 1,
                    source_row: r.source_row == null ? null : Number(r.source_row),
                    requested_name: r.requested_name || '',
                    code: r.code || '',
                    cas: r.cas || '',
                })),
            }),
            credentials: 'same-origin',
        });
        if (!response.ok) return;
        const data = await response.json();
        if (data && Array.isArray(data.results)) {
            data.results.forEach((item) => {
                if (item && item.request_id) {
                    qqPreflightResults.set(item.request_id, item);
                }
            });
            qqUpdateGridRowStatus();
        }
    } catch (_) {
        // Preflight is non-blocking
    }
}

function qqRenumberGrid() {
    document.querySelectorAll('#qqRequestBody tr').forEach((tr, index) => {
        const cell = tr.querySelector('.qq-col-idx');
        if (cell) cell.textContent = String(index + 1);
    });
    qqUpdateGridRowStatus();
}

function qqUpdateScopeForRow(tr) {
    const codeInput = tr.querySelector('.qq-input-code');
    const scopeSel = tr.querySelector('.qq-input-scope');
    if (!scopeSel) return;
    const hasCode = Boolean(qqText(codeInput?.value));
    scopeSel.disabled = !hasCode;
    if (!hasCode) { scopeSel.value = QQ_SCOPE_DEFAULT; scopeSel.classList.add('is-neutral'); }
    else scopeSel.classList.remove('is-neutral');
}

/* ═══════════════ per-row brand policy column (Phase 3B2) ═══════════════ */

/** Mirrors the backend's `row_equivalent` resolution (row scope override,
 * else the global "Tìm sản phẩm tương đương" default). */
function qqRowResolvedEquivalent(tr) {
    const scope = tr.querySelector('.qq-input-scope')?.value || QQ_SCOPE_DEFAULT;
    if (scope === QQ_SCOPE_EXACT) return false;
    if (scope === QQ_SCOPE_EQUIV) return true;
    return Boolean(document.getElementById('qqEquivDefault')?.checked);
}

/**
 * True for "Exact Code, no equivalent search" rows — the one case where the
 * backend bypasses brand policy entirely (`is_exact_code` in search.py), so
 * the row's policy control must show as locked/"Đúng Code" and never gate
 * Match, regardless of whatever override happens to be stored for it.
 */
function qqRowIsExactCodeLocked(tr) {
    const hasCode = Boolean(qqText(tr.querySelector('.qq-input-code')?.value));
    return hasCode && !qqRowResolvedEquivalent(tr);
}

/** True if this row's OWN override is invalid and would block Match (exact-
 * code-locked rows are always exempt — brand policy never applies to them). */
function qqRowBlocksMatch(tr) {
    if (qqRowIsExactCodeLocked(tr)) return false;
    const requestId = tr.dataset.requestId;
    const policy = qqRowBrandPolicies.get(requestId);
    if (!policy || policy.mode === QQ_ROW_POLICY_INHERIT) return false;
    return !qqRowPolicyValidation(policy).valid;
}

function qqAnyRowPolicyBlocksMatch() {
    return Array.from(document.querySelectorAll('#qqRequestBody tr')).some(qqRowBlocksMatch);
}

/** Re-render one row's "Chính sách hãng" cell: locked/"Đúng Code" control
 * for exact-code rows, else the summary text + edit button + warning badge. */
function qqUpdateRowPolicyCell(tr) {
    const cell = tr.querySelector('.qq-col-policy');
    if (!cell) return;
    const locked = qqRowIsExactCodeLocked(tr);
    const lockedWrap = cell.querySelector('.qq-row-policy-locked');
    const normalWrap = cell.querySelector('.qq-row-policy-normal');
    if (lockedWrap) lockedWrap.hidden = !locked;
    if (normalWrap) normalWrap.hidden = locked;
    if (locked) return;

    const requestId = tr.dataset.requestId;
    const policy = qqRowBrandPolicies.get(requestId) || qqDefaultRowPolicy();
    const summaryEl = cell.querySelector('.qq-row-policy-summary');
    if (summaryEl) summaryEl.textContent = qqRowPolicySummary(policy);

    const warnBtn = cell.querySelector('.qq-row-policy-warn-badge');
    if (warnBtn) {
        const isOverride = policy.mode !== QQ_ROW_POLICY_INHERIT;
        warnBtn.hidden = !(isOverride && !qqRowPolicyValidation(policy).valid);
    }
}

function qqUpdateAllRowPolicyCells() {
    document.querySelectorAll('#qqRequestBody tr').forEach(qqUpdateRowPolicyCell);
}

/** Build the "Chính sách hãng" grid cell — two mutually-exclusive views
 * (locked "Đúng Code" vs. summary+edit+warning), toggled by
 * qqUpdateRowPolicyCell. No tier editor is ever inlined into the grid. */
function qqBuildRowPolicyCell(tr, requestId) {
    const td = document.createElement('td');
    td.className = 'qq-grid-cell qq-col-policy';

    const lockedWrap = document.createElement('span');
    lockedWrap.className = 'qq-row-policy-locked';
    lockedWrap.hidden = true;
    const lockedBtn = document.createElement('button');
    lockedBtn.type = 'button';
    lockedBtn.className = 'qq-row-policy-locked-btn';
    lockedBtn.disabled = true;
    lockedBtn.textContent = 'Đúng Code';
    lockedBtn.title = 'Đúng Code, không tìm tương đương — chính sách hãng không áp dụng';
    lockedWrap.appendChild(lockedBtn);
    td.appendChild(lockedWrap);

    const normalWrap = document.createElement('span');
    normalWrap.className = 'qq-row-policy-normal';

    const summarySpan = document.createElement('span');
    summarySpan.className = 'qq-row-policy-summary';
    normalWrap.appendChild(summarySpan);

    const warnBtn = document.createElement('button');
    warnBtn.type = 'button';
    warnBtn.className = 'qq-row-policy-warn-badge';
    warnBtn.hidden = true;
    warnBtn.title = 'Chính sách hãng riêng của dòng này chưa hợp lệ — bấm để sửa';
    const warnIcon = document.createElement('i');
    warnIcon.className = 'fas fa-exclamation-triangle';
    warnIcon.setAttribute('aria-hidden', 'true');
    warnBtn.appendChild(warnIcon);
    warnBtn.addEventListener('click', () => qqOpenRowPolicyDialog(tr));
    normalWrap.appendChild(warnBtn);

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'qq-row-policy-edit-btn';
    editBtn.title = 'Chỉnh chính sách hãng riêng cho dòng này';
    const editIcon = document.createElement('i');
    editIcon.className = 'fas fa-sliders-h';
    editIcon.setAttribute('aria-hidden', 'true');
    editBtn.appendChild(editIcon);
    editBtn.addEventListener('click', () => qqOpenRowPolicyDialog(tr));
    normalWrap.appendChild(editBtn);

    td.appendChild(normalWrap);
    return td;
}

function qqCreateRequestRow(data = {}) {
    const tr = document.createElement('tr');
    /* Stable identity: mint once, never reused. Preserve source_row from file import. */
    const requestId = (data.request_id && !qqRequestIdentity.has(data.request_id))
        ? data.request_id
        : qqNewRequestId();
    tr.dataset.requestId = requestId;
    if (data.source_row != null) tr.dataset.sourceRow = String(data.source_row);
    qqEnsureRequestId({ ...data, request_id: requestId });
    /* Every row — manual, pasted, or imported from a request file — starts
     * with an explicit INHERIT policy entry (Phase 3B2). */
    qqEnsureRowPolicy(requestId);

    const idx = document.createElement('td');
    idx.className = 'qq-col-idx';
    tr.appendChild(idx);

    const fields = [
        { cls: 'qq-input-name', key: 'requested_name', placeholder: 'Tên tham khảo' },
        { cls: 'qq-input-code', key: 'code', placeholder: 'Code' },
        { cls: 'qq-input-cas', key: 'cas', placeholder: 'CAS' },
    ];
    fields.forEach(({ cls, key, placeholder }) => {
        const td = document.createElement('td');
        td.className = 'qq-grid-cell';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = `qq-grid-input ${cls}`;
        input.value = data[key] || '';
        input.placeholder = placeholder;
        input.addEventListener('input', () => {
            qqUpdateScopeForRow(tr);
            qqUpdateBrandWarning();
            qqUpdateGridRowStatus();
            if (cls === 'qq-input-code') qqUpdateRowPolicyCell(tr);
        });
        td.appendChild(input);
        tr.appendChild(td);
    });

    const scopeTd = document.createElement('td');
    scopeTd.className = 'qq-grid-cell qq-col-scope';
    const scopeSel = document.createElement('select');
    scopeSel.className = 'qq-input-scope qq-scope-select';
    [
        [QQ_SCOPE_DEFAULT, 'Theo thiết lập chung'],
        [QQ_SCOPE_EXACT, 'Chỉ đúng Code'],
        [QQ_SCOPE_EQUIV, 'Tìm tương đương'],
    ].forEach(([value, label]) => {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label;
        scopeSel.appendChild(opt);
    });
    scopeSel.value = data.scope || QQ_SCOPE_DEFAULT;
    scopeSel.addEventListener('change', () => {
        qqUpdateBrandWarning();
        qqUpdateRowPolicyCell(tr);
    });
    scopeTd.appendChild(scopeSel);
    tr.appendChild(scopeTd);

    tr.appendChild(qqBuildRowPolicyCell(tr, requestId));

    qqUpdateScopeForRow(tr);
    qqUpdateRowPolicyCell(tr);
    return tr;
}

function qqSetRequestRows(rows) {
    const body = document.getElementById('qqRequestBody');
    if (!body) return;
    body.replaceChildren();
    /* Clearing the grid invalidates all prior identities; new rows mint fresh ids.
     * Row-level brand policy overrides reset the same way — a fresh grid (manual
     * clear or request-file import) always starts every row at INHERIT. */
    qqRequestIdentity = new Map();
    qqRowBrandPolicies = new Map();
    const slice = rows.slice(0, QQ_MAX_ROWS);
    if (!slice.length) body.appendChild(qqCreateRequestRow());
    else slice.forEach((row) => body.appendChild(qqCreateRequestRow(row)));
    qqRenumberGrid();
    qqUpdateBrandWarning();
    qqUpdateGridRowStatus();
    qqUpdateAllRowPolicyCells();
}

function qqAddRows(rows, replace) {
    if (replace) { qqSetRequestRows(rows.length ? rows : [qqBlankRow()]); return; }
    const body = document.getElementById('qqRequestBody');
    if (!body) return;
    const current = body.querySelectorAll('tr').length;
    rows.slice(0, Math.max(0, QQ_MAX_ROWS - current)).forEach((row) => body.appendChild(qqCreateRequestRow(row)));
    if (!body.querySelector('tr')) body.appendChild(qqCreateRequestRow());
    qqRenumberGrid();
    qqUpdateBrandWarning();
}

const QQ_GRID_INPUT_CLASSES = ['qq-input-name', 'qq-input-code', 'qq-input-cas'];

function qqApplyPasteMatrix(matrix, startRow, startCol) {
    if (!matrix.length) return;
    const body = document.getElementById('qqRequestBody');
    if (!body) return;
    /* Ensure enough rows exist, minting fresh ids only for new rows. */
    let rows = body.querySelectorAll('tr');
    while (rows.length < startRow + matrix.length) {
        body.appendChild(qqCreateRequestRow());
        rows = body.querySelectorAll('tr');
    }
    matrix.forEach((pasteRow, rowOffset) => {
        const tr = rows[startRow + rowOffset];
        if (!tr) return;
        pasteRow.forEach((value, colOffset) => {
            const fi = startCol + colOffset;
            if (fi >= QQ_GRID_INPUT_CLASSES.length) return;
            const input = tr.querySelector(`.${QQ_GRID_INPUT_CLASSES[fi]}`);
            if (input) input.value = value || '';
        });
        qqUpdateScopeForRow(tr);
    });
    qqRenumberGrid();
    qqUpdateBrandWarning();
    qqRunPreflight(qqReadRequestRows());
}

function qqClearAllGrid() {
    qqExportedRequestIds.clear();
    qqActiveLifecycleFilter = null;
    qqPreflightResults.clear();
    qqSetRequestRows(Array.from({ length: QQ_INITIAL_ROW_COUNT }, () => qqBlankRow()));
    document.querySelector('#qqRequestBody .qq-input-name')?.focus();
}

function qqFocusFirstNameCell() {
    document.querySelector('#qqRequestBody .qq-input-name')?.focus();
}

/* ═══════════════ request file wizard ═══════════════ */

function qqSetRequestFileStatus(message, kind) {
    const el = document.getElementById('qqRequestFileStatus');
    if (!el) return;
    el.classList.remove('is-loading', 'is-error', 'is-success');
    el.textContent = qqText(message);
    el.hidden = !message;
    if (kind === 'loading') el.classList.add('is-loading');
    if (kind === 'error') el.classList.add('is-error');
    if (kind === 'success') el.classList.add('is-success');
}

function qqSetRequestSource(source) {
    qqRequestSource = source === 'file' ? 'file' : 'manual';
    document.querySelectorAll('[data-request-source]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.requestSource === qqRequestSource);
    });
    const manualWrap = document.getElementById('qqManualEntryWrap');
    const fileWizard = document.getElementById('qqFileWizard');
    if (manualWrap) manualWrap.hidden = qqRequestSource !== 'manual';
    if (fileWizard) fileWizard.hidden = qqRequestSource !== 'file';
    qqUpdateMatchButton();
}

function qqResetRequestFileState(clearInput = true) {
    qqRequestAnalyzeSeq += 1;
    qqRequestFile = null;
    qqRequestAnalyze = null;
    qqRequestAnalyzeInProgress = false;
    qqRequestParseInProgress = false;
    if (clearInput) {
        const input = document.getElementById('qqRequestFileInput');
        if (input) input.value = '';
    }
    const info = document.getElementById('qqRequestFileInfo');
    if (info) info.textContent = 'Chưa chọn file.';
    const resetBtn = document.getElementById('qqRequestFileResetBtn');
    if (resetBtn) resetBtn.hidden = true;
    document.getElementById('qqMappingPanel')?.setAttribute('hidden', '');
    document.getElementById('qqRequestFileWarnings')?.setAttribute('hidden', '');
    document.getElementById('qqFilePreviewBody')?.replaceChildren();
    qqSetRequestFileStatus('', '');
    qqUpdateMatchButton();
}

function qqBuildAnalyzeFormData(file, sheet, headerRow) {
    const fd = new FormData();
    fd.append('file', file);
    if (sheet) fd.append('sheet', sheet);
    if (headerRow) fd.append('header_row', String(headerRow));
    return fd;
}

async function qqAnalyzeRequestFile(options = {}) {
    if (!qqRequestFile || qqRequestAnalyzeInProgress) return;
    const seq = ++qqRequestAnalyzeSeq;
    qqRequestAnalyzeInProgress = true;
    qqSetRequestFileStatus('Đang phân tích file…', 'loading');
    qqRenderRequestFileControlsDisabled(true);
    try {
        const response = await fetch(QQ_REQUEST_FILE_ANALYZE_ENDPOINT, {
            method: 'POST',
            body: qqBuildAnalyzeFormData(qqRequestFile, options.sheet, options.headerRow),
            credentials: 'same-origin',
        });
        const data = await response.json().catch(() => ({}));
        if (seq !== qqRequestAnalyzeSeq) return;
        if (!response.ok) {
            qqRequestAnalyze = null;
            qqRenderRequestFileAnalyze();
            qqSetRequestFileStatus(qqRequestFileErrorMessage(response.status, data.error), 'error');
            return;
        }
        qqRequestAnalyze = data;
        qqRenderRequestFileAnalyze();
        qqSetRequestFileStatus('Đã phân tích file.', 'success');
    } catch (_err) {
        if (seq === qqRequestAnalyzeSeq) {
            qqRequestAnalyze = null;
            qqRenderRequestFileAnalyze();
            qqSetRequestFileStatus('Không thể kết nối máy chủ để phân tích file.', 'error');
        }
    } finally {
        if (seq === qqRequestAnalyzeSeq) {
            qqRequestAnalyzeInProgress = false;
            qqRenderRequestFileControlsDisabled(false);
            if (qqRequestAnalyze) qqUpdateMappingState();
        }
    }
}

function qqRequestFileErrorMessage(status, bodyMessage) {
    if (status === 401) return 'Chưa đăng nhập.';
    if (status === 403) return 'Không có quyền hoặc chưa gán team.';
    if (status === 400) return bodyMessage || 'File hoặc mapping không hợp lệ.';
    if (status === 413) return bodyMessage || 'File quá 10MB hoặc vượt 2.000 dòng.';
    if (status >= 500) return 'Server đang lỗi khi xử lý file.';
    return bodyMessage || 'Không xử lý được file.';
}

function qqRenderRequestFileControlsDisabled(disabled) {
    [
        'qqRequestFileInput',
        'qqRequestSheetSelect',
        'qqRequestHeaderRowSelect',
        'qqMapNameSelect',
        'qqMapCodeSelect',
        'qqMapCasSelect',
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.disabled = Boolean(disabled || qqRequestParseInProgress);
    });
    const importBtn = document.getElementById('qqImportFileRowsBtn');
    if (importBtn && disabled) importBtn.disabled = true;
}

function qqHandleRequestFile(file) {
    qqResetRequestFileState(false);
    if (!file) return;
    qqRequestFile = file;
    const info = document.getElementById('qqRequestFileInfo');
    if (info) info.textContent = `${file.name} · ${qqFormatBytes(file.size)}`;
    const resetBtn = document.getElementById('qqRequestFileResetBtn');
    if (resetBtn) resetBtn.hidden = false;
    if (!/\.(xlsx|csv)$/i.test(file.name || '')) {
        qqSetRequestFileStatus('Chỉ hỗ trợ file .xlsx hoặc .csv.', 'error');
        return;
    }
    if (file.size > QQ_REQUEST_FILE_MAX_BYTES) {
        qqSetRequestFileStatus('File quá lớn, tối đa 10MB.', 'error');
        return;
    }
    qqAnalyzeRequestFile();
}

function qqRenderRequestFileAnalyze() {
    const panel = document.getElementById('qqMappingPanel');
    if (!panel) return;
    if (!qqRequestAnalyze) {
        panel.hidden = true;
        qqRenderRequestFileWarnings([]);
        return;
    }
    panel.hidden = false;
    qqPopulateSheetSelect();
    qqPopulateHeaderRowSelect();
    qqPopulateMappingSelects();
    qqRenderRequestFileWarnings(qqRequestAnalyze.warnings || []);
    qqUpdateMappingState();
}

function qqRenderRequestFileWarnings(warnings) {
    const wrap = document.getElementById('qqRequestFileWarnings');
    if (!wrap) return;
    wrap.replaceChildren();
    warnings.forEach((warning) => {
        const p = document.createElement('p');
        p.textContent = qqText(warning);
        wrap.appendChild(p);
    });
    wrap.hidden = !warnings.length;
}

function qqPopulateSheetSelect() {
    const sel = document.getElementById('qqRequestSheetSelect');
    if (!sel || !qqRequestAnalyze) return;
    const current = sel.value || qqRequestAnalyze.suggested_sheet || '';
    sel.replaceChildren();
    (qqRequestAnalyze.sheets || []).forEach((sheet) => {
        const opt = document.createElement('option');
        opt.value = sheet;
        opt.textContent = sheet;
        sel.appendChild(opt);
    });
    sel.value = (qqRequestAnalyze.sheets || []).includes(current) ? current : (qqRequestAnalyze.suggested_sheet || '');
}

function qqPopulateHeaderRowSelect() {
    const sel = document.getElementById('qqRequestHeaderRowSelect');
    if (!sel || !qqRequestAnalyze) return;
    const selected = String(qqRequestAnalyze.suggested_header_row || 1);
    const rows = new Set([selected]);
    (qqRequestAnalyze.header_candidates || []).forEach((candidate) => rows.add(String(candidate.row)));
    sel.replaceChildren();
    Array.from(rows).sort((a, b) => Number(a) - Number(b)).forEach((row) => {
        const opt = document.createElement('option');
        opt.value = row;
        opt.textContent = `Dòng ${row}`;
        sel.appendChild(opt);
    });
    sel.value = selected;
}

function qqMappingSelects() {
    return [
        ['requested_name', document.getElementById('qqMapNameSelect')],
        ['code', document.getElementById('qqMapCodeSelect')],
        ['cas', document.getElementById('qqMapCasSelect')],
    ];
}

function qqPopulateMappingSelects() {
    if (!qqRequestAnalyze) return;
    const columns = qqRequestAnalyze.columns || [];
    qqMappingSelects().forEach(([field, sel]) => {
        if (!sel) return;
        const suggestion = qqRequestAnalyze.suggested_mapping?.[field] || {};
        const suggestedColumn = suggestion.ambiguous ? '' : suggestion.column;
        sel.replaceChildren();
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = field === 'requested_name' ? 'Không dùng Name' : 'Không map';
        sel.appendChild(blank);
        columns.forEach((col) => {
            const opt = document.createElement('option');
            opt.value = String(col.index);
            opt.textContent = `${col.letter} - ${col.header || '(trống)'}`;
            sel.appendChild(opt);
        });
        sel.value = suggestedColumn === null || suggestedColumn === undefined ? '' : String(suggestedColumn);
    });
}

function qqCurrentRequestFileMapping() {
    const sheet = document.getElementById('qqRequestSheetSelect')?.value || '';
    const headerRow = Number(document.getElementById('qqRequestHeaderRowSelect')?.value || qqRequestAnalyze?.suggested_header_row || 1);
    const readCol = (id) => {
        const value = document.getElementById(id)?.value || '';
        return value === '' ? null : Number(value);
    };
    return {
        sheet,
        header_row: headerRow,
        requested_name: readCol('qqMapNameSelect'),
        code: readCol('qqMapCodeSelect'),
        cas: readCol('qqMapCasSelect'),
    };
}

function qqValidateRequestFileMapping(mapping) {
    const used = new Map();
    for (const field of ['requested_name', 'code', 'cas']) {
        const col = mapping[field];
        if (col === null || col === undefined) continue;
        if (used.has(col)) return `Cột ${col + 1} đang được map vào nhiều trường.`;
        used.set(col, field);
    }
    if (mapping.code === null && mapping.cas === null) return 'Cần chọn ít nhất Code hoặc CAS.';
    return '';
}

function qqUpdateMappingState() {
    const mapping = qqCurrentRequestFileMapping();
    const error = qqValidateRequestFileMapping(mapping);
    const selectedByField = new Map();
    qqMappingSelects().forEach(([field, sel]) => {
        if (sel?.value !== '') selectedByField.set(sel.value, field);
    });
    qqMappingSelects().forEach(([field, sel]) => {
        if (!sel) return;
        Array.from(sel.options).forEach((opt) => {
            if (opt.value === '') { opt.disabled = false; return; }
            opt.disabled = selectedByField.has(opt.value) && selectedByField.get(opt.value) !== field;
        });
    });
    const errorEl = document.getElementById('qqMappingError');
    if (errorEl) {
        errorEl.textContent = error;
        errorEl.hidden = !error;
    }
    const btn = document.getElementById('qqImportFileRowsBtn');
    if (btn) btn.disabled = Boolean(error || qqRequestParseInProgress || qqRequestAnalyzeInProgress);
    qqRenderRequestFilePreview(mapping);
}

function qqRenderRequestFilePreview(mapping) {
    const body = document.getElementById('qqFilePreviewBody');
    if (!body || !qqRequestAnalyze) return;
    body.replaceChildren();
    (qqRequestAnalyze.preview || []).forEach((row, index) => {
        const sourceRow = index + 1;
        if (sourceRow <= mapping.header_row) return;
        const tr = document.createElement('tr');
        [sourceRow, row[mapping.requested_name] || '', row[mapping.code] || '', row[mapping.cas] || ''].forEach((value) => {
            const td = document.createElement('td');
            td.textContent = qqText(value);
            tr.appendChild(td);
        });
        body.appendChild(tr);
    });
}

function qqGridHasUserData() {
    return qqFilterSubmittableRows(qqReadRequestRows()).length > 0;
}

function qqConfirmReplaceGridIfNeeded() {
    if (!qqGridHasUserData()) return Promise.resolve(true);
    const dialog = document.getElementById('qqReplaceDialog');
    if (!dialog || typeof dialog.showModal !== 'function') return Promise.resolve(false);
    return new Promise((resolve) => {
        const onClose = () => {
            dialog.removeEventListener('close', onClose);
            resolve(dialog.returnValue === 'replace');
        };
        dialog.addEventListener('close', onClose);
        dialog.showModal();
    });
}

async function qqImportRequestFileRows() {
    if (qqRequestParseInProgress || !qqRequestFile || !qqRequestAnalyze) return;
    const mapping = qqCurrentRequestFileMapping();
    const error = qqValidateRequestFileMapping(mapping);
    if (error) {
        qqSetRequestFileStatus(error, 'error');
        qqUpdateMappingState();
        return;
    }
    const confirmed = await qqConfirmReplaceGridIfNeeded();
    if (!confirmed) return;

    qqRequestParseInProgress = true;
    qqRenderRequestFileControlsDisabled(true);
    qqUpdateMatchButton();
    qqSetRequestFileStatus('Đang nhập danh sách vào Quick Quote…', 'loading');
    const fd = new FormData();
    fd.append('file', qqRequestFile);
    fd.append('mapping', JSON.stringify(mapping));
    try {
        const response = await fetch(QQ_REQUEST_FILE_PARSE_ENDPOINT, {
            method: 'POST',
            body: fd,
            credentials: 'same-origin',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            qqSetRequestFileStatus(qqRequestFileErrorMessage(response.status, data.error), 'error');
            return;
        }
        const rows = (data.rows || []).map((row) => ({
            request_id: qqNewRequestId(),
            requested_name: row.requested_name || '',
            code: row.code || '',
            cas: row.cas || '',
            scope: QQ_SCOPE_DEFAULT,
            source_row: row.source_row == null ? null : Number(row.source_row),
        }));
        while (rows.length < QQ_INITIAL_ROW_COUNT) rows.push(qqBlankRow());
        qqSetRequestRows(rows);
        qqInvalidateResults();
        qqRunPreflight(rows);
        qqSetRequestSource('manual');
        qqFocusFirstNameCell();
        qqSetStatus(`Đã nhập ${data.rows ? data.rows.length : 0} dòng từ ${data.filename || qqRequestFile.name}.`, 'success');
    } catch (_err) {
        qqSetRequestFileStatus('Không thể kết nối máy chủ để nhập file.', 'error');
    } finally {
        qqRequestParseInProgress = false;
        qqRenderRequestFileControlsDisabled(false);
        qqUpdateMatchButton();
    }
}

function qqInitRequestFileWizard() {
    document.querySelectorAll('[data-request-source]').forEach((btn) => {
        btn.addEventListener('click', () => qqSetRequestSource(btn.dataset.requestSource || 'manual'));
    });
    const input = document.getElementById('qqRequestFileInput');
    input?.addEventListener('change', () => qqHandleRequestFile(input.files?.[0] || null));
    document.getElementById('qqRequestFileResetBtn')?.addEventListener('click', () => qqResetRequestFileState(true));
    const drop = document.getElementById('qqRequestFileDrop');
    ['dragenter', 'dragover'].forEach((eventName) => {
        drop?.addEventListener(eventName, (e) => {
            e.preventDefault();
            drop.classList.add('is-dragover');
        });
    });
    ['dragleave', 'drop'].forEach((eventName) => {
        drop?.addEventListener(eventName, (e) => {
            e.preventDefault();
            drop.classList.remove('is-dragover');
        });
    });
    drop?.addEventListener('drop', (e) => {
        const file = e.dataTransfer?.files?.[0] || null;
        qqHandleRequestFile(file);
    });
    document.getElementById('qqRequestSheetSelect')?.addEventListener('change', (e) => {
        qqAnalyzeRequestFile({ sheet: e.target.value });
    });
    document.getElementById('qqRequestHeaderRowSelect')?.addEventListener('change', (e) => {
        qqAnalyzeRequestFile({
            sheet: document.getElementById('qqRequestSheetSelect')?.value || '',
            headerRow: e.target.value,
        });
    });
    qqMappingSelects().forEach(([, sel]) => sel?.addEventListener('change', qqUpdateMappingState));
    document.getElementById('qqImportFileRowsBtn')?.addEventListener('click', qqImportRequestFileRows);
    qqSetRequestSource('manual');
}

/* ═══════════════ brand policy widget (mode + allowlist + tiers) ═══════════════ */

function qqNewTierId() {
    qqTierIdCounter += 1;
    return `tier-${qqTierIdCounter}`;
}

function qqTierLabel(index) {
    return `Ưu tiên ${index + 1}`;
}

/** Build one tier row's DOM (label, combobox skeleton, move/remove controls). */
function qqBuildTierRow(tier, index, total, ctx) {
    const row = document.createElement('div');
    row.className = 'qq-tier-row';
    row.dataset.tierId = tier.id;

    const label = document.createElement('span');
    label.className = 'qq-tier-label';
    label.textContent = qqTierLabel(index);
    row.appendChild(label);

    const body = document.createElement('div');
    body.className = 'qq-tier-body';

    const chips = document.createElement('div');
    chips.className = 'qq-brand-chips';
    chips.setAttribute('aria-live', 'polite');
    body.appendChild(chips);

    const combo = document.createElement('div');
    combo.className = 'qq-brand-combo';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'qq-brand-search-input';
    input.placeholder = 'Gõ để lọc hoặc dán: LGC, TRC, Accu… — Enter để chọn';
    input.autocomplete = 'off';
    input.setAttribute('aria-label', `Tìm hoặc dán brand cho ${qqTierLabel(index)}`);
    combo.appendChild(input);

    const dropdown = document.createElement('div');
    dropdown.className = 'qq-brand-dropdown';
    dropdown.hidden = true;
    const list = document.createElement('ul');
    list.className = 'qq-brand-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Gợi ý brand');
    dropdown.appendChild(list);
    const empty = document.createElement('p');
    empty.className = 'qq-brand-empty';
    empty.textContent = 'Không tìm thấy brand phù hợp.';
    empty.hidden = true;
    dropdown.appendChild(empty);
    combo.appendChild(dropdown);
    body.appendChild(combo);

    const feedback = document.createElement('p');
    feedback.className = 'qq-brand-paste-feedback';
    feedback.hidden = true;
    body.appendChild(feedback);

    row.appendChild(body);

    const controls = document.createElement('div');
    controls.className = 'qq-tier-controls';

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'qq-tier-ctrl-btn qq-tier-move-up-btn';
    upBtn.title = 'Đưa ưu tiên này lên';
    upBtn.setAttribute('aria-label', `Đưa ${qqTierLabel(index)} lên`);
    upBtn.disabled = index === 0;
    const upIcon = document.createElement('i');
    upIcon.className = 'fas fa-arrow-up';
    upIcon.setAttribute('aria-hidden', 'true');
    upBtn.appendChild(upIcon);
    upBtn.addEventListener('click', () => qqMoveTierIn(ctx, tier.id, -1));
    controls.appendChild(upBtn);

    const downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'qq-tier-ctrl-btn qq-tier-move-down-btn';
    downBtn.title = 'Đưa ưu tiên này xuống';
    downBtn.setAttribute('aria-label', `Đưa ${qqTierLabel(index)} xuống`);
    downBtn.disabled = index === total - 1;
    const downIcon = document.createElement('i');
    downIcon.className = 'fas fa-arrow-down';
    downIcon.setAttribute('aria-hidden', 'true');
    downBtn.appendChild(downIcon);
    downBtn.addEventListener('click', () => qqMoveTierIn(ctx, tier.id, 1));
    controls.appendChild(downBtn);

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'qq-tier-ctrl-btn qq-tier-remove-btn';
    removeBtn.title = 'Xóa mức ưu tiên này';
    removeBtn.setAttribute('aria-label', `Xóa ${qqTierLabel(index)}`);
    const removeIcon = document.createElement('i');
    removeIcon.className = 'fas fa-trash';
    removeIcon.setAttribute('aria-hidden', 'true');
    removeBtn.appendChild(removeIcon);
    removeBtn.addEventListener('click', () => qqRemoveTierFrom(ctx, tier.id));
    controls.appendChild(removeBtn);

    row.appendChild(controls);

    return { row, pickerRoot: body };
}

/**
 * Generic tier-list engine shared by the global conditions widget AND the
 * per-row policy dialog (Phase 3B2) — this IS "reusing the tier builder":
 * same DOM builder + same qqCreateBrandPicker, driven by whichever `tiers`
 * array + container the caller's ctx points at.
 *   ctx = { tiers: Array<{id, brands:Set}>, containerEl, keyPrefix, onChange, trackKey? }
 * `tiers` must be mutated in place (push/splice) — callers must never do
 * `ctx.tiers = ...` reassignment, since row dialogs pass a reference to a
 * specific row policy's own `tiers` array and reassigning the local
 * variable would silently detach it from that policy object.
 */
function qqRenderTierListInCtx(ctx) {
    const container = ctx.containerEl;
    if (!container) return;
    /* Tear down this ctx's existing pickers first so listeners never leak across re-renders. */
    ctx.tiers.forEach((t) => qqDestroyBrandPicker(`${ctx.keyPrefix}:${t.id}`));
    container.replaceChildren();
    ctx.tiers.forEach((tier, index) => {
        const { row, pickerRoot } = qqBuildTierRow(tier, index, ctx.tiers.length, ctx);
        container.appendChild(row);
        const pickerKey = `${ctx.keyPrefix}:${tier.id}`;
        qqCreateBrandPicker({
            key: pickerKey,
            root: pickerRoot,
            brands: tier.brands,
            onChange: ctx.onChange,
        });
        if (typeof ctx.trackKey === 'function') ctx.trackKey(pickerKey);
    });
}

function qqAddTierTo(ctx) {
    ctx.tiers.push({ id: qqNewTierId(), brands: new Set() });
    qqRenderTierListInCtx(ctx);
    ctx.onChange();
}

function qqRemoveTierFrom(ctx, id) {
    const idx = ctx.tiers.findIndex((t) => t.id === id);
    if (idx < 0) return;
    ctx.tiers.splice(idx, 1);
    qqRenderTierListInCtx(ctx);
    ctx.onChange();
}

function qqMoveTierIn(ctx, id, delta) {
    const idx = ctx.tiers.findIndex((t) => t.id === id);
    if (idx < 0) return;
    const target = idx + delta;
    if (target < 0 || target >= ctx.tiers.length) return;
    const [tier] = ctx.tiers.splice(idx, 1);
    ctx.tiers.splice(target, 0, tier);
    qqRenderTierListInCtx(ctx);
    ctx.onChange();
}

/* Global conditions widget's tier ctx — `tiers` is a direct reference to the
 * module-level qqPriorityTiers array, mutated in place by the generic
 * engine above so this reference never goes stale. */
const qqGlobalTierCtx = {
    tiers: qqPriorityTiers,
    containerEl: null,
    keyPrefix: 'global',
    onChange: () => qqUpdateBrandWarning(),
};

function qqRenderTierList() {
    qqGlobalTierCtx.containerEl = document.getElementById('qqTierList');
    qqRenderTierListInCtx(qqGlobalTierCtx);
    qqRenderPolicyHints();
}

function qqAddTier() {
    qqGlobalTierCtx.containerEl = document.getElementById('qqTierList');
    qqAddTierTo(qqGlobalTierCtx);
}

function qqRemoveTier(id) {
    qqGlobalTierCtx.containerEl = document.getElementById('qqTierList');
    qqRemoveTierFrom(qqGlobalTierCtx, id);
}

function qqMoveTier(id, delta) {
    qqGlobalTierCtx.containerEl = document.getElementById('qqTierList');
    qqMoveTierIn(qqGlobalTierCtx, id, delta);
}

const QQ_POLICY_HINT_TEXT = {
    [QQ_POLICY_ALL_AVAILABLE]: 'Tất cả hãng có sẵn — không cần chọn brand.',
    [QQ_POLICY_ALLOWLIST_ONLY]: 'Chỉ những hãng đã chọn ở trên mới được dùng để match.',
    [QQ_POLICY_PRIORITY_FALLBACK]: 'Ưu tiên hãng ở mức 1; tự động dùng mức kế tiếp nếu mức trên không có kết quả hợp lệ.',
};

const QQ_POLICY_INVALID_MESSAGES = {
    [QQ_POLICY_ALLOWLIST_ONLY]: 'Cần chọn ít nhất một brand cho "Chỉ các hãng được chọn".',
    [QQ_POLICY_PRIORITY_FALLBACK]: 'Cần ít nhất một mức ưu tiên có chọn brand.',
};

/** Re-render every visual consequence of the current policy mode/validity. */
function qqRenderPolicyHints() {
    const { mode, valid } = qqPolicyValidation();
    /* Scoped to #qqPolicyWidget so this never touches the row-policy
     * dialog's own (differently-scoped) tier rows/hints when both happen
     * to exist in the DOM at once. */
    const widget = document.getElementById('qqPolicyWidget');

    const allowlistHint = widget?.querySelector('.qq-allowlist-required-hint');
    if (allowlistHint) allowlistHint.hidden = !(mode === QQ_POLICY_ALLOWLIST_ONLY && !valid);

    const tierHint = widget?.querySelector('.qq-tier-required-hint');
    if (tierHint) tierHint.hidden = !(mode === QQ_POLICY_PRIORITY_FALLBACK && !valid);

    widget?.querySelectorAll('.qq-tier-row').forEach((row) => {
        const tier = qqPriorityTiers.find((t) => t.id === row.dataset.tierId);
        row.classList.toggle('is-empty-tier', Boolean(tier) && tier.brands.size === 0);
    });

    const policyHint = document.getElementById('qqPolicyHint');
    if (policyHint) policyHint.textContent = QQ_POLICY_HINT_TEXT[mode] || '';
}

/** Switch global brand-selection mode. Always invalidates stale results. */
function qqSetPolicyMode(mode) {
    if (
        mode !== QQ_POLICY_PRIORITY_FALLBACK
        && mode !== QQ_POLICY_ALLOWLIST_ONLY
        && mode !== QQ_POLICY_ALL_AVAILABLE
    ) return;
    if (qqBrandPolicyMode === mode) return;
    qqBrandPolicyMode = mode;

    document.querySelectorAll('[data-policy-mode]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.policyMode === mode);
    });
    const allowlistPanel = document.getElementById('qqAllowlistPanel');
    if (allowlistPanel) allowlistPanel.hidden = mode !== QQ_POLICY_ALLOWLIST_ONLY;
    const tierPanel = document.getElementById('qqTierPanel');
    if (tierPanel) tierPanel.hidden = mode !== QQ_POLICY_PRIORITY_FALLBACK;

    /* First time entering PRIORITY_FALLBACK: seed one empty tier so the panel isn't blank. */
    if (mode === QQ_POLICY_PRIORITY_FALLBACK && qqPriorityTiers.length === 0) {
        qqPriorityTiers.push({ id: qqNewTierId(), brands: new Set() });
        qqRenderTierList();
    }

    qqUpdateBrandWarning();
}

function qqInitPolicyWidget() {
    /* Collect canonical brand list from the shared template before any picker exists. */
    const tpl = document.getElementById('qqBrandOptionsTemplate');
    qqAllBrands = (tpl && tpl.content)
        ? Array.from(tpl.content.querySelectorAll('.qq-brand-option')).map((el) => el.dataset.brand || '').filter(Boolean)
        : [];

    document.querySelectorAll('[data-policy-mode]').forEach((btn) => {
        btn.addEventListener('click', () => qqSetPolicyMode(btn.dataset.policyMode || QQ_POLICY_ALL_AVAILABLE));
    });

    qqCreateBrandPicker({
        key: 'allowlist',
        root: document.getElementById('qqAllowlistPanel'),
        brands: qqAllowlistBrands,
        onChange: qqUpdateBrandWarning,
    });

    document.getElementById('qqAddTierBtn')?.addEventListener('click', qqAddTier);

    qqRenderPolicyHints();
}

/* ═══════════════ per-row policy dialog (Phase 3B2) ═══════════════ */

const QQ_ROW_POLICY_LABELS = {
    [QQ_ROW_POLICY_INHERIT]: 'Theo thiết lập chung',
    [QQ_POLICY_PRIORITY_FALLBACK]: 'Ưu tiên riêng',
    [QQ_POLICY_ALLOWLIST_ONLY]: 'Chỉ hãng riêng',
    [QQ_POLICY_ALL_AVAILABLE]: 'Tất cả hãng',
};

/** Tier ctx bound to whichever row is currently open in the dialog. Built
 * fresh on each open since the target policy object changes per row. */
function qqRowTierCtx(policy) {
    return {
        tiers: policy.tiers,
        containerEl: document.getElementById('qqRowTierList'),
        keyPrefix: `row:${qqRowPolicyEditingRequestId}`,
        onChange: () => qqOnRowPolicyChanged(qqRowPolicyEditingRequestId),
        trackKey: (key) => qqRowDialogPickerKeys.push(key),
    };
}

function qqRenderRowTierList(policy) {
    qqRenderTierListInCtx(qqRowTierCtx(policy));
}

/** Tear down every picker created for whichever row's dialog session was
 * previously open — the dialog DOM nodes are reused across rows, so without
 * this, switching rows would leak stale document-level listeners. */
function qqCloseRowPolicyDialogCleanup() {
    qqDestroyBrandPicker('row-allowlist');
    qqRowDialogPickerKeys.forEach((key) => qqDestroyBrandPicker(key));
    qqRowDialogPickerKeys = [];
}

function qqRenderRowAllowlistPicker(policy) {
    qqDestroyBrandPicker('row-allowlist');
    qqCreateBrandPicker({
        key: 'row-allowlist',
        root: document.getElementById('qqRowAllowlistPanel'),
        brands: policy.allowlistBrands,
        onChange: () => qqOnRowPolicyChanged(qqRowPolicyEditingRequestId),
    });
}

/** Re-render every visual consequence of the dialog's current mode/validity
 * — mirrors qqRenderPolicyHints but scoped to the row dialog + INHERIT-aware. */
function qqRenderRowPolicyHints(policy) {
    const { mode, valid } = qqRowPolicyValidation(policy);
    const dialog = document.getElementById('qqRowPolicyDialog');

    const allowlistHint = dialog?.querySelector('.qq-row-allowlist-required-hint');
    if (allowlistHint) allowlistHint.hidden = !(mode === QQ_POLICY_ALLOWLIST_ONLY && !valid);

    const tierHint = dialog?.querySelector('.qq-row-tier-required-hint');
    if (tierHint) tierHint.hidden = !(mode === QQ_POLICY_PRIORITY_FALLBACK && !valid);

    dialog?.querySelectorAll('#qqRowTierList .qq-tier-row').forEach((row) => {
        const tier = policy.tiers.find((t) => t.id === row.dataset.tierId);
        row.classList.toggle('is-empty-tier', Boolean(tier) && tier.brands.size === 0);
    });

    const hintEl = document.getElementById('qqRowPolicyHint');
    if (hintEl) {
        hintEl.textContent = mode === QQ_ROW_POLICY_INHERIT
            ? `Theo chính sách hãng chung hiện tại: ${QQ_POLICY_HINT_TEXT[qqBrandPolicyMode] || ''}`
            : (QQ_POLICY_HINT_TEXT[mode] || '');
    }
}

/** Switch the row dialog's own mode (independent of the global segmented
 * control — different DOM, different data attribute, different state). */
function qqSetRowPolicyMode(mode) {
    const requestId = qqRowPolicyEditingRequestId;
    if (!requestId) return;
    if (![QQ_ROW_POLICY_INHERIT, QQ_POLICY_PRIORITY_FALLBACK, QQ_POLICY_ALLOWLIST_ONLY, QQ_POLICY_ALL_AVAILABLE].includes(mode)) return;
    const policy = qqEnsureRowPolicy(requestId);
    if (policy.mode === mode) return;
    policy.mode = mode;

    const dialog = document.getElementById('qqRowPolicyDialog');
    dialog?.querySelectorAll('[data-row-policy-mode]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.rowPolicyMode === mode);
    });
    const allowlistPanel = document.getElementById('qqRowAllowlistPanel');
    if (allowlistPanel) allowlistPanel.hidden = mode !== QQ_POLICY_ALLOWLIST_ONLY;
    const tierPanel = document.getElementById('qqRowTierPanel');
    if (tierPanel) tierPanel.hidden = mode !== QQ_POLICY_PRIORITY_FALLBACK;

    if (mode === QQ_POLICY_PRIORITY_FALLBACK && policy.tiers.length === 0) {
        policy.tiers.push({ id: qqNewTierId(), brands: new Set() });
        qqRenderRowTierList(policy);
    }

    qqOnRowPolicyChanged(requestId);
}

/** Every mutation inside the row dialog funnels through here: refresh the
 * dialog's own hints, refresh that row's grid cell, and invalidate stale
 * results — never touches grid input, request_id/order/source_row, or any
 * other row's policy. */
function qqOnRowPolicyChanged(requestId) {
    const policy = qqRowBrandPolicies.get(requestId) || qqDefaultRowPolicy();
    qqRenderRowPolicyHints(policy);
    const tr = document.querySelector(`#qqRequestBody tr[data-request-id="${requestId}"]`);
    if (tr) qqUpdateRowPolicyCell(tr);
    qqUpdateMatchButton();
    qqInvalidateResults();
}

function qqOpenRowPolicyDialog(tr) {
    if (!tr || qqRowIsExactCodeLocked(tr)) return;
    qqCloseRowPolicyDialogCleanup();

    const requestId = tr.dataset.requestId;
    qqRowPolicyEditingRequestId = requestId;
    const policy = qqEnsureRowPolicy(requestId);

    const rowNumber = Array.from(document.querySelectorAll('#qqRequestBody tr')).indexOf(tr) + 1;
    const titleEl = document.getElementById('qqRowPolicyDialogTitle');
    if (titleEl) titleEl.textContent = `Thiết lập hãng cho dòng #${rowNumber}`;

    const dialog = document.getElementById('qqRowPolicyDialog');
    dialog?.querySelectorAll('[data-row-policy-mode]').forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.rowPolicyMode === policy.mode);
    });
    const allowlistPanel = document.getElementById('qqRowAllowlistPanel');
    if (allowlistPanel) allowlistPanel.hidden = policy.mode !== QQ_POLICY_ALLOWLIST_ONLY;
    const tierPanel = document.getElementById('qqRowTierPanel');
    if (tierPanel) tierPanel.hidden = policy.mode !== QQ_POLICY_PRIORITY_FALLBACK;

    qqRenderRowAllowlistPicker(policy);
    qqRenderRowTierList(policy);
    qqRenderRowPolicyHints(policy);

    if (typeof dialog?.showModal === 'function') dialog.showModal();
}

function qqInitRowPolicyDialog() {
    const dialog = document.getElementById('qqRowPolicyDialog');
    if (!dialog) return;

    dialog.querySelectorAll('[data-row-policy-mode]').forEach((btn) => {
        btn.addEventListener('click', () => qqSetRowPolicyMode(btn.dataset.rowPolicyMode || QQ_ROW_POLICY_INHERIT));
    });
    document.getElementById('qqRowAddTierBtn')?.addEventListener('click', () => {
        const policy = qqRowPolicyEditingRequestId ? qqEnsureRowPolicy(qqRowPolicyEditingRequestId) : null;
        if (policy) qqAddTierTo(qqRowTierCtx(policy));
    });
    dialog.addEventListener('close', () => {
        qqRowPolicyEditingRequestId = null;
        qqCloseRowPolicyDialogCleanup();
    });
}

/* ═══════════════ summary ═══════════════ */

function qqRenderSummary(counts, totalRows) {
    const el = document.getElementById('qqSummary');
    if (!el) return;
    el.replaceChildren();

    const summaryWrap = document.createElement('div');
    summaryWrap.className = 'qq-summary-bar';

    /* Left: Request count and selected lines count */
    const textCounters = document.createElement('div');
    textCounters.className = 'qq-summary-counters';

    const reqItem = document.createElement('span');
    reqItem.className = 'qq-summary-count-item qq-summary-count-requests';
    const reqVal = document.createElement('strong');
    reqVal.className = 'qq-count-val';
    reqVal.textContent = String(counts.total_requests);
    reqItem.appendChild(reqVal);
    reqItem.appendChild(document.createTextNode(' yêu cầu'));
    textCounters.appendChild(reqItem);

    const dot = document.createElement('span');
    dot.className = 'qq-summary-count-dot';
    dot.textContent = '•';
    textCounters.appendChild(dot);

    const lineItem = document.createElement('span');
    lineItem.className = 'qq-summary-count-item qq-summary-count-lines';
    const lineVal = document.createElement('strong');
    lineVal.className = 'qq-count-val';
    lineVal.textContent = String(counts.total_selected_lines);
    lineItem.appendChild(lineVal);
    lineItem.appendChild(document.createTextNode(' sản phẩm được chọn'));
    textCounters.appendChild(lineItem);

    summaryWrap.appendChild(textCounters);

    /* Right: 5 lifecycle badge filter buttons */
    const badgeGroup = document.createElement('div');
    badgeGroup.className = 'qq-summary-badges';
    badgeGroup.setAttribute('role', 'group');
    badgeGroup.setAttribute('aria-label', 'Lọc kết quả theo trạng thái');

    const badgeDefs = [
        { key: QQ_LIFECYCLE_SELECTED, label: 'Đã chọn', count: counts.selected },
        { key: QQ_LIFECYCLE_REVIEW, label: 'Cần xem', count: counts.review },
        { key: QQ_LIFECYCLE_UNRESOLVED, label: 'Chưa resolve', count: counts.unresolved },
        { key: QQ_LIFECYCLE_BLOCKED, label: 'Bị chặn', count: counts.blocked },
        { key: QQ_LIFECYCLE_EXPORTED, label: 'Đã xuất', count: counts.exported },
    ];

    badgeDefs.forEach(({ key, label, count }) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        const isActive = qqActiveLifecycleFilter === key;
        btn.className = `qq-lifecycle-btn qq-lifecycle-btn-${key.toLowerCase()}${isActive ? ' is-active' : ''}${count === 0 ? ' is-empty' : ''}`;
        btn.setAttribute('data-lifecycle', key);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        if (count === 0 && !isActive) {
            btn.disabled = true;
            btn.setAttribute('aria-disabled', 'true');
        }

        const labelSpan = document.createElement('span');
        labelSpan.className = 'qq-lifecycle-btn-label';
        labelSpan.textContent = label;
        btn.appendChild(labelSpan);

        const countSpan = document.createElement('span');
        countSpan.className = 'qq-lifecycle-btn-count';
        countSpan.textContent = String(count);
        btn.appendChild(countSpan);

        btn.addEventListener('click', () => {
            if (qqActiveLifecycleFilter === key) {
                qqActiveLifecycleFilter = null;
            } else {
                qqActiveLifecycleFilter = key;
            }
            qqRenderPreview();
        });

        badgeGroup.appendChild(btn);
    });

    summaryWrap.appendChild(badgeGroup);
    el.appendChild(summaryWrap);
}

/* ═══════════════ compliance CSS ═══════════════ */

function qqComplianceClass(label) {
    const map = {
        'CẤM NHẬP': 'warning-cam-nhap',
        'Phụ lục II': 'warning-phu-luc-ii',
        'Phụ lục III': 'warning-phu-luc-iii',
        'TỒN KHO': 'warning-ton-kho',
        'Được bán': 'warning-duoc-ban',
        'Chưa xác định': 'warning-chua-xac-dinh',
        'Không phát hiện hạn chế': 'warning-khong-phat-hien',
    };
    return map[label] || '';
}

/* ═══════════════ result table (product-search style) ═══════════════ */

/*
 * Columns rendered per candidate row:
 * [checkbox] | Yêu cầu | Trạng thái | Name | Code | CAS | Brand | Size | Unit_Price | Note | Compliance | Compliance Note | Loại khớp
 */

function qqAppendCell(tr, text, className) {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.textContent = qqText(text);
    tr.appendChild(td);
    return td;
}

/**
 * Merge the skipped tiers from `fallback_path` (backend-provided) with the
 * winning tier (derived client-side from `candidates` + `effective_brand_policy`,
 * since the backend only appends to fallback_path for tiers that did NOT
 * match). Returns entries sorted by tier index; [] when the row wasn't
 * resolved via PRIORITY_FALLBACK at all.
 */
function qqFallbackTierEntries(result) {
    const entries = (result.fallback_path || []).map((fp) => ({
        tierIndex: fp.tier,
        brands: fp.brands || [],
        eligibleCount: fp.eligible_count || 0,
        rejected: fp.rejected_counts || {},
        matched: false,
    }));

    const matchedIdx = result.matched_priority_tier;
    if (matchedIdx != null) {
        const policy = result.effective_brand_policy;
        const tiers = (policy && policy.mode === QQ_POLICY_PRIORITY_FALLBACK) ? (policy.priority_tiers || []) : [];
        const cands = result.candidates || [];
        const eligibleCount = cands.filter(qqIsSelectableCandidate).length;
        const complianceCount = cands.filter(
            (c) => c.ineligible_reason === 'COMPLIANCE_BLOCKED' || QQ_BLOCKED_COMPLIANCE.has(c.Compliance || c.compliance)
        ).length;
        const noPriceCount = cands.filter(
            (c) => !qqIsSelectableCandidate(c)
                && (c.Unit_Price_Value || 0) <= 0
                && c.ineligible_reason !== 'COMPLIANCE_BLOCKED'
                && !QQ_BLOCKED_COMPLIANCE.has(c.Compliance || c.compliance)
        ).length;
        entries.push({
            tierIndex: matchedIdx,
            brands: (tiers[matchedIdx] && tiers[matchedIdx].brands) || [],
            eligibleCount,
            rejected: { COMPLIANCE: complianceCount, FILTER: 0, NO_VALID_PRICE: noPriceCount },
            matched: true,
        });
    }

    return entries.sort((a, b) => a.tierIndex - b.tierIndex);
}

/** Build the "Chi tiết chọn hãng" mini-table for the expandable detail row. */
function qqBuildFallbackDetailTable(entries) {
    const table = document.createElement('table');
    table.className = 'qq-fallback-detail-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    ['Ưu tiên', 'Brand', 'Ứng viên hợp lệ', 'Bị loại: Compliance', 'Bị loại: Bộ lọc', 'Bị loại: Không có giá'].forEach((label) => {
        const th = document.createElement('th');
        th.textContent = label;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    entries.forEach((entry) => {
        const tr = document.createElement('tr');
        if (entry.matched) tr.classList.add('is-matched-tier');

        const tierTd = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = 'qq-tier-badge';
        badge.textContent = qqTierLabel(entry.tierIndex);
        tierTd.appendChild(badge);
        tr.appendChild(tierTd);

        qqAppendCell(tr, entry.brands.join(', '));
        qqAppendCell(tr, String(entry.eligibleCount));
        qqAppendCell(tr, String(entry.rejected.COMPLIANCE || 0));
        qqAppendCell(tr, String(entry.rejected.FILTER || 0));
        qqAppendCell(tr, String(entry.rejected.NO_VALID_PRICE || 0));

        tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
}

function qqBuildStatusCellContent(td, lifecycleInfo, result, resultIndex) {
    td.replaceChildren();
    const tag = document.createElement('span');
    tag.className = `qq-lifecycle-tag qq-lifecycle-tag-${lifecycleInfo.lifecycle.toLowerCase()}`;
    tag.textContent = QQ_LIFECYCLE_LABELS[lifecycleInfo.lifecycle] || lifecycleInfo.lifecycle;
    td.appendChild(tag);

    /* "Hãng riêng" badge (Phase 3B2) — the row currently carries its own
     * override, distinct from the global policy. Read from qqRowBrandPolicies
     * (not from result.effective_brand_policy, which looks identical whether
     * it came from the row override or the global fallback). Combined with
     * the tier badge below when the row's own fallback actually kicked in,
     * so we never show two separate badges for the same fact. */
    const rowPolicyForResult = qqRowBrandPolicies.get(qqRequestIdForResult(result, resultIndex));
    const hasOwnPolicy = Boolean(rowPolicyForResult) && rowPolicyForResult.mode !== QQ_ROW_POLICY_INHERIT;

    /* "Ưu tiên N" badge — never shown for ALLOWLIST_ONLY/ALL_AVAILABLE (matched_priority_tier stays null there). */
    if (result.matched_priority_tier != null) {
        const tierBadge = document.createElement('span');
        tierBadge.className = 'qq-tier-badge';
        tierBadge.textContent = hasOwnPolicy
            ? `Hãng riêng · ${qqTierLabel(result.matched_priority_tier)}`
            : qqTierLabel(result.matched_priority_tier);
        td.appendChild(tierBadge);
    } else if (hasOwnPolicy) {
        const ownBadge = document.createElement('span');
        ownBadge.className = 'qq-own-policy-badge';
        ownBadge.textContent = 'Hãng riêng';
        td.appendChild(ownBadge);
    }

    const reasonText = QQ_REASON_CODE_LABELS[lifecycleInfo.reason_code]
        || QQ_REASON_LABELS[lifecycleInfo.reason_code]
        || QQ_REASON_LABELS[result.reason]
        || lifecycleInfo.reason_code
        || '';
    if (reasonText) {
        const reasonDiv = document.createElement('div');
        reasonDiv.className = 'qq-status-reason';
        reasonDiv.textContent = reasonText;
        td.appendChild(reasonDiv);
    }

    if (lifecycleInfo.lifecycle === QQ_LIFECYCLE_UNRESOLVED) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'qq-row-action-btn qq-action-edit-req';
        btn.textContent = 'Sửa yêu cầu';
        btn.title = 'Chuyển đến dòng yêu cầu trong bảng nhập để sửa Code/CAS';
        btn.addEventListener('click', () => {
            const reqId = qqRequestIdForResult(result, resultIndex);
            const targetRow = document.querySelector(`#qqRequestBody tr[data-request-id="${reqId}"]`);
            if (targetRow) {
                qqSetRequestSource('manual');
                targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
                const input = targetRow.querySelector('.qq-input-code') || targetRow.querySelector('.qq-input-cas') || targetRow.querySelector('.qq-input-name');
                input?.focus();
            }
        });
        td.appendChild(btn);
    } else if (lifecycleInfo.lifecycle === QQ_LIFECYCLE_REVIEW) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'qq-row-action-btn qq-action-pick-cand';
        btn.textContent = 'Chọn sản phẩm';
        btn.title = 'Tập trung chọn sản phẩm cho yêu cầu này';
        btn.addEventListener('click', () => {
            const reqId = qqRequestIdForResult(result, resultIndex);
            const firstCb = document.querySelector(`tr[data-parent-request-id="${reqId}"] .qq-result-cb:not(:disabled)`);
            firstCb?.focus();
        });
        td.appendChild(btn);
    } else if (lifecycleInfo.lifecycle === QQ_LIFECYCLE_BLOCKED) {
        const hint = document.createElement('div');
        hint.className = 'qq-blocked-hint';
        hint.textContent = 'Tất cả sản phẩm bị cấm nhập / chưa xác định';
        td.appendChild(hint);
    }

    /* "Chi tiết chọn hãng" toggle — wired to its detail <tr> by the caller, which has tbody access. */
    if (qqFallbackTierEntries(result).length > 0) {
        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'qq-fallback-toggle-btn';
        const icon = document.createElement('i');
        icon.className = 'fas fa-chevron-right';
        icon.setAttribute('aria-hidden', 'true');
        toggleBtn.appendChild(icon);
        toggleBtn.appendChild(document.createTextNode(' Chi tiết chọn hãng'));
        td.appendChild(toggleBtn);
    }
}

function qqWireFallbackToggle(toggleBtn, detailTr) {
    if (!toggleBtn || !detailTr) return;
    toggleBtn.addEventListener('click', () => {
        detailTr.hidden = !detailTr.hidden;
        toggleBtn.classList.toggle('is-expanded', !detailTr.hidden);
    });
}

/**
 * Build the hidden detail <tr> for a result's fallback breakdown and wire the
 * status cell's toggle button (if present) to show/hide it. Must be called
 * right after the result's own row(s) have been appended to `tbody`, since it
 * appends the detail row immediately after the current tbody tail. The row
 * is tagged with `data-parent-request-id` so a later status-cell rebuild
 * (e.g. after a manual pick change) can re-find and re-wire it.
 */
function qqAttachFallbackDetailRow(tbody, statusTd, result, columnCount, requestId) {
    const toggleBtn = statusTd?.querySelector('.qq-fallback-toggle-btn');
    if (!toggleBtn) return;
    const entries = qqFallbackTierEntries(result);
    if (!entries.length) return;

    const detailTr = document.createElement('tr');
    detailTr.className = 'qq-fallback-detail-row';
    detailTr.hidden = true;
    detailTr.dataset.parentRequestId = requestId;
    const detailTd = document.createElement('td');
    detailTd.colSpan = columnCount;
    detailTd.appendChild(qqBuildFallbackDetailTable(entries));
    detailTr.appendChild(detailTd);

    qqWireFallbackToggle(toggleBtn, detailTr);
    tbody.appendChild(detailTr);
}

function qqRenderResultTable(results) {
    const container = document.getElementById('qqResultGroups');
    if (!container) return;
    container.replaceChildren();

    const allResultsWithIndex = results.map((result, resultIndex) => ({
        result,
        resultIndex,
        lifecycleInfo: qqGetRequestLifecycle(result, resultIndex),
    }));

    const visibleItems = allResultsWithIndex.filter(({ lifecycleInfo }) => {
        if (!qqActiveLifecycleFilter) return true;
        return lifecycleInfo.lifecycle === qqActiveLifecycleFilter;
    });

    if (visibleItems.length === 0) {
        const emptyNotice = document.createElement('div');
        emptyNotice.className = 'qq-result-empty-filter';
        const emptyP = document.createElement('p');
        emptyP.className = 'qq-empty-filter-text';
        emptyP.appendChild(document.createTextNode('Không có yêu cầu nào ở trạng thái '));
        const strongLabel = document.createElement('strong');
        strongLabel.textContent = QQ_LIFECYCLE_LABELS[qqActiveLifecycleFilter] || qqActiveLifecycleFilter;
        emptyP.appendChild(strongLabel);
        emptyP.appendChild(document.createTextNode('.'));
        emptyNotice.appendChild(emptyP);
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'btn-secondary nav-button qq-btn-reset-filter';
        resetBtn.textContent = 'Xem tất cả yêu cầu';
        resetBtn.addEventListener('click', () => {
            qqActiveLifecycleFilter = null;
            qqRenderPreview();
        });
        emptyNotice.appendChild(resetBtn);
        container.appendChild(emptyNotice);
        return;
    }

    const wrap = document.createElement('div');
    wrap.className = 'qq-table-wrap qq-result-table-wrap';

    const table = document.createElement('table');
    table.className = 'qq-result-table';
    table.id = 'qqResultTableEl';

    /* thead: 13 columns */
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    [
        '', 'Yêu cầu', 'Trạng thái', 'Sản phẩm', 'Code', 'CAS', 'Brand',
        'Size', 'Giá nhập', 'Note', 'Compliance', 'Ghi chú CL', 'Loại khớp',
    ].forEach((label) => {
        const th = document.createElement('th');
        th.textContent = label;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    wrap.appendChild(table);
    container.appendChild(wrap);

    visibleItems.forEach(({ result, resultIndex, lifecycleInfo }, visibleIndex) => {
        const candidates = result.candidates || [];
        const isManual = qqStrategy === 'MANUAL';
        const selectedCands = qqEffectiveSelectedCandidates(result, resultIndex);
        const reqOrder = result.request_order || (resultIndex + 1);
        const reqLabel = [result.requested_code, result.requested_cas].filter(Boolean).join(' / ')
            || result.requested_name || `#${reqOrder}`;
        const requestId = qqRequestIdForResult(result, resultIndex);

        /* Separator row between requests if multiple */
        if (visibleIndex > 0) {
            const sepTr = document.createElement('tr');
            sepTr.className = 'qq-result-sep';
            const sepTd = document.createElement('td');
            sepTd.colSpan = 13;
            sepTr.appendChild(sepTd);
            tbody.appendChild(sepTr);
        }

        if (!candidates.length) {
            /* no candidates: show status-only row */
            const tr = document.createElement('tr');
            tr.dataset.parentRequestId = requestId;
            tr.className = `qq-row-${lifecycleInfo.lifecycle.toLowerCase()}`;
            /* empty checkbox */
            const cbTd = document.createElement('td');
            cbTd.className = 'qq-cell-cb';
            tr.appendChild(cbTd);

            const reqTd = document.createElement('td');
            reqTd.className = 'qq-cell-req';
            const strongOrder = document.createElement('strong');
            strongOrder.textContent = `#${reqOrder}`;
            reqTd.appendChild(strongOrder);
            reqTd.appendChild(document.createTextNode(`: ${qqText(reqLabel)}`));
            tr.appendChild(reqTd);

            const statusTd = document.createElement('td');
            statusTd.colSpan = 11;
            statusTd.className = 'qq-cell-lifecycle';
            qqBuildStatusCellContent(statusTd, lifecycleInfo, result, resultIndex);
            tr.appendChild(statusTd);

            tbody.appendChild(tr);
            qqAttachFallbackDetailRow(tbody, statusTd, result, 13, requestId);
            return;
        }

        let groupStatusTd = null;
        candidates.forEach((candidate, candIndex) => {
            const eligible = qqIsSelectableCandidate(candidate);
            const isAutoExcluded = Boolean(candidate.auto_excluded);
            const isSelectedByStrategy = selectedCands.some(
                (s) => s.product_id === candidate.product_id
            );
            const isManualPicked = isManual && qqUserPicks.has(requestId)
                && (qqUserPicks.get(requestId) || []).some(
                    (p) => p.product_id === candidate.product_id
                );

            const tr = document.createElement('tr');
            tr.dataset.parentRequestId = requestId;
            if (!eligible || isAutoExcluded) tr.classList.add('qq-row-blocked');
            else if (isSelectedByStrategy || isManualPicked) tr.classList.add('qq-row-selected');

            /* checkbox cell */
            const cbTd = document.createElement('td');
            cbTd.className = 'qq-cell-cb';
            if (eligible && !isAutoExcluded) {
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'qq-result-cb';
                if (isManual) {
                    cb.checked = isManualPicked;
                    cb.addEventListener('change', () => {
                        let picks = qqUserPicks.get(requestId) || [];
                        if (cb.checked) {
                            if (!picks.some((p) => p.product_id === candidate.product_id)) {
                                picks = [...picks, candidate];
                            }
                        } else {
                            picks = picks.filter((p) => p.product_id !== candidate.product_id);
                        }
                        qqUserPicks.set(requestId, picks);
                        tr.classList.toggle('qq-row-selected', cb.checked);
                        qqUpdateCopyButton();
                        qqUpdateExportButton();
                        const newCounts = qqSummarizeResults(qqResults);
                        qqRenderSummary(newCounts, qqResults.length);
                        const groupStatusTd = document.querySelector(`tr[data-parent-request-id="${requestId}"] .qq-cell-lifecycle`);
                        if (groupStatusTd) {
                            qqBuildStatusCellContent(groupStatusTd, qqGetRequestLifecycle(result, resultIndex), result, resultIndex);
                            /* Rebuild replaced the toggle button node — re-wire it to the existing detail row. */
                            const existingDetailTr = document.querySelector(`tr.qq-fallback-detail-row[data-parent-request-id="${requestId}"]`);
                            const newToggleBtn = groupStatusTd.querySelector('.qq-fallback-toggle-btn');
                            if (existingDetailTr && newToggleBtn) {
                                newToggleBtn.classList.toggle('is-expanded', !existingDetailTr.hidden);
                                qqWireFallbackToggle(newToggleBtn, existingDetailTr);
                            }
                        }
                    });
                } else {
                    cb.checked = isSelectedByStrategy;
                    cb.disabled = true;
                }
                cbTd.appendChild(cb);
            } else {
                /* blocked/excluded: disabled checkbox */
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.disabled = true;
                cb.className = 'qq-result-cb';
                cbTd.appendChild(cb);
            }
            tr.appendChild(cbTd);

            /* request label only on first candidate of each result */
            if (candIndex === 0) {
                const reqTd = document.createElement('td');
                reqTd.className = 'qq-cell-req';
                reqTd.rowSpan = candidates.length;
                const strongOrder = document.createElement('strong');
                strongOrder.textContent = `#${reqOrder}`;
                reqTd.appendChild(strongOrder);
                reqTd.appendChild(document.createTextNode(`: ${qqText(reqLabel)}`));
                tr.appendChild(reqTd);

                /* lifecycle status column spanning all candidate rows */
                const statusTd = document.createElement('td');
                statusTd.className = 'qq-cell-lifecycle';
                statusTd.rowSpan = candidates.length;
                qqBuildStatusCellContent(statusTd, lifecycleInfo, result, resultIndex);
                tr.appendChild(statusTd);
                groupStatusTd = statusTd;
            }

            /* product cells */
            qqAppendCell(tr, candidate.Name || candidate.name || '', 'qq-cell-product');
            qqAppendCell(tr, candidate.Code || candidate.code || '', 'qq-cell-code');
            qqAppendCell(tr, candidate.Cas || candidate.cas || '', 'qq-cell-cas');
            qqAppendCell(tr, candidate.Brand || candidate.brand || '', 'qq-cell-brand');
            qqAppendCell(tr, candidate.Size || candidate.size || '', 'qq-cell-size');
            qqAppendCell(tr, candidate.Unit_Price || '', 'qq-cell-price');
            qqAppendCell(tr, candidate.Note || candidate.note || '', 'qq-cell-note');

            /* compliance with colour */
            const compLabel = candidate.Compliance || candidate.compliance || '';
            const compTd = document.createElement('td');
            compTd.textContent = compLabel;
            const compCss = candidate.compliance_css || qqComplianceClass(compLabel);
            if (compCss) compTd.className = compCss;
            tr.appendChild(compTd);

            qqAppendCell(tr, candidate.Compliance_Note || candidate.compliance_note || '', 'qq-cell-comp-note');

            /* match mode badge */
            const modeTd = document.createElement('td');
            modeTd.className = 'qq-cell-match-mode';
            const mode = result.match_mode || '';
            if (mode) {
                const badge = document.createElement('span');
                badge.className = `qq-match-mode qq-match-${mode.toLowerCase()}`;
                badge.textContent = qqFormatMatchMode(mode);
                modeTd.appendChild(badge);
            }
            /* warnings inline */
            const warns = candidate.warnings || result.warnings || [];
            if (warns.length) {
                const wt = document.createElement('div');
                wt.className = 'qq-inline-warn';
                wt.textContent = warns.map(qqFormatWarning).join('; ');
                modeTd.appendChild(wt);
            }
            tr.appendChild(modeTd);

            tbody.appendChild(tr);
        });

        qqAttachFallbackDetailRow(tbody, groupStatusTd, result, 13, requestId);
    });
}

/* ═══════════════ preview ═══════════════ */

function qqRenderPreview() {
    const section = document.getElementById('qqPreviewSection');
    const empty = document.getElementById('qqEmptyPreview');
    if (!section) return;
    section.hidden = false;

    if (!qqResults.length) {
        if (empty) empty.hidden = false;
        document.getElementById('qqSummary')?.replaceChildren();
        document.getElementById('qqResultGroups')?.replaceChildren();
        return;
    }
    if (empty) empty.hidden = true;

    const manualHint = document.getElementById('qqManualHint');
    if (manualHint) manualHint.hidden = qqStrategy !== 'MANUAL';

    qqRenderSummary(qqSummarizeResults(qqResults), qqResults.length);
    qqRenderResultTable(qqResults);
    qqUpdateCopyButton();
}

/* ═══════════════ match ═══════════════ */

/** Vietnamese Match-blocked explanation for a specific invalid row, with the
 * grid's 1-based position so the user can find it without opening anything. */
function qqExplainRowPolicyBlocked(tr) {
    const rowNumber = Array.from(document.querySelectorAll('#qqRequestBody tr')).indexOf(tr) + 1;
    qqSetStatus(
        `Dòng #${rowNumber}: chính sách hãng riêng chưa hợp lệ — bấm biểu tượng cảnh báo ở cột "Chính sách hãng" để sửa.`,
        'error',
    );
}

function qqRunMatch() {
    if (qqMatchInProgress) return;
    const rows = qqReadRequestRows();
    if (!qqHasMatchableRows(rows)) {
        qqSetStatus('Cần ít nhất một dòng không trống.', 'error');
        return;
    }
    const invalidRowTr = Array.from(document.querySelectorAll('#qqRequestBody tr')).find(qqRowBlocksMatch);
    if (invalidRowTr) {
        qqExplainRowPolicyBlocked(invalidRowTr);
        return;
    }
    const equivDefault = document.getElementById('qqEquivDefault')?.checked || false;
    const policyState = qqPolicyValidation();
    if (!policyState.valid) {
        qqSetStatus(QQ_POLICY_INVALID_MESSAGES[policyState.mode] || 'Cách chọn hãng chưa hợp lệ.', 'error');
        return;
    }
    const payload = qqBuildMatchPayload(
        rows,
        qqBuildGlobalBrandPolicyPayload(),
        document.getElementById('qqSizeFilter')?.value || '',
        qqStrategy,
        equivDefault,
    );
    if (!payload.rows.length) {
        qqSetStatus('Không có dòng hợp lệ để gửi.', 'error');
        return;
    }
    qqUserPicks = new Map();
    qqLegacyResultIds = new Map();
    qqSetStatus('Đang match…', 'loading');
    qqMatchInProgress = true;
    qqUpdateMatchButton();

    $.ajax({
        url: '/api/quote-assistant/match',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        timeout: QQ_AJAX_TIMEOUT_MS,
        success(data) {
            qqResults = (data && data.results) ? data.results : [];
            qqRenderPreview();
            const counts = qqSummarizeResults(qqResults);
            let statusMsg = `Hoàn tất ${qqResults.length} dòng — đã chọn ${counts.matched}, cần xem ${counts.manual_review}, chưa resolve ${counts.unresolved}.`;
            if (qqStrategy === 'MANUAL' && counts.matched === 0 && counts.manual_review > 0) {
                statusMsg += ' Chọn sản phẩm ở cột checkbox để copy/xuất Excel.';
            }
            qqSetStatus(statusMsg, 'success');
        },
        error(xhr) {
            qqResults = [];
            qqLegacyResultIds = new Map();
            qqRenderPreview();
            qqSetStatus(qqFormatAjaxError(xhr, 'Match thất bại.'), 'error');
        },
        complete() {
            qqMatchInProgress = false;
            qqUpdateMatchButton();
        },
    });
}

/* ═══════════════ copy ═══════════════ */

function qqCopyResults() {
    const payload = qqBuildCopyPayload(qqResults);
    if (!payload) {
        qqExplainCopyBlocked();
        return;
    }
    const done = () => qqSetStatus('Đã copy kết quả (không có header).', 'success');
    if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(payload).then(done).catch(() => {
            const ta = document.createElement('textarea');
            ta.value = payload;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            done();
        });
        return;
    }
    done();
}

/* ═══════════════ export workbook ═══════════════ */

function qqSafeFilenameFromDisposition(disposition) {
    if (!disposition) return null;
    /* UTF-8 encoded: filename*=UTF-8''name.xlsx */
    const utf8Match = disposition.match(/filename\*\s*=\s*UTF-8''([^;\s]+)/i);
    if (utf8Match) {
        try { return decodeURIComponent(utf8Match[1]); } catch (_e) { /* fall through */ }
    }
    /* ASCII: filename="name.xlsx" or filename=name.xlsx */
    const asciiMatch = disposition.match(/filename\s*=\s*"?([^";]+)"?/i);
    if (asciiMatch) return asciiMatch[1].trim();
    return null;
}

function qqTemplateNameFromDownload(filename) {
    const name = filename || (qqActiveTemplate && qqActiveTemplate.filename) || 'mẫu báo giá';
    return name.replace(/_draft(\.xlsx)$/i, '$1');
}

/** A valid .xlsx download must be a ZIP: reject anything that is not a PK archive. */
async function qqBlobLooksLikeXlsx(blob) {
    if (!blob || !blob.size) return false;
    try {
        const head = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
        return head[0] === 0x50 && head[1] === 0x4b;
    } catch (_e) {
        return false;
    }
}

function qqTriggerBlobDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), QQ_OBJECT_URL_REVOKE_MS);
}

function qqExportErrorMessage(status, bodyMessage) {
    if (status === 401) return 'Chưa đăng nhập.';
    if (status === 403) return 'Không có quyền hoặc chưa gán team.';
    if (status === 409) return 'Chưa có mẫu báo giá active. Vui lòng liên hệ admin.';
    if (status === 400) return bodyMessage || 'Template hoặc danh sách sản phẩm không hợp lệ.';
    if (status === 413) return 'Dữ liệu quá lớn để xuất báo giá.';
    if (status >= 500) return 'Server đang lỗi khi tạo báo giá.';
    return bodyMessage || `Xuất báo giá thất bại (${status}).`;
}

async function qqSubmitExport() {
    if (qqExportInProgress) return;

    const exportItems = qqBuildExportItems(qqResults);
    /* Export includes every request after Match (selected/unresolved/blocked/
     * review) as either a product line or a placeholder, so it only requires
     * at least one request — unlike Copy, it never requires a selection. */
    if (!qqResults.length) {
        qqExplainExportBlocked();
        return;
    }
    if (!qqHasActiveTemplate()) {
        qqUpdateExportButton();
        qqSetStatus('Chưa có mẫu báo giá. Vui lòng liên hệ admin.', 'error');
        return;
    }

    qqExportInProgress = true;
    qqUpdateExportButton();
    qqSetStatus('Đang tạo báo giá…', 'loading');

    const fd = new FormData();
    /* v2 contract: export_items is the single source of truth. */
    fd.append('export_items', JSON.stringify(exportItems));

    try {
        const response = await fetch(QQ_EXPORT_ENDPOINT, {
            method: 'POST',
            body: fd,
            credentials: 'same-origin',
        });

        if (!response.ok) {
            let bodyMessage = '';
            const ct = response.headers.get('Content-Type') || '';
            if (ct.includes('application/json')) {
                try {
                    const json = await response.json();
                    if (json && json.error) bodyMessage = String(json.error);
                } catch (_e) { /* ignore */ }
            }
            if (response.status === 409) {
                qqActiveTemplate = null;
                qqTemplateState = 'missing';
                qqRenderTemplateStatus();
            }
            qqSetStatus(qqExportErrorMessage(response.status, bodyMessage), 'error');
            return;
        }

        const blob = await response.blob();
        if (!(await qqBlobLooksLikeXlsx(blob))) {
            qqSetStatus('File báo giá trả về không hợp lệ, vui lòng thử lại.', 'error');
            return;
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const filename = qqSafeFilenameFromDisposition(disposition) || 'quote_draft.xlsx';

        qqTriggerBlobDownload(blob, filename);

        exportItems.forEach((it) => {
            if (it && it.request_id && it.lines && it.lines.length > 0) {
                qqExportedRequestIds.add(it.request_id);
            }
        });

        qqRenderPreview();
        qqSetStatus(`Đã xuất báo giá bằng mẫu ${qqTemplateNameFromDownload(filename)}.`, 'success');
    } catch (err) {
        qqSetStatus('Không thể kết nối đến máy chủ khi tạo báo giá.', 'error');
    } finally {
        qqExportInProgress = false;
        qqUpdateExportButton();
    }
}

/* ═══════════════ paste into grid ═══════════════ */

function qqHandlePaste(event) {
    const text = event.clipboardData?.getData('text/plain') || '';
    if (!text?.trim()) return;
    event.preventDefault();
    const matrix = qqParsePasteMatrix(text);
    if (!matrix.length) return;
    const anchor = qqGetPasteAnchor(document.activeElement);
    qqApplyPasteMatrix(matrix, anchor.rowIndex, anchor.colIndex);
}

/* ═══════════════ size mode ═══════════════ */

function qqSetSizeMode(mode) {
    qqSizeMode = mode;
    const wrap = document.getElementById('qqExactSizeWrap');
    if (wrap) wrap.hidden = mode !== 'EXACT';
    if (mode !== 'EXACT') {
        const input = document.getElementById('qqSizeFilter');
        if (input) input.value = '';
    }
    qqInvalidateResults();
}

/* ═══════════════ DOMContentLoaded ═══════════════ */

document.addEventListener('DOMContentLoaded', () => {
    qqClearAllGrid();
    qqInitPolicyWidget();
    qqInitRowPolicyDialog();
    qqInitRequestFileWizard();
    qqLoadActiveTemplateMetadata();
    qqUpdateCopyButton();

    document.getElementById('qqAddRowBtn')?.addEventListener('click', () => qqAddRows([qqBlankRow()], false));
    document.getElementById('qqRemoveRowBtn')?.addEventListener('click', () => {
        const body = document.getElementById('qqRequestBody');
        const rows = body?.querySelectorAll('tr') || [];
        if (rows.length > 1) {
            const removed = rows[rows.length - 1];
            qqForgetRequestId(removed.dataset.requestId);
            qqForgetRowPolicy(removed.dataset.requestId);
            removed.remove();
            qqRenumberGrid();
            qqUpdateBrandWarning();
        }
    });
    document.getElementById('qqClearAllBtn')?.addEventListener('click', qqClearAllGrid);
    document.getElementById('qqMatchBtn')?.addEventListener('click', qqRunMatch);
    document.getElementById('qqCopyBtn')?.addEventListener('click', qqCopyResults);
    document.getElementById('qqCopyBtnBottom')?.addEventListener('click', qqCopyResults);
    document.getElementById('qqExportBtn')?.addEventListener('click', qqSubmitExport);
    document.getElementById('qqExportBtnBottom')?.addEventListener('click', qqSubmitExport);
    document.getElementById('qqRequestBody')?.addEventListener('paste', qqHandlePaste);

    document.getElementById('qqEquivDefault')?.addEventListener('change', () => {
        qqUpdateBrandWarning();
        /* Flips which rows resolve to "Exact Code, no equivalent" and are
         * therefore locked out of row-level brand policy. */
        qqUpdateAllRowPolicyCells();
    });

    /* preparation type segmented */
    document.querySelectorAll('.qq-segment[data-preparation-type]').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.qq-segment[data-preparation-type]').forEach((el) => el.classList.remove('is-active'));
            btn.classList.add('is-active');
            qqPreparationType = btn.dataset.preparationType || 'ANY';
            qqInvalidateResults();
        });
    });

    /* size mode select */
    document.getElementById('qqSizeModeSelect')?.addEventListener('change', (e) => {
        qqSetSizeMode(e.target.value || 'ANY');
    });

    /* exact size input */
    document.getElementById('qqSizeFilter')?.addEventListener('input', qqInvalidateResults);

    /* strategy select */
    document.getElementById('qqStrategySelect')?.addEventListener('change', (e) => {
        qqStrategy = e.target.value || 'MANUAL';
        qqUserPicks = new Map();
        /* switching strategy invalidates prior picks/results */
        qqInvalidateResults();
    });
});

function qqSetStrategy(strategy) {
    qqStrategy = strategy || 'MANUAL';
    qqUserPicks = new Map();
}

function qqSetUserPick(requestId, candidate) {
    let picks = qqUserPicks.get(requestId) || [];
    if (!picks.some((p) => p.product_id === candidate.product_id)) {
        picks = [...picks, candidate];
    }
    qqUserPicks.set(requestId, picks);
}

/* ═══════════════ test surface ═══════════════ */

if (typeof window !== 'undefined') {
    window.QQ_TEST = {
        qqParsePasteMatrix,
        qqParsePasteGrid,
        qqParseTsvCells,
        qqParsePasteLines,
        qqApplyPasteToRows,
        qqBlankRow,
        qqFilterSubmittableRows,
        qqIsSubmittableRow,
        qqBuildMatchPayload,
        qqHasMatchableRows,
        qqNewRequestId,
        qqEnsureRequestId,
        qqForgetRequestId,
        qqRequestIdForResult,
        qqBuildExportItems,
        qqBuildExportPlaceholder,
        qqIsSelectableCandidate,
        qqEffectiveSelected,
        qqEffectiveSelectedCandidates,
        qqDisplayProduct,
        qqIsReferenceOnly,
        qqFormatStatus,
        qqFormatMatchMode,
        qqFormatWarning,
        qqSummarizeResults,
        qqBuildCopyPayload,
        qqHasCopyableRows,
        qqBuildExportSelections,
        qqGetRequestLifecycle,
        qqFormatLifecycle,
        qqFormatReasonCode,
        qqRunPreflight,
        qqUpdateGridRowStatus,
        qqSetSoftDisabled,
        qqSetStrategy,
        qqSetUserPick,
        qqExplainCopyBlocked,
        qqExplainExportBlocked,
        qqBlobLooksLikeXlsx,
        qqTriggerBlobDownload,
        qqSubmitExport,
        qqBuildAnalyzeFormData,
        qqCurrentRequestFileMapping,
        qqValidateRequestFileMapping,
        qqRequestFileErrorMessage,
        qqImportRequestFileRows,
        qqHandleRequestFile,
        qqSetRequestSource,
        qqResetRequestFileState,
        qqRenderRequestFilePreview,
        qqConfirmReplaceGridIfNeeded,
        qqSafeFilenameFromDisposition,
        qqTemplateNameFromDownload,
        qqLoadActiveTemplateMetadata,
        qqRenderTemplateStatus,
        qqHasActiveTemplate,
        qqExportErrorMessage,
        qqExcelSafeCell,
        qqResolveBrandToken,
        qqPolicyValidation,
        qqBuildGlobalBrandPolicyPayload,
        qqLegacyBrandsFromPolicy,
        qqAddTier,
        qqRemoveTier,
        qqMoveTier,
        qqSetPolicyMode,
        qqFallbackTierEntries,
        qqSplitTokens,
        qqSplitList,
        /* Phase 3B2: per-row brand policy override */
        qqDefaultRowPolicy,
        qqEnsureRowPolicy,
        qqForgetRowPolicy,
        qqRowPolicySummary,
        qqRowPolicyValidation,
        qqBuildRowBrandPolicyPayload,
        qqBuildBrandPolicyPayloadFrom,
        qqRowIsExactCodeLocked,
        qqRowResolvedEquivalent,
        qqRowBlocksMatch,
        qqAnyRowPolicyBlocksMatch,
        qqUpdateRowPolicyCell,
        qqUpdateAllRowPolicyCells,
        qqOpenRowPolicyDialog,
        qqSetRowPolicyMode,
        qqOnRowPolicyChanged,
        QQ_ROW_POLICY_INHERIT,
        QQ_LIFECYCLE_SELECTED,
        QQ_LIFECYCLE_REVIEW,
        QQ_LIFECYCLE_UNRESOLVED,
        QQ_LIFECYCLE_BLOCKED,
        QQ_LIFECYCLE_EXPORTED,
        QQ_LIFECYCLE_LABELS,
        QQ_REASON_CODE_LABELS,
        QQ_BLOCKED_COMPLIANCE,
        QQ_REASON_LABELS,
        QQ_MATCH_MODE_LABELS,
        QQ_WARNING_LABELS,
        QQ_INITIAL_ROW_COUNT,
        QQ_GRID_FIELDS,
        QQ_SCOPE_DEFAULT,
        QQ_SCOPE_EXACT,
        QQ_SCOPE_EQUIV,
        QQ_REQUEST_FILE_MAX_BYTES,
        QQ_REQUEST_FILE_ANALYZE_ENDPOINT,
        QQ_REQUEST_FILE_PARSE_ENDPOINT,
    };
}
