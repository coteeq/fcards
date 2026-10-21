// Run with: node tests/test_answer_match.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const cases = require('./fixtures/answer_matches.json');
const context = vm.createContext({});
for (const script of ['casefold.js', 'answer-match.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../fcard/static', script), 'utf8'), context);
}
const matches = vm.runInContext('fcardAnswersMatch', context);
for (const [answer, expected, accepted] of cases) {
  assert.equal(matches(answer, expected), accepted, `${JSON.stringify(answer)} vs ${JSON.stringify(expected)}`);
}
console.log(`ok: ${cases.length} shared German/Unicode answer cases; plain u never substitutes for ü`);
