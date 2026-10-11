// Script de la page admin/admin.html, extrait du gabarit (Phase 8, point 2).
// Charge en fin de page via le bloc `scripts_end` de admin/base.html : le
// navigateur peut le mettre en cache, et le gabarit redevient du HTML.

var sortableInstance = null;

function sendOrder() {
    var order = [];
    // Enveloppes (.dashboard-card-slot) : présentes même quand la
    // carte n'est pas encore chargée (squelette) ou a répondu vide
    // (carte « Alertes » sans alerte).
    document.querySelectorAll('#sortable-dashboard .dashboard-card-slot').forEach(function (item, index) {
        var cardId = parseInt(item.getAttribute('data-card-id'), 10);
        order.push({
            id: cardId,
            position: index + 1
        });
    });

    fetch('/admin/dashboard/save_order', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ order: order })
    }).then(response => {
        if (!response.ok) {
            if (typeof AdminFeedback !== 'undefined') {
                AdminFeedback.error('Erreur lors de la sauvegarde de l\'ordre des cartes',
                    { retry: function () { sendOrder(); } });
            } else {
                console.error('Erreur lors de la sauvegarde de l\'ordre');
            }
        }
    }).catch(error => {
        if (typeof AdminFeedback !== 'undefined') {
            AdminFeedback.error('Erreur de connexion lors de la sauvegarde',
                { retry: function () { sendOrder(); } });
        } else {
            console.error('Erreur réseau:', error);
        }
    });
}

function initializeSortable() {
    var dashboardEl = document.getElementById('sortable-dashboard');
    if (!dashboardEl) return;
    
    if (sortableInstance) {
        sortableInstance.destroy();
    }
    
    sortableInstance = new Sortable(dashboardEl, {
        handle: '.card-header',
        animation: 150,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        onEnd: function (evt) {
            sendOrder();
        }
    });
}

document.addEventListener('DOMContentLoaded', function () {
    initializeSortable();
});

// Réinitialiser Sortable après mise à jour HTMX
document.body.addEventListener('htmx:afterSwap', function(evt) {
    var target = evt.detail.target;
    if (target.id === 'sortable-dashboard') {
        initializeSortable();
        return;
    }
    // Rechargement du contenu d'une carte (lazy, polling, Actualiser) :
    // horodatage « mis à jour il y a… » et fin de l'animation du bouton.
    var slot = (target && target.closest)
        ? (target.classList.contains('dashboard-card-slot')
           ? target : target.closest('.dashboard-card-slot'))
        : null;
    if (slot) {
        slot.setAttribute('data-updated-at', String(Date.now()));
        var btn = slot.querySelector('[data-card-refresh]');
        if (btn) { btn.classList.remove('is-refreshing'); }
        updateCardTimestamps();
    }
});

// « Mis à jour il y a… » dans l'en-tête de chaque carte (card_shell).
function updateCardTimestamps() {
    document.querySelectorAll('.dashboard-card-slot[data-updated-at]').forEach(function (slot) {
        var el = slot.querySelector('[data-card-updated]');
        if (!el) { return; }
        var age = Math.floor((Date.now() - Number(slot.getAttribute('data-updated-at'))) / 1000);
        var label;
        if (age < 10) { label = "à l'instant"; }
        else if (age < 60) { label = 'il y a ' + age + ' s'; }
        else if (age < 3600) { label = 'il y a ' + Math.floor(age / 60) + ' min'; }
        else { label = 'il y a ' + Math.floor(age / 3600) + ' h'; }
        el.textContent = label;
        el.hidden = false;
        el.title = new Date(Number(slot.getAttribute('data-updated-at'))).toLocaleTimeString();
    });
}

document.addEventListener('DOMContentLoaded', function () {
    setInterval(updateCardTimestamps, 15000);
});

// Rafraîchissement temps réel du tableau de bord (évènement admin
// 'refresh_dashboard', cf. admin.js) : on récupère les enveloppes du
// catalogue, puis on réordonne les nœuds existants si seules les positions
// ont changé — sinon on remplace tout.
function refresh_dashboard(){
    fetch('/admin/dashboard/cards', { credentials: 'same-origin' })
        .then(function (response) {
            if (!response.ok) { throw new Error('HTTP ' + response.status); }
            return response.text();
        })
        .then(function (html) {
            var dashboard = document.getElementById('sortable-dashboard');
            if (!dashboard) { return; }
            var doc = document.createElement('template');
            doc.innerHTML = html;
            var wanted = Array.prototype.map.call(
                doc.content.querySelectorAll('.dashboard-card-slot'),
                function (slot) { return slot.getAttribute('data-card-id'); });
            var current = Array.prototype.map.call(
                dashboard.querySelectorAll('.dashboard-card-slot'),
                function (slot) { return slot.getAttribute('data-card-id'); });
            var sameIds = current.length === wanted.length &&
                current.slice().sort().join(',') === wanted.slice().sort().join(',');
            if (!sameIds) {
                dashboard.innerHTML = html;
                if (typeof htmx !== 'undefined') { htmx.process(dashboard); }
                if (typeof initializeSortable === 'function') { initializeSortable(); }
            } else if (current.join(',') !== wanted.join(',')) {
                var byId = {};
                dashboard.querySelectorAll('.dashboard-card-slot').forEach(function (slot) {
                    byId[slot.getAttribute('data-card-id')] = slot;
                });
                wanted.forEach(function (id) { dashboard.appendChild(byId[id]); });
            }
            var empty = document.getElementById('dashboard-empty');
            if (empty) { empty.hidden = wanted.length > 0; }
        })
        .catch(function (err) {
            // Rafraîchissement de fond : pas de toast en cas d'échec réseau.
            console.warn('Rafraîchissement du tableau de bord :', err);
        });
}

