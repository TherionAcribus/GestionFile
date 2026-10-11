// Gestionnaire des cartes du tableau de bord (fragment dashboard_select.html,
// chargé dans le panneau « Personnaliser » de admin.html).
//
// Écouteurs posés une seule fois (délégation) : le fragment est réinjecté par
// HTMX, un <script> interne serait rejoué et ses écouteurs s'empileraient.

var cardListSortable = null;

function initializeCardListSortable() {
    var cardListEl = document.getElementById('card-list-sortable');
    if (!cardListEl || typeof Sortable === 'undefined') { return; }
    if (cardListSortable) {
        cardListSortable.destroy();
        cardListSortable = null;
    }
    cardListSortable = new Sortable(cardListEl, {
        handle: '.card-item-drag-handle',
        animation: 150,
        ghostClass: 'card-item-ghost',
        chosenClass: 'card-item-chosen',
        dragClass: 'card-item-drag',
        fallbackTolerance: 3,
        onEnd: function () { setDirty(true); }
    });
}

// Marque le gestionnaire comme modifié non enregistré (badge + data-dirty :
// admin.js refuse alors de réinjecter la liste au gré des évènements temps
// réel, qui détruirait les changements en cours).
function setDirty(flag) {
    var list = document.getElementById('card-list-sortable');
    if (list) {
        if (flag) { list.setAttribute('data-dirty', '1'); }
        else { list.removeAttribute('data-dirty'); }
    }
    var badge = document.querySelector('[data-card-manager-dirty]');
    if (badge) { badge.hidden = !flag; }
}

function toggleCardVisibility(button) {
    var cardItem = button.closest('.card-item');
    var icon = button.querySelector('i');
    var hidden = cardItem.classList.toggle('card-item-hidden');
    icon.className = hidden ? 'bi bi-eye-slash' : 'bi bi-eye';
    button.title = hidden ? 'Afficher' : 'Masquer';
    button.setAttribute('aria-pressed', hidden ? 'false' : 'true');
    setDirty(true);
}

function saveCardConfiguration(btn) {
    var visibleCards = [];
    var cardOrder = [];
    document.querySelectorAll('#card-list-sortable .card-item').forEach(function (item, index) {
        cardOrder.push({ id: parseInt(item.getAttribute('data-card-id'), 10), position: index + 1 });
        if (!item.classList.contains('card-item-hidden')) {
            visibleCards.push(item.getAttribute('data-card-name'));
        }
    });

    fetch('/admin/dashboard/save_configuration', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visible_cards: visibleCards, card_order: cardOrder })
    }).then(function (response) {
        if (!response.ok) { throw new Error('HTTP ' + response.status); }
        return response.text();
    }).then(function (html) {
        var dashboard = document.getElementById('sortable-dashboard');
        dashboard.innerHTML = html;
        // Sans htmx.process, les enveloppes insérées à la main ne
        // déclenchaient jamais leur chargement (cartes figées en squelette).
        if (typeof htmx !== 'undefined') { htmx.process(dashboard); }
        if (typeof initializeSortable === 'function') { initializeSortable(); }
        var empty = document.getElementById('dashboard-empty');
        if (empty) {
            empty.hidden = dashboard.querySelectorAll('.dashboard-card-slot').length > 0;
        }

        setDirty(false);
        if (btn) {
            var originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-check-lg" aria-hidden="true"></i> Enregistré';
            btn.classList.replace('btn-primary', 'btn-success');
            setTimeout(function () {
                btn.innerHTML = originalHTML;
                btn.classList.replace('btn-success', 'btn-primary');
            }, 2000);
        }
    }).catch(function (error) {
        if (typeof AdminFeedback !== 'undefined') {
            AdminFeedback.error("L'enregistrement de la configuration a échoué.",
                { retry: function () { saveCardConfiguration(btn); } });
        } else {
            console.error('Enregistrement du tableau de bord :', error);
        }
    });
}

