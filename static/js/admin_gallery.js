// Extrait de admin.js (Phase 8, point 1) : ce comportement ne concerne
// que la page Galerie. Charge via `{% block scripts_end %}`, donc apres
// admin.js et admin_macros.js (`defer` respecte l'ordre du document).
//
// Tout est delegue sur `document` : enregistre une seule fois, et valable
// pour les fragments injectes plus tard par HTMX.

(function () {
    'use strict';

    // --- Choix des fichiers ------------------------------------------------

    // Verifie la selection AVANT l'envoi (nombre et poids) : l'utilisateur
    // n'attend pas la fin d'un long televersement pour apprendre un refus.
    function decrireSelection(champ) {
        var bouton = document.getElementById('upload-button');
        var resume = document.getElementById('gallery-upload-selection');
        var fichiers = Array.prototype.slice.call(champ.files || []);
        var maxFichiers = Number(champ.dataset.maxFiles || 0);
        var maxOctets = Number(champ.dataset.maxMb || 0) * 1024 * 1024;
        var tropLourds = fichiers.filter(function (f) { return maxOctets && f.size > maxOctets; });
        var message = '';
        var bloque = fichiers.length === 0;

        if (maxFichiers && fichiers.length > maxFichiers) {
            message = fichiers.length + ' fichiers choisis : ' + maxFichiers + ' au maximum par envoi.';
            bloque = true;
        } else if (tropLourds.length) {
            message = 'Trop volumineux (plus de ' + champ.dataset.maxMb + ' Mo) : '
                + tropLourds.map(function (f) { return f.name; }).join(', ') + '.';
            bloque = true;
        } else if (fichiers.length) {
            var total = fichiers.reduce(function (somme, f) { return somme + f.size; }, 0);
            message = fichiers.length + (fichiers.length > 1 ? ' images prêtes' : ' image prête')
                + ' à envoyer (' + (total / 1048576).toFixed(1).replace('.', ',') + ' Mo).';
        }
        if (resume) {
            resume.textContent = message;
            resume.classList.toggle('text-danger', bloque && fichiers.length > 0);
        }
        if (bouton) { bouton.disabled = bloque; }
    }

    document.addEventListener('change', function (evt) {
        if (evt.target && evt.target.id === 'file-input') { decrireSelection(evt.target); }
    });

    // Glisser-deposer sur la zone d'ajout.
    function zoneDepot(evt) {
        return evt.target && evt.target.closest ? evt.target.closest('.gallery-dropzone') : null;
    }
    ['dragenter', 'dragover'].forEach(function (type) {
        document.addEventListener(type, function (evt) {
            var zone = zoneDepot(evt);
            if (!zone) { return; }
            evt.preventDefault();
            zone.classList.add('is-dragover');
        });
    });
    document.addEventListener('dragleave', function (evt) {
        var zone = zoneDepot(evt);
        if (zone && !zone.contains(evt.relatedTarget)) { zone.classList.remove('is-dragover'); }
    });
    document.addEventListener('drop', function (evt) {
        var zone = zoneDepot(evt);
        if (!zone) { return; }
        evt.preventDefault();
        zone.classList.remove('is-dragover');
        var champ = zone.querySelector('#file-input');
        if (!champ || !evt.dataTransfer || !evt.dataTransfer.files.length) { return; }
        champ.files = evt.dataTransfer.files;
        decrireSelection(champ);
    });

    // --- Apres une requete -------------------------------------------------

    document.body.addEventListener('htmx:afterRequest', function (evt) {
        var detail = evt.detail || {};
        var source = detail.elt;
        if (!source || !detail.successful) { return; }
        // Envoi termine : formulaire remis a zero (204 = refus, on garde la selection).
        if (source.id === 'gallery-upload-form' && detail.xhr && detail.xhr.status === 200) {
            source.reset();
            var champ = document.getElementById('file-input');
            if (champ) { decrireSelection(champ); }
        }
        // Galerie creee : formulaire vide et replie.
        if (source.id === 'create-gallery-form' && detail.xhr && detail.xhr.status === 200) {
            source.reset();
            var panneau = document.getElementById('create-gallery-panel');
            if (panneau && window.bootstrap) {
                var repli = bootstrap.Collapse.getOrCreateInstance(panneau, { toggle: false });
                // Bootstrap ignore hide() pendant l'animation d'ouverture.
                if (panneau.classList.contains('collapsing')) {
                    panneau.addEventListener('shown.bs.collapse', function () { repli.hide(); }, { once: true });
                } else {
                    repli.hide();
                }
            }
        }
    });

    // --- Clics -------------------------------------------------------------

    document.addEventListener('click', function (evt) {
        var cible = evt.target && evt.target.closest ? evt.target : null;
        if (!cible) { return; }

        // « Fermer » la galerie ouverte.
        if (cible.closest('[data-gallery-close]')) {
            var contenu = document.getElementById('content');
            if (contenu) { contenu.innerHTML = ''; }
            return;
        }

        // Vignette : ouvre l'image d'origine dans la modale Bootstrap.
        var vignette = cible.closest('.gallery-thumb');
        if (!vignette) { return; }
        var modale = document.getElementById('image-modal');
        var image = document.getElementById('modal-image');
        if (!modale || !image) { return; }
        var titre = document.getElementById('image-modal-title');
        image.src = vignette.dataset.fullSrc;
        image.alt = vignette.dataset.label || '';
        if (titre) { titre.textContent = vignette.dataset.label || 'Image'; }
        // getOrCreateInstance : getInstance renvoyait null tant que la modale
        // n'avait jamais ete ouverte — le clic ne faisait donc rien.
        bootstrap.Modal.getOrCreateInstance(modale).show();
    });
})();
