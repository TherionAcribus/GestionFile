// Page Traductions — filtrage de la liste des textes à traduire.
//
// La liste (#translations_list) est réinjectée par HTMX à chaque changement
// de langue cible et après la collecte : les écouteurs sont délégués sur
// `document`, posés une fois — comme admin_flag_upload.js — et restent
// actifs après chaque échange sans s'accumuler.

function filterTranslationItems() {
    var list = document.getElementById('translations_list');
    if (!list) { return; }

    var search = document.getElementById('translation_filter');
    var query = (search && search.value ? search.value : '').toLowerCase();
    var onlyMissing = document.getElementById('translation_only_missing');
    var missing = !!(onlyMissing && onlyMissing.checked);

    list.querySelectorAll('.translation-item').forEach(function (item) {
        var matchesQuery = !query ||
            item.textContent.toLowerCase().indexOf(query) !== -1;
        var matchesMissing = !missing || item.hasAttribute('data-missing');
        item.hidden = !(matchesQuery && matchesMissing);
    });
}

document.addEventListener('input', function (evt) {
    var input = evt.target;
    if (input && input.matches && input.matches('#translation_filter')) {
        filterTranslationItems();
    }
});

document.addEventListener('change', function (evt) {
    var input = evt.target;
    if (input && input.matches && input.matches('#translation_only_missing')) {
        filterTranslationItems();
    }
});
