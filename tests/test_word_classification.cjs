// Run with: node tests/test_word_classification.cjs
const assert = require('node:assert/strict');
const cases = require('./fixtures/german_hints.json');
const { fixture } = require('./card_dom.cjs');
function test(name, run) { run(); console.log('ok:', name); }
const selected = (group) => group.querySelectorAll('input[type="radio"]').find((input) => input.checked)?.value || null;
const mode = (group) => group.querySelector('input[type="hidden"]').value;
const back = (s) => s.form.querySelector('[name="back"], [name="back_text"]');

test('client and server share the same conservative inference cases', () => {
  for (const row of cases) {
    const s = fixture({ back: [[row.text, '']], classification: {} });
    s.load('word-classification.js');
    assert.equal(selected(s.choices.type), row.type, row.text);
    assert.equal(s.choices.gender.hidden ? null : selected(s.choices.gender), row.gender, row.text);
    assert.equal(s.choices.type.dataset.selectionMode, 'auto');
    assert.equal(s.requests.length, 0);
  }
});

test('quick-add selects type locally while gender remains silent', () => {
  const s = fixture({ quick: true, classification: {} });
  s.load('word-classification.js');
  assert.equal(s.choices.gender, null);
  s.type(s.form.elements.front, 'der Morgen');
  assert.equal(selected(s.choices.type), null);
  for (const [text, expected] of [['der Morgen', 'noun'], ['gehen', 'verb'], ['gehen wir', 'phrase'], ['groß', null]]) {
    s.type(back(s), text);
    assert.equal(selected(s.choices.type), expected);
    assert.equal(s.choices.type.dataset.selectionMode, 'auto');
  }
});

test('clicking even an already-selected automatic type locks it and changes its color state', () => {
  const s = fixture({ back: [['der Morgen', '']], classification: {} });
  s.load('word-classification.js');
  assert.equal(selected(s.choices.type), 'noun');
  s.choices.choose('word_type', 'noun');
  assert.equal(mode(s.choices.type), 'manual');
  assert.equal(s.choices.type.dataset.selectionMode, 'manual');
  assert.match(s.choices.type.querySelector('[data-choice-status]').textContent, /Manual/);
  s.type(back(s), 'gehen');
  assert.equal(selected(s.choices.type), 'noun');
  assert.equal(s.choices.gender.hidden, false);
  // Locking type alone does not lock gender: it still follows the article.
  s.type(back(s), 'das Haus');
  assert.equal(selected(s.choices.gender), 'neuter');
  assert.equal(mode(s.choices.gender), 'auto');
});

test('keyboard changes are manual and cannot be overwritten by later hints', () => {
  const s = fixture({ classification: {} }); s.load('word-classification.js');
  s.choices.choose('word_type', 'adjective', 'change');
  s.type(back(s), 'der Morgen');
  assert.equal(selected(s.choices.type), 'adjective');
  assert.equal(s.choices.gender.hidden, true);
  assert.equal(s.choices.type.dataset.selectionMode, 'manual');
});

test('manual gender survives article changes and hiding/showing the noun controls', () => {
  const s = fixture({ back: [['der Morgen', '']], classification: {} });
  s.load('word-classification.js');
  s.choices.choose('gender', 'masculine'); // Already selected, but now explicitly locked.
  s.type(back(s), 'die Katze');
  assert.equal(selected(s.choices.gender), 'masculine');
  assert.equal(s.choices.gender.dataset.selectionMode, 'manual');
  assert.equal(mode(s.choices.type), 'auto');
  s.type(back(s), 'gehen');
  assert.equal(s.choices.gender.hidden, true);
  s.type(back(s), 'das Haus');
  assert.equal(s.choices.gender.hidden, false);
  assert.equal(selected(s.choices.gender), 'masculine');
  s.choices.choose('gender', '');
  s.type(back(s), 'die Katze');
  assert.equal(selected(s.choices.gender), null);
  assert.equal(mode(s.choices.gender), 'manual');
});

test('only canonical back drives hints and deleting it recomputes automatic selections', () => {
  const s = fixture({ back: [['der Morgen', ''], ['gehen', '']], classification: {} });
  s.load('card-editor.js'); s.load('word-classification.js');
  s.type(s.form.elements.front_text, 'spazieren gehen');
  s.type(s.rows('back')[1].querySelector('input'), 'guten Morgen');
  assert.equal(selected(s.choices.type), 'noun');
  s.click('remove_back_0');
  assert.equal(selected(s.choices.type), 'phrase');
  assert.equal(s.choices.gender.hidden, true);
});

test('committed values are manual on load, including unknown type and gender', () => {
  const s = fixture({ back: [['die Katze', '']], classification: {
    committed: true, type: 'noun', gender: 'masculine',
  } });
  s.load('word-classification.js');
  assert.equal(selected(s.choices.gender), 'masculine');
  s.type(back(s), 'gehen');
  assert.equal(selected(s.choices.type), 'noun');
  assert.equal(selected(s.choices.gender), 'masculine');
  assert.equal(s.choices.type.dataset.selectionMode, 'manual');
  const unknown = fixture({ back: [['der Morgen', '']], classification: { committed: true } });
  unknown.load('word-classification.js');
  assert.equal(selected(unknown.choices.type), null);
  assert.equal(selected(unknown.choices.gender), null);
  assert.equal(unknown.choices.gender.hidden, true);
  unknown.choices.choose('word_type', 'noun');
  assert.equal(unknown.choices.gender.hidden, false);
  assert.equal(selected(unknown.choices.gender), null); // Does not silently infer der.
});

test('composition waits for completion; submit refresh catches autofill changes', () => {
  const s = fixture({ classification: {} }); s.load('word-classification.js');
  s.type(back(s), 'gehen', { isComposing: true });
  assert.equal(selected(s.choices.type), null);
  back(s).emit('compositionend');
  assert.equal(selected(s.choices.type), 'verb');
  back(s).value = 'der Morgen';
  assert.equal(s.form.emit('submit').defaultPrevented, false);
  assert.equal(selected(s.choices.type), 'noun');
  assert.equal(selected(s.choices.gender), 'masculine');
});

test('all entry enhancements coexist without treating classifications as word suggestions', () => {
  const s = fixture({ front: [['morning', '']], back: [['der Morgen', '']], classification: {} });
  s.load('card-entry.js'); s.load('card-editor.js'); s.load('word-classification.js');
  s.choices.choose('word_type', 'noun');
  s.choices.choose('gender', 'feminine');
  s.flush();
  assert.deepEqual(JSON.parse(s.requests[0].options.body).words, ['der Morgen', 'morning']);
  assert.equal(s.form.emit('submit').defaultPrevented, false);
  assert.equal(selected(s.choices.gender), 'feminine');
});

test('pages without classification controls are untouched', () => {
  fixture().load('word-classification.js');
});
