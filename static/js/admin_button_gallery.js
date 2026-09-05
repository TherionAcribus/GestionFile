// Extrait de admin.js (Phase 8, point 1) : ce comportement ne concerne
// que la modale de choix d'image d'un bouton (page Patient). Charge via `{% block scripts_end %}`, donc apres
// admin.js et admin_macros.js (`defer` respecte l'ordre du document).
//
// La delegation reste posee sur `document` : elle couvre donc aussi les
// fragments injectes plus tard par HTMX.

// --- Modale galerie de boutons : sélection et upload d'image ---------------
//
// Les handlers onclick/onchange inline des modales galerie (templates
// patient_page_button_modal_gallery*.html) sont incompatibles avec la CSP
// script-src 'self'. Remplacés par de la délégation d'événements sur data-*.
//
// 1. Clic sur une vignette : appelle selectImage (définie dans
//    admin_fragments.js, globale pour compat ascendante).
document.addEventListener('click', function (evt) {
    var btn = evt.target.closest('[data-select-image]');
    if (!btn) { return; }
    if (typeof selectImage === 'function') {
        selectImage(btn.dataset.selectImage);
    }
});

// 2. Bouton « Ouvrir une image » : déclenche le clic sur l'input file suivant.
document.addEventListener('click', function (evt) {
    var btn = evt.target.closest('[data-action="open-file-dialog"]');
    if (!btn) { return; }
    var input = btn.parentElement && btn.parentElement.querySelector('input[type="file"]');
    if (input) { input.click(); }
});

// 3. input file « Choisir un fichier » : soumet le formulaire parent
//    (soumission native, non interceptée par HTMX — le jeton CSRF est porté
//    par le champ caché csrf_token).
document.addEventListener('change', function (evt) {
    var input = evt.target.closest('[data-action="submit-on-change"]');
    if (!input) { return; }
    if (input.form) { input.form.submit(); }
});
