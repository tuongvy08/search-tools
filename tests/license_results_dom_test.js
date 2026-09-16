/* Executable DOM/JS regression coverage for Phase 6D5.
 * Run: node tests/license_results_dom_test.js
 * This deliberately uses a tiny DOM harness so it runs without a browser
 * dependency and exercises the same render/filter/selection functions.
 */
const fs = require('fs');

class ClassList {
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
}

class Element {
  constructor(tagName, owner) {
    this.tagName = tagName.toUpperCase();
    this.owner = owner;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.classList = new ClassList();
    this.style = {};
    this.value = '';
    this.checked = false;
    this.indeterminate = false;
    this.disabled = false;
    this.textContent = '';
    this.id = '';
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentNode = null; });
    children.forEach((child) => { child.parentNode = this; });
    this.children = children;
  }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type, event = {}) {
    const payload = { target: this, ...event };
    (this.listeners[type] || []).forEach((listener) => listener(payload));
    return payload;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'id') this.id = String(value); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); }
  select() { this.owner.selectedText = this.value; }
  insertCell() { const cell = new Element('td', this.owner); this.appendChild(cell); return cell; }
  insertRow() { const row = new Element('tr', this.owner); this.appendChild(row); return row; }
  createTHead() { this.tHead = new Element('thead', this.owner); this.appendChild(this.tHead); return this.tHead; }
  createTBody() { this.tBodies = [new Element('tbody', this.owner)]; this.appendChild(this.tBodies[0]); return this.tBodies[0]; }
  get rows() { return this.children.filter((child) => child.tagName === 'TR'); }
  get tBodies() { return this._tBodies || []; }
  set tBodies(value) { this._tBodies = value; }
}

class Document {
  constructor() {
    this.elements = [];
    this.body = new Element('body', this);
    this.execCommandResult = true;
    this.selectedText = '';
  }
  createElement(tagName) { const element = new Element(tagName, this); this.elements.push(element); return element; }
  createTextNode(text) { const element = new Element('#text', this); element.textContent = String(text); return element; }
  getElementById(id) {
    let found = null;
    const visit = (node) => {
      if (found) return;
      if (node.id === id) { found = node; return; }
      node.children.forEach(visit);
    };
    visit(this.body);
    return found;
  }
  execCommand(command) { return command === 'copy' && this.execCommandResult; }
  querySelectorAll(selector) {
    if (selector === '#licenseWarnings input[name="license_status"]:checked') {
      return this.elements.filter((element) => element.parentNode && element.tagName === 'INPUT' && element.name === 'license_status' && element.checked);
    }
    return [];
  }
}

const documentTarget = new Document();
const licenseWarnings = documentTarget.createElement('div');
licenseWarnings.id = 'licenseWarnings';
const operationStatus = documentTarget.createElement('div');
operationStatus.id = 'operationStatus';
documentTarget.body.appendChild(licenseWarnings);
documentTarget.body.appendChild(operationStatus);

global.document = documentTarget;
global.navigator = {};
global.window = {};
let lastStatusHtml = '';
global.TeamPermissions = {
  can: (key) => key === 'COPY',
  field: (key) => key === 'Cas' || key === 'Compliance' || key === 'Compliance_Note',
};
global.$ = () => ({
  removeClass() { return this; }, addClass() { return this; }, hide() { return this; },
  empty() { return this; }, show() { return this; }, html(value) { if (value !== undefined) lastStatusHtml = value; return this; }, ready() { return this; },
});

eval(`${fs.readFileSync('static/script.js', 'utf8')}
globalThis.__phase6d5 = {
  normalizeLicenseFilterText, filterLicenseRows, renderLicenseTable, renderLicenseTableRows,
  licenseTsv, licenseTsvCell, copyTextWithFallback, licenseSelectedRows, copySelectedLicenseRows,
  emptyStatus: LICENSE_EMPTY_STATUS,
  setRows(rows) { licenseBatchRows = rows; licenseVisibleRows = rows.slice(); selectedLicenseRowKeys.clear(); },
  setVisibleRows(rows) { licenseVisibleRows = rows; },
  clearSelection() { selectedLicenseRowKeys.clear(); },
  selectionSize() { return selectedLicenseRowKeys.size; },
};`);
const phase6d5 = globalThis.__phase6d5;

