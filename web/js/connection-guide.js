'use strict';
// Direction selection opens the existing controlled form. No network request,
// saved setting, checkbox, credential or runtime state is changed here.
(() => {
    const open = document.getElementById('open-connection-setup');
    const section = document.getElementById('connection-setup');
    const title = document.getElementById('connection-setup-title');
    if (!open || !section || !title) return;
    open.addEventListener('click', () => {
        section.open = true;
        title.focus();
    });
})();
