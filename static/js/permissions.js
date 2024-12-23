function updateSelectClass(select) {
    console.log('Updating select class:', select.value);
    select.classList.remove('select-none', 'select-read', 'select-write');
    select.classList.add('select-' + select.value);
}

function initializePermissionSelects() {
    console.log('Initializing selects');
    document.querySelectorAll('.permission-select').forEach(select => {
        updateSelectClass(select);
        // Retirer l'ancien event listener s'il existe
        select.removeEventListener('change', function() { updateSelectClass(this); });
        // Ajouter le nouveau
        select.addEventListener('change', function() {
            updateSelectClass(this);
        });
    });
}

// Initialiser au chargement de la page
initializePermissionSelects();

// Réinitialiser après les mises à jour HTMX
document.addEventListener('htmx:afterSettle', function(evt) {
    console.log('HTMX update detected');
    if (evt.detail.target.querySelector('.permission-select')) {
        initializePermissionSelects();
    }
});
