// Run with: node tests/test_study_keyboard.cjs (no dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(
  path.join(__dirname, '../fcard/static/study.js'), 'utf8'
);

function setup() {
  let listener, submitted = 0;
  const input = {
    form: { requestSubmit() { submitted++; } },
    addEventListener(type, callback) {
      assert.equal(type, 'keydown');
      listener = callback;
    },
  };
  vm.runInNewContext(source, { document: { querySelector() { return input; } } });
  return (options = {}) => {
    let prevented = false;
    listener({
      key: 'Enter', shiftKey: false, isComposing: false,
      preventDefault() { prevented = true; },
      ...options,
    });
    return { prevented, submitted };
  };
}

assert.deepEqual(setup()(), { prevented: true, submitted: 1 });
console.log('ok: Enter requests native answer submission');

assert.deepEqual(setup()({ shiftKey: true }), { prevented: false, submitted: 0 });
console.log('ok: Shift+Enter preserves native newline behavior');

for (const options of [
  { isComposing: true }, { keyCode: 229 }, { key: 'Tab' }, { key: 'a' },
]) {
  assert.deepEqual(setup()(options), { prevented: false, submitted: 0 });
}
console.log('ok: composition and other keys are left alone');

assert.doesNotThrow(() => vm.runInNewContext(source, {
  document: { querySelector() { return null; } },
}));
console.log('ok: revealed/empty study pages require no answer handler');
