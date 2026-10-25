// Keep grading stricter than search: ü/ue are equivalent, but u is not ü.
// Normalize both raw strings, not a saved lesson's legacy expected_key, so existing
// lessons also use the current matcher without rewriting their snapshots.
const fcardAnswerKey = (text, question = {}) => {
  const key = fcardCasefold(text.normalize('NFC')).replace(/ü/g, 'ue');
  return question.kind === 'back_to_front' && question.word_type === 'verb'
    ? key.replace(/^to /, '') : key;
};
const fcardAnswersMatch = (answer, expected, question = {}) => fcardAnswerKey(answer, question) === fcardAnswerKey(expected, question);
