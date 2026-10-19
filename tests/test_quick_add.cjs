// Run with: node tests/test_quick_add.cjs (no dependencies).
function test(name, run) {
  run();
  console.log('ok:', name);
}
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(
  path.join(__dirname, '../fcard/static/quick-add.js'), 'utf8'
);

function setup() {
  let listener, focused = 0, submitted = 0;
  const front = {};
  const back = { focus() { focused++; } };
  const form = {
    elements: { front, back },
    addEventListener(type, callback) {
      assert.equal(type, 'keydown');
      listener = callback;
    },
    requestSubmit() { submitted++; },
  };
  vm.runInNewContext(source, { document: { querySelector() { return form; } } });
  return {
    front, back,
    key(target, options = {}) {
      let prevented = false;
      listener({
        key: 'Enter', target, isComposing: false,
        preventDefault() { prevented = true; },
        ...options,
      });
      return { prevented, focused, submitted };
    },
  };
}

test('Enter in Front focuses Back without submitting', () => {
  const s = setup();
  assert.deepEqual(s.key(s.front), { prevented: true, focused: 1, submitted: 0 });
});

test('Enter in Back requests native form submission/validation', () => {
  const s = setup();
  assert.deepEqual(s.key(s.back), { prevented: true, focused: 0, submitted: 1 });
});

test('composition, other keys, and other controls are left alone', () => {
  const s = setup();
  for (const [target, options] of [
    [s.front, { isComposing: true }],
    [s.back, { keyCode: 229 }],
    [s.front, { key: 'Tab' }],
    [s.back, { key: 'a' }],
    [{}, {}],
  ]) {
    assert.deepEqual(s.key(target, options), { prevented: false, focused: 0, submitted: 0 });
  }
});

test('pages without a quick-add form do nothing', () => {
  assert.doesNotThrow(() => vm.runInNewContext(source, {
    document: { querySelector() { return null; } },
  }));
});
