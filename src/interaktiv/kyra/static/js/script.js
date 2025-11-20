document.addEventListener('DOMContentLoaded', function() {
  const createButton = document.getElementById('create-prompt-button');
  const createForm = document.querySelector('.content fieldset.prompt-create-form');

  if (createButton && createForm) {
    createButton.addEventListener('click', function() {

      createForm.classList.toggle('is-visible');

      if (createForm.classList.contains('is-visible')) {
        createButton.textContent = createButton.getAttribute('data-hide-text') || 'Hide Form';
      } else {
        createButton.textContent = createButton.getAttribute('data-show-text') || 'Create new Prompt';
      }
    });
  }
});
