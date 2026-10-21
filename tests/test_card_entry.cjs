// Run with: node tests/test_card_entry.cjs
const assert = require('node:assert/strict');
const { fixture } = require('./card_dom.cjs');
const card = (front = 'Brot') => ({ id: 1, front, back: 'bread', deck: 'German',
  match: 'Brote', url: '/decks/German?page=1#card-1' });
async function test(name, run) { await run(); console.log('ok:', name); }

(async () => {
  await test('quick-add suggestions are requested while typing, before submission', async () => {
    const s = fixture({ quick: true });
    s.load('card-entry.js');
    s.type(s.form.elements.front, ' Brote ');
    assert.equal(s.requests.length, 0);
    s.flush();
    assert.equal(s.requests.length, 1);
    const request = s.requests[0];
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-token');
    assert.deepEqual(JSON.parse(request.options.body), { words: ['Brote'], exclude: null });
    await s.respond(0, { results: [card()], more: false });
    assert.equal(s.panel.hidden, false);
    assert.match(s.status.textContent, /may already exist/);
    const link = s.list.querySelector('a');
    assert.match(link.textContent, /Brot ↔ bread/);
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener');
  });

  await test('saving trims fields but does not block duplicate cards', async () => {
    const s = fixture({ quick: true, front: [[' Brot ', '']], back: [[' bread ', '']] });
    s.load('card-entry.js');
    s.flush();
    await s.respond(0, { results: [card()], more: false });
    const event = s.form.emit('submit');
    assert.equal(event.defaultPrevented, false);
    assert.equal(s.form.elements.front.value, 'Brot');
    assert.equal(s.form.elements.back.value, 'bread');
    assert.equal(s.form.elements.csrf.value, 'csrf-token');
    assert.equal(s.panel.hidden, true);
  });

  await test('extended fields, descriptions and meta are trimmed; interior spaces stay', async () => {
    const s = fixture({ cardId: '42', front: [[' die  Tür ', ' noun ']], back: [[' the  door ', '']] });
    s.meta.value = ' neuter ';
    s.load('card-editor.js'); s.load('card-entry.js');
    s.flush();
    assert.equal(JSON.parse(s.requests[0].options.body).exclude, 42);
    await s.respond(0, { results: [], more: false });
    assert.equal(s.form.emit('submit').defaultPrevented, false);
    assert.equal(s.form.elements.front_text.value, 'die  Tür');
    assert.equal(s.form.elements.front_description.value, 'noun');
    assert.equal(s.meta.value, 'neuter');
  });

  await test('new rows participate and deleting rows refreshes suggestions', async () => {
    const s = fixture();
    s.load('card-editor.js'); s.load('card-entry.js');
    s.click('add_front');
    const input = s.rows('front')[1].querySelector('input');
    s.type(input, 'Brote'); s.flush();
    assert.deepEqual(JSON.parse(s.requests[0].options.body).words, ['Brote']);
    await s.respond(0, { results: [card()], more: false });
    assert.equal(s.panel.hidden, false);
    s.click('remove_front_1');
    assert.equal(s.panel.hidden, true);
    assert.equal(s.list.children.length, 0);
    assert.equal(s.timers.size, 0);
  });

  await test('composition is respected and stale replies cannot replace current hints', async () => {
    const s = fixture({ quick: true }); s.load('card-entry.js');
    s.form.elements.front.emit('compositionstart');
    s.type(s.form.elements.front, 'ü', { isComposing: true }); s.flush();
    assert.equal(s.requests.length, 0);
    s.form.elements.front.emit('compositionend'); s.flush();
    assert.equal(s.requests.length, 1);
    s.type(s.form.elements.front, 'Brot'); s.flush();
    assert.equal(s.requests[0].options.signal.aborted, true);
    await s.respond(1, { results: [card('NEW')], more: false });
    await s.respond(0, { results: [card('OLD')], more: false });
    assert.match(s.list.textContent, /NEW/);
    assert.doesNotMatch(s.list.textContent, /OLD/);
  });

  await test('description-only changes do not trigger checks; whitespace-only required input is rejected', async () => {
    const s = fixture({ front: [[' Brot ', '']], back: [[' bread ', '']] });
    s.load('card-editor.js'); s.load('card-entry.js'); s.flush();
    await s.respond(0, { results: [], more: false });
    s.type(s.form.elements.front_description, 'noun'); s.flush();
    assert.equal(s.requests.length, 1);
    s.form.elements.front_text.value = '   ';
    assert.equal(s.form.emit('submit').defaultPrevented, true);
  });

  await test('safe hints and lookup failures never prevent saving', async () => {
    const s = fixture({ quick: true, front: [['a', '']], back: [['b', '']] });
    s.load('card-entry.js'); s.flush();
    await s.respond(0, { results: [card('<img onerror=alert(1)>')], more: false });
    assert.match(s.list.textContent, /<img onerror=alert\(1\)>/);
    assert.equal(s.list.querySelectorAll('img').length, 0);
    s.type(s.form.elements.front, 'new'); s.flush();
    s.requests[1].reject(new Error('Network down'));
    await new Promise(setImmediate);
    assert.match(s.status.textContent, /You can still save/);
    assert.equal(s.form.emit('submit').defaultPrevented, false);
  });
})().catch((error) => { console.error(error); process.exitCode = 1; });
