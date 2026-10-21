// Run with: node tests/test_lesson_ui.cjs
const assert = require('node:assert/strict');
const { fixture } = require('./card_dom.cjs');
const question = (changes = {}) => ({
  kind: 'front_to_back', label: 'Front → Back', prompt: '<img onerror=alert(1)>', expected: 'Straße', expected_key: 'strasse',
  forms: { front: [{ text: '<img onerror=alert(1)>', description: 'example' }], back: [{ text: 'Straße', description: '' }] }, meta: '',
  ratings: [{ value: 'correct', label: 'Correct', correct: true }, { value: 'incorrect', label: 'Incorrect', correct: false }],
  controls: [{ value: 'up5', label: 'Extra +5' }], defaults: { correct: 'correct', incorrect: 'incorrect' }, ...changes,
});
async function setup({ cursor = 0, ratings = [], questions = Array.from({ length: 20 }, () => question()), completed_at = null } = {}) {
  const s = fixture();
  const element = (tag, attr) => { const n = s.doc.createElement(tag); if (attr) n.setAttribute(attr, ''); return n; };
  const root = element('div', 'data-lesson');
  Object.assign(root.dataset, { lessonId: 'uuid', url: '/api/lessons/uuid', stateUrl: '/api/lessons/uuid/state', finishUrl: '/api/lessons/uuid/finish' });
  const csrf = element('input'); csrf.name = 'csrf'; csrf.value = 'csrf-token';
  const status = element('p', 'data-sync-status'), error = element('p', 'data-lesson-error');
  const useSaved = element('button', 'data-use-saved'), stage = element('div', 'data-lesson-stage');
  root.append(csrf, status, error, useSaved, stage); s.doc.append(root);
  s.load('casefold.js'); s.load('answer-match.js'); s.load('lesson-sync.js'); s.load('lesson.js');
  const data = { id: 'uuid', revision: 0, state: { cursor, ratings }, questions, completed_at, skipped: 0 };
  await s.respond(0, data);
  return { ...s, stage, root, status, error, data, get focus() { return s.focused; } };
}
async function test(name, run) { await run(); console.log('ok:', name); }
(async () => {
  await test('checking, correcting, and Next are immediate even without a sync response', async () => {
    const s = await setup();
    assert.equal(s.requests.length, 1); assert.equal(s.stage.querySelectorAll('img').length, 0);
    assert.match(s.stage.textContent, /<img onerror/);
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'STRASSE'; form.emit('submit');
    assert.match(s.stage.textContent, /Automatic verdict: correct/);
    assert.equal(s.stage.querySelectorAll('img').length, 0);
    const details = s.stage.querySelector('details'); assert.equal(details.open, undefined);
    const incorrect = s.stage.querySelectorAll('input').find((i) => i.name === 'rating' && i.value === 'incorrect');
    incorrect.checked = true; incorrect.emit('change');
    const next = s.stage.querySelectorAll('button').find((b) => b.textContent.includes('Next question'));
    assert.equal(s.focus, next); next.emit('click');
    assert.match(s.stage.textContent, /Question 2 \/ 20/);
    assert.equal(s.requests.length, 1); // No round trip was needed to check/correct/advance.
    s.flush();
    await new Promise(setImmediate);
    const payload = JSON.parse(s.requests[1].options.body);
    assert.equal(payload.state.cursor, 1);
    assert.deepEqual(payload.state.ratings, [{ rating: 'incorrect', control: null }]);
    assert.equal(s.requests[1].options.headers['X-CSRF-Token'], 'csrf-token');
    assert.equal(s.requests[1].options.keepalive, true);
    assert.doesNotMatch([...s.saved.values()].join(''), /STRASSE|auto_verdict|corrected/);
  });
  await test('saved lesson snapshots use the upgraded matcher without rewriting their old key', async () => {
    for (const [answer, expectedVerdict] of [['frueh', 'correct'], ['FRÜH', 'correct'], ['fruh', 'incorrect']]) {
      const questions = Array.from({ length: 20 }, () => question({ expected: 'früh', expected_key: 'früh' }));
      const s = await setup({ questions });
      const form = s.stage.querySelector('form'); form.querySelector('textarea').value = answer; form.emit('submit');
      assert.match(s.stage.textContent, new RegExp('Automatic verdict: ' + expectedVerdict));
      assert.equal(s.requests.length, 1);
    }
  });
  await test('gender uses unselected radios and local grading; empty answer is incorrect', async () => {
    const questions = Array.from({ length: 20 }, () => question({ kind: 'gender', prompt: 'Morgen', expected: 'der', expected_key: 'der' }));
    const s = await setup({ questions });
    assert.equal(s.stage.querySelector('textarea'), null);
    const radios = s.stage.querySelectorAll('input'); assert.equal(radios.length, 3);
    assert.equal(radios.some((r) => r.checked), false);
    s.stage.querySelector('form').emit('submit');
    assert.match(s.stage.textContent, /Automatic verdict: incorrect/);
    assert.match(s.stage.textContent, /Expected answer: der/);
  });
  await test('last verdict has End lesson, and schedules are committed only by that action', async () => {
    const ratings = Array.from({ length: 19 }, () => ({ rating: 'correct', control: null }));
    const s = await setup({ cursor: 19, ratings });
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'Straße'; form.emit('submit');
    assert.doesNotMatch(s.stage.textContent, /Next question/);
    const end = s.stage.querySelectorAll('button').find((b) => b.textContent === 'End lesson'); assert.ok(end);
    end.emit('click'); await new Promise(setImmediate);
    assert.equal(s.requests[1].url, '/api/lessons/uuid/finish');
    assert.equal(s.stage.inert, true);
    const payload = JSON.parse(s.requests[1].options.body); assert.equal(payload.state.ratings.length, 20);
    await s.respond(1, { ...s.data, revision: 1, state: payload.state, completed_at: 123 });
    assert.match(s.stage.textContent, /Lesson complete/); assert.match(s.stage.textContent, /20 reviews applied/);
    assert.equal(s.saved.size, 0);
  });
  await test('resuming a revealed verdict does not invent a raw answer or original grade', async () => {
    const s = await setup({ cursor: 0, ratings: [{ rating: 'incorrect', control: null }] });
    assert.match(s.stage.textContent, /Saved verdict/);
    assert.match(s.stage.textContent, /Your answer: \(not stored\)/);
    assert.doesNotMatch(s.stage.textContent, /Automatic verdict/);
    assert.equal(s.stage.querySelectorAll('input').find((i) => i.value === 'incorrect').checked, true);
  });
})().catch((error) => { console.error(error); process.exitCode = 1; });
