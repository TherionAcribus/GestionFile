// Extrait de admin.js (Phase 8, point 1) : ce comportement ne concerne
// que le televersement du drapeau d'une langue (page Traductions). Charge via `{% block scripts_end %}`, donc apres
// admin.js et admin_macros.js (`defer` respecte l'ordre du document).
//
// La delegation reste posee sur `document` : elle couvre donc aussi les
// fragments injectes plus tard par HTMX.

// Televersement du drapeau d'une langue.
//
// Deux gabarits (le formulaire d'ajout et le tableau) portaient chacun un
// <script> quasi identique qui faisait `querySelectorAll('input[type=file]')`
// puis attachait un ecouteur `change` a CHACUN. Comme les fragments sont
// reinjectes par HTMX, ces ecouteurs s'accumulaient a chaque echange.
//
// Ici : un seul ecouteur delegue sur `document`, pose une fois. Il ne reagit
// qu'aux champs marques `data-flag-upload`, et met a jour le champ cache
// designe par `data-flag-target` (seul point de variation entre les deux
// gabarits, desormais porte par le HTML et non par le JavaScript).
document.addEventListener('change', function (evt) {
    var input = evt.target;
    if (!input || !input.matches || !input.matches('input[type="file"][data-flag-upload]')) {
        return;
    }
    if (!input.files || !input.files[0]) { return; }

    var formData = new FormData();
    formData.append('file', input.files[0]);

    fetch('/admin/languages/upload_flag_image', { method: 'POST', body: formData })
        .then(function (response) { return response.json(); })
        .then(function (data) {
            if (!data.url) {
                alert("Erreur lors du telechargement de l'image : " + data.error);
                return;
            }
            var cible = document.getElementById(input.dataset.flagTarget);
            if (cible) { cible.value = data.url; }

            var cellule = input.closest('td');
            if (!cellule) { return; }
            var img = cellule.querySelector('img');
            if (img) {
                img.src = data.url;
            } else {
                var nouvelle = document.createElement('img');
                nouvelle.src = data.url;
                nouvelle.width = 30;
                cellule.prepend(nouvelle);
            }
        })
        .catch(function (error) { console.error('Erreur:', error); });
});
