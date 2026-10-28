// Onglet « Notifications du comptoir » (templates/admin/app_notifications.html).
// « Remettre le texte d'origine » recopie le texte par défaut dans le champ
// puis signale la saisie : le bouton « Enregistrer » s'active comme pour une
// frappe au clavier (admin_macros.js) — rien n'est enregistré sans clic.
document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-notif-reset]');
    if (!btn) return;
    var field = document.getElementById(btn.getAttribute('data-notif-reset'));
    if (!field) return;
    field.value = btn.getAttribute('data-default-text') || '';
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.focus();
});
