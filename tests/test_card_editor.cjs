// Run with: node tests/test_card_editor.cjs
const assert = require('node:assert/strict');
const { fixture } = require('./card_dom.cjs');
function test(name, run) { run(); console.log('ok:', name); }

test('add/remove works locally, keeps values, and reindexes canonical fields', () => {
  const s = fixture({ front: [['Brot', 'first'], ['Brote', 'plural']], back: [['bread', '']] });
  s.load('card-editor.js');
  assert.equal(s.click('add_front').defaultPrevented, true);
  assert.equal(s.rows('front').length, 3);
  assert.equal(s.rows('front')[2].querySelector('input').value, '');
  assert.equal(s.rows('back')[0].querySelector('input').value, 'bread');
  assert.equal(s.requests.length, 0);
  assert.equal(s.form.querySelector('[value="add_front"]').type, 'button');
  assert.equal(s.click('remove_front_0', true).defaultPrevented, true);
  assert.equal(s.rows('front').length, 2);
  const canonical = s.rows('front')[0].querySelector('input');
  assert.equal(canonical.value, 'Brote');
  assert.equal(canonical.id, 'front-text-1');
  assert.equal(canonical.required, true);
  assert.equal(s.rows('front')[0].querySelector('label').htmlFor, canonical.id);
  assert.equal(s.rows('front')[1].querySelector('input').required, false);
  assert.equal(s.rows('front')[1].querySelector('.form-remove').value, 'remove_front_1');
});

test('last row cannot be removed and the 100-row limit is maintained', () => {
  const front = Array.from({ length: 99 }, (_, i) => [`word${i}`, '']);
  const s = fixture({ front });
  s.load('card-editor.js');
  assert.equal(s.rows('back')[0].querySelector('.form-remove').disabled, true);
  s.click('remove_back_0');
  assert.equal(s.rows('back').length, 1);
  s.click('add_front');
  assert.equal(s.rows('front').length, 100);
  assert.equal(s.form.querySelector('[value="add_front"]').disabled, true);
  s.click('add_front');
  assert.equal(s.rows('front').length, 100);
  s.click('remove_front_99');
  assert.equal(s.form.querySelector('[value="add_front"]').disabled, false);
});

test('verb preset is local, preserves text, and is idempotent', () => {
  const s = fixture({ front: [['gehen', 'old']] });
  s.load('card-editor.js');
  s.click('verb_front');
  assert.equal(s.rows('front').length, 7);
  assert.equal(s.rows('front')[0].querySelector('input').value, 'gehen');
  assert.deepEqual(s.rows('front').map((row) => row.querySelector('[name="front_description"]').value),
    ['', '1sg', '1pl', '2sg', '2pl', '3sg', '3pl']);
  s.click('verb_front');
  assert.equal(s.rows('front').length, 7);
  assert.equal(s.requests.length, 0);
});

test('pages without an extended editor are untouched', () => {
  const s = fixture({ quick: true });
  s.load('card-editor.js');
  assert.equal(s.requests.length, 0);
});
