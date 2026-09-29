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
        fallbackTolerance: 3
    });
}

function toggleCardVisibility(button) {
    var cardItem = button.closest('.card-item');
    var icon = button.querySelector('i');
    var hidden = cardItem.classList.toggle('card-item-hidden');
    icon.className = hidden ? 'bi bi-eye-slash' : 'bi bi-eye';
    button.title = hidden ? 'Afficher' : 'Masquer';
    button.setAttribute('aria-pressed', hidden ? 'false' : 'true');
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
        console.error('Enregistrement du tableau de bord :', error);
        alert("L'enregistrement de la configuration a échoué.");
    });
}

// --- Comportements délégués (CSP : pas d'onclick inline) -----------------
document.addEventListener('click', function (evt) {
    if (!evt.target || !evt.target.closest) { return; }
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
