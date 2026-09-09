const fs = require('fs');

class EventTarget {
  constructor() { this.listeners = {}; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type, event = {}) { for (const listener of this.listeners[type] || []) listener(event); return event; }
}

class Form extends EventTarget {
  constructor() {
    super();
    this.dataset = {};
    this.attributes = {};
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
}

const form = new Form();
const windowTarget = new EventTarget();
global.document = { querySelectorAll: () => [form] };
global.window = windowTarget;

eval(fs.readFileSync('static/admin_ux.js', 'utf8'));

const first = form.dispatch('submit', { defaultPrevented: false, preventDefault() { this.prevented = true; } });
if (first.prevented || form.dataset.adminSubmitting !== 'true' || form.attributes['aria-busy'] !== 'true') process.exit(1);

const second = form.dispatch('submit', { defaultPrevented: false, preventDefault() { this.prevented = true; } });
if (!second.prevented) process.exit(2);

windowTarget.dispatch('pageshow');
if (form.dataset.adminSubmitting || form.attributes['aria-busy']) process.exit(3);

const cancelled = form.dispatch('submit', { defaultPrevented: true, preventDefault() { this.prevented = true; } });
if (cancelled.prevented || form.dataset.adminSubmitting) process.exit(4);
