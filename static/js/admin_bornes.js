// Confirmation avant retrait d'une borne de la supervision : la CSP interdit
// les handlers inline (onsubmit), la confirmation est donc déléguée ici via
// l'attribut data-confirm du formulaire.
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('form[data-confirm]').forEach(function(form) {
        form.addEventListener('submit', function(evt) {
            if (!window.confirm(form.getAttribute('data-confirm'))) {
                evt.preventDefault();
            }
        });
    });
});
