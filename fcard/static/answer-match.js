// Keep grading stricter than search: ü/ue are equivalent, but u is not ü.
// Normalize both raw strings, not a saved lesson's legacy expected_key, so existing
// lessons also use the current matcher without rewriting their snapshots.
const fcardAnswerKey = (text) => fcardCasefold(text.normalize('NFC')).replace(/ü/g, 'ue');
const fcardAnswersMatch = (answer, expected) => fcardAnswerKey(answer) === fcardAnswerKey(expected);
