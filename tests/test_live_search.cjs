// Run with: node tests/test_live_search.cjs (no dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../fcard/static/search.js'), 'utf8');

function setup() {
  let active;
  class Element {
    constructor(tag, className = '') {
      this.tag = tag;
      this.className = className;
      this.children = [];
      this.attributes = new Map();
      this.listeners = {};
      this.hidden = false;
      this.text = '';
    }
    set textContent(text) { this.text = String(text); this.children = []; }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
    set innerHTML(_) { throw new Error('Never insert search data as HTML'); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; this.text = ''; }
    setAttribute(name, value) { this.attributes.set(name, value); }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    emit(type, options = {}) {
      const event = { target: this, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...options };
      for (const listener of this.listeners[type] || []) listener(event);
      return event;
    }
    focus() { active = this; }
    contains(node) { return this === node || this.children.some((c) => c.contains(node)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0]; }
    querySelectorAll(selector) {
      const matches = (node) => selector.startsWith('.')
        ? node.className.split(' ').includes(selector.slice(1)) : node.tag === selector;
      return this.children.flatMap((child) => [
        ...(matches(child) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    }
  }
  const form = new Element('form');
  const input = new Element('input');
  input.value = '';
  const panel = new Element('div', 'live-search-panel');
  panel.hidden = true;
  const status = new Element('p', 'live-search-status');
  const list = new Element('ul', 'live-search-list');
  const all = new Element('a', 'live-search-all');
  panel.append(status, list, all);
  form.append(input, panel);
  form.elements = { q: input };
  form.action = 'http://localhost/search';
  form.dataset = { liveUrl: '/search/live' };
  const doc = new Element('document');
  doc.querySelectorAll = () => [form];
  doc.createElement = (tag) => new Element(tag);
  let timerId = 0;
  const timers = new Map();
  const requests = [];
  vm.runInNewContext(source, {
    document: doc, window: { location: { href: 'http://localhost/' } },
    URL, AbortController,
    setTimeout(callback, delay) {
      assert.equal(delay, 250);
      timers.set(++timerId, callback);
      return timerId;
    },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
    },
  });
  return {
    form, input, panel, status, list, all, doc, timers, requests,
    get active() { return active; },
    type(query, options) { input.value = query; input.emit('input', options); },
    flush() {
      const callbacks = [...timers.values()];
      timers.clear();
      callbacks.forEach((callback) => callback());
    },
    async respond(index, data, ok = true) {
      requests[index].resolve({ ok, json: async () => data });
      await new Promise(setImmediate);
    },
  };
}

const result = (front, back = 'bread') => ({
  id: 1, deck: 'German', front, back, match: null, url: '/search?q=brot#card-1',
});
const payload = (...results) => ({ searchable: true, results, more: false });

async function test(name, run) {
  await run();
  console.log('ok:', name);
}

(async () => {
  await test('typing is debounced and queries are encoded, not interpolated', async () => {
    const s = setup();
    s.type('b'); s.type('br'); s.type('BÜC & K');
    assert.equal(s.requests.length, 0);
    assert.equal(s.timers.size, 1);
    s.flush();
    assert.equal(s.requests.length, 1);
    assert.equal(s.requests[0].url.searchParams.get('q'), 'BÜC & K');
    assert.equal(s.requests[0].options.credentials, 'same-origin');
    await s.respond(0, payload(result('Bückerei')));
    assert.equal(s.panel.hidden, false);
    assert.equal(s.list.children.length, 1);
    assert.equal(new URL(s.all.href).searchParams.get('q'), 'BÜC & K');
  });

  await test('stale responses are ignored even before the next debounce fires', async () => {
    const s = setup();
    s.type('old'); s.flush();
    s.type('new');
    assert.equal(s.requests[0].options.signal.aborted, true);
    await s.respond(0, payload(result('OLD'))); // Simulate a server ignoring cancellation.
    assert.equal(s.panel.hidden, true);
    assert.equal(s.list.children.length, 0);
    s.flush();
    await s.respond(1, payload(result('NEW')));
    assert.match(s.list.textContent, /NEW/);
    assert.doesNotMatch(s.list.textContent, /OLD/);
  });

  await test('out-of-order responses cannot replace newer results', async () => {
    const s = setup();
    s.type('old'); s.flush();
    s.type('new'); s.flush();
    await s.respond(1, payload(result('NEW')));
    await s.respond(0, payload(result('OLD')));
    assert.match(s.list.textContent, /NEW/);
    assert.doesNotMatch(s.list.textContent, /OLD/);
  });

  await test('clearing the input cancels a request and closes results', async () => {
    const s = setup();
    s.type('brot'); s.flush();
    s.type('');
    assert.equal(s.requests[0].options.signal.aborted, true);
    assert.equal(s.timers.size, 0);
    await s.respond(0, payload(result('Brot')));
    assert.equal(s.panel.hidden, true);
    assert.equal(s.list.children.length, 0);
    assert.equal(s.input.attributes.get('aria-expanded'), 'false');
  });

  await test('result text is rendered safely and includes matched variants', async () => {
    const s = setup();
    s.type('brot'); s.flush();
    const card = result('<img src=x onerror=alert(1)>');
    card.match = { text: 'Brote', description: 'plural' };
    await s.respond(0, { ...payload(card), more: true });
    assert.match(s.list.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.match(s.list.textContent, /German · Brote \(plural\)/);
    assert.equal(s.list.querySelectorAll('img').length, 0);
    assert.match(s.status.textContent, /1\+ matching card/);
  });

  await test('composition does not issue intermediate requests', async () => {
    const s = setup();
    s.input.emit('compositionstart');
    s.type('u', { isComposing: true });
    s.type('ü', { isComposing: true });
    s.flush();
    assert.equal(s.requests.length, 0);
    s.input.emit('compositionend');
    s.flush();
    assert.equal(s.requests.length, 1);
    assert.equal(s.requests[0].url.searchParams.get('q'), 'ü');
    await s.respond(0, payload());
  });

  await test('empty results and unsupported queries have useful messages', async () => {
    const s = setup();
    s.type('unknown'); s.flush();
    await s.respond(0, payload());
    assert.equal(s.status.textContent, 'No matching cards.');
    assert.equal(s.all.hidden, true);
    s.type('!!!'); s.flush();
    await s.respond(1, { searchable: false, results: [], more: false });
    assert.match(s.status.textContent, /Enter English\/German/);
  });

  await test('server/network errors preserve normal form submission', async () => {
    const s = setup();
    s.type('brot'); s.flush();
    await s.respond(0, { error: 'Session expired.' }, false);
    assert.match(s.status.textContent, /Session expired/);
    s.type('bread'); s.flush();
    s.requests[1].reject(new Error('Network unavailable'));
    await new Promise(setImmediate);
    assert.match(s.status.textContent, /Search button/);
    const event = s.form.emit('submit');
    assert.equal(event.defaultPrevented, false);
    assert.equal(s.panel.hidden, true);
  });

  await test('arrows navigate links; Escape and outside clicks close results', async () => {
    const s = setup();
    s.type('brot'); s.flush();
    await s.respond(0, payload(result('Brot'), result('Brote')));
    const links = s.list.querySelectorAll('a');
    s.form.emit('keydown', { key: 'ArrowDown', target: s.input });
    assert.equal(s.active, links[0]);
    s.form.emit('keydown', { key: 'ArrowDown', target: links[0] });
    assert.equal(s.active, links[1]);
    s.form.emit('keydown', { key: 'Escape', target: links[1] });
    assert.equal(s.active, s.input);
    assert.equal(s.panel.hidden, true);
    s.type('brot'); s.flush();
    await s.respond(1, payload(result('Brot')));
    s.doc.emit('pointerdown', { target: {} });
    assert.equal(s.panel.hidden, true);
  });

  await test('pages without search inputs do nothing', async () => {
    vm.runInNewContext(source, { document: { querySelectorAll: () => [] } });
  });
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
