const quickAdd = document.querySelector('[data-quick-add]');
if (quickAdd) {
  const front = quickAdd.elements.front;
  const back = quickAdd.elements.back;
  quickAdd.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
    if (event.target !== front && event.target !== back) return;
    event.preventDefault();
    if (event.target === front) back.focus();
    else quickAdd.requestSubmit();
  });
}
