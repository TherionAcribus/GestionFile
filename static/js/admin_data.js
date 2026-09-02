// Script de la page admin/data.html, extrait du gabarit (Phase 8, point 2).
// Charge en fin de page via le bloc `scripts_end` de admin/base.html : le
// navigateur peut le mettre en cache, et le gabarit redevient du HTML.

function triggerManualArchive() {
    const btn = document.querySelector('#manualArchiveForm button');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Traitement...';
    
    const days = document.getElementById('daysInput').value;
    const compress = document.getElementById('compressCheck').checked;
    
    fetch('/admin/data/manual', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `days=${days}&compress=${compress}`
    })
    .then(response => response.json())
    .then(data => {
        const resultDiv = document.getElementById('manualResult');
        if (data.success) {
            resultDiv.innerHTML = `<div class="alert alert-success">${data.message}</div>`;
            setTimeout(() => location.reload(), 2000);
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">Erreur: ${data.message}</div>`;
            btn.disabled = false;
            btn.textContent = "Lancer l'archivage";
        }
    })
    .catch(error => {
        console.error('Error:', error);
        btn.disabled = false;
        btn.textContent = "Lancer l'archivage";
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
            resultDiv.innerHTML = `<div class="alert alert-success">${data.message}</div>`;
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">Erreur: ${data.message}</div>`;
        }
    });
}

function confirmDeleteAggregated() {
    const days = document.getElementById('aggregatedDaysInput').value;
    const modal = new bootstrap.Modal(document.getElementById('modal_delete'));
    const modalBody = document.getElementById('modal-htmx');
    
    // Configure modal content
    document.getElementById('modalDeleteLabel').textContent = 'Confirmer la suppression';
    modalBody.innerHTML = `
        <p>Êtes-vous sûr de vouloir supprimer les statistiques agrégées plus anciennes que <strong>${days} jours</strong> ?</p>
        <p class="text-danger">Cette action est irréversible.</p>
        <div class="d-flex justify-content-end gap-2">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
            <button type="button" class="btn btn-danger" onclick="deleteAggregated(${days})">Confirmer la suppression</button>
        </div>
    `;
    
    modal.show();
}

function deleteAggregated(days) {
    const modal = bootstrap.Modal.getInstance(document.getElementById('modal_delete'));
    const resultDiv = document.getElementById('deleteAggregatedResult');
    
    // Close modal
    modal.hide();
    
    // Show loading state
    resultDiv.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Suppression en cours...';
    
    fetch('/admin/data/delete_aggregated', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `days=${days}`
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            resultDiv.innerHTML = `<div class="alert alert-success">${data.message}</div>`;
            setTimeout(() => location.reload(), 2000);
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">Erreur: ${data.message}</div>`;
        }
    })
    .catch(error => {
        console.error('Error:', error);
        resultDiv.innerHTML = `<div class="alert alert-danger">Erreur réseau</div>`;
    });
}
