document.querySelectorAll('[data-card-editor]').forEach((form) => {
  function rows(side) {
    return [...form.querySelector(`[data-form-rows="${side}"]`).children];
  }

  function renumber(side) {
    const current = rows(side);
    current.forEach((row, index) => {
      const number = index + 1;
      const text = row.querySelector(`[name="${side}_text"]`);
      const description = row.querySelector(`[name="${side}_description"]`);
      const remove = row.querySelector('.form-remove');
      const canonical = index === 0 ? ' (canonical)' : '';
      const label = `${side === 'front' ? 'Front' : 'Back'} form ${number}`;
      row.setAttribute('aria-label', label + canonical);
      text.id = `${side}-text-${number}`;
      text.required = index === 0;
      text.placeholder = index === 0 ? 'Text (canonical)' : 'Text';
      description.id = `${side}-description-${number}`;
      const labels = row.querySelectorAll('label');
      labels[0].htmlFor = text.id;
      labels[0].textContent = label + ' text' + canonical;
      labels[1].htmlFor = description.id;
      labels[1].textContent = label + ' description (optional)';
      remove.value = `remove_${side}_${index}`;
      remove.setAttribute('aria-label', `Remove ${side} form ${number}`);
      remove.disabled = current.length === 1;
      remove.title = remove.disabled ? 'Each side must keep one form' : 'Remove this form';
    });
    form.querySelector(`[value="add_${side}"]`).disabled = current.length >= 100;
  }

  function append(side) {
    const container = form.querySelector(`[data-form-rows="${side}"]`);
    const row = container.firstElementChild.cloneNode(true);
    row.querySelectorAll('input').forEach((input) => { input.value = ''; });
    container.append(row);
    return row;
  }

  form.addEventListener('click', (event) => {
    const button = event.target.closest('button[name="action"]');
    if (!button || !form.contains(button) || button.disabled) return;
    const action = /^(add|verb)_(front|back)$/.exec(button.value);
    const removal = /^remove_(front|back)_(\d+)$/.exec(button.value);
    if (!action && !removal) return;
    event.preventDefault();
    const side = action ? action[2] : removal[1];
    let focus;
    if (action?.[1] === 'add') {
      if (rows(side).length >= 100) return;
      focus = append(side);
    } else if (action?.[1] === 'verb') {
      const descriptions = ['', '1sg', '1pl', '2sg', '2pl', '3sg', '3pl'];
      while (rows(side).length < descriptions.length) append(side);
      rows(side).slice(0, 7).forEach((row, index) => {
        row.querySelector(`[name="${side}_description"]`).value = descriptions[index];
      });
      focus = rows(side)[0];
    } else {
      const index = Number(removal[2]);
      const current = rows(side);
      if (current.length === 1 || !current[index]) return;
      current[index].remove();
      focus = rows(side)[index] || rows(side).at(-1);
    }
    renumber(side);
    focus.querySelector('input').focus();
    form.dispatchEvent(new Event('card-forms-changed'));
  });

  form.querySelectorAll('button[name="action"]').forEach((button) => {
    if (button.value !== 'save') button.type = 'button';
  });
  renumber('front');
  renumber('back');
});
