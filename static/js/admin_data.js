// Script de la page admin/data.html, extrait du gabarit (Phase 8, point 2).
// Charge en fin de page via le bloc `scripts_end` de admin/base.html : le
// navigateur peut le mettre en cache, et le gabarit redevient du HTML.

// --- Décompte préalable (modale de confirmation) ---------------------------

function fetchDataPreview(days, target) {
    return fetch('/admin/data/preview?days=' + encodeURIComponent(days) + '&target=' + target)
        .then(function (response) { return response.json(); });
}

function describePreview(info) {
    if (!info.rows) {
        return '<p><strong>Aucune ligne</strong> n\'est concernée par ce critère.</p>';
    }
    var range = (info.oldest && info.newest)
        ? ' (du <strong>' + info.oldest + '</strong> au <strong>' + info.newest + '</strong>)'
        : '';
    var days = info.days ? ', réparties sur <strong>' + info.days + ' jour(s)</strong>' : '';
    return '<p><strong>' + info.rows + ' ligne(s)</strong>' + days + ' concernée(s)' + range + '.</p>';
}

// --- Espace disque : logique réutilisable vs physique -----------------------

function loadStorageStats() {
    var container = document.getElementById('storageStats');
    if (!container) { return; }

    fetch('/admin/data/storage')
        .then(function (response) { return response.json(); })
        .then(function (data) {
            var cmdEl = document.getElementById('maintenanceCommand');
            if (!data.success || !data.supported) {
                container.innerHTML =
                    '<span class="text-muted">Relevé indisponible' +
                    (data.engine ? ' pour ce moteur (' + data.engine + ')' : '') +
                    '.</span>';
                if (cmdEl) { cmdEl.textContent = 'OPTIMIZE TABLE / VACUUM selon le moteur'; }
                return;
            }

            var rows = data.tables.map(function (t) {
                return '<tr><td><code>' + t.name + '</code></td>' +
                    '<td class="text-end">' + (t.rows_estimate == null ? '—' : t.rows_estimate) + '</td>' +
                    '<td class="text-end">' + t.physical + '</td>' +
                    '<td class="text-end">' + t.reusable + '</td></tr>';
            }).join('');

            container.innerHTML =
                '<table class="table table-sm mb-0">' +
                '<thead><tr><th>Table</th><th class="text-end">Lignes (est.)</th>' +
                '<th class="text-end">Taille physique</th>' +
                '<th class="text-end">Espace réutilisable</th></tr></thead>' +
                '<tbody>' + rows + '</tbody></table>';
            if (cmdEl) { cmdEl.textContent = data.maintenance; }
        })
        .catch(function () {
            container.innerHTML =
                '<span class="text-muted">Erreur réseau lors du relevé.</span>';
        });
}

function showModalError(modalBody, message) {
    modalBody.innerHTML =
        '<div class="alert alert-danger">' + message + '</div>' +
        '<div class="d-flex justify-content-end">' +
        '<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fermer</button>' +
        '</div>';
}

// Ouvre la modale de confirmation après décompte. `mode` : 'archive' (agrège
// puis supprime) ou 'purge' (suppression définitive, sans statistiques).
function openDataConfirmModal(mode, days, target) {
    var daysInt = parseInt(days, 10);
    var modal = new bootstrap.Modal(document.getElementById('modal_delete'));
    var modalBody = document.getElementById('modal-htmx');
    var isPurge = mode === 'purge';
    var isAggregated = target === 'aggregated';

    if (!Number.isFinite(daysInt)) {
        showModalError(modalBody, 'Nombre de jours invalide.');
        modal.show();
        return;
    }

    document.getElementById('modalDeleteLabel').textContent = isPurge
        ? 'Confirmer la purge définitive'
        : (isAggregated ? 'Confirmer la suppression' : 'Confirmer l\'archivage');

    modalBody.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Décompte des lignes concernées...';
    modal.show();

    fetchDataPreview(daysInt, target)
        .then(function (info) {
            if (!info.success) {
                showModalError(modalBody, info.message || 'Le décompte a échoué.');
                return;
            }

            var explanation;
            if (isAggregated) {
                explanation = '<p>Les <strong>statistiques agrégées</strong> plus anciennes que ' +
                    '<strong>' + daysInt + ' jours</strong> seront supprimées.</p>';
            } else if (isPurge) {
                explanation = '<p>Les lignes détaillées de l\'historique plus anciennes que ' +
                    '<strong>' + daysInt + ' jours</strong> seront <strong>supprimées sans ' +
                    'aucune agrégation</strong> : aucune statistique ne sera conservée.</p>';
            } else {
                explanation = '<p>Les lignes détaillées de l\'historique plus anciennes que ' +
                    '<strong>' + daysInt + ' jours</strong> seront remplacées par des ' +
                    '<strong>statistiques quotidiennes agrégées</strong>. Les dossiers ' +
                    'individuels seront supprimés.</p>';
            }

            var diskNote = isAggregated ? '' :
                '<p class="text-muted small mb-2">L\'espace libéré reste ' +
                'réutilisable par la base ; le fichier disque n\'est réduit ' +
                'que par une maintenance planifiée (carte « Espace disque »).</p>';

            modalBody.innerHTML =
                explanation +
                describePreview(info) +
                diskNote +
                '<p class="text-danger">Cette action est irréversible.</p>' +
                '<div class="d-flex justify-content-end gap-2">' +
                '<button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>' +
                '<button type="button" class="btn ' + (isPurge ? 'btn-danger' : 'btn-warning') + '" ' +
                'data-confirm-mode="' + mode + '" data-confirm-days="' + daysInt + '" ' +
                'data-confirm-target="' + target + '"' +
                (info.rows ? '' : ' disabled') + '>Confirmer</button>' +
                '</div>';
        })
        .catch(function () {
            showModalError(modalBody, 'Erreur réseau lors du décompte.');
        });
}