function attachedElements(root = documentTarget.body) {
  const output = [];
  const visit = (node) => { output.push(node); node.children.forEach(visit); };
  visit(root);
  return output;
}

const rows = [
  { Cas: '50-00-0', Compliance_Status: 'Cấm nhập', Compliance_Note: 'Giấy phép Đã cấp\nA\tB', Compliance_Css: 'warning-cam-nhap' },
  { Cas: '64-17-5', Compliance_Status: '', Compliance_Note: 'Không có ghi chú' },
  { Cas: '50-00-0', Compliance_Status: 'Phụ lục II', Compliance_Note: 'Theo dõi hồ sơ' },
];

if (phase6d5.normalizeLicenseFilterText('  GiẤy   PHÉP ĐÃ  ') !== 'giay phep da') process.exit(1);
if (phase6d5.filterLicenseRows(rows, new Set(['Cấm nhập']), 'giay phep').length !== 1) process.exit(2);
if (phase6d5.filterLicenseRows(rows, new Set([phase6d5.emptyStatus]), '').length !== 1) process.exit(3);
if (phase6d5.filterLicenseRows(rows, new Set(['Cấm nhập']), 'khong co').length !== 0) process.exit(4);

const batchRows = rows.map((item, index) => {
  const copy = Object.assign({}, item);
  Object.defineProperty(copy, '__licenseRowKey', { value: `license-${index}` });
  return copy;
});
phase6d5.setRows(batchRows);
phase6d5.renderLicenseTable();

const selectVisible = documentTarget.getElementById('licenseSelectVisible');
selectVisible.dispatch('click');
if (phase6d5.selectionSize() !== 3) process.exit(5);
if (documentTarget.getElementById('licenseBatchCount').textContent !== 'Đang hiển thị 3/3 · đã chọn 3') process.exit(6);

const headerRow = documentTarget.getElementById('licenseResultsTable').tHead.rows[0];
if (headerRow.children.some((cell) => cell.tagName !== 'TH' || cell.scope !== 'col')) process.exit(18);
if (documentTarget.getElementById('licenseSelectAllRows').attributes['aria-label'] !== 'Chọn tất cả dòng đang hiển thị') process.exit(19);

phase6d5.setVisibleRows(phase6d5.filterLicenseRows(batchRows, new Set(['Cấm nhập']), 'giay phep'));
phase6d5.renderLicenseTableRows();
if (phase6d5.selectionSize() !== 3 || documentTarget.getElementById('licenseBatchCount').textContent !== 'Đang hiển thị 1/3 · đã chọn 3') process.exit(7);

phase6d5.setVisibleRows(batchRows.slice());
phase6d5.renderLicenseTableRows();
const visibleCheckbox = documentTarget.getElementById('licenseResultsTable').tBodies[0].rows[0].children[0].children[0];
visibleCheckbox.checked = false;
visibleCheckbox.dispatch('change');
const headerCheckbox = documentTarget.getElementById('licenseSelectAllRows');
if (!headerCheckbox.indeterminate || phase6d5.selectionSize() !== 2) process.exit(8);

phase6d5.renderLicenseTableRows();
if (phase6d5.licenseTsv(phase6d5.licenseSelectedRows()) !== 'CAS\tTình trạng quản lý\tGhi chú quản lý\r\n64-17-5\t\tKhông có ghi chú\r\n50-00-0\tPhụ lục II\tTheo dõi hồ sơ') process.exit(9);
if (phase6d5.licenseTsvCell('A\tB\nC') !== '"A\tB\nC"') process.exit(10);

const statusBadge = documentTarget.getElementById('licenseResultsTable').tBodies[0].rows[0].children[2].children[0];
if (statusBadge.textContent.includes('<script>')) process.exit(11);

