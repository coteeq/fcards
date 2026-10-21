document.querySelectorAll('[data-word-classification]').forEach((root) => {
  const form = root.closest('form');
  const type = root.querySelector('[data-choice="word_type"]');
  const gender = root.querySelector('[data-choice="gender"]');
  const mode = (group) => group.querySelector('input[type="hidden"]');
  const radios = (group) => [...group.querySelectorAll('input[type="radio"]')];
  const selected = (group) => radios(group).find((input) => input.checked)?.value || '';
  const select = (group, value) => radios(group).forEach((input) => { input.checked = input.value === value; });
  const articleGenders = { der: 'masculine', die: 'feminine', das: 'neuter' };

  function refresh() {
    const text = form.querySelector('[name="back"], [name="back_text"]').value.trim();
    const match = /^(der|die|das)\s+(\S[\s\S]*)$/i.exec(text);
    const article = match?.[1].toLowerCase();
    const word = match ? match[2] : text;
    const hint = word.split(/\s+/).filter(Boolean).length > 1 ? 'phrase'
      : article ? 'noun' : word.toLowerCase().endsWith('en') ? 'verb' : '';
    if (mode(type).value !== 'manual') select(type, hint);
    if (gender) {
      gender.hidden = selected(type) !== 'noun';
      if (mode(gender).value !== 'manual') select(gender, articleGenders[article] || '');
    }
    for (const group of [type, gender].filter(Boolean)) {
      const manual = mode(group).value === 'manual';
      group.dataset.selectionMode = manual ? 'manual' : 'auto';
      group.querySelector('[data-choice-status]').textContent = manual ? 'Manual · locked' : 'Automatic';
    }
  }

  function lock(event) {
    if (!event.target.matches('[name="word_type"], [name="gender"]')) return;
    const group = event.target.closest('[data-choice]');
    mode(group).value = 'manual';
    refresh();
  }
  // Click matters even for an already-selected automatic option; change also handles
  // keyboard arrow selection. Programmatically setting checked fires neither event.
  form.addEventListener('click', lock);
  form.addEventListener('change', lock);
  form.addEventListener('input', (event) => {
    if (event.target.matches('[name="back"], [name="back_text"]') && !event.isComposing) refresh();
  });
  form.addEventListener('compositionend', refresh);
  form.addEventListener('card-forms-changed', refresh);
  form.addEventListener('submit', refresh);
  refresh();
});
