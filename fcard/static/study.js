const studyAnswer = document.querySelector('[data-study-answer]');
if (studyAnswer) {
  studyAnswer.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    studyAnswer.form.requestSubmit();
  });
}
