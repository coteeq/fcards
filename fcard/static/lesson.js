document.querySelectorAll('[data-lesson]').forEach(async (root) => {
  const stage = root.querySelector('[data-lesson-stage]');
  const status = root.querySelector('[data-sync-status]');
  const errorBox = root.querySelector('[data-lesson-error]');
  const useSaved = root.querySelector('[data-use-saved]');
  const key = 'fcard.lesson.' + root.dataset.lessonId;
  const storage = { getItem: (k) => localStorage.getItem(k), setItem: (k, v) => localStorage.setItem(k, v), removeItem: (k) => localStorage.removeItem(k) };
  let data, sync, endRequested = false;
  const ephemeral = new Map();
  const node = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  };
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
  function completed(result) {
    stage.inert = false;
    stage.replaceChildren(node('h3', '', 'Lesson complete'));
    stage.append(node('p', '', `${20 - result.skipped} reviews applied. ${result.skipped ? result.skipped + ' changed or removed questions were skipped to protect their schedules.' : 'All 20 questions are finished.'}`));
    const list = node('ol', 'lesson-results');
    data.questions.forEach((q, index) => list.append(node('li', '', `${q.prompt} — ${q.ratings.find((r) => r.value === result.state.ratings[index].rating).label}`)));
    stage.append(list);
  }
  function render() {
    if (sync.completed) { completed({ ...data, state: sync.state }); return; }
    stage.replaceChildren();
    stage.inert = sync.blocked || endRequested;
    const { cursor, ratings } = sync.state;
    const question = data.questions[cursor];
    stage.append(node('h3', 'card-header', `Question ${cursor + 1} / 20 · ${question.label}`));
    const body = node('div', 'card-body');
    const prompt = node('p', 'prompt text', question.prompt); prompt.id = 'lesson-prompt';
    body.append(prompt);
    stage.append(body);
    if (ratings.length === cursor) {
      const form = node('form');
      let input;
      if (question.kind === 'gender') {
        const fieldset = node('fieldset');
        fieldset.append(node('legend', '', 'What is the gender of this German word?'));
        for (const [value, label] of [['der', 'masculine'], ['die', 'feminine'], ['das', 'neuter']]) {
          const choice = node('input'); choice.type = 'radio'; choice.name = 'answer'; choice.value = value;
          const wrapper = node('label', 'radio-label'); wrapper.append(choice, node('span', '', `${value} (${label})`));
          fieldset.append(wrapper);
        }
        fieldset.setAttribute('aria-describedby', 'lesson-prompt');
        input = fieldset.querySelector('input'); form.append(fieldset);
      } else {
        const label = node('label', '', 'Your answer'); label.htmlFor = 'lesson-answer';
        input = node('textarea'); input.id = 'lesson-answer'; input.name = 'answer'; input.rows = 2; input.maxLength = 500;
        input.setAttribute('aria-describedby', 'lesson-prompt');
        input.autocomplete = 'off'; input.spellcheck = false; input.setAttribute('enterkeyhint', 'done');
        form.append(label, input);
      }
      const check = node('button', 'primary', 'Check answer');
      form.append(node('p', 'muted', question.kind === 'gender'
        ? 'Choose a gender, then check. Enter checks; arrow keys change the selection. Empty answers count as incorrect.'
        : 'Case is ignored; ß/ss and ü/ue match, but u does not match ü. Spaces and punctuation still matter. Enter checks; Shift+Enter adds a newline. Empty answers count as incorrect.'), check);
      form.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229 || event.target === check) return;
        event.preventDefault(); form.requestSubmit();
      });
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (sync.blocked || endRequested) return;
        const answer = question.kind === 'gender' ? [...form.querySelectorAll('input')].find((i) => i.checked)?.value || '' : input.value;
        const auto = fcardAnswersMatch(answer, question.expected) ? 'correct' : 'incorrect';
        ephemeral.set(cursor, { answer, auto });
        ratings.push({ rating: question.defaults[auto], control: null });
        sync.changed(); render();
      });
      body.append(form); input.focus();
    } else {
      const memory = ephemeral.get(cursor);
      body.append(node('p', '', memory ? `Automatic verdict: ${memory.auto}` : 'Saved verdict (the original answer is not stored).'));
      body.append(node('p', 'text', `Your answer: ${memory ? memory.answer || '(empty)' : '(not stored)'}`));
      body.append(node('p', 'text', 'Expected answer: ' + question.expected));
      reveal(question);
      const details = node('details', 'card'); details.append(node('summary', 'card-header', 'Keep or correct the verdict'));
      const controls = node('div', 'card-body');
      const fieldset = node('fieldset'); fieldset.append(node('legend', '', 'Final verdict for this question'));
      for (const rating of question.ratings) {
        const radio = node('input'); radio.type = 'radio'; radio.name = 'rating'; radio.value = rating.value;
        radio.checked = ratings[cursor].rating === rating.value;
        radio.addEventListener('change', () => { if (sync.blocked || endRequested) return; ratings[cursor].rating = radio.value; sync.changed(); });
        const label = node('label', 'radio-label'); label.append(radio, node('span', '', rating.label)); fieldset.append(label);
      }
      controls.append(fieldset);
      if (question.controls.length) {
        const label = node('label', '', 'Extra adjustment'); label.htmlFor = 'lesson-adjustment';
        const select = node('select'); select.id = 'lesson-adjustment';
        const none = node('option', '', 'No extra adjustment'); none.value = ''; select.append(none);
        for (const control of question.controls) { const option = node('option', '', control.label); option.value = control.value; select.append(option); }
        select.value = ratings[cursor].control || '';
        select.addEventListener('change', () => { if (sync.blocked || endRequested) return; ratings[cursor].control = select.value || null; sync.changed(); });
        controls.append(label, select);
      }
      details.append(controls); stage.append(details);
      const last = cursor === 19;
      const next = node('button', 'primary', last ? 'End lesson' : 'Next question →');
      next.addEventListener('click', () => {
        if (sync.blocked || endRequested) return;
        if (last) { endRequested = true; stage.inert = true; next.textContent = 'Ending lesson…'; sync.finish(); }
        else { sync.state.cursor++; sync.changed(); render(); }
      });
      stage.append(next); next.focus();
    }
  }
  try {
    data = await request(root.dataset.url);
    sync = new LessonSync(data, {
      request: (finish, payload) => request(finish ? root.dataset.finishUrl : root.dataset.stateUrl, payload),
      storage, key, status: (text) => { status.textContent = text; }, error: showError,
      complete: (result) => { data = { ...data, ...result }; completed(result); },
    });
    endRequested = Boolean(sync.retry?.finish);
    if (!sync.blocked) status.textContent = sync.completed ? 'Lesson complete — scheduling updated.' : 'Progress saved. Answers are checked on this device.';
    render();
    sync.flush();
    window.addEventListener('online', () => sync.flush());
    window.addEventListener('pagehide', () => sync.flush());
    document.addEventListener('visibilitychange', () => { if (document.hidden) sync.flush(); });
  } catch (error) { showError(error.message, error.status); }
});
