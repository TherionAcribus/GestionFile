// Extrait de admin.js (Phase 8, point 1) : ce comportement ne concerne
// que la page Galerie. Charge via `{% block scripts_end %}`, donc apres
// admin.js et admin_macros.js (`defer` respecte l'ordre du document).
//
// La delegation reste posee sur `document` : elle couvre donc aussi les
// fragments injectes plus tard par HTMX.

// --- Galerie d'images -----------------------------------------------------
//
// Ces deux comportements vivaient dans des <script> de fragments HTMX, donc
// rejoues a chaque echange : les ecouteurs s'empilaient (notamment celui pose
// sur document.body par gallery_manage). Delegues ici, ils sont enregistres une
// seule fois et couvrent le contenu injecte plus tard.

// Le bouton d'envoi ne s'active qu'une fois un fichier choisi.
document.addEventListener('change', function (evt) {
    if (!evt.target || evt.target.id !== 'file-input') { return; }
    var bouton = document.getElementById('upload-button');
    if (bouton) { bouton.disabled = evt.target.files.length === 0; }
});

// Apres l'envoi (la liste d'images est rafraichie), on remet le formulaire a zero.
document.body.addEventListener('htmx:afterRequest', function (evt) {
    if (!evt.detail || !evt.detail.target || evt.detail.target.id !== 'image-list') { return; }
    var bouton = document.getElementById('upload-button');
    var champ = document.getElementById('file-input');
    if (bouton) { bouton.disabled = true; }
    if (champ) { champ.value = ''; }
});

// Clic sur une vignette : ouvre l'image dans la modale Bootstrap.
document.addEventListener('click', function (evt) {
    var img = evt.target;
    if (!img || !img.closest || !img.closest('.thumbnail') || img.tagName !== 'IMG') { return; }
    var modale = document.getElementById('image-modal');
    var cible = document.getElementById('modal-image');
    if (!modale || !cible) { return; }
    // data-full-src pointe vers l'image originale (la vignette peut être une
    // miniature légère) ; sans cet attribut, on repli sur le src de la vignette.
    cible.src = img.dataset.fullSrc || img.src;
    // La modale est une modale Bootstrap (class="modal fade"), pas Materialize.
    // L'ancien code appelait M.Modal (Materialize) qui n'est pas chargé sur
    // les pages admin -> ReferenceError.
    var instance = bootstrap.Modal.getInstance(modale);
    if (instance) { instance.show(); }
});
