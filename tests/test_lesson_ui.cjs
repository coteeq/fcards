// Run with: node tests/test_lesson_ui.cjs
const assert = require('node:assert/strict');
const { fixture } = require('./card_dom.cjs');
const question = (changes = {}) => ({
  kind: 'front_to_back', label: 'Front → Back', prompt: '<img onerror=alert(1)>', expected: 'Straße', expected_key: 'strasse',
  forms: { front: [{ text: '<img onerror=alert(1)>', description: 'example' }], back: [{ text: 'Straße', description: '' }] }, meta: '',
  ratings: [{ value: 'correct', label: 'Correct', correct: true }, { value: 'incorrect', label: 'Incorrect', correct: false }],
  controls: [{ value: 'up5', label: 'Extra +5' }], defaults: { correct: 'correct', incorrect: 'incorrect' }, ...changes,
});
async function setup({ cursor = 0, ratings = [], questions = Array.from({ length: 20 }, () => question()), completed_at = null, hash = '', savedEntries = [] } = {}) {
  const s = fixture({ hash, savedEntries });
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
    assert.equal(s.stage.attributes.has('data-verdict'), false);
    assert.match(s.stage.textContent, /<img onerror/);
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'STRASSE'; form.emit('submit');
    assert.match(s.stage.textContent, /Automatic verdict: correct/);
    assert.equal(s.stage.attributes.get('data-verdict'), 'correct');
    assert.equal(s.stage.querySelectorAll('img').length, 0);
    const details = s.stage.querySelector('details'); assert.equal(details.open, undefined);
    const incorrect = s.stage.querySelectorAll('input').find((i) => i.name === 'rating' && i.value === 'incorrect');
    incorrect.checked = true; incorrect.emit('change');
    assert.equal(s.stage.attributes.get('data-verdict'), 'incorrect');
    const retype = s.stage.querySelector('.lesson-retype');
    assert.ok(s.focus === retype.querySelector('textarea'));
    retype.querySelector('textarea').value = 'STRASSE';
    retype.querySelector('textarea').emit('keydown', { key: 'Enter' });
    assert.match(s.stage.textContent, /Question 2 \/ 20/);
    assert.equal(s.stage.attributes.has('data-verdict'), false);
    assert.equal(s.requests.length, 1); // No round trip was needed to check/correct/advance.
    s.flush();
    await new Promise(setImmediate);
    const payload = JSON.parse(s.requests[1].options.body);
    assert.equal(payload.state.cursor, 1);
    assert.deepEqual(payload.state.ratings, [{ rating: 'incorrect', control: null }]);
    assert.equal(s.requests[1].options.headers['X-CSRF-Token'], 'csrf-token');
    assert.equal(s.requests[1].options.keepalive, true);
    assert.doesNotMatch(s.saved.get('fcard.lesson.uuid') || '', /STRASSE|auto_verdict|corrected/);
    assert.match(s.saved.get('fcard.lesson.uuid.answers'), /STRASSE/);
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
    assert.equal(s.stage.attributes.get('data-verdict'), 'incorrect');
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
    assert.equal(s.saved.has('fcard.lesson.uuid'), false);
    assert.equal(s.saved.has('fcard.lesson.uuid.answers'), true);
    assert.equal(s.stage.attributes.has('data-verdict'), false);
    assert.equal(s.location.hash, '#complete');
    s.history.back();
    assert.equal(s.location.hash, '#question=20');
    assert.match(s.stage.textContent, /review only/);
    assert.equal(s.stage.querySelectorAll('input').every((input) => input.disabled), true);
    assert.ok(s.stage.querySelectorAll('button').find((button) => button.textContent === 'Lesson summary'));
    assert.equal(s.requests.length, 2); // Reviewing cannot apply schedules again.
    s.history.forward();
    assert.match(s.stage.textContent, /Lesson complete/);
  });
  await test('resuming a revealed verdict does not invent a raw answer or original grade', async () => {
    const s = await setup({ cursor: 0, ratings: [{ rating: 'incorrect', control: null }] });
    assert.match(s.stage.textContent, /Saved verdict/);
    assert.equal(s.stage.attributes.get('data-verdict'), 'incorrect');
    assert.match(s.stage.textContent, /Your answer: \(unavailable on this device\)/);
    assert.doesNotMatch(s.stage.textContent, /Automatic verdict/);
    assert.equal(s.stage.querySelectorAll('input').find((i) => i.value === 'incorrect').checked, true);
  });
  await test('Back and Forward show stable verdicts without rewinding progress or regrading', async () => {
    const questions = Array.from({ length: 20 }, (_, i) => question({ prompt: 'Word ' + (i + 1) }));
    const s = await setup({ questions });
    assert.equal(s.location.hash, '#question=1');
    let form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'STRASSE'; form.emit('submit');
    assert.equal(s.history.length, 1); // Answer and verdict share one question entry.
    s.stage.querySelectorAll('button').find((b) => b.textContent.includes('Next question')).emit('click');
    assert.equal(s.location.hash, '#question=2');
    form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'Straße'; form.emit('submit');
    const saved = s.saved.get('fcard.lesson.uuid');
    s.history.back();
    assert.equal(s.location.hash, '#question=1');
    assert.equal(s.stage.querySelector('.prompt').textContent, 'Word 1');
    assert.match(s.stage.textContent, /Your answer: STRASSE/);
    assert.match(s.stage.textContent, /review only/);
    assert.equal(s.stage.querySelectorAll('input').filter((i) => i.name === 'rating').every((i) => i.disabled), true);
    assert.equal(s.saved.get('fcard.lesson.uuid'), saved);
    s.history.forward();
    assert.equal(s.location.hash, '#question=2');
    assert.match(s.stage.textContent, /Your answer: Straße/);
    assert.equal(s.stage.querySelector('.prompt').textContent, 'Word 2');
    assert.equal(s.requests.length, 1);
  });
  await test('incorrect retype blocks Enter, shakes on mismatch, and accepts German spelling equivalents', async () => {
    const questions = Array.from({ length: 20 }, () => question({ expected: 'früh' }));
    const s = await setup({ questions });
    let form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'wrong'; form.emit('submit');
    form = s.stage.querySelector('.lesson-retype');
    const input = form.querySelector('textarea');
    assert.ok(s.focus === input);
    input.value = 'fruh';
    assert.equal(input.emit('keydown', { key: 'Enter' }).defaultPrevented, true);
    assert.equal(s.location.hash, '#question=1');
    assert.equal(input.attributes.get('aria-invalid'), 'true');
    assert.equal(form.attributes.get('data-shake'), 'true');
    assert.equal(input.emit('keydown', { key: 'Enter', shiftKey: true }).defaultPrevented, false);
    assert.equal(input.emit('keydown', { key: 'Enter', isComposing: true }).defaultPrevented, false);
    assert.equal(input.emit('keydown', { key: 'Enter', keyCode: 229 }).defaultPrevented, false);
    input.value = 'frueh'; input.emit('input');
    assert.equal(input.attributes.has('aria-invalid'), false);
    input.emit('keydown', { key: 'Enter' });
    assert.equal(s.location.hash, '#question=2');
    const notes = JSON.parse(s.saved.get('fcard.lesson.uuid.answers'));
    assert.equal(notes[0][1].answer, 'wrong'); // Retyping never replaces the mistake.
    assert.equal(notes[0][1].retypeDone, true);
    const pending = JSON.parse(s.saved.get('fcard.lesson.uuid'));
    assert.deepEqual(pending.state.ratings, [{ rating: 'incorrect', control: null }]);
    assert.doesNotMatch(JSON.stringify(pending), /wrong|frueh|retypeDone/);
  });
  await test('Skip retype advances without changing the incorrect rating', async () => {
    const s = await setup();
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = ''; form.emit('submit');
    s.stage.querySelectorAll('button').find((b) => b.textContent === 'Skip retype').emit('click');
    assert.equal(s.location.hash, '#question=2');
    const pending = JSON.parse(s.saved.get('fcard.lesson.uuid'));
    assert.equal(pending.state.ratings[0].rating, 'incorrect');
    assert.equal(pending.state.cursor, 1);
    s.history.back();
    assert.equal(s.stage.querySelector('.lesson-retype'), null); // Historical review is not another exercise.
    assert.match(s.stage.textContent, /Your answer: \(empty\)/);
  });
  await test('a manual correction to correct removes the retype gate', async () => {
    const s = await setup();
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'wrong'; form.emit('submit');
    assert.ok(s.stage.querySelector('.lesson-retype'));
    const correct = s.stage.querySelectorAll('input').find((i) => i.value === 'correct');
    correct.checked = true; correct.emit('change');
    assert.equal(s.stage.querySelector('.lesson-retype'), null);
    assert.equal(s.stage.attributes.get('data-verdict'), 'correct');
    s.stage.querySelectorAll('button').find((b) => b.textContent.includes('Next question')).emit('click');
    assert.equal(s.location.hash, '#question=2');
  });
  await test('completion review shows every correct answer and the original mistakes, safely and with padding', async () => {
    const ratings = Array.from({ length: 20 }, (_, i) => ({ rating: i < 2 ? 'incorrect' : 'correct', control: null }));
    const savedEntries = [['fcard.lesson.uuid.answers', JSON.stringify([[0, { answer: '<img onerror=alert(1)>', retypeDone: true }], [1, { answer: '', retypeDone: true }]])]];
    const s = await setup({ cursor: 19, ratings, completed_at: 123, savedEntries });
    assert.equal(s.location.hash, '#complete');
    const rows = s.stage.querySelectorAll('.lesson-result');
    assert.equal(rows.length, 20);
    const table = s.stage.querySelector('table');
    assert.ok(table);
    assert.deepEqual(table.querySelector('thead').querySelectorAll('th').map((cell) => cell.textContent),
      ['#', 'Result', 'Question', 'Correct answer', 'Your mistake']);
    assert.equal(rows.every((row) => row.querySelectorAll('td')[2].textContent === 'Straße'), true);
    assert.equal(rows[0].querySelectorAll('td')[0].textContent, 'NOK');
    assert.equal(rows[0].attributes.get('data-verdict'), 'incorrect');
    assert.equal(rows[0].querySelectorAll('td')[3].textContent, '<img onerror=alert(1)>');
    assert.equal(rows[1].querySelectorAll('td')[3].textContent, '(empty)');
    assert.equal(rows[2].querySelectorAll('td')[0].textContent, 'OK');
    assert.equal(rows[2].attributes.get('data-verdict'), 'correct');
    assert.equal(rows[2].querySelectorAll('td')[3].textContent, '—');
    assert.equal(s.stage.querySelectorAll('img').length, 0);
    assert.match(s.stage.querySelector('.lesson-complete-heading').className, /card-body/);
    s.navigate('#question=1');
    assert.equal(s.stage.querySelectorAll('input').every((input) => input.disabled), true);
    assert.match(s.stage.textContent, /Your answer: <img/);
    s.history.back();
    assert.equal(s.location.hash, '#complete');
    assert.equal(s.requests.length, 1);
  });
  await test('reload restores browser-local mistakes and honors a past-question deep link', async () => {
    const s = await setup();
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'mistake'; form.emit('submit');
    s.stage.querySelectorAll('button').find((b) => b.textContent === 'Skip retype').emit('click');
    const pending = JSON.parse(s.saved.get('fcard.lesson.uuid'));
    const restored = await setup({ cursor: pending.state.cursor, ratings: pending.state.ratings,
      hash: '#question=1', savedEntries: [...s.saved] });
    assert.match(restored.stage.textContent, /Question 1 \/ 20/);
    assert.match(restored.stage.textContent, /Your answer: mistake/);
    assert.match(restored.stage.textContent, /review only/);
    assert.equal(restored.stage.querySelector('.lesson-retype'), null);
  });
  await test('future-question hashes cannot bypass retype or advance server progress', async () => {
    const s = await setup({ hash: '#question=20' });
    assert.equal(s.location.hash, '#question=1');
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'wrong'; form.emit('submit');
    s.navigate('#complete');
    assert.equal(s.location.hash, '#question=1');
    assert.ok(s.stage.querySelector('.lesson-retype'));
    s.navigate('#question=2');
    assert.equal(s.location.hash, '#question=1');
    const pending = JSON.parse(s.saved.get('fcard.lesson.uuid'));
    assert.equal(pending.state.cursor, 0);
    assert.equal(pending.state.ratings.length, 1);
  });
  await test('last incorrect verdict cannot end until retyped or explicitly skipped', async () => {
    const ratings = Array.from({ length: 19 }, () => ({ rating: 'correct', control: null }));
    const s = await setup({ cursor: 19, ratings });
    let form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'mistake'; form.emit('submit');
    form = s.stage.querySelector('.lesson-retype');
    assert.ok(form.querySelectorAll('button').find((button) => button.textContent === 'End lesson'));
    form.querySelector('textarea').value = 'still wrong'; form.emit('submit');
    assert.equal(s.requests.length, 1);
    assert.equal(s.location.hash, '#question=20');
    form.querySelectorAll('button').find((button) => button.textContent === 'Skip retype').emit('click');
    await new Promise(setImmediate);
    assert.equal(s.requests[1].url, '/api/lessons/uuid/finish');
    const payload = JSON.parse(s.requests[1].options.body);
    assert.equal(payload.state.ratings[19].rating, 'incorrect');
    assert.doesNotMatch(s.requests[1].options.body, /mistake|still wrong|retype/);
    await s.respond(1, { ...s.data, revision: 1, state: payload.state, completed_at: 123 });
    assert.equal(s.stage.querySelectorAll('.lesson-result')[19].querySelectorAll('td')[3].textContent, 'mistake');
    assert.equal(s.stage.querySelectorAll('.lesson-result')[19].querySelectorAll('td')[2].textContent, 'Straße');
  });
  await test('summary without local answers says unavailable instead of inventing a mistake', async () => {
    const ratings = Array.from({ length: 20 }, () => ({ rating: 'incorrect', control: null }));
    const s = await setup({ cursor: 19, ratings, completed_at: 123 });
    assert.equal(s.stage.querySelectorAll('.lesson-result').length, 20);
    assert.equal(s.stage.querySelectorAll('.lesson-result').every((row) => row.textContent.includes('(unavailable on this device)')), true);
    assert.equal(s.requests.length, 1);
  });
  await test('verdict edit button opens the original card in a new tab without changing lesson state', async () => {
    const questions = Array.from({ length: 20 }, () => question({ edit_url: '/decks/German/cards/42/edit' }));
    const s = await setup({ questions });
    assert.equal(s.stage.querySelector('a'), null);
    const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'STRASSE'; form.emit('submit');
    const edit = s.stage.querySelectorAll('a').find((link) => link.textContent === 'Edit card');
    assert.ok(edit);
    assert.equal(edit.href, '/decks/German/cards/42/edit');
    assert.equal(edit.target, '_blank');
    assert.equal(edit.rel, 'noopener');
    const pending = s.saved.get('fcard.lesson.uuid');
    edit.emit('click');
    assert.equal(s.location.hash, '#question=1');
    assert.equal(s.saved.get('fcard.lesson.uuid'), pending);
    assert.equal(s.requests.length, 1);
    assert.match(s.stage.textContent, /keeps its saved wording/);
  });
  await test('German to English verbs accept optional to for checking and retyping', async () => {
    const questions = Array.from({ length: 20 }, () => question({
      kind: 'back_to_front', word_type: 'verb', prompt: 'gehen', expected: 'to go',
    }));
    const s = await setup({ questions });
    assert.match(s.stage.textContent, /prefix is optional/);
    let form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'go'; form.emit('submit');
    assert.equal(s.stage.attributes.get('data-verdict'), 'correct');
    assert.match(s.stage.textContent, /Automatic verdict: correct/);
    s.stage.querySelectorAll('button').find((b) => b.textContent.includes('Next question')).emit('click');
    form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'went'; form.emit('submit');
    form = s.stage.querySelector('.lesson-retype'); form.querySelector('textarea').value = 'GO'; form.emit('submit');
    assert.equal(s.location.hash, '#question=3');
    const pending = JSON.parse(s.saved.get('fcard.lesson.uuid'));
    assert.equal(pending.state.ratings[1].rating, 'incorrect');
    s.history.back(); s.history.back();
    assert.match(s.stage.textContent, /Automatic verdict: correct/);
  });
  await test('optional to is not applied to other types, unknown types, or other directions', async () => {
    for (const context of [
      { kind: 'back_to_front', word_type: 'noun' },
      { kind: 'back_to_front', word_type: null },
      { kind: 'front_to_back', word_type: 'verb' },
      { kind: 'meta', word_type: 'verb' },
    ]) {
      const s = await setup({ questions: Array.from({ length: 20 }, () => question({ ...context, expected: 'to go' })) });
      assert.doesNotMatch(s.stage.textContent, /prefix is optional/);
      const form = s.stage.querySelector('form'); form.querySelector('textarea').value = 'go'; form.emit('submit');
      assert.equal(s.stage.attributes.get('data-verdict'), 'incorrect');
      assert.ok(s.stage.querySelector('.lesson-retype'));
    }
  });
})().catch((error) => { console.error(error); process.exitCode = 1; });