// --- Actions ----------------------------------------------------------------

// mode 'archive' -> POST /admin/data/archive ; 'purge' -> POST /admin/data/purge
function runDataOperation(mode, days) {
    var modal = bootstrap.Modal.getInstance(document.getElementById('modal_delete'));
    var isPurge = mode === 'purge';
    var resultDiv = document.getElementById(isPurge ? 'purgeResult' : 'manualResult');
    var backupCheck = document.getElementById(isPurge ? 'purgeBackupCheck' : 'archiveBackupCheck');
    var backup = backupCheck ? backupCheck.checked : false;

    if (modal) { modal.hide(); }

    resultDiv.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Traitement en cours...';

    fetch(isPurge ? '/admin/data/purge' : '/admin/data/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'days=' + encodeURIComponent(days) + '&backup=' + backup
    })
    .then(function (response) { return response.json(); })
    .then(function (data) {
        if (data.success) {
            resultDiv.innerHTML = '<div class="alert alert-success">' + data.message + '</div>';
            setTimeout(function () { location.reload(); }, 2000);
        } else {
            // partial : échec après journées déjà validées — avertissement
            // (résultat partiel), pas simple erreur.
            var cls = data.partial ? 'alert-warning' : 'alert-danger';
            resultDiv.innerHTML = '<div class="alert ' + cls + '">Erreur: ' + data.message + '</div>';
        }
        loadStorageStats();
    })
    .catch(function (error) {
        console.error('Error:', error);
        resultDiv.innerHTML = '<div class="alert alert-danger">Erreur réseau</div>';
    });
}

function deleteAggregated(days) {
    var modal = bootstrap.Modal.getInstance(document.getElementById('modal_delete'));
    var resultDiv = document.getElementById('deleteAggregatedResult');

    if (modal) { modal.hide(); }

    resultDiv.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Suppression en cours...';

    fetch('/admin/data/delete_aggregated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'days=' + encodeURIComponent(days)
    })
    .then(function (response) { return response.json(); })
    .then(function (data) {
        if (data.success) {
            resultDiv.innerHTML = '<div class="alert alert-success">' + data.message + '</div>';
            setTimeout(function () { location.reload(); }, 2000);
        } else {
            resultDiv.innerHTML = '<div class="alert alert-danger">Erreur: ' + data.message + '</div>';
        }
        loadStorageStats();
    })
    .catch(function (error) {
        console.error('Error:', error);
        resultDiv.innerHTML = '<div class="alert alert-danger">Erreur réseau</div>';
    });
}

function saveAutoConfig() {
    const days = document.getElementById('autoDays').value;
    const compress = document.getElementById('autoCompress').checked;
    const enabled = document.getElementById('autoArchiveEnabled').checked;
    
    fetch('/admin/data/config', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `archive_days=${days}&archive_compressed=${compress}&auto_archive_enabled=${enabled}`
    })
    .then(response => response.json())
    .then(data => {
        const resultDiv = document.getElementById('configResult');
        if (data.success) {
            // warning : config persistée mais scheduler non mis à jour — le
            // succès affiché doit rester honnête.
            const cls = data.warning ? 'alert-warning' : 'alert-success';
            const msg = data.warning ? `${data.message} ${data.warning}` : data.message;
            resultDiv.innerHTML = `<div class="alert ${cls}">${msg}</div>`;
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">Erreur: ${data.message}</div>`;
        }
    });
}

// --- Comportements délégués (pas de onclick inline, CSP script-src 'self') ---

document.addEventListener('click', function (evt) {
    if (!evt.target || !evt.target.closest) { return; }
    var btn;
    if (evt.target.closest('#btn-manual-archive')) {
        openDataConfirmModal('archive', document.getElementById('daysInput').value, 'history');
    } else if (evt.target.closest('#btn-purge-history')) {
        openDataConfirmModal('purge', document.getElementById('purgeDaysInput').value, 'history');
    } else if (evt.target.closest('#btn-delete-aggregated')) {
        openDataConfirmModal('aggregated', document.getElementById('aggregatedDaysInput').value, 'aggregated');
    } else if (evt.target.closest('#btn-save-auto-config')) {
        saveAutoConfig();
    } else if ((btn = evt.target.closest('[data-confirm-days]'))) {
        if (btn.getAttribute('data-confirm-target') === 'aggregated') {
            deleteAggregated(btn.getAttribute('data-confirm-days'));
        } else {
            runDataOperation(btn.getAttribute('data-confirm-mode'), btn.getAttribute('data-confirm-days'));
        }
    }
});

// Relevé initial de la carte « Espace disque ».
document.addEventListener('DOMContentLoaded', loadStorageStats);