// Recharge une seule carte (réglages modifiés par un autre admin) : le
// serveur diffuse 'refresh_dashboard_card' avec data = {name}.
function reloadDashboardSlot(slot) {
    htmx.ajax('GET', slot.getAttribute('data-card-url'),
              { target: slot, swap: 'innerHTML' });
}

function refresh_dashboard_card(msg) {
    var name = msg && msg.data && msg.data.name;
    if (!name) { return; }
    var slot = document.querySelector(
        '#sortable-dashboard .dashboard-card-slot[data-card-name="' + name + '"]');
    if (slot) { reloadDashboardSlot(slot); }
}

// -------- Point 5.3 : squelette + état d'erreur par carte --------
// Chaque carte est chargée en différé (voir _dashboard_cards.html). Si sa
// requête échoue (erreur serveur ou réseau), on remplace le squelette par un
// état d'erreur avec un bouton « Réessayer », sans casser les autres cartes.

function dashboardSkeletonHTML(cardId) {
    var tpl = document.getElementById('dashboard-skeleton');
    var node = tpl.content.firstElementChild.cloneNode(true);
    node.id = 'card-' + cardId;
    return node.outerHTML;
}

function dashboardCardError(wrapper) {
    var cardId = wrapper.getAttribute('data-card-id');
    wrapper.innerHTML = '<div id="card-' + cardId + '" class="dashboard-card card-load-error">'
        + '<div class="card"><div class="card-header d-flex justify-content-between align-items-center">'
        + '<span class="text-danger"><i class="bi bi-exclamation-triangle-fill"></i> Erreur de chargement</span>'
        + '</div><div class="card-body text-center">'
        + '<p class="text-muted small mb-2">Impossible de charger cette carte.</p>'
        + '<button type="button" class="btn btn-sm btn-outline-primary" data-card-retry>'
        + '<i class="bi bi-arrow-clockwise"></i> Réessayer</button>'
        + '</div></div></div>';
}

function dashboardCardRetry(btn) {
    var wrapper = btn.closest('.dashboard-card-slot');
    if (!wrapper) return;
    wrapper.innerHTML = dashboardSkeletonHTML(wrapper.getAttribute('data-card-id'));
    reloadDashboardSlot(wrapper);
}

// Délégation : les boutons « Réessayer » et « Actualiser » sont injectés
// dynamiquement — un attribut onclick serait bloqué par la CSP.
document.body.addEventListener('click', function (e) {
    var btn = (e.target && e.target.closest)
        ? e.target.closest('[data-card-retry]') : null;
    if (btn) { dashboardCardRetry(btn); return; }
    var refresh = (e.target && e.target.closest)
        ? e.target.closest('[data-card-refresh]') : null;
    if (refresh) {
        var slot = refresh.closest('.dashboard-card-slot');
        if (!slot) { return; }
        refresh.classList.add('is-refreshing');
        reloadDashboardSlot(slot);
    }
});

function dashboardSlotFromEvent(evt) {
    var t = evt.target;
    return (t && t.closest) ? t.closest('.dashboard-card-slot') : null;
}

// Seules les requêtes GET (chargement différé de la carte) basculent le slot
// en état d'erreur : un POST qui échoue dans le slot (ex. bouton « Masquer »)
// est déjà couvert par le toast global de admin.js.
function dashboardRequestIsGet(evt) {
    var cfg = evt.detail && evt.detail.requestConfig;
    return cfg && String(cfg.verb).toLowerCase() === 'get';
}

document.body.addEventListener('htmx:responseError', function(evt) {
    if (!dashboardRequestIsGet(evt)) return;
    var wrapper = dashboardSlotFromEvent(evt);
    if (wrapper) dashboardCardError(wrapper);
});
document.body.addEventListener('htmx:sendError', function(evt) {
    if (!dashboardRequestIsGet(evt)) return;
    var wrapper = dashboardSlotFromEvent(evt);
    if (wrapper) dashboardCardError(wrapper);
});
