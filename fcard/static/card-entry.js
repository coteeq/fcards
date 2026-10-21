document.querySelectorAll('[data-card-entry]').forEach((form) => {
  const wordSelector = '[name="front"], [name="back"], [name="front_text"], [name="back_text"]';
  const entrySelector = wordSelector + ', [name="front_description"], [name="back_description"], [name="meta"]';
  const panel = document.getElementById(form.dataset.suggestionsTarget);
  const status = panel.querySelector('[data-duplicate-status]');
  const list = panel.querySelector('[data-duplicate-list]');
  let timer, controller, version = 0, composing = false, lastKey = '';

  function cancel() {
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    version++;
    panel.hidden = true;
    panel.removeAttribute('aria-busy');
  }

  async function check(words, serial) {
    controller = new AbortController();
    panel.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(form.dataset.suggestionsUrl, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': form.elements.csrf.value },
        body: JSON.stringify({ words, exclude: Number(form.dataset.cardId) || null }),
      });
      const data = await response.json();
      if (serial !== version) return;
      if (!response.ok) throw new Error('Check unavailable');
      list.replaceChildren();
      for (const card of data.results) {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = card.url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = `${card.front} ↔ ${card.back} · ${card.deck} (matches “${card.match}”)`;
        item.append(link);
        list.append(item);
      }
      status.textContent = data.more
        ? 'These words may already exist (showing five cards). You can still save.'
        : 'These words may already exist. You can still save.';
      panel.hidden = data.results.length === 0;
    } catch (error) {
      if (serial !== version || error.name === 'AbortError') return;
      list.replaceChildren();
      status.textContent = 'Could not check existing words. You can still save.';
      panel.hidden = false;
    } finally {
      if (serial === version) {
        panel.removeAttribute('aria-busy');
        controller = null;
      }
    }
  }

  function schedule() {
    if (composing) return;
    const words = [...new Set([...form.querySelectorAll(wordSelector)]
      .map((input) => input.value.trim())
      .filter((word) => word && word.length <= 500))].sort();
    const key = JSON.stringify(words);
    if (key === lastKey) return;
    lastKey = key;
    cancel();
    list.replaceChildren();
    if (!words.length) return;
    const serial = version;
    timer = setTimeout(() => check(words, serial), 300);
  }

  form.addEventListener('input', (event) => {
    if (!event.target.matches(wordSelector)) return;
    if (event.isComposing) { cancel(); lastKey = ''; }
    else schedule();
  });
  form.addEventListener('compositionstart', (event) => {
    if (!event.target.matches(wordSelector)) return;
    composing = true;
    cancel();
    lastKey = '';
  });
  form.addEventListener('compositionend', () => { composing = false; schedule(); });
  form.addEventListener('card-forms-changed', schedule);
  form.addEventListener('submit', (event) => {
    form.querySelectorAll(entrySelector).forEach((input) => { input.value = input.value.trim(); });
    if (event.submitter?.formNoValidate) return;
    if (!form.reportValidity()) {
      event.preventDefault();
      return;
    }
    cancel();
  });
  schedule();
});
