/* Phase 6C4: opt-in native form guard. Fetch/AJAX forms keep their lifecycle. */
(() => {
  const selector = 'form[data-admin-submit], form.apply-form';

  function reset(form) {
    delete form.dataset.adminSubmitting;
    form.removeAttribute('aria-busy');
  }

  document.querySelectorAll(selector).forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return;
      if (form.dataset.adminSubmitting === 'true') {
        event.preventDefault();
        return;
      }
      form.dataset.adminSubmitting = 'true';
      form.setAttribute('aria-busy', 'true');
    });
  });

  window.addEventListener('pageshow', () => {
    document.querySelectorAll(selector).forEach(reset);
  });
})();
