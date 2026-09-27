const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class ClassList {
    constructor() { this.values = new Set(); }
    toggle(name, enabled) { enabled ? this.values.add(name) : this.values.delete(name); }
}

const label = { classList: new ClassList() };
const checkbox = { checked: false, disabled: false, closest: () => label };
const hint = { textContent: '' };
const licenseWarnings = { children: [], style: {}, replaceChildren(...items) { this.children = items; } };
const elements = { inStockOnly: checkbox, inStockOnlyHint: hint, licenseWarnings };
const document = {
    getElementById: (id) => elements[id] || null,
    createElement: () => ({ className: '', textContent: '', children: [], appendChild(item) { this.children.push(item); } }),
    createTextNode: (text) => ({ textContent: String(text) }),
};
const window = { __multiMode: 'findcode', productSuggestions: { dismiss() {} } };

let query = 'catalog';
const values = { '#advCasInput': '50-00-0', '#multiInput': '' };
let statuses = [];
let resets = 0;
let displayed = [];
let requests = [];
let rafCallbacks = [];
window.requestAnimationFrame = (fn) => { rafCallbacks.push(fn); };
const chains = new Map();
function chain(selector) {
    if (!chains.has(selector)) {
        chains.set(selector, {
            val(value) {
                if (value !== undefined) { values[selector] = value; return this; }
                return selector === '#searchQuery' ? query : (values[selector] || '');
            },
            hide() { return this; }, show() { return this; },
            prop() { return this; }, is() { return false; },
            empty() { return this; }, html() { return this; }, attr() { return this; }, focus() { return this; },
        });
    }
    return chains.get(selector);
}
function $(selector) { return chain(selector); }
$.ajax = (config) => {
    const request = {
        config,
        aborted: false,
        abort() {
            this.aborted = true;
            config.error?.({ status: 0 }, 'abort');
            config.complete?.();
        },
    };
    requests.push(request);
    return request;
};

const context = vm.createContext({
    console, document, window, $, AJAX_LONG_TIMEOUT_MS: 180000,
    setTimeout: (fn) => { fn(); },
    requestAnimationFrame: (fn) => { rafCallbacks.push(fn); },
    resetLicenseBatchState() {},
    setBatchRunning() {}, countBatchItems: () => 1,
    renderLicenseTable() {},
    getAdvSelectedBrands: () => [], getAdvSelectedSizes: () => [],
    clearRowSelection() { resets += 1; },
    updateBrandFilterOptions() {}, updateSizeFilterOptions() {},
    displayResults(rows) { displayed = rows.slice(); },
    setOperationStatus(message, kind) { statuses.push({ message, kind }); },
    formatAjaxError() { return 'failure'; },
});

const source = fs.readFileSync('static/script.js', 'utf8');
const start = source.indexOf('function cancelProductSearch(');
const end = source.indexOf('function updateBrandFilterOptions(');
assert(start >= 0 && end > start);
const advancedStart = source.indexOf('function runAdvancedSearch(');
const findStart = source.indexOf('function runFindCodeBatch(');
const findEnd = source.indexOf('$(document).ready(');
const advancedPanelStart = source.indexOf('function openAdvancedPanel(');
const advancedPanelEnd = source.indexOf('function getAdvSelectedBrands(');
vm.runInContext(`
let searchResults = [];
let resultSource = 'SEARCH';
let licenseBatchRows = [];
let licenseVisibleRows = [];
const selectedLicenseRowKeys = new Set();
let advOptionsData = { brands: [], size_pairs: [] };
let activeProductSearchRequest = null;
let productSearchGeneration = 0;
const activeAlternateSearchRequests = new Set();
let alternateSearchGeneration = 0;
${source.slice(start, end)}
${source.slice(advancedPanelStart, advancedPanelEnd)}
${source.slice(advancedStart, findStart)}
${source.slice(findStart, findEnd)}
globalThis.api = { searchProducts, cancelProductSearch, handleInStockOnlyChange, suspendProductSearchForOtherMode,
  runAdvancedSearch, runFindCodeBatch, runLicenseBatch, closeAdvancedPanel, closeMultiModePanel,
  results: () => searchResults, licenseRows: () => licenseBatchRows,
  generation: () => productSearchGeneration };
`, context);
const api = context.api;

// Default-off Product Search includes an explicit, validated API flag.
api.searchProducts();
assert.equal(requests.length, 1);
assert(requests[0].config.url.includes('in_stock_only=0'));
assert.equal(window.__multiMode, null);
assert.equal(checkbox.disabled, false);

// A fast toggle aborts the old request, clears old selection/results, and
// prevents the old response from winning even if its callback arrives late.
checkbox.checked = true;
api.handleInStockOnlyChange();
assert.equal(requests.length, 2);
assert.equal(requests[0].aborted, true);
assert(requests[1].config.url.includes('in_stock_only=1'));
requests[0].config.success({ results: [{ Name: 'STALE' }] });
assert.equal(JSON.stringify(api.results()), '[]');
requests[1].config.success({ results: [{ Name: 'LATEST' }] });
assert.equal(JSON.stringify(api.results()), JSON.stringify([{ Name: 'LATEST' }]));
assert.deepEqual(displayed, [{ Name: 'LATEST' }]);
assert(resets >= 2);

