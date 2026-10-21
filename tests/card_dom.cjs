// Small DOM/fetch fixture for dependency-free card entry script tests.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture({ quick = false, front = [['', '']], back = [['', '']], cardId = '', classification = null } = {}) {
  let focused;
  class Element {
    constructor(tag, attrs = {}) {
      this.tag = tag;
      this.attributes = new Map(Object.entries(attrs));
      this.dataset = {};
      for (const [name, value] of Object.entries(attrs)) {
        if (name.startsWith('data-')) {
          this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
        } else this[name === 'class' ? 'className' : name] = value;
      }
      this.children = [];
      this.listeners = {};
      this.value ||= '';
      this.disabled = false;
      this.hidden = false;
      this.required = false;
      this.formNoValidate = false;
      this.text = '';
    }
    append(...nodes) { nodes.forEach((node) => { node.remove(); node.parentNode = this; this.children.push(node); }); }
    remove() {
      if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
      this.parentNode = null;
    }
    get firstElementChild() { return this.children[0]; }
    set textContent(text) { this.text = String(text); this.replaceChildren(); }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(''); }
    set innerHTML(_) { throw new Error('Unsafe HTML insertion'); }
    replaceChildren(...nodes) {
      [...this.children].forEach((child) => child.remove());
      this.append(...nodes);
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    removeAttribute(name) { this.attributes.delete(name); }
    matches(selector) {
      return selector.split(',').some((part) => {
        part = part.trim();
        if (part.startsWith('.')) return (this.className || '').split(' ').includes(part.slice(1));
        const match = /^(\w+)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/.exec(part);
        if (!match || (match[1] && match[1] !== this.tag)) return false;
        if (!match[2]) return true;
        const value = this[match[2]] ?? this.attributes.get(match[2]);
        return match[3] === undefined ? value !== undefined : value === match[3];
      });
    }
    querySelectorAll(selector) {
      return this.children.flatMap((child) => [
        ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    contains(node) { return this === node || this.children.some((c) => c.contains(node)); }
    focus() { focused = this; }
    cloneNode(deep) {
      const node = new Element(this.tag, Object.fromEntries(this.attributes));
      for (const key of ['value', 'name', 'id', 'required', 'disabled', 'type', 'className', 'formNoValidate', 'text']) {
        if (this[key] !== undefined) node[key] = this[key];
      }
      if (deep) node.append(...this.children.map((child) => child.cloneNode(true)));
      return node;
    }
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    dispatchEvent(event) {
      event.target ||= this;
      for (const listener of this.listeners[event.type] || []) listener(event);
      if (event.bubbles && this.parentNode) this.parentNode.dispatchEvent(event);
      return !event.defaultPrevented;
    }
    emit(type, options = {}) {
      const event = { type, target: this, bubbles: true, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...options };
      this.dispatchEvent(event);
      return event;
    }
  }
  const doc = new Element('document');
  doc.createElement = (tag) => new Element(tag);
  doc.getElementById = (id) => doc.querySelector(`[id="${id}"]`);
  const form = new Element('form', {
    'data-card-entry': '', 'data-card-id': cardId,
    'data-suggestions-target': 'duplicates', 'data-suggestions-url': '/cards/suggestions',
    ...(!quick ? { 'data-card-editor': '' } : {}),
  });
  form.append(new Element('input', { name: 'csrf', value: 'csrf-token' }));
  function row(side, [text, description]) {
    const node = new Element('div', { class: 'form-editor-row' });
    const remove = new Element('button', { class: 'form-remove', name: 'action' });
    remove.formNoValidate = true;
    const svg = new Element('svg'); svg.append(new Element('path')); remove.append(svg);
    node.append(new Element('label'), new Element('input', { name: `${side}_text`, value: text }),
      new Element('label'), new Element('input', { name: `${side}_description`, value: description }), remove);
    return node;
  }
  for (const [side, values] of [['front', front], ['back', back]]) {
    if (quick) {
      const input = new Element('input', { name: side, value: values[0][0] });
      input.required = true;
      form.append(input);
    } else {
      const container = new Element('div', { 'data-form-rows': side });
      container.append(...values.map((value) => row(side, value)));
      const add = new Element('button', { name: 'action', value: `add_${side}` });
      const verb = new Element('button', { name: 'action', value: `verb_${side}` });
      add.formNoValidate = verb.formNoValidate = true;
      form.append(verb, container, add);
    }
  }
  const meta = new Element('textarea', { name: 'meta' });
  form.append(meta, new Element('button', { name: 'action', value: 'save' }));
  let choices;
  if (classification) {
    const root = new Element('div', { 'data-word-classification': '' });
    function group(name, values, selected, mode) {
      const node = new Element('fieldset', { 'data-choice': name, 'data-selection-mode': mode });
      node.append(new Element('input', { type: 'hidden', name: name + '_mode', value: mode }));
      for (const value of values) {
        const label = new Element('label');
        const input = new Element('input', { type: 'radio', name, value });
        input.checked = value === selected;
        label.append(input, new Element('span'));
        node.append(label);
      }
      node.append(new Element('small', { 'data-choice-status': '' }));
      root.append(node);
      return node;
    }
    const initialMode = classification.committed ? 'manual' : 'auto';
    const type = group('word_type', ['adjective', 'verb', 'noun', 'phrase'],
      classification.type, classification.typeMode || initialMode);
    const gender = quick ? null : group('gender', ['masculine', 'feminine', 'neuter', ''],
      classification.gender ?? (classification.committed ? '' : null),
      classification.genderMode || initialMode);
    form.append(root);
    choices = { root, type, gender,
      choose(name, value, event = 'click') {
        const radios = (name === 'word_type' ? type : gender).querySelectorAll('input[type="radio"]');
        radios.forEach((input) => { input.checked = input.value === value; });
        radios.find((input) => input.checked).emit(event);
      },
    };
  }
  form.elements = new Proxy({}, { get: (_, name) => form.querySelector(`[name="${String(name)}"]`) });
  form.reportValidity = () => !form.querySelectorAll('input').some((input) => input.required && !input.value);
  const panel = new Element('aside', { id: 'duplicates' });
  const status = new Element('p', { 'data-duplicate-status': '' });
  const list = new Element('ul', { 'data-duplicate-list': '' });
  panel.append(status, list); panel.hidden = true;
  doc.append(form, panel);
  let timerId = 0;
  const timers = new Map(), requests = [];
  const saved = new Map();
  const context = vm.createContext({
    document: doc, AbortController,
    window: { addEventListener() {} }, location: { reload() {} },
    localStorage: { getItem: (key) => saved.get(key) || null, setItem: (key, value) => saved.set(key, value), removeItem: (key) => saved.delete(key) },
    Event: class { constructor(type) { this.type = type; } },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })); },
  });
  return {
    doc, form, panel, status, list, meta, timers, requests, choices, saved,
    get focused() { return focused; },
    rows: (side) => [...form.querySelector(`[data-form-rows="${side}"]`).children],
    load(script) { vm.runInContext(fs.readFileSync(path.join(__dirname, '../fcard/static', script), 'utf8'), context); },
    click(value, icon = false) {
      const button = form.querySelector(`button[value="${value}"]`);
      return (icon ? button.querySelector('path') : button).emit('click');
    },
    type(input, value, options = {}) { input.value = value; input.emit('input', options); },
    flush() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((fn) => fn()); },
    async respond(index, data, ok = true) {
      requests[index].resolve({ ok, json: async () => data });
      await new Promise(setImmediate);
    },
  };
}
module.exports = { fixture };
