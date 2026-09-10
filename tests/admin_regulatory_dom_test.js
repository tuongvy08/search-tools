const fs = require('fs');

class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type, event = {}) { for (const listener of this.listeners[type] || []) listener(event); }
}

class ClassList {
  constructor(...names) { this.names = new Set(names); }
  add(name) { this.names.add(name); }
  remove(name) { this.names.delete(name); }
  has(name) { return this.names.has(name); }
}

function previewNode(...classes) {
  const properties = {};
  return {
    classList: new ClassList(...classes),
    style: { setProperty(name, value) { properties[name] = value; }, properties },
  };
}

const badge = previewNode('compliance-badge', 'regulatory-color-red');
const row = previewNode('regulatory-row', 'regulatory-color-red');
row.querySelector = (selector) => selector === '.compliance-badge' ? badge : null;

const save = { disabled: true };
const customChoice = { checked: false, value: '#64748B', dataset: {} };
const customInput = new EventTarget();
customInput.value = '#ffffff';
const output = { textContent: '' };
const form = new EventTarget();
form.closest = () => row;
form.querySelector = (selector) => ({
  '[data-save-color]': save,
  '[data-custom-color-choice]': customChoice,
  '[data-custom-color-input]': customInput,
  '[data-color-hex]': output,
}[selector] || null);

const documentTarget = new EventTarget();
documentTarget.querySelectorAll = () => [form];
documentTarget.querySelector = () => null;
global.document = documentTarget;

eval(fs.readFileSync('static/admin_regulatory.js', 'utf8'));
documentTarget.dispatch('DOMContentLoaded');

const swatch = {
  value: '#1D4ED8',
  dataset: { colorSeed: '', colorBg: '#D6DFF8', colorFg: '#111827' },
  matches: (selector) => selector === 'input[name="color_hex"]',
};
form.dispatch('change', { target: swatch });
if (!row.classList.has('regulatory-color-custom') || row.classList.has('regulatory-color-red')) process.exit(1);
if (row.style.properties['--reg-bg'] !== '#D6DFF8' || badge.style.properties['--reg-fg'] !== '#111827') process.exit(2);
if (save.disabled || output.textContent !== '#1D4ED8') process.exit(3);

customInput.dispatch('input');
if (!customChoice.checked || customChoice.value !== '#FFFFFF') process.exit(4);
if (row.style.properties['--reg-bg'] !== '#FFFFFF' || row.style.properties['--reg-fg'] !== '#111827') process.exit(5);
if (output.textContent !== '#FFFFFF') process.exit(6);
