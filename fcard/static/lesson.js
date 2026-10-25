document.querySelectorAll('[data-lesson]').forEach(async (root) => {
  const stage = root.querySelector('[data-lesson-stage]');
  const status = root.querySelector('[data-sync-status]');
  const errorBox = root.querySelector('[data-lesson-error]');
  const useSaved = root.querySelector('[data-use-saved]');
  const key = 'fcard.lesson.' + root.dataset.lessonId;
  const notesKey = key + '.answers';
  const storage = { getItem: (k) => localStorage.getItem(k), setItem: (k, v) => localStorage.setItem(k, v), removeItem: (k) => localStorage.removeItem(k) };
  const attempts = new Map();
  let data, sync, view, displayedHash, endRequested = false, notesUnavailable = false;
  const node = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function remember(index, patch) {
    attempts.set(index, { ...attempts.get(index), ...patch });
    try { storage.setItem(notesKey, JSON.stringify([...attempts])); }
    catch (_) { notesUnavailable = true; }
  }
  function loadNotes() {
    try {
      const notes = JSON.parse(storage.getItem(notesKey) || '[]');
      if (!Array.isArray(notes) || notes.length > 20) return;
      for (const entry of notes) {
        if (!Array.isArray(entry) || entry.length !== 2) continue;
        const [index, value] = entry;
        if (!Number.isInteger(index) || index < 0 || index >= 20 || !value || typeof value !== 'object') continue;
        const note = { retypeDone: value.retypeDone === true };
        if (typeof value.answer === 'string' && value.answer.length <= 500) note.answer = value.answer;
        attempts.set(index, note);
      }
    } catch (_) { notesUnavailable = true; }
  }
  function showError(message, code) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    stage.inert = true;
    useSaved.hidden = code !== 409;
  }
  useSaved.addEventListener('click', () => { try { storage.removeItem(key); } catch (_) {} location.reload(); });
  async function request(url, payload) {
    const response = await fetch(url, {
      method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', keepalive: Boolean(payload),
      headers: payload ? { 'Content-Type': 'application/json', 'X-CSRF-Token': root.querySelector('[name="csrf"]').value } : {},
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    let result;
    try { result = await response.json(); } catch (_) { result = {}; }
    if (!response.ok || !result.id) {
      const error = new Error(result.error || 'Could not reach the lesson server. Reload to retry; local changes are kept.');
      error.status = response.status;
      throw error;
    }
    return result;
  }
  const isCorrect = (question, rating) => question.ratings.find((choice) => choice.value === rating).correct;
  function paintVerdict(question, rating) {
    stage.setAttribute('data-verdict', isCorrect(question, rating) ? 'correct' : 'incorrect');
  }
  function reveal(question) {
    const sides = node('div', 'card-sides');
    for (const side of ['front', 'back']) {
      const section = node('section', 'card-side card-side--' + side);
      section.setAttribute('aria-label', side);
      const list = node('ol', 'form-list');
      question.forms[side].forEach((form, index) => {
        const row = node('li', 'form-row' + (index === 0 ? ' canonical' : '') + (!form.description ? ' form-row--plain' : ''));
        row.append(node('span', 'form-text', form.text));
        if (form.description) row.append(node('small', 'muted form-description', form.description));
        list.append(row);
      });
      section.append(list); sides.append(section);
    }
    stage.append(sides);
    if (question.meta) stage.append(node('p', 'card-meta text', 'Metadata: ' + question.meta));
  }
  // The displayed question is independent of the synced high-water cursor. Browser
  // Back/Forward only changes the view, never truncates answers or schedules twice.
  function showRoute() {
    const max = sync.completed ? 19 : sync.state.cursor;
    const match = /^#question=(\d+)$/.exec(window.location.hash);
    const index = match ? Number(match[1]) - 1 : max;
    view = window.location.hash === '#complete' && sync.completed ? 'complete'
      : Number.isInteger(index) && index >= 0 && index <= max ? index : max;
    const hash = view === 'complete' ? '#complete' : '#question=' + (view + 1);
    if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
    displayedHash = hash;
    render();
  }
  function go(target) {
    const hash = target === 'complete' ? '#complete' : '#question=' + (target + 1);
    if (window.location.hash !== hash) window.history.pushState(null, '', hash);
    showRoute();
  }
  function completed() {
    stage.inert = false;
    const heading = node('div', 'card-body lesson-complete-heading');
    heading.append(node('h3', '', 'Lesson complete'));
    heading.append(node('p', '', `${20 - data.skipped} reviews applied. ${data.skipped ? data.skipped + ' changed or removed questions were skipped to protect their schedules.' : 'All 20 questions are finished.'}`));
    heading.append(node('p', 'muted', notesUnavailable
      ? 'Original answers are available in this tab only; browser storage is unavailable.'
      : 'Original answers are kept only in this browser, not synced to the server.'));
    stage.append(heading);
    const wrapper = node('div', 'lesson-results-wrap');
    wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Lesson results');
    const table = node('table', 'lesson-results');
    const head = node('thead'), headers = node('tr');
    for (const title of ['#', 'Result', 'Question', 'Correct answer', 'Your mistake']) {
      const cell = node('th', '', title); cell.setAttribute('scope', 'col'); headers.append(cell);
    }
    head.append(headers); table.append(head);
    const body = node('tbody');
    data.questions.forEach((question, index) => {
      const rating = question.ratings.find((r) => r.value === sync.state.ratings[index].rating);
      const row = node('tr', 'lesson-result');
      row.setAttribute('data-verdict', rating.correct ? 'correct' : 'incorrect');
      const number = node('th'); number.setAttribute('scope', 'row');
      const link = node('a', '', String(index + 1)); link.href = '#question=' + (index + 1);
      link.setAttribute('aria-label', 'Review verdict for question ' + (index + 1));
      number.append(link);
      const result = node('td', 'lesson-result-status', rating.correct ? 'OK' : 'NOK');
      result.setAttribute('aria-label', rating.correct ? 'Correct' : 'Incorrect');
      const prompt = node('td', 'text', question.prompt); prompt.title = question.label;
      const original = attempts.get(index)?.answer;
      const mistake = rating.correct ? '—' : original === undefined ? '(unavailable on this device)' : original || '(empty)';
      row.append(number, result, prompt, node('td', 'text', question.expected), node('td', 'text', mistake));
      body.append(row);
    });
    table.append(body); wrapper.append(table); stage.append(wrapper);
  }
  function keyboardSubmit(form, input) {
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
      event.preventDefault(); form.requestSubmit();
    });
  }
  function render() {
    stage.removeAttribute('data-verdict');
    stage.replaceChildren();
    if (view === 'complete') { completed(); return; }
    stage.inert = sync.blocked || endRequested;
    const cursor = view;
    const { ratings } = sync.state;
    const question = data.questions[cursor];
    const active = !sync.completed && cursor === sync.state.cursor;
    stage.append(node('h3', 'card-header', `Question ${cursor + 1} / 20 · ${question.label}`));
    const body = node('div', 'card-body');
    const prompt = node('p', 'prompt text', question.prompt); prompt.id = 'lesson-prompt';
    body.append(prompt); stage.append(body);
    if (ratings.length === cursor) {
      const form = node('form');
      let input;
      if (question.kind === 'gender') {
        const fieldset = node('fieldset');
        fieldset.append(node('legend', '', 'What is the gender of this German word?'));
        for (const [value, label] of [['der', 'masculine'], ['die', 'feminine'], ['das', 'neuter']]) {
          const choice = node('input'); choice.type = 'radio'; choice.name = 'answer'; choice.value = value;
          const wrapper = node('label', 'radio-label'); wrapper.append(choice, node('span', '', `${value} (${label})`));
          fieldset.append(wrapper); keyboardSubmit(form, choice);
        }
        fieldset.setAttribute('aria-describedby', 'lesson-prompt');
        input = fieldset.querySelector('input'); form.append(fieldset);
      } else {
        const label = node('label', '', 'Your answer'); label.htmlFor = 'lesson-answer';
        input = node('textarea'); input.id = 'lesson-answer'; input.name = 'answer'; input.rows = 2; input.maxLength = 500;
        input.setAttribute('aria-describedby', 'lesson-prompt'); input.setAttribute('enterkeyhint', 'done');
        input.autocomplete = 'off'; input.spellcheck = false;
        form.append(label, input); keyboardSubmit(form, input);
      }
      const check = node('button', 'primary', 'Check answer');
      form.append(node('p', 'muted', question.kind === 'gender'
        ? 'Choose a gender, then check. Enter checks; arrow keys change the selection. Empty answers count as incorrect.'
        : 'Case is ignored; ß/ss and ü/ue match, but u does not match ü. Spaces and punctuation still matter. Enter checks; Shift+Enter adds a newline.'), check);
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (sync.blocked || endRequested || !active || ratings.length !== cursor) return;
        const answer = question.kind === 'gender' ? [...form.querySelectorAll('input')].find((i) => i.checked)?.value || '' : input.value;
        const auto = fcardAnswersMatch(answer, question.expected) ? 'correct' : 'incorrect';
        remember(cursor, { answer, retypeDone: false });
        ratings.push({ rating: question.defaults[auto], control: null });
        sync.changed(); render(); // Same history entry now represents the verdict.
      });
      body.append(form); input.focus();
      return;
    }
    paintVerdict(question, ratings[cursor].rating);
    const memory = attempts.get(cursor);
    const original = memory?.answer;
    body.append(node('p', '', original !== undefined
      ? `Automatic verdict: ${fcardAnswersMatch(original, question.expected) ? 'correct' : 'incorrect'}`
      : 'Saved verdict (the original answer is unavailable on this device).'));
    body.append(node('p', 'text', `Your answer: ${original === undefined ? '(unavailable on this device)' : original || '(empty)'}`));
    body.append(node('p', 'text', 'Expected answer: ' + question.expected));
    if (question.edit_url) {
      const tools = node('div', 'actions');
      const edit = node('a', 'button', 'Edit card');
      edit.href = question.edit_url; edit.target = '_blank'; edit.rel = 'noopener';
      edit.title = 'Edit original card (opens in a new tab)';
      tools.append(edit); body.append(tools);
      body.append(node('p', 'muted', 'Edits apply to future lessons; this lesson keeps its saved wording.'));
    }
    if (!active) body.append(node('p', 'muted', 'Previously answered · review only. Browser Forward returns to later questions.'));
    reveal(question);
    const details = node('details', 'card'); details.append(node('summary', 'card-header', 'Keep or correct the verdict'));
    const controls = node('div', 'card-body');
    const fieldset = node('fieldset'); fieldset.append(node('legend', '', 'Final verdict for this question'));
    const actions = node('div', 'lesson-actions');
    for (const rating of question.ratings) {
      const radio = node('input'); radio.type = 'radio'; radio.name = 'rating'; radio.value = rating.value;
      radio.checked = ratings[cursor].rating === rating.value; radio.disabled = !active;
      radio.addEventListener('change', () => {
        if (sync.blocked || endRequested || !active) return;
        ratings[cursor].rating = radio.value; paintVerdict(question, radio.value); sync.changed(); renderActions();
      });
      const label = node('label', 'radio-label'); label.append(radio, node('span', '', rating.label)); fieldset.append(label);
    }
    controls.append(fieldset);
    if (question.controls.length) {
      const label = node('label', '', 'Extra adjustment'); label.htmlFor = 'lesson-adjustment';
      const select = node('select'); select.id = 'lesson-adjustment'; select.disabled = !active;
      const none = node('option', '', 'No extra adjustment'); none.value = ''; select.append(none);
      for (const control of question.controls) { const option = node('option', '', control.label); option.value = control.value; select.append(option); }
      select.value = ratings[cursor].control || '';
      select.addEventListener('change', () => { if (sync.blocked || endRequested || !active) return; ratings[cursor].control = select.value || null; sync.changed(); });
      controls.append(label, select);
    }
    details.append(controls); stage.append(details, actions);
    function advance() {
      if (sync.blocked || endRequested) return;
      if (!active) { go(cursor === 19 ? 'complete' : cursor + 1); return; }
      if (cursor === 19) {
        endRequested = true; stage.inert = true; sync.finish();
      } else { sync.state.cursor++; sync.changed(); go(cursor + 1); }
    }
    function renderActions() {
      actions.replaceChildren();
      const last = cursor === 19;
      const title = !active && last ? 'Lesson summary' : last ? 'End lesson' : 'Next question →';
      if (active && !isCorrect(question, ratings[cursor].rating) && !attempts.get(cursor)?.retypeDone) {
        const form = node('form', 'lesson-retype'); form.setAttribute('data-retype', '');
        const label = node('label', '', 'Retype the correct answer to continue'); label.htmlFor = 'lesson-retype';
        const input = node('textarea'); input.id = 'lesson-retype'; input.rows = 2; input.maxLength = 500;
        input.autocomplete = 'off'; input.spellcheck = false; input.setAttribute('enterkeyhint', 'done');
        input.setAttribute('aria-describedby', 'lesson-retype-feedback');
        const feedback = node('p', 'muted', 'Use the same spelling rules as the answer matcher. Shift+Enter adds a newline.');
        feedback.id = 'lesson-retype-feedback'; feedback.setAttribute('role', 'status');
        const next = node('button', 'primary', title);
        const skip = node('button', '', 'Skip retype'); skip.type = 'button';
        if (last) skip.setAttribute('aria-label', 'Skip retype and end lesson');
        skip.addEventListener('click', () => {
          if (sync.blocked || endRequested) return;
          remember(cursor, { retypeDone: true }); advance();
        });
        keyboardSubmit(form, input);
        input.addEventListener('input', () => { input.removeAttribute('aria-invalid'); form.removeAttribute('data-shake'); });
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          if (sync.blocked || endRequested) return;
          if (!fcardAnswersMatch(input.value, question.expected)) {
            input.setAttribute('aria-invalid', 'true');
            feedback.textContent = 'Not quite — retype the correct answer shown above, or choose Skip retype.';
            form.removeAttribute('data-shake'); void form.offsetWidth; form.setAttribute('data-shake', 'true');
            input.focus(); return;
          }
          remember(cursor, { retypeDone: true }); advance();
        });
        const buttons = node('div', 'actions'); buttons.append(next, skip);
        form.append(label, input, feedback, buttons); actions.append(form); input.focus();
      } else {
        const next = node('button', 'primary', title); next.addEventListener('click', advance);
        actions.append(next); next.focus();
      }
    }
    renderActions();
  }
  try {
    data = await request(root.dataset.url);
    loadNotes();
    sync = new LessonSync(data, {
      request: (finish, payload) => request(finish ? root.dataset.finishUrl : root.dataset.stateUrl, payload),
      storage, key, status: (text) => { status.textContent = text; }, error: showError,
      complete: (result) => {
        data = { ...data, ...result }; endRequested = false;
        if (view === 19) go('complete'); else render();
      },
    });
    endRequested = Boolean(sync.retry?.finish);
    if (!sync.blocked) status.textContent = sync.completed ? 'Lesson complete — scheduling updated.' : 'Progress saved. Answers are checked on this device.';
    if (!window.location.hash && sync.completed) window.history.replaceState(null, '', '#complete');
    showRoute(); sync.flush();
    const historyChanged = () => { if (window.location.hash !== displayedHash) showRoute(); };
    window.addEventListener('popstate', historyChanged);
    window.addEventListener('hashchange', historyChanged);
    window.addEventListener('online', () => sync.flush());
    window.addEventListener('pagehide', () => sync.flush());
    document.addEventListener('visibilitychange', () => { if (document.hidden) sync.flush(); });
  } catch (error) { showError(error.message, error.status); }
});
