const assert = require('assert');
const fs = require('fs');
class Target {
  constructor() { this.listeners = {}; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name, event = {}) { (this.listeners[name] || []).forEach(fn => fn(event)); }
}
const form = new Target();
const status = {textContent: ''};
form.dataset = {submitMessage: 'Đang lưu…'};
form.attributes = {};
form.setAttribute = (key, value) => { form.attributes[key] = value; };
form.removeAttribute = key => { delete form.attributes[key]; };
form.querySelector = () => status;
global.document = {
  querySelectorAll: selector => selector === '[data-stock-submit-status]' ? [status] : [form],
  querySelector: () => null,
};
global.window = new Target();
eval(fs.readFileSync('static/admin_ux.js', 'utf8'));
eval(fs.readFileSync('static/admin_stock.js', 'utf8'));
const submit = () => ({defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }});
let event = submit();
form.dispatch('submit', event);
assert(!event.defaultPrevented);
assert.equal(status.textContent, 'Đang lưu…');
assert.equal(form.attributes['aria-busy'], 'true');
event = submit();
form.dispatch('submit', event);
assert(event.defaultPrevented);
window.dispatch('pageshow');
assert.equal(status.textContent, '');
assert.equal(form.dataset.adminSubmitting, undefined);
assert.equal(form.attributes['aria-busy'], undefined);
form.dispatch('submit', {defaultPrevented: true});
assert.equal(status.textContent, '');
console.log('stock quick edit: loading, duplicate submit, history reset, cancelled submit PASS');