const xssRows = [{
  Cas: '<script>alert("cas")</script>\"><img src=x onerror=alert(1)>',
  Compliance_Status: '<img src=x onerror="alert(2)">',
  Compliance_Note: '<script>alert("note")</script>\"><img src=x onerror=alert(3)>',
}];
phase6d5.setRows(xssRows.map((item, index) => {
  const copy = Object.assign({}, item);
  Object.defineProperty(copy, '__licenseRowKey', { value: `license-xss-${index}` });
  return copy;
}));
phase6d5.renderLicenseTable();
const xssTable = documentTarget.getElementById('licenseResultsTable');
const xssCells = xssTable.tBodies[0].rows[0].children;
if (attachedElements().some((node) => node.tagName === 'SCRIPT' || node.tagName === 'IMG')) process.exit(20);
if (!xssCells.some((cell) => cell.textContent.includes('<script>') || cell.textContent.includes('<img'))) process.exit(21);
if (attachedElements().some((node) => Object.keys(node.attributes).some((name) => name.toLowerCase().startsWith('on')))) process.exit(22);
if (!phase6d5.licenseTsv(xssRows).includes('"<script>alert(""cas"")</script>')) process.exit(23);

const nonEmptyRows = [{ Cas: '1-1-1', Compliance_Status: 'Cấm nhập', Compliance_Note: 'note' }];
phase6d5.setRows(nonEmptyRows.map((item, index) => {
  const copy = Object.assign({}, item);
  Object.defineProperty(copy, '__licenseRowKey', { value: `license-nonempty-${index}` });
  return copy;
}));
phase6d5.renderLicenseTable();
if (attachedElements().some((node) => node.textContent === 'Không có tình trạng')) process.exit(24);

global.TeamPermissions = { can: () => true, field: (key) => key === 'Compliance' };
phase6d5.renderLicenseTable();
if (documentTarget.getElementById('licenseNoteFilter')) process.exit(12);
if (phase6d5.licenseTsv([{ Compliance_Status: 'Cấm nhập', Compliance_Note: 'hidden' }]) !== 'Tình trạng quản lý\r\nCấm nhập') process.exit(13);

const copyOffRows = [{ Cas: '2-2-2', Compliance_Status: 'Cấm nhập', Compliance_Note: 'secret' }];
phase6d5.setRows(copyOffRows);
global.TeamPermissions = { can: () => false, field: (key) => key === 'Cas' || key === 'Compliance' || key === 'Compliance_Note' };
phase6d5.renderLicenseTable();
if (documentTarget.getElementById('licenseSelectAllRows') || documentTarget.getElementById('licenseSelectVisible') || documentTarget.getElementById('licenseClearSelection') || documentTarget.getElementById('licenseCopySelected') || documentTarget.getElementById('licenseBatchCount')) process.exit(25);
if (documentTarget.getElementById('licenseResultsTable').tHead.rows[0].children.some((cell) => cell.tagName === 'TH' && cell.classList.contains('license-col-select'))) process.exit(26);
if (documentTarget.getElementById('licenseResultsTable').tBodies[0].rows[0].children.some((cell) => cell.classList.contains('license-col-select'))) process.exit(27);
if (phase6d5.selectionSize() !== 0) process.exit(28);
global.TeamPermissions = { can: () => true, field: (key) => key === 'Cas' || key === 'Compliance' || key === 'Compliance_Note' };

(async () => {
  phase6d5.clearSelection();
  await phase6d5.copySelectedLicenseRows();
  if (!lastStatusHtml.includes('Chọn ít nhất một dòng kết quả Check license')) process.exit(14);

  delete global.navigator.clipboard;
  documentTarget.execCommandResult = true;
  phase6d5.copyTextWithFallback('fallback').then(() => {
    if (documentTarget.selectedText !== 'fallback') process.exit(15);
    documentTarget.execCommandResult = false;
    return phase6d5.copyTextWithFallback('nope').then(() => process.exit(16), () => process.exit(0));
  }).catch(() => process.exit(17));
})();
