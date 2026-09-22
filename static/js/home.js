// Script de la page d'accueil (templates/home.html).
// Le bouton « Documentation » utilisait un attribut onclick (alert + return
// false), incompatible avec la CSP script-src 'self'. Le comportement est
// ici, par délégation.

document.addEventListener('click', function (e) {
    var link = e.target.closest ? e.target.closest('[data-docs-soon]') : null;
    if (!link) { return; }
    e.preventDefault();
    alert('Documentation à venir prochainement !');
});
