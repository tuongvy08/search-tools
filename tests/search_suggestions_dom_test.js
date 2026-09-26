const assert = require('assert');
class Element {
    constructor() { this.listeners = {}; this.attributes = {}; this.children = []; this.value = ''; this.textContent = ''; this.hidden = true; }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    dispatch(name, event = {}) { event.preventDefault ||= () => { event.prevented = true; }; for (const fn of this.listeners[name] || []) fn(event); return event; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    appendChild(node) { this.children.push(node); }
    replaceChildren() { this.children = []; }
    contains(node) { return this.children.includes(node); }
    scrollIntoView() {}
    set innerHTML(_) { throw new Error('Unsafe HTML'); }
}
const input = new Element(), list = new Element(), status = new Element(), document = new Element();
document.getElementById = id => id === 'searchSuggestions' ? list : status;
document.createElement = () => new Element();
document.activeElement = input;
global.document = document;
let nextTimer = 1, timers = new Map(), requests = [], searches = 0;
global.setTimeout = (fn, delay) => { assert.equal(delay, 300); const id = nextTimer++; timers.set(id, fn); return id; };
global.clearTimeout = id => timers.delete(id);
global.fetch = (url, options) => new Promise(resolve => requests.push({url,options,resolve}));
const tick = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); };
const flush = () => new Promise(resolve => setImmediate(resolve));
const respond = (request, values) => request.resolve({ok:true, redirected:false, json:async () => ({suggestions:values.map(value => ({value,label:value}))})});
const type = value => { input.value = value; input.dispatch('input'); };
const {initProductSuggestions} = require('../static/search_suggestions.js');
const ui = initProductSuggestions(input, () => searches++);
(async () => {
    type('ab'); tick(); assert.equal(requests.length,0);
    type('first'); type('latest'); tick(); assert.equal(requests.length,1);
    assert(requests[0].url.startsWith('/search/suggestions?'));
    type('newest'); tick(); assert(requests[0].options.signal.aborted);
    respond(requests[1],['<img src=x onerror=alert(1)>']); await flush();
    respond(requests[0],['STALE']); await flush();
    assert.equal(list.children[0].textContent,'<img src=x onerror=alert(1)>');
    assert.equal(input.attributes['aria-expanded'],'true');
    input.dispatch('keydown',{key:'ArrowDown'}); assert(input.attributes['aria-activedescendant']);
    input.dispatch('keydown',{key:'Enter'}); assert.equal(searches,1); assert.equal(input.value,'<img src=x onerror=alert(1)>');
    assert(list.hidden); assert.equal(input.attributes['aria-expanded'],'false');
    for (const action of ['clear','Escape','blur','outside','select']) {
        document.activeElement = input;
        type('pending'); tick(); const req = requests.at(-1);
        if(action==='clear') type('');
        else if(action==='Escape') input.dispatch('keydown',{key:'Escape'});
        else if(action==='blur') { document.activeElement = null; input.dispatch('blur'); }
        else if(action==='outside') document.dispatch('pointerdown',{target:new Element()});
        else ui.dismiss();
        respond(req,['LATE']); await flush(); assert(list.hidden); assert.equal(list.children.length,0);
    }
    document.activeElement=input;
    input.dispatch('compositionstart'); type('tiếng'); tick(); const count=requests.length;
    input.dispatch('keydown',{key:'Enter',isComposing:true}); assert.equal(searches,1);
    input.dispatch('compositionend'); tick(); assert.equal(requests.length,count+1);
    respond(requests.at(-1),['Tiếng Việt']); await flush();
    input.dispatch('keydown',{key:'ArrowUp'}); assert.equal(list.children[0].attributes['aria-selected'],'true');
    list.children[0].dispatch('pointerdown', {pointerType:'touch'}); assert.equal(searches,1);
    assert(list.children[0].dispatch('mousedown', {button:0}).prevented);
    list.children[0].dispatch('click', {button:0}); assert.equal(searches,2); assert.equal(input.value,'Tiếng Việt');
    input.dispatch('keydown',{key:'Enter'}); assert.equal(searches,3);
    type('keyboard'); tick(); respond(requests.at(-1), ['First', 'Middle', 'Last']); await flush();
    input.dispatch('keydown',{key:'ArrowUp'}); assert.equal(list.children[2].attributes['aria-selected'],'true');
    input.dispatch('keydown',{key:'ArrowDown'}); assert.equal(list.children[0].attributes['aria-selected'],'true');
    input.dispatch('keydown',{key:'ArrowUp'}); assert.equal(list.children[2].attributes['aria-selected'],'true');
    input.dispatch('keydown',{key:'Enter'}); assert.equal(searches,4); assert.equal(input.value,'Last');
    type('scrollable'); tick(); respond(requests.at(-1), Array.from({length:10}, (_, i) => `Option ${i}`)); await flush();
    const scrollOption = list.children[0];
    scrollOption.dispatch('pointerdown', {pointerType:'touch'});
    scrollOption.dispatch('pointermove', {pointerType:'touch'});
    scrollOption.dispatch('pointercancel', {pointerType:'touch'});
    assert.equal(searches,4); assert.equal(list.children.length,10); assert(!list.hidden);
    scrollOption.dispatch('click', {button:2}); assert.equal(searches,4);
    const tappedOption = list.children[9];
    tappedOption.dispatch('pointerdown', {pointerType:'touch'});
    tappedOption.dispatch('pointerup', {pointerType:'touch'});
    assert.equal(searches,4);
    assert(tappedOption.dispatch('mousedown', {button:0}).prevented);
    tappedOption.dispatch('click', {button:0}); assert.equal(searches,5); assert.equal(input.value,'Option 9');
    type('failure'); tick(); requests.at(-1).resolve({ok:false}); await flush(); assert(status.textContent.includes('không khả dụng'));
    console.log('Suggestions DOM: debounce, abort+late response, clear/escape/blur/outside/select, IME, keyboard, text-only PASS');
})().catch(error => { console.error(error); process.exitCode=1; });
