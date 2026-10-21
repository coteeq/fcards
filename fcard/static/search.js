document.querySelectorAll('[data-live-search]').forEach((form) => {
  const input = form.elements.q;
  const panel = form.querySelector('.live-search-panel');
  const status = panel.querySelector('.live-search-status');
  const list = panel.querySelector('.live-search-list');
  const all = panel.querySelector('.live-search-all');
  let timer, controller, version = 0, composing = false;

  function close() {
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    version++;
    panel.hidden = true;
    panel.removeAttribute('aria-busy');
    input.setAttribute('aria-expanded', 'false');
  }

  function show(message) {
    panel.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    status.textContent = message;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function load(query, serial) {
    controller = new AbortController();
    const url = new URL(form.dataset.liveUrl, window.location.href);
    url.searchParams.set('q', query);
    panel.setAttribute('aria-busy', 'true');
    show('Searching…');
    try {
      const response = await fetch(url, {
        signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      const data = await response.json();
      if (serial !== version) return;
      if (!response.ok) throw new Error(data.error || 'Search unavailable.');
      list.replaceChildren();
      for (const result of data.results) {
        const item = document.createElement('li');
        const link = element('a', 'live-search-result');
        link.href = result.url;
        const sides = element('span', 'live-search-sides');
        sides.append(element('span', '', result.front), element('span', '', result.back));
        let meta = result.deck;
        if (result.match) {
          meta += ` · ${result.match.text}`;
          if (result.match.description) meta += ` (${result.match.description})`;
        }
        link.append(sides, element('small', 'live-search-meta muted', meta));
        item.append(link);
        list.append(item);
      }
      const count = data.results.length;
      show(!data.searchable ? 'Enter English/German letters or digits.'
        : count ? `${count}${data.more ? '+' : ''} matching card${count === 1 ? '' : 's'}`
        : 'No matching cards.');
      const full = new URL(form.action, window.location.href);
      full.searchParams.set('q', query);
      all.href = full.href;
      all.hidden = count === 0;
    } catch (error) {
      if (serial !== version || error.name === 'AbortError') return;
      show(`${error.message} Use the Search button to continue.`);
    } finally {
      if (serial === version) {
        panel.removeAttribute('aria-busy');
        controller = null;
      }
    }
  }

  function schedule() {
    close();
    list.replaceChildren();
    all.hidden = true;
    if (composing || !input.value.trim() || input.value.length > 500) return;
    const query = input.value;
    const serial = version;
    timer = setTimeout(() => load(query, serial), 250);
  }

  input.addEventListener('input', (event) => {
    if (event.isComposing) close();
    else schedule();
  });
  input.addEventListener('compositionstart', () => { composing = true; close(); });
  input.addEventListener('compositionend', () => { composing = false; schedule(); });
  form.addEventListener('submit', close);
  form.addEventListener('keydown', (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      input.focus();
    } else if (!panel.hidden && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      const links = [...panel.querySelectorAll('a')].filter((link) => !link.hidden);
      if (!links.length) return;
      const current = links.indexOf(event.target);
      const next = current + (event.key === 'ArrowDown' ? 1 : -1);
      event.preventDefault();
      if (event.target === input) links[event.key === 'ArrowDown' ? 0 : links.length - 1].focus();
      else (links[next] || input).focus();
    }
  });
  document.addEventListener('pointerdown', (event) => {
    if (!form.contains(event.target)) close();
  });
});
