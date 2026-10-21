// Run with: node tests/test_lesson_sync.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const clone = (value) => JSON.parse(JSON.stringify(value));
const state = (n = 0) => ({ cursor: Math.min(n, 19), ratings: Array.from({ length: n }, () => ({ rating: 'correct', control: null })) });
const response = (revision, value, done = false) => ({ id: 'uuid', revision, state: clone(value), completed_at: done ? 123 : null, skipped: 0 });
function harness(data = response(0, state()), initial = null) {
  const timers = new Map(), saved = new Map(), calls = [], errors = [], statuses = [], completed = [];
  let id = 0;
  if (initial) saved.set('lesson', JSON.stringify(initial));
  const context = vm.createContext({ setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(n) { timers.delete(n); } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../fcard/static/lesson-sync.js'), 'utf8'), context);
  const Sync = vm.runInContext('LessonSync', context);
  const sync = new Sync(clone(data), {
    request: (finish, payload) => new Promise((resolve, reject) => calls.push({ finish, payload, resolve, reject })),
    storage: { getItem: (key) => saved.get(key) || null, setItem: (key, value) => saved.set(key, value), removeItem: (key) => saved.delete(key) },
    key: 'lesson', status: (text) => statuses.push(text), error: (...args) => errors.push(args), complete: (data) => completed.push(data),
  });
  return { sync, saved, calls, errors, statuses, completed, timers };
}
const tick = () => new Promise(setImmediate);
async function test(name, run) { await run(); console.log('ok:', name); }
(async () => {
  await test('background sync sends only final choices and clears the outbox on acknowledgement', async () => {
    const h = harness(); h.sync.state = state(1); h.sync.changed();
    assert.equal(h.calls.length, 0);
    assert.equal(JSON.parse(h.saved.get('lesson')).state.ratings.length, 1);
    const flight = h.sync.flush(); await tick();
    assert.deepEqual(clone(h.calls[0].payload), { revision: 0, state: state(1) });
    h.calls[0].resolve(response(1, state(1))); await flight;
    assert.equal(h.saved.size, 0); assert.equal(h.sync.revision, 1);
  });
  await test('new answers never wait for the in-flight write and cannot be overwritten by an old acknowledgement', async () => {
    const h = harness(); h.sync.state = state(1); h.sync.changed();
    const first = h.sync.flush(); await tick();
    h.sync.state = state(2); h.sync.changed();
    assert.equal(h.calls[0].payload.state.ratings.length, 1);
    h.calls[0].resolve(response(1, state(1))); await first;
    assert.equal(h.sync.state.ratings.length, 2);
    assert.equal(JSON.parse(h.saved.get('lesson')).revision, 1);
    const second = h.sync.flush(); await tick();
    assert.equal(h.calls[1].payload.revision, 1);
    h.calls[1].resolve(response(2, state(2))); await second;
    assert.equal(h.saved.size, 0);
  });
  await test('lost acknowledgement retries the identical old body before sending newer progress', async () => {
    const h = harness(); h.sync.state = state(1); h.sync.changed();
    const first = h.sync.flush(); await tick();
    h.sync.state = state(2); h.sync.changed();
    h.calls[0].reject(new Error('connection lost after server commit')); await first;
    const retry = h.sync.flush(); await tick();
    assert.deepEqual(h.calls[1].payload, h.calls[0].payload);
    h.calls[1].resolve(response(1, state(1))); await retry;
    const latest = h.sync.flush(); await tick();
    assert.equal(h.calls[2].payload.revision, 1);
    assert.equal(h.calls[2].payload.state.ratings.length, 2);
    h.calls[2].resolve(response(2, state(2))); await latest;
  });
  await test('reload reconciles a committed request whose acknowledgement was lost', async () => {
    const old = { revision: 0, state: state(2), sent: { finish: false, payload: { revision: 0, state: state(1) } } };
    // Server JSON key order is not necessarily the JavaScript insertion order.
    const data = response(1, state(1)); data.state.ratings[0] = { control: null, rating: 'correct' };
    const h = harness(data, old);
    assert.equal(h.sync.blocked, false); assert.equal(h.sync.state.ratings.length, 2);
    const flight = h.sync.flush(); await tick();
    assert.equal(h.calls[0].payload.revision, 1);
    h.calls[0].resolve(response(2, state(2))); await flight;
  });
  await test('reload preserves a pending undo even if it currently equals the server state', async () => {
    const initial = state(1), sent = state(1); sent.ratings[0].rating = 'incorrect';
    const h = harness(response(0, initial), { revision: 0, state: initial, sent: { finish: false, payload: { revision: 0, state: sent } } });
    const first = h.sync.flush(); await tick(); h.calls[0].resolve(response(1, sent)); await first;
    const second = h.sync.flush(); await tick();
    assert.equal(h.calls[1].payload.state.ratings[0].rating, 'correct');
    h.calls[1].resolve(response(2, initial)); await second;
  });
  await test('conflicting tabs block sync and preserve local work instead of last-writer-wins', async () => {
    const h = harness(response(2, state(3)), { revision: 0, state: state(1) });
    assert.equal(h.sync.blocked, true); assert.equal(h.errors[0][1], 409);
    assert.equal(h.saved.size, 1); await h.sync.flush(); assert.equal(h.calls.length, 0);
  });
  await test('authentication and revision failures do not keep retrying destructive writes', async () => {
    for (const status of [400, 401, 403, 404, 409]) {
      const h = harness(); h.sync.state = state(1); h.sync.changed();
      const flight = h.sync.flush(); await tick();
      h.calls[0].reject(Object.assign(new Error('reload required'), { status })); await flight;
      assert.equal(h.sync.blocked, true); assert.equal(h.saved.size, 1);
      await h.sync.flush(); assert.equal(h.calls.length, 1);
    }
  });
  await test('End lesson serializes behind pending sync and completes exactly once', async () => {
    const h = harness(); h.sync.state = state(19); h.sync.changed();
    const first = h.sync.flush(); await tick();
    h.sync.state = state(20); h.sync.changed();
    const end = h.sync.finish();
    assert.equal(h.calls.length, 1);
    h.calls[0].resolve(response(1, state(19))); await first; await tick();
    assert.equal(h.calls[1].finish, true);
    assert.equal(h.calls[1].payload.state.ratings.length, 20);
    h.calls[1].resolve(response(2, state(20), true)); assert.equal(await end, true);
    assert.equal(h.completed.length, 1); assert.equal(h.saved.size, 0);
    await h.sync.finish(); assert.equal(h.calls.length, 2);
  });
  await test('failed End lesson is recoverable after navigation and keeps its completion intent', async () => {
    const pending = { revision: 0, state: state(20), sent: { finish: true, payload: { revision: 0, state: state(20) } } };
    const h = harness(response(0, state()), pending);
    const flight = h.sync.flush(); await tick(); assert.equal(h.calls[0].finish, true);
    h.calls[0].resolve(response(1, state(20), true)); await flight;
    assert.equal(h.completed.length, 1);
    const done = harness(response(1, state(20), true), pending);
    assert.equal(done.sync.completed, true); assert.equal(done.saved.size, 0);
  });
  await test('client casefold matches Unicode grading without trimming or transliterating', () => {
    const context = vm.createContext({});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../fcard/static/casefold.js'), 'utf8'), context);
    const fold = vm.runInContext('fcardCasefold', context);
    for (const [input, output] of [['Straße', 'strasse'], ['STRASSE', 'strasse'], ['ΑΣ', 'ασ'], ['ﬃ', 'ffi'], ['İ', 'i\u0307'], ['Ä', 'ä'], [' bread ', ' bread '], ['bréad!', 'bréad!']]) assert.equal(fold(input), output);
  });
})().catch((error) => { console.error(error); process.exitCode = 1; });