// --- Comportements délégués (CSP : pas d'onclick inline) -----------------
document.addEventListener('click', function (evt) {
    if (!evt.target || !evt.target.closest) { return; }
    var move = evt.target.closest('[data-card-move]');
    if (move) {
        evt.preventDefault();
        var item = move.closest('.card-item');
        var moved = false;
        if (move.getAttribute('data-card-move') === 'up' && item.previousElementSibling) {
            item.parentNode.insertBefore(item, item.previousElementSibling);
            moved = true;
        } else if (move.getAttribute('data-card-move') === 'down' && item.nextElementSibling) {
            item.parentNode.insertBefore(item.nextElementSibling, item);
            moved = true;
        }
        if (moved) {
            setDirty(true);
            move.focus();   // le focus suit le bouton déplacé
        }
        return;
    }
    var toggle = evt.target.closest('.btn-toggle-visibility');
    if (toggle) {
        evt.preventDefault();
        toggleCardVisibility(toggle);
        return;
    }
    var save = evt.target.closest('[data-card-manager-save]');
    if (save) {
        evt.preventDefault();
        saveCardConfiguration(save);
        return;
    }
    var toggleSettings = evt.target.closest('[data-card-settings-toggle]');
    if (toggleSettings) {
        evt.preventDefault();
        var form = document.getElementById(toggleSettings.getAttribute('aria-controls'));
        if (form) {
            form.hidden = !form.hidden;
            toggleSettings.setAttribute('aria-expanded', form.hidden ? 'false' : 'true');
        }
    }
});

// Réglages propres à une carte : enregistrés immédiatement (ils ne
// participent pas au badge « Modifications non enregistrées » qui ne couvre
// que visibilité et ordre).
function saveCardSettings(form) {
    var name = form.getAttribute('data-card-settings');
    var payload = {};
    new FormData(form).forEach(function (value, key) { payload[key] = Number(value); });
    fetch('/admin/dashboard/settings/' + name, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(function (response) {
        if (response.ok) {
            if (typeof AdminFeedback !== 'undefined') {
                AdminFeedback.toast('Réglages enregistrés', true);
            }
            form.hidden = true;
            var toggle = document.querySelector(
                '[data-card-settings-toggle][aria-controls="' + form.id + '"]');
            if (toggle) { toggle.setAttribute('aria-expanded', 'false'); }
            return null;
        }
        return response.text();
    }).then(function (errorText) {
        if (errorText !== null && typeof AdminFeedback !== 'undefined') {
            AdminFeedback.error(errorText || "Échec de l'enregistrement des réglages");
        }
    }).catch(function (error) {
        if (typeof AdminFeedback !== 'undefined') {
            AdminFeedback.error("Échec de l'enregistrement des réglages");
        } else {
            console.error('Réglages de carte :', error);
        }
    });
}

document.addEventListener('submit', function (evt) {
    var form = evt.target && evt.target.closest
        ? evt.target.closest('form[data-card-settings]') : null;
    if (!form) { return; }
    evt.preventDefault();
    if (!form.reportValidity()) { return; }
    saveCardSettings(form);
});

// Après « Réinitialiser » (hx-post) : le tableau de bord est remplacé par la
// réponse ; on purge le badge et on recharge la liste du gestionnaire.
document.body.addEventListener('htmx:afterRequest', function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (!(elt && elt.hasAttribute && elt.hasAttribute('data-card-manager-reset'))) { return; }
    if (!evt.detail.successful) { return; }
    setDirty(false);
    if (typeof refresh_dashboard_select === 'function') { refresh_dashboard_select(); }
    var empty = document.getElementById('dashboard-empty');
    if (empty) {
        empty.hidden = document.querySelectorAll(
            '#sortable-dashboard .dashboard-card-slot').length > 0;
    }
});

// Le fragment arrive (première ouverture ou refresh_dashboard_select) :
// la liste devient triable.
document.body.addEventListener('htmx:afterSwap', function (evt) {
    var target = evt.detail && evt.detail.target;
    if (target && (target.id === 'div_select_dashboard' || target.id === 'card-list-sortable')) {
        initializeCardListSortable();
    }
});
