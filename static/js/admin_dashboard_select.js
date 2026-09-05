// Gestionnaire des cartes du tableau de bord.
// Extrait du fragment templates/admin/dashboard_select.html (Phase 8, point 2).
//
// Le fragment etant reinjecte par HTMX, son <script> etait rejoue a chaque
// echange : l'ecouteur `htmx:afterSwap` ci-dessous etait donc repose sur
// document.body a chaque fois, et ils s'empilaient. Il est desormais pose une
// seule fois, au chargement de la page.
//
// Un bloc `DOMContentLoaded` final a ete supprime : il ne contenait que deux
// console.log et ne pouvait de toute facon jamais s'executer, l'evenement etant
// deja passe au moment ou le fragment arrive dans la page.

var cardListSortable = null;
var isCardManagerOpen = false;

function toggleCardManager(evt) {
    if (evt) evt.stopPropagation();
    
    var content = document.getElementById('card-manager-content');
    var icon = document.getElementById('card-manager-toggle-icon');
    
    
    if (isCardManagerOpen) {
        content.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
        isCardManagerOpen = false;
    } else {
        content.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
        isCardManagerOpen = true;
        
        // Initialiser Sortable quand on ouvre
        setTimeout(function() {
            initializeCardListSortable();
        }, 150);
    }
}

function initializeCardListSortable() {
    var cardListEl = document.getElementById('card-list-sortable');
    if (!cardListEl) {
        console.error('Element card-list-sortable non trouvé');
        return;
    }
    
    if (cardListSortable) {
        cardListSortable.destroy();
        cardListSortable = null;
    }
    
    
    try {
        cardListSortable = new Sortable(cardListEl, {
            handle: '.card-item-drag-handle',
            animation: 150,
            ghostClass: 'card-item-ghost',
            chosenClass: 'card-item-chosen',
            dragClass: 'card-item-drag',
            forceFallback: false,
            fallbackTolerance: 3
        });
    } catch(e) {
        console.error('Erreur lors de l\'initialisation de Sortable:', e);
    }
}

function toggleCardVisibility(cardId, button) {
    var cardItem = button.closest('.card-item');
    var icon = button.querySelector('i');
    
    cardItem.classList.toggle('card-item-hidden');
    
    if (cardItem.classList.contains('card-item-hidden')) {
        icon.className = 'bi bi-eye-slash';
        button.title = 'Afficher';
    } else {
        icon.className = 'bi bi-eye';
        button.title = 'Masquer';
    }
}

function saveCardConfiguration(evt) {
    if (evt) evt.stopPropagation();
    
    var cardItems = document.querySelectorAll('.card-item');
    var visibleCards = [];
    var cardOrder = [];
    
    cardItems.forEach(function(item, index) {
        var cardName = item.getAttribute('data-card-name');
        var isVisible = !item.classList.contains('card-item-hidden');
        
        cardOrder.push({
            id: parseInt(item.getAttribute('data-card-id')),
            position: index + 1
        });
        
        if (isVisible) {
            visibleCards.push(cardName);
        }
    });
    
    
    var btn = evt ? evt.target.closest('button') : document.querySelector('.card-manager-footer button');
    
    fetch('/admin/dashboard/save_configuration', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            visible_cards: visibleCards,
            card_order: cardOrder
        })
    }).then(response => {
        if (response.ok) {
            return response.text();
        } else {
            throw new Error('Erreur lors de la sauvegarde');
        }
    }).then(html => {
        document.getElementById('sortable-dashboard').innerHTML = html;
        if (typeof htmx !== 'undefined') {
            htmx.trigger('#sortable-dashboard', 'cardsUpdated');
        }
        
        if (btn) {
            var originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-check-lg"></i> Sauvegardé !';
            btn.classList.add('btn-success');
            btn.classList.remove('btn-primary');
            
            setTimeout(function() {
                btn.innerHTML = originalHTML;
                btn.classList.remove('btn-success');
                btn.classList.add('btn-primary');
            }, 2000);
        }
    }).catch(error => {
        console.error('Erreur:', error);
        alert('Erreur lors de la sauvegarde de la configuration');
    });
}


document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'card-list-sortable') {
        if (isCardManagerOpen) {
            initializeCardListSortable();
        }
    }
});
