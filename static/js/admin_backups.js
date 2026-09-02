// Page « Sauvegardes » de l'administration.
// Extrait du fragment templates/admin/app_backups.html (Phase 8, point 2).
//
// Le fragment etant reinjecte par HTMX, son <script> etait rejoue a chaque
// echange : les ecouteurs `change` poses sur chaque case a cocher s'empilaient.
// Les fonctions ci-dessous restent des globales (appelees par les onclick du
// fragment) ; ce qui doit s'EXECUTER passe par la delegation et par une
// initialisation declenchee a l'arrivee du fragment.

function toggleAllExportChecks(checked) {
    document.querySelectorAll('.export-chk').forEach(function(chk) {
        chk.checked = checked;
    });
    updateExportEstimate();
}

function selectedExportSections() {
    var checks = document.querySelectorAll('.export-chk:checked');
    return Array.from(checks).map(function(c) { return c.value; });
}

function prepareExport() {
    var sections = selectedExportSections();
    if (sections.length === document.querySelectorAll('.export-chk').length) {
        document.getElementById('export_sections_input').value = 'all';
    } else {
        document.getElementById('export_sections_input').value = sections.join(',');
    }
}

// Estimation de la taille des images sélectionnées (point 13) : avertit
// l'utilisateur avant un export lourd.
function updateExportEstimate() {
    var box = document.getElementById('exportSizeEstimate');
    if (!box) { return; }
    var imgSelected = Array.from(document.querySelectorAll('.export-img-chk:checked'))
        .map(function(c) { return c.value; });
    if (imgSelected.length === 0) {
        box.innerHTML = '';
        return;
    }
    box.innerHTML = '<span class="text-muted"><i class="bi bi-hourglass-split"></i> Estimation de la taille des images…</span>';
    fetch('/admin/backup/estimate?sections=' + encodeURIComponent(imgSelected.join(',')), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
        .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function(est) {
            var cls = est.heavy ? 'alert alert-warning py-2' : 'alert alert-info py-2';
            var icon = est.heavy ? 'bi-exclamation-triangle' : 'bi-info-circle';
            var msg = '<i class="bi ' + icon + '"></i> Images sélectionnées : <strong>'
                + est.images_human + '</strong>.';
            if (est.heavy) {
                msg += ' Cet export sera volumineux (seuil ' + est.warning_human
                    + ') ; la sauvegarde sera produite en archive .zip.';
            } else {
                msg += ' La sauvegarde sera produite en archive .zip.';
            }
            box.innerHTML = '<div class="' + cls + ' mb-0">' + msg + '</div>';
        })
        .catch(function() {
            box.innerHTML = '<span class="text-muted small">Estimation de taille indisponible.</span>';
        });
}

// --- Comportements delegues (poses une seule fois) -------------------------

// Cocher/decocher une section met a jour « Tout selectionner » et l'estimation.
document.addEventListener('change', function (evt) {
    if (!evt.target || !evt.target.matches || !evt.target.matches('.export-chk')) { return; }
    synchroniserToutSelectionner();
    updateExportEstimate();
});

// Changer de fichier invalide la previsualisation et le dernier rapport : sans
// cela, le bouton « Restaurer » enverrait le NOUVEAU fichier avec les sections
// cochees de l'ANCIENNE previsualisation.
document.addEventListener('change', function (evt) {
    if (!evt.target || evt.target.id !== 'importFileInput') { return; }
    var previsualisation = document.getElementById('importPreviewResult');
    var rapport = document.getElementById('importResult');
    if (previsualisation) { previsualisation.innerHTML = ''; }
    if (rapport) { rapport.innerHTML = ''; }
});

function synchroniserToutSelectionner() {
    var tout = document.getElementById('chk_export_all');
    if (!tout) { return; }
    tout.checked = document.querySelectorAll('.export-chk').length
        === document.querySelectorAll('.export-chk:checked').length;
}

// Etat initial a l'arrivee du fragment : les images sont decochees, donc
// « Tout selectionner » aussi.
function initSauvegardes(racine) {
    var portee = racine || document;
    if (!portee.querySelector || !portee.querySelector('.export-chk')) {
        if (!(portee.matches && portee.matches('.export-chk'))) { return; }
    }
    synchroniserToutSelectionner();
    updateExportEstimate();
}

document.addEventListener('htmx:afterSettle', function (evt) {
    var cible = evt.detail && evt.detail.target;
    if (cible) { initSauvegardes(cible); }
});
document.addEventListener('DOMContentLoaded', function () { initSauvegardes(document); });
