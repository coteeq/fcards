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
for (const [answer, expected, accepted, question = {}] of cases) {
  assert.equal(matches(answer, expected, question), accepted, `${JSON.stringify(answer)} vs ${JSON.stringify(expected)} (${JSON.stringify(question)})`);
}
console.log(`ok: ${cases.length} shared spelling/context cases; optional to only for German → English verbs`);
