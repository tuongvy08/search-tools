const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Minimal DOM for the production stock renderer. Any HTML insertion fails.
class Element {
    constructor(tag) {
        this.tag = tag;
        this.children = [];
        this.textContent = '';
        this.classList = { add() {} };
    }
    appendChild(child) { this.children.push(child); }
    set innerHTML(_) { throw new Error('Stock notes must be rendered as plain text'); }
}
const context = vm.createContext({ document: { createElement: tag => new Element(tag) } });
const script = fs.readFileSync('static/script.js', 'utf8');
vm.runInContext(script.slice(script.indexOf('function stockText('), script.indexOf('function badgeForCompliance(')), context);

function render(option) {
    const cell = new Element('td');
    context.setStockCell({ insertCell: () => cell }, { Stock_Options: [option] });
    return cell.children[0].children[0];
}
const note = 'Kho A\nHàng mẫu <img src=x onerror=alert(1)>';
const base = { Stock_Quantity: 2, Stock_State: 'missing', Stock_Match: 'exact_code' };
const line = render({ ...base, Stock_Note: note });
const notes = line.children.filter(child => child.className === 'stock-note');
assert.equal(notes.length, 1);
assert.equal(notes[0].textContent, `Ghi chú: ${note}`);
assert.equal(notes[0].children.length, 0);
for (const option of [base, { ...base, Stock_Note: '' }]) {
    assert.equal(render(option).children.filter(child => child.className === 'stock-note').length, 0);
}
console.log('Stock note DOM tests passed (plain text, multiline, empty/hidden).');
