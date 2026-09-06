/**
 * Behavioural regression harness for the Quick Quote download path.
 * Loads static/quick_quote.js with a minimal DOM stub and asserts that the
 * download anchor is clicked while attached and that the blob URL survives the click.
 *
 * Run: node tests/quick_quote_export_dom.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const jsPath = path.join(__dirname, '..', 'static', 'quick_quote.js');
const source = fs.readFileSync(jsPath, 'utf8');

const trace = { appended: [], clicks: [], removed: [], created: [], revoked: [] };

function makeAnchor() {
    const anchor = {
        tagName: 'A',
        href: '',
        download: '',
        rel: '',
        _attached: false,
        click() {
            trace.clicks.push({
                href: anchor.href,
                download: anchor.download,
                attachedAtClick: anchor._attached,
                revokedCountAtClick: trace.revoked.length,
            });
        },
        remove() {
            anchor._attached = false;
            trace.removed.push({ revokedCountAtRemove: trace.revoked.length });
        },
    };
    return anchor;
}

const documentStub = {
    addEventListener() {},
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement(tag) {
        if (tag !== 'a') return { tagName: String(tag).toUpperCase(), appendChild() {}, remove() {} };
        const anchor = makeAnchor();
        trace.created.push(anchor);
        return anchor;
    },
    body: {
        appendChild(node) {
            node._attached = true;
            trace.appended.push({ tagName: node.tagName, clicksBefore: trace.clicks.length });
            return node;
        },
        removeChild(node) {
            node._attached = false;
            return node;
        },
        contains: (node) => Boolean(node && node._attached),
    },
};

const urlStub = {
    createObjectURL: () => 'blob:quick-quote-test',
    revokeObjectURL: (url) => trace.revoked.push({ url, at: Date.now() }),
};

const windowStub = {};
// eslint-disable-next-line no-new-func
new Function('window', 'document', 'URL', source)(windowStub, documentStub, urlStub);

const QQ = windowStub.QQ_TEST;
assert.ok(QQ, 'QQ_TEST surface must be exported');
assert.strictEqual(typeof QQ.qqTriggerBlobDownload, 'function', 'qqTriggerBlobDownload must be exported');
assert.strictEqual(typeof QQ.qqBlobLooksLikeXlsx, 'function', 'qqBlobLooksLikeXlsx must be exported');

function blobStub(bytes) {
    const buffer = Uint8Array.from(bytes);
    return {
        size: buffer.length,
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        slice: (start, end) => ({
            arrayBuffer: async () => buffer.slice(start, end).buffer,
        }),
    };
}

const PK_BYTES = [0x50, 0x4b, 0x03, 0x04, 0x00, 0x01];

async function main() {
    /* 1. ZIP signature validation */
    assert.strictEqual(await QQ.qqBlobLooksLikeXlsx(blobStub(PK_BYTES)), true, 'PK blob must be accepted');
    assert.strictEqual(await QQ.qqBlobLooksLikeXlsx(blobStub([0x3c, 0x21, 0x44, 0x4f])), false, 'HTML blob must be rejected');
    assert.strictEqual(await QQ.qqBlobLooksLikeXlsx(blobStub([])), false, 'Empty blob must be rejected');

    /* 2. Download anchor lifecycle */
    QQ.qqTriggerBlobDownload(blobStub(PK_BYTES), 'From_BG_V2_draft.xlsx');

    assert.strictEqual(trace.appended.length, 1, 'anchor must be appended to the document once');
    assert.strictEqual(trace.clicks.length, 1, 'anchor must be clicked exactly once');
    assert.strictEqual(trace.appended[0].clicksBefore, 0, 'anchor must be appended before it is clicked');
    assert.strictEqual(trace.clicks[0].attachedAtClick, true, 'anchor must still be attached when clicked');
    assert.strictEqual(trace.clicks[0].download, 'From_BG_V2_draft.xlsx', 'download filename must be set');
    assert.strictEqual(trace.clicks[0].href, 'blob:quick-quote-test', 'anchor href must be the blob URL');
    assert.strictEqual(trace.removed.length, 1, 'anchor must be removed after the click');

    /* 3. Object URL must outlive the click, then be revoked asynchronously */
    assert.strictEqual(trace.clicks[0].revokedCountAtClick, 0, 'blob URL must not be revoked before the click');
    assert.strictEqual(trace.revoked.length, 0, 'blob URL must not be revoked synchronously after the click');

    await new Promise((resolve) => setTimeout(resolve, 1400));
    assert.strictEqual(trace.revoked.length, 1, 'blob URL must be revoked after the delay');
    assert.strictEqual(trace.revoked[0].url, 'blob:quick-quote-test', 'the created blob URL must be revoked');

    /* 4. Phase 1: request identity helpers exist and produce stable ids */
    assert.strictEqual(typeof QQ.qqNewRequestId, 'function', 'qqNewRequestId must be exported');
    assert.strictEqual(typeof QQ.qqEnsureRequestId, 'function', 'qqEnsureRequestId must be exported');
    assert.strictEqual(typeof QQ.qqRequestIdForResult, 'function', 'qqRequestIdForResult must be exported');
    assert.strictEqual(typeof QQ.qqBuildExportItems, 'function', 'qqBuildExportItems must be exported');

    const id1 = QQ.qqNewRequestId();
    const id2 = QQ.qqNewRequestId();
    assert.ok(id1 && id2, 'request ids must be non-empty');
    assert.notStrictEqual(id1, id2, 'each request must get a distinct request_id');

    /* 5. export_items keyed by request_id, multi-select uses display order */
    const candA = { product_id: 101, eligible: true, Compliance: 'Được bán' };
    const candB = { product_id: 205, eligible: true, Compliance: 'Được bán' };
    const candC = { product_id: 307, eligible: true, Compliance: 'Được bán' };
    const results = [
        {
            request_id: 'r5', request_order: 5, source_row: 12,
            requested_name: 'req5', requested_code: 'C5', requested_cas: 'CAS5',
            reason: 'SELECTED_LOWEST_OVERALL',
            selected_candidates: [candA, candB, candC],
            candidates: [candA, candB, candC],
        },
    ];
    const items = QQ.qqBuildExportItems(results);
    assert.strictEqual(items.length, 1, 'one export item per request');
    assert.strictEqual(items[0].request_id, 'r5');
    assert.strictEqual(items[0].request_order, 5);
    assert.strictEqual(items[0].source_row, 12);
    assert.strictEqual(items[0].requested_name, 'req5');
    assert.strictEqual(items[0].lines.length, 3, 'three lines for multi-select');
    assert.deepStrictEqual(
        items[0].lines.map((l) => l.product_id),
        [101, 205, 307],
        'selection_order must follow candidate display order, not click order',
    );
    assert.deepStrictEqual(
        items[0].lines.map((l) => l.selection_order),
        [1, 2, 3],
    );

    /* 6. distinct request_id for identical requests */
    const dupResults = [
        { request_id: 'd1', request_order: 1, source_row: null, requested_name: 'same',
          requested_code: '', requested_cas: '', reason: 'MANUAL_SELECTION_REQUIRED',
          selected_candidates: [], candidates: [candA] },
        { request_id: 'd2', request_order: 2, source_row: null, requested_name: 'same',
          requested_code: '', requested_cas: '', reason: 'MANUAL_SELECTION_REQUIRED',
          selected_candidates: [], candidates: [candA] },
    ];
    const dupItems = QQ.qqBuildExportItems(dupResults);
    assert.notStrictEqual(dupItems[0].request_id, dupItems[1].request_id, 'identical rows keep distinct ids');

    /* 7. Phase 2: Lifecycle calculation and 5-badge mutual exclusivity */
    assert.strictEqual(typeof QQ.qqGetRequestLifecycle, 'function', 'qqGetRequestLifecycle must be exported');
    assert.strictEqual(typeof QQ.qqSummarizeResults, 'function', 'qqSummarizeResults must be exported');

    const unresRow = { request_id: 'u1', reason: 'MISSING_IDENTIFIER', lifecycle: 'UNRESOLVED', reason_code: 'MISSING_IDENTIFIER', candidates: [] };
    const blockedCand = { product_id: 999, Compliance: 'CẤM NHẬP', eligible: false };
    const blockedRow = { request_id: 'b1', reason: 'MANUAL_REVIEW', lifecycle: 'BLOCKED', reason_code: 'COMPLIANCE_BLOCKED', candidates: [blockedCand] };
    const autoRow = { request_id: 's1', reason: 'SELECTED_LOWEST_OVERALL', lifecycle: 'SELECTED', reason_code: 'AUTO_SELECTED', selected_candidates: [candA], candidates: [candA] };
    const reviewRow = { request_id: 'rv1', reason: 'MANUAL_SELECTION_REQUIRED', lifecycle: 'REVIEW', reason_code: 'MANUAL_SELECTION_REQUIRED', selected_candidates: [], candidates: [candA, candB] };

    // Test under AUTO strategy
    QQ.qqSetStrategy('LOWEST_OVERALL');

    const lcUnres = QQ.qqGetRequestLifecycle(unresRow, 0);
    assert.strictEqual(lcUnres.lifecycle, 'UNRESOLVED');
    assert.strictEqual(lcUnres.reason_code, 'MISSING_IDENTIFIER');

    const lcBlocked = QQ.qqGetRequestLifecycle(blockedRow, 1);
    assert.strictEqual(lcBlocked.lifecycle, 'BLOCKED');
    assert.strictEqual(lcBlocked.reason_code, 'COMPLIANCE_BLOCKED');

    const lcAuto = QQ.qqGetRequestLifecycle(autoRow, 2);
    assert.strictEqual(lcAuto.lifecycle, 'SELECTED');
    assert.strictEqual(lcAuto.reason_code, 'AUTO_SELECTED');

    const lcReview = QQ.qqGetRequestLifecycle(reviewRow, 3);
    assert.strictEqual(lcReview.lifecycle, 'REVIEW');
    assert.strictEqual(lcReview.reason_code, 'MANUAL_SELECTION_REQUIRED');

    const summary = QQ.qqSummarizeResults([unresRow, blockedRow, autoRow, reviewRow]);
    assert.strictEqual(summary.total_requests, 4);
    assert.strictEqual(summary.unresolved, 1);
    assert.strictEqual(summary.blocked, 1);
    assert.strictEqual(summary.selected, 1);
    assert.strictEqual(summary.review, 1);
    assert.strictEqual(summary.exported, 0);
    assert.strictEqual(
        summary.selected + summary.review + summary.unresolved + summary.blocked + summary.exported,
        summary.total_requests,
        'the 5 lifecycle badge counts must sum exactly to total_requests',
    );

    /* 7b. Phase 4A: export_items placeholder classification/reason for rows
     * with no selection (order-preserving export must still send them). */
    const placeholderItems = QQ.qqBuildExportItems([unresRow, blockedRow, reviewRow]);
    assert.strictEqual(placeholderItems.length, 3, 'placeholder rows are still sent, one item each');
    assert.deepStrictEqual(placeholderItems.map((it) => it.lines.length), [0, 0, 0]);
    assert.strictEqual(placeholderItems[0].placeholder.classification, 'UNRESOLVED');
    assert.strictEqual(placeholderItems[0].placeholder.reason_code, 'MISSING_IDENTIFIER');
    assert.strictEqual(placeholderItems[1].placeholder.classification, 'BLOCKED');
    assert.strictEqual(placeholderItems[1].placeholder.reason_code, 'COMPLIANCE_BLOCKED');
    assert.strictEqual(placeholderItems[2].placeholder.classification, 'REVIEW');
    assert.strictEqual(placeholderItems[2].placeholder.reason_code, 'MANUAL_SELECTION_REQUIRED');
    /* selected row keeps lines, no placeholder key */
    const selectedItems = QQ.qqBuildExportItems([autoRow]);
    assert.strictEqual(selectedItems[0].lines.length, 1);
    assert.strictEqual(selectedItems[0].placeholder, undefined, 'selected rows must not carry a placeholder');

    // Test under MANUAL mode + manual pick
    QQ.qqSetStrategy('MANUAL');
    const lcManualBefore = QQ.qqGetRequestLifecycle(autoRow, 2);
    assert.strictEqual(lcManualBefore.lifecycle, 'REVIEW');
    assert.strictEqual(lcManualBefore.reason_code, 'MANUAL_SELECTION_REQUIRED');

    QQ.qqSetUserPick('s1', candA);
    const lcManualAfter = QQ.qqGetRequestLifecycle(autoRow, 2);
    assert.strictEqual(lcManualAfter.lifecycle, 'SELECTED');
    assert.strictEqual(lcManualAfter.reason_code, 'MANUALLY_SELECTED');

    console.log('quick_quote export DOM harness: OK');
}

main().catch((err) => {
    console.error(err && err.message ? err.message : err);
    process.exit(1);
});
