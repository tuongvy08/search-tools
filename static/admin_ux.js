/* Phase 6C4: prevent accidental duplicate submits on destructive admin forms. */
(() => { document.querySelectorAll('form[data-admin-submit],form.apply-form').forEach((form) => form.addEventListener('submit', () => { const button=form.querySelector('button[type="submit"]'); if (button && !button.disabled) { button.disabled=true; button.setAttribute('aria-busy','true'); } })); })();
