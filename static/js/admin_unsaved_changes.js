// ============================================================================
//  Point 6 (audit Admin) — Détection des modifications non enregistrées.
//
//  Sur les pages de configuration admin, les champs portent un attribut
//  `data-initial-value` et un bouton « Enregistrer » qui s'active quand la
//  valeur diffère de l'initiale. Ce module :
//
//  1. Suit en temps réel s'il existe au moins un champ modifié non encore
//     enregistré (bouton « Enregistrer » actif = modification en cours).
//  2. Avertit l'utilisateur avant de quitter la page (beforeunload) UNIQUEMENT
//     s'il y a des modifications non enregistrées — pas inconditionnellement.
//  3. Ajoute un indicateur visuel discret (badge) dans la barre latérale quand
//     des modifications sont en cours.
//
//  Pourquoi pas un beforeunload inconditionnel ? phone.js le fait déjà pour
//  l'affichage téléphone (page publique), mais sur les pages admin où
//  l'utilisateur navigue beaucoup, un avertissement systématique devient
//  rapidement irritant et finit par être ignoré.
// ============================================================================

(function () {
    'use strict';

    var hasUnsavedChanges = false;

    // --- Détection : un bouton « Enregistrer » actif = modification en cours ---
    // Les macros admin (macros.html) activent/désactivent le bouton selon que
    // la valeur diffère de data-initial-value. On scanne les boutons portant
    // l'id *_button et on vérifie s'ils ne sont pas disabled.
    function refreshUnsavedState() {
        var buttons = document.querySelectorAll('button[id$="_button"]');
        var dirty = false;
        for (var i = 0; i < buttons.length; i++) {
            // Un bouton d'enregistrement actif (non disabled) signifie qu'une
            // modification est en attente de sauvegarde. On ignore les boutons
            // qui ne sont pas des boutons de sauvegarde (ex. boutons d'action).
            if (!buttons[i].disabled && buttons[i].textContent.trim().indexOf('Enregistrer') !== -1) {
                dirty = true;
                break;
            }
        }
        hasUnsavedChanges = dirty;
        updateBadge(dirty);
    }

    // --- Indicateur visuel discret dans la sidebar ---
    function updateBadge(show) {
        var badge = document.getElementById('unsaved-changes-badge');
        if (!badge) return;
        badge.style.display = show ? '' : 'none';
    }

    // --- beforeunload : avertir seulement s'il y a des modifs non sauvegardées ---
    window.addEventListener('beforeunload', function (event) {
        refreshUnsavedState();
        if (hasUnsavedChanges) {
            // Message standard (les navigateurs modernes ignorent le texte
            // personnalisé et affichent leur propre message).
            event.preventDefault();
            event.returnValue = '';
            return '';
        }
    });

    // --- Suivi en temps réel : vérifier après chaque interaction ---
    document.addEventListener('DOMContentLoaded', function () {
        // Vérification initiale.
        refreshUnsavedState();

        // Re-vérifier après chaque frappe clavier ou changement dans un champ.
        document.addEventListener('input', refreshUnsavedState);
        document.addEventListener('change', refreshUnsavedState);

        // Re-vérifier après un clic (le bouton peut être désactivé par le clic).
        document.addEventListener('click', function () {
            // Légèrement différé pour laisser le handler du clic mettre à jour
            // l'état du bouton avant de scanner.
            setTimeout(refreshUnsavedState, 50);
        });

        // Re-vérifier après une requête HTMX (le fragment peut contenir de
        // nouveaux champs avec data-initial-value).
        document.addEventListener('htmx:afterRequest', function () {
            setTimeout(refreshUnsavedState, 100);
        });
        document.addEventListener('htmx:afterSwap', function () {
            setTimeout(refreshUnsavedState, 100);
        });
    });
})();