// Entering another mode cancels Product Search; abort is not shown as an error
// and a late response cannot overwrite Find Code / Advanced Search state.
api.searchProducts();
const pending = requests.at(-1);
const errorsBefore = statuses.filter((item) => item.kind === 'error').length;
api.suspendProductSearchForOtherMode();
pending.config.success({ results: [{ Name: 'LATE MODE OVERWRITE' }] });
assert.notEqual(JSON.stringify(api.results()), JSON.stringify([{ Name: 'LATE MODE OVERWRITE' }]));
assert.equal(statuses.filter((item) => item.kind === 'error').length, errorsBefore);
assert.equal(checkbox.disabled, true);
assert(hint.textContent.includes('Không áp dụng trong chế độ hiện tại'));

// Reverse race: an old Advanced callback cannot overwrite a newer filtered
// Product Search response, and its abort is silent.
api.runAdvancedSearch();
const advanced = requests.at(-1);
query = 'new product';
checkbox.checked = true;
api.searchProducts();
const afterAdvancedProduct = requests.at(-1);
afterAdvancedProduct.config.success({ results: [{ Name: 'PRODUCT', Stock_Options: [{ Stock_Quantity: 1 }] }] });
advanced.config.success({ results: [{ Name: 'STALE ADVANCED', Stock_Options: [] }] });
assert.equal(api.results()[0].Name, 'PRODUCT');
assert.equal(advanced.aborted, true);

// Completed Advanced results are cleared when Cancel returns to Product
// Search, so a still-checked stock filter can never label unfiltered rows.
api.runAdvancedSearch();
const completedAdvanced = requests.at(-1);
completedAdvanced.config.success({ results: [{ Name: 'COMPLETED ADVANCED', Stock_Options: [] }] });
assert.equal(api.results()[0].Name, 'COMPLETED ADVANCED');
api.closeAdvancedPanel();
assert.equal(JSON.stringify(api.results()), '[]');
assert.equal(checkbox.checked, true);
assert.equal(checkbox.disabled, false);

// A delayed Find Code send is invalidated before it issues HTTP; a sent Find
// Code request is aborted and cannot overwrite a later Product Search either.
window.__multiMode = 'findcode';
const beforeScheduled = requests.length;
api.runFindCodeBatch('CODE-1');
assert.equal(rafCallbacks.length, 1);
query = 'wins before scheduled find';
api.searchProducts();
rafCallbacks.shift()();
assert.equal(requests.length, beforeScheduled + 1); // Product Search only.

window.__multiMode = 'findcode';
api.runFindCodeBatch('CODE-2');
rafCallbacks.shift()();
const findRequest = requests.at(-1);
assert.equal(findRequest.config.url, '/find_code_batch');
query = 'wins after sent find';
api.searchProducts();
const afterFindProduct = requests.at(-1);
afterFindProduct.config.success({ results: [{ Name: 'PRODUCT AFTER FIND', Stock_Options: [{ Stock_Quantity: 1 }] }] });
findRequest.config.success({ results: [{ Name: 'STALE FIND', Stock_Options: [] }] });
assert.equal(api.results()[0].Name, 'PRODUCT AFTER FIND');
assert.equal(findRequest.aborted, true);

// Completed Find Code results follow the same return policy.
window.__multiMode = 'findcode';
api.runFindCodeBatch('CODE-3');
rafCallbacks.shift()();
const completedFind = requests.at(-1);
completedFind.config.success({ results: [{ Name: 'COMPLETED FIND', Stock_Options: [] }] });
assert.equal(api.results()[0].Name, 'COMPLETED FIND');
api.closeMultiModePanel();
assert.equal(JSON.stringify(api.results()), '[]');
assert.equal(checkbox.checked, true);
assert.equal(checkbox.disabled, false);

// Check License participates in the same generation lifecycle: A is aborted
// by Product Search, B wins, and a forced late A callback cannot overwrite B.
window.__multiMode = 'license';
api.runLicenseBatch('CAS-A');
const licenseA = requests.at(-1);
query = 'product between licenses';
api.searchProducts();
api.suspendProductSearchForOtherMode();
window.__multiMode = 'license';
api.runLicenseBatch('CAS-B');
const licenseB = requests.at(-1);
licenseB.config.success({ results: [{ Cas: 'CAS-B' }] });
licenseA.config.success({ results: [{ Cas: 'CAS-A' }] });
assert.equal(api.licenseRows()[0].Cas, 'CAS-B');
assert.equal(licenseA.aborted, true);

// Empty-query toggle cancels a pending request, clears previously rendered
// rows, and never issues a blank-query request.
query = 'pending before clear';
api.searchProducts();
const pendingBeforeClear = requests.at(-1);
query = '   ';
const countBeforeEmpty = requests.length;
api.handleInStockOnlyChange();
assert.equal(requests.length, countBeforeEmpty);
assert.equal(pendingBeforeClear.aborted, true);
assert.equal(JSON.stringify(api.results()), '[]');
pendingBeforeClear.config.success({ results: [{ Name: 'STALE EMPTY' }] });
assert.equal(JSON.stringify(api.results()), '[]');
assert(statuses.at(-1).message.includes('Nhập từ khóa'));

console.log('Stock-only DOM: bidirectional races, completed-mode reset, Check License lifecycle, empty guard PASS');
