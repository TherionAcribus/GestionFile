// ============================================================================
//  Couche de connexion temps réel partagée (point 6.2)
//
//  - Une seule connexion par namespace, créée à la demande (lazy) et
//    réutilisée : plus de multiplication de Managers Socket.IO.
//  - Chaque page ne s'abonne qu'aux évènements qui la concernent (présence
//    d'un point d'ancrage DOM). Un namespace ciblé (ex. /socket_update_patient)
//    n'est donc ouvert que si la page en a l'usage.
//  - Aucun log verbeux en production : les traces onAny / (dé)connexion ne sont
//    émises qu'en mode debug (window.ADMIN_RT_DEBUG === true, ou hôte local).
//    Les erreurs de connexion restent, elles, toujours journalisées.
// ============================================================================
const AdminRealtime = (function () {
    const DEBUG = (window.ADMIN_RT_DEBUG === true)
        || /^(localhost|127\.0\.0\.1|\[::1\]|::1)$/.test(window.location.hostname);

    const sockets = Object.create(null); // namespace -> socket (mémoïsé)
    let baseUrl = null;

    function getBaseUrl() {
        if (baseUrl === null) {
            // Socket.IO attend une URL http(s) ; on s'appuie sur l'hôte courant
            // pour rester compatible avec un reverse proxy (Coolify).
            const proto = window.location.protocol === 'https:' ? 'https://' : 'http://';
            baseUrl = proto + window.location.host;
        }
        return baseUrl;
    }

    // Ouvre (ou réutilise) la connexion vers un namespace.
    function connect(namespace) {
        let socket = sockets[namespace];
        if (socket) {
            return socket;
        }
        socket = io.connect(getBaseUrl() + namespace, { query: "username=admin_interface" });
        sockets[namespace] = socket;

        // Les erreurs de connexion sont toujours utiles (faible volume).
        socket.on('connect_error', function (err) {
            console.error('WebSocket connection error', namespace, err);
        });

        if (DEBUG) {
            socket.on('connect', function () { console.log('WebSocket connected', namespace); });
            socket.on('disconnect', function () { console.log('WebSocket disconnected', namespace); });
            // En client v4, les évènements de (re)connexion sont émis par le
            // Manager (socket.io), pas par le socket lui-même.
            socket.io.on('reconnect', function (n) { console.log('WebSocket reconnected', namespace, 'after', n, 'attempts'); });
            socket.io.on('reconnect_attempt', function (n) { console.log('WebSocket reconnect attempt', namespace, n); });
            socket.onAny(function (event) { console.log('WebSocket event', namespace, event); });
        }
        return socket;
    }

    // S'abonne à un évènement. Si `anchor` est fourni et absent du DOM, on
    // n'ouvre pas la connexion et on n'enregistre pas le handler : l'évènement
    // n'est pas utile à cette page.
    function on(namespace, event, handler, anchor) {
        if (anchor && !document.querySelector(anchor)) {
            return false;
        }
        connect(namespace).on(event, handler);
        return true;
    }

    // Rattrapage à la reconnexion (émis par le Manager). Sans effet si la
    // connexion n'a pas déjà été ouverte par un abonnement.
    function onReconnect(namespace, handler) {
        const socket = sockets[namespace];
        if (socket) {
            socket.io.on('reconnect', handler);
        }
    }

    return { connect: connect, on: on, onReconnect: onReconnect, isDebug: function () { return DEBUG; } };
})();


// ----------------------------------------------------------------------------
//  Abonnements temps réel de la page admin courante.
//
//  Chaque abonnement ciblé est conditionné à la présence de son point d'ancrage
//  DOM : une page ne reçoit que les évènements qui la concernent, et le
//  namespace file (/socket_update_patient) n'est ouvert que là où la file est
//  affichée. Le namespace /socket_admin reste ouvert sur toutes les pages admin
//  car il porte des évènements transversaux : toasts de retour d'action
//  (display_toast, ~200 appels serveur) et changement de thème admin
//  (refresh_colors, qui recharge la page pour appliquer le nouveau thème).
// ----------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
    const NS_QUEUE = '/socket_update_patient';
    const NS_ADMIN = '/socket_admin';

    // --- File des patients : sur la page de la file ET sur le dashboard ---
    // Sur la page /admin/queue : #div_queue_table est présent.
    // Sur le dashboard : la carte "Patients" est enveloppée par un slot portant
    //   data-card-url="/admin/queue/dashboard" (présent dès le HTML initial,
    //   avant le chargement différé du contenu de la carte).
    // L'un OU l'autre suffit à justifier l'abonnement au namespace.
    var queueOnPage = document.querySelector('#div_queue_table');
    var queueOnDashboard = document.querySelector('[data-card-url="/admin/queue/dashboard"]');
    if (queueOnPage || queueOnDashboard) {
        AdminRealtime.on(NS_QUEUE, 'update', function () { refresh_queue(); });
        // Rattrape les mises à jour manquées pendant une coupure.
        AdminRealtime.onReconnect(NS_QUEUE, function () { refresh_queue(); });
    }

    // --- Évènements transversaux (toutes les pages admin) ---
    AdminRealtime.on(NS_ADMIN, 'update', function (msg) { display_toast(msg); });
    AdminRealtime.on(NS_ADMIN, 'refresh_colors', function () { refresh_page(); });

    // --- Évènements ciblés (seulement si leur ancrage est présent) ---
    AdminRealtime.on(NS_ADMIN, 'refresh_activity_table', function () {
        refresh_activity_table();
        refresh_activity_staff_table();
    }, '#div_activity_table');

    AdminRealtime.on(NS_ADMIN, 'refresh_button_order', function () { refresh_button_order(); }, '#order_buttons');
    AdminRealtime.on(NS_ADMIN, 'refresh_counter_order', function () { refresh_counter_order(); }, '#order_counters');
    AdminRealtime.on(NS_ADMIN, 'refresh_languages_order', function () { refresh_languages_order(); }, '#order_languages');
    AdminRealtime.on(NS_ADMIN, 'refresh_dashboard_select', function () { refresh_dashboard_select(); }, '#div_select_dashboard');

    // Galerie : liste + affichage d'une nouvelle galerie.
    AdminRealtime.on(NS_ADMIN, 'refresh_gallery_list', function (msg) { refresh_gallery_list(msg); }, '#galleries_list');
    AdminRealtime.on(NS_ADMIN, 'display_new_gallery', function (msg) {
        const nameInput = document.getElementById('name');
        if (nameInput) { nameInput.value = ''; }
        display_new_gallery(msg);
    }, '#galleries_list');

    // Annonce (onglet audio).
    AdminRealtime.on(NS_ADMIN, 'refresh_sound', function () { refresh_sound(); }, '#announce_current_signal');
    AdminRealtime.on(NS_ADMIN, 'audio_test', function (msg) { playAudio(msg); }, '#announce_current_signal');

    // Dashboard : sélecteur de cartes et cartes "glanceable".
    AdminRealtime.on(NS_ADMIN, 'refresh_printer_dashboard', function (msg) { refresh_printer_dashboard(msg); }, '#sortable-dashboard');
    AdminRealtime.on(NS_ADMIN, 'refresh_counter_dashboard', function (msg) { refresh_counter_dashboard(msg); }, '#sortable-dashboard');
    // Liste des tâches planifiées (page Planification).
    AdminRealtime.on(NS_ADMIN, 'refresh_schedule_tasks_list', function (msg) { refresh_schedule_tasks_list(msg); }, '#div_schedule_tasks_list');

    // Rattrapage à la reconnexion pour l'état "glanceable" affiché sans action
    // de l'admin, uniquement pour ce qui est réellement présent sur la page.
    AdminRealtime.onReconnect(NS_ADMIN, function () {
        if (document.querySelector('#div_schedule_tasks_list')) { refresh_schedule_tasks_list(); }
        if (document.querySelector('#sortable-dashboard')) {
            refresh_counter_dashboard();
            refresh_printer_dashboard();
        }
    });
});


// -------------- TABS BOOTSTRAP  --------------

document.addEventListener('DOMContentLoaded', function() {
    // Fonction pour activer un onglet
    function activateTab(tabId) {
        var tabElement = document.querySelector('#' + tabId + '-tab');
        if (tabElement) {
            var tab = new bootstrap.Tab(tabElement);
            tab.show();
            return true;
        }
        return false;
    }

    // Fonction pour obtenir le paramètre 'tab' de l'URL
    function getTabFromUrl() {
        var urlParams = new URLSearchParams(window.location.search);
        return urlParams.get('tab') || getDefaultTab();
    }

    // Fonction pour obtenir l'ID du premier onglet disponible (onglet par défaut)
    function getDefaultTab() {
        var firstTab = document.querySelector('button[data-bs-toggle="tab"]');
        return firstTab ? firstTab.id.replace('-tab', '') : null;
    }

    // Fonction pour mettre à jour l'URL
    function updateUrl(tabId) {
        var url = new URL(window.location);
        url.searchParams.set('tab', tabId);
        history.pushState({tabId: tabId}, '', url);
    }

    // Activer l'onglet initial ou le premier onglet disponible
    var initialTab = getTabFromUrl();
    if (!activateTab(initialTab)) {
        initialTab = getDefaultTab();
        if (initialTab) {
            activateTab(initialTab);
        }
    }

    // Ajouter des écouteurs d'événements pour les clics sur les onglets
    document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(function(tabEl) {
        tabEl.addEventListener('shown.bs.tab', function(event) {
            var id = event.target.id.replace('-tab', '');
            updateUrl(id);
        });
    });

    // Gérer les événements de navigation (boutons précédent/suivant du navigateur)
    window.addEventListener('popstate', function(event) {
        var tabId = getTabFromUrl();
        if (!activateTab(tabId)) {
            var defaultTab = getDefaultTab();
            if (defaultTab) {
                activateTab(defaultTab);
            }
        }
    });
});


// ============================================================================
//  Point 7.5 — Mécanisme commun de retour utilisateur (accessible)
//
//  États couverts : chargement, succès, erreur, perte de connexion, nouvelle
//  tentative. Chaque message est annoncé dans une zone `aria-live` (lecteur
//  d'écran) ; les messages visibles importants sont rendus en toast Bootstrap,
//  avec au besoin un bouton « Réessayer ».
//
//  Les régions `aria-live` et le conteneur de toasts sont fournis par
//  base.html (#admin-live-polite, #admin-live-assertive, #admin-toast-container)
//  et recréés à la volée si absents (robustesse).
// ============================================================================
const AdminFeedback = (function () {
    function byId(id) { return document.getElementById(id); }

    // Annonce au lecteur d'écran. On vide puis réécrit au tick suivant pour
    // forcer la ré-annonce même lorsque le texte est identique.
    function announce(message, assertive) {
        const region = byId(assertive ? 'admin-live-assertive' : 'admin-live-polite');
        if (!region) return;
        region.textContent = '';
        window.setTimeout(function () { region.textContent = message; }, 30);
    }

    function container() {
        let c = byId('admin-toast-container');
        if (!c) {
            c = document.createElement('div');
            c.id = 'admin-toast-container';
            c.className = 'toast-container position-fixed top-0 end-0 p-3';
            document.body.appendChild(c);
        }
        return c;
    }

    // Toast visible. opts : { css, live, autohide, delay, retry(fn), retryLabel }
    function showToast(message, opts) {
        opts = opts || {};
        const toast = document.createElement('div');
        toast.className = 'toast align-items-center border-0 ' + (opts.css || 'bg-secondary text-white');
        toast.setAttribute('role', opts.live === 'assertive' ? 'alert' : 'status');
        toast.setAttribute('aria-live', opts.live || 'polite');
        toast.setAttribute('aria-atomic', 'true');

        const flex = document.createElement('div');
        flex.className = 'd-flex';

        const body = document.createElement('div');
        body.className = 'toast-body';
        body.textContent = message;
        flex.appendChild(body);

        if (typeof opts.retry === 'function') {
            const retryBtn = document.createElement('button');
            retryBtn.type = 'button';
            retryBtn.className = 'btn btn-sm btn-light me-2 my-auto';
            retryBtn.textContent = opts.retryLabel || 'Réessayer';
            retryBtn.addEventListener('click', function () {
                hide(toast);
                opts.retry();
            });
            flex.appendChild(retryBtn);
        }

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-close btn-close-white me-2 m-auto';
        close.setAttribute('data-bs-dismiss', 'toast');
        close.setAttribute('aria-label', 'Fermer');
        flex.appendChild(close);

        toast.appendChild(flex);
        container().appendChild(toast);

        const bsToast = new bootstrap.Toast(toast, {
            autohide: opts.autohide !== false,
            delay: opts.delay || 4000
        });
        toast.addEventListener('hidden.bs.toast', function () { toast.remove(); });
        bsToast.show();
        return toast;
    }

    function hide(toast) {
        try {
            const inst = bootstrap.Toast.getInstance(toast);
            if (inst) { inst.hide(); return; }
        } catch (e) { /* ignore */ }
        toast.remove();
    }

    // ---- États (API publique) ----

    // Chargement / succès : annonce lecteur d'écran uniquement (le succès
    // visible reste porté par le toast WebSocket, cf. display_toast).
    function loading(message) { announce(message || 'Chargement…', false); }
    function announceSuccess(message) { announce(message || 'Enregistré.', false); }
    function announceError(message) { announce(message || "Une erreur est survenue.", true); }

    // Erreur visible + éventuelle nouvelle tentative (cas non couverts par le
    // WebSocket : échec réseau, statut HTTP d'erreur, expiration).
    function error(message, opts) {
        opts = opts || {};
        const msg = message || "Une erreur est survenue.";
        announce(msg, true);
        return showToast(msg, {
            css: 'bg-danger text-white', live: 'assertive',
            autohide: !opts.retry, retry: opts.retry
        });
    }

    // Toast générique visible (utilisé par les évènements WebSocket).
    function toast(message, isSuccess) {
        const msg = message || 'Enregistrement effectué';
        announce(msg, !isSuccess);
        return showToast(msg, {
            css: isSuccess ? 'bg-success text-white' : 'bg-danger text-white',
            live: isSuccess ? 'polite' : 'assertive',
            autohide: true
        });
    }

    function connectionLost(opts) {
        opts = opts || {};
        const msg = "Connexion temps réel perdue : les mises à jour automatiques sont suspendues.";
        announce(msg, true);
        return showToast(msg, {
            css: 'bg-warning', live: 'assertive', autohide: false,
            retry: opts.retry, retryLabel: 'Reconnecter'
        });
    }

    function connectionRestored() {
        const msg = 'Connexion rétablie.';
        announce(msg, false);
        return showToast(msg, { css: 'bg-success text-white', live: 'polite', autohide: true });
    }

    return {
        loading: loading,
        announceSuccess: announceSuccess,
        announceError: announceError,
        error: error,
        toast: toast,
        connectionLost: connectionLost,
        connectionRestored: connectionRestored,
        showToast: showToast
    };
})();


// Retour des évènements WebSocket transversaux (namespace /socket_admin).
// Diffusé à TOUS les administrateurs : reste le canal VISIBLE inter-clients.
// L'acquittement HTTP (aria-live) est géré en parallèle par les handlers HTMX
// ci-dessous, de sorte que l'auteur d'une action ne dépend pas du WebSocket.
function display_toast(data) {
    const payload = (data && data.data) || {};
    AdminFeedback.toast(payload.message, payload.success === true);
}


// ---------------------------------------------------------------------------
//  Câblage du retour utilisateur commun (point 7.5)
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', function () {
    setupHttpFeedback();
    setupRealtimeConnectionFeedback();
});

// Une requête est « mutante » si son verbe n'est pas GET.
function isMutatingRequest(evt) {
    const cfg = evt.detail && evt.detail.requestConfig;
    const verb = cfg && cfg.verb ? String(cfg.verb).toLowerCase() : '';
    return !!verb && verb !== 'get';
}

// Les éléments marqués `data-feedback-skip` gèrent leur propre retour (ex. le
// bouton de configuration universel, qui affiche un message près du champ).
function feedbackSkipped(evt) {
    const elt = evt.detail && evt.detail.elt;
    return !!(elt && elt.closest && elt.closest('[data-feedback-skip]'));
}

// Reconstruit la requête ayant échoué pour la relancer à l'identique.
function retryFromEvent(evt) {
    const cfg = evt.detail && evt.detail.requestConfig;
    const elt = evt.detail && evt.detail.elt;
    if (!cfg || !cfg.verb || !cfg.path || !elt) return null;
    return function () {
        const ctx = { source: elt };
        if (cfg.target) ctx.target = cfg.target;
        const swap = elt.getAttribute && elt.getAttribute('hx-swap');
        if (swap) ctx.swap = swap;
        htmx.ajax(cfg.verb, cfg.path, ctx);
    };
}

function setupHttpFeedback() {
    const body = document.body;

    // Chargement (annoncé au lecteur d'écran) pour toute sauvegarde.
    body.addEventListener('htmx:beforeRequest', function (evt) {
        if (!isMutatingRequest(evt) || feedbackSkipped(evt)) return;
        AdminFeedback.loading('Enregistrement…');
    });

    // Acquittement HTTP autoritatif porté par l'en-tête HX-Trigger. Correct
    // même quand la route renvoie 204 sur un échec de validation : le drapeau
    // `success` provient du serveur, pas du seul statut HTTP. Annonce aria-live
    // uniquement (le toast visible est déjà fourni par le WebSocket).
    body.addEventListener('adminFeedback', function (evt) {
        const d = evt.detail || {};
        if (d.success) AdminFeedback.announceSuccess(d.message || 'Enregistré.');
        else AdminFeedback.announceError(d.message || "Échec de l'enregistrement.");
    });

    // Erreur applicative (4xx/5xx) : toast visible + nouvelle tentative.
    body.addEventListener('htmx:responseError', function (evt) {
        if (feedbackSkipped(evt)) return;
        const xhr = evt.detail && evt.detail.xhr;
        const serverMsg = (xhr && xhr.responseText ? xhr.responseText.trim() : '');
        const msg = serverMsg || ("L'enregistrement a échoué (erreur " + (xhr ? xhr.status : '?') + ").");
        AdminFeedback.error(msg, { retry: retryFromEvent(evt) });
    });

    // Échec réseau : la requête n'a pas abouti.
    body.addEventListener('htmx:sendError', function (evt) {
        if (feedbackSkipped(evt)) return;
        AdminFeedback.error('Connexion au serveur impossible. Vérifiez votre réseau.', { retry: retryFromEvent(evt) });
    });

    // Expiration.
    body.addEventListener('htmx:timeout', function (evt) {
        if (feedbackSkipped(evt)) return;
        AdminFeedback.error('Le serveur met trop de temps à répondre.', { retry: retryFromEvent(evt) });
    });
}

// Perte / rétablissement de la connexion temps réel (WebSocket).
function setupRealtimeConnectionFeedback() {
    if (typeof AdminRealtime === 'undefined') return;
    const socket = AdminRealtime.connect('/socket_admin');
    if (!socket) return;
    let lostToast = null;

    socket.on('disconnect', function () {
        if (lostToast) return;
        lostToast = AdminFeedback.connectionLost({
            retry: function () { try { socket.connect(); } catch (e) { /* ignore */ } }
        });
    });

    function restored() {
        if (!lostToast) return;
        lostToast = null;
        AdminFeedback.connectionRestored();
    }
    socket.on('connect', restored);
    if (socket.io && socket.io.on) {
        socket.io.on('reconnect', restored);
    }
}

// -------------- QUEUE  --------------

// La mise à jour de la file passe désormais par Socket.IO (namespace
// /socket_update_patient, abonnement géré par AdminRealtime plus haut).
// L'ancien EventSource vers /events/update_patients (route supprimée côté
// serveur) a été retiré : il générait une boucle de requêtes 404 sur chaque
// page admin.

function refresh_queue(){
    var queueTable = document.querySelector('#div_queue_table');
    // La carte "Patients" du dashboard est enveloppée par un slot portant
    // data-card-url="/admin/queue/dashboard" (stable, présent dès le HTML
    // initial). L'ancien sélecteur '#card-queue' ne matchait jamais car
    // l'id de la carte est numérique (card-{{ dashboardcard.id }}).
    var queueCardSlot = document.querySelector('[data-card-url="/admin/queue/dashboard"]');

    // Vérifie si div_queue_table existe (page /admin/queue)
    if (queueTable) {
        htmx.trigger(queueTable, 'refresh_queue_patient', {target: "#div_queue_table"});
    }

    // Vérifie si la carte dashboard existe
    if (queueCardSlot) {
        htmx.trigger(queueCardSlot, 'refresh_queue_patient');
    }
}

$(document).ready(function() {
    $('#select_patient_filter').select2({
    placeholder: "Patients à afficher",
    allowClear: true
        });
    });
    window.addEventListener("DOMContentLoaded", (e) => {
        $('select').on('select2:select select2:unselect', function (e) {
            // Déclencher manuellement l'événement change pour HTMX
            $(this).closest('select').get(0).dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

// -------------- GALERIES --------------

function display_new_gallery(data) {

    // Construire l'URL de la galerie
    let url = "/admin/gallery/__NAME__".replace('__NAME__', data.data);

    // Utiliser HTMX pour envoyer une requête GET
    htmx.ajax('GET', url, { target: '#content' });
}


function refresh_gallery_list(data) {
    htmx.trigger('#galleries_list', 'refresh_gallery_list', {target: "#galleries_list"});
}

// -------------- DASHBOARD --------------

function refresh_dashboard_select(){
    htmx.trigger('#div_select_dashboard', 'refresh_dashboard_select', {target: "#div_select_dashboard"});
}

// -------------- ACTIVITY --------------

function refresh_activity_table(){
    htmx.trigger('#div_activity_table', 'refresh_activity_table', {target: "#div_activity_table"});
}

function refresh_activity_staff_table(){
    htmx.trigger('#div_activity_staff_table', 'refresh_activity_staff_table', {target: "#div_activity_staff_table"});
}


// ---------------- DASHBOARD ----------------
//
// Les cartes du dashboard ont un id numérique (card-{{ dashboardcard.id }}) :
// les anciens sélecteurs '#card-printer' / '#card-counter' / '#card-queue' ne
// matchaient jamais. On utilise data-card-url (stable, présent dès le HTML
// initial sur le slot de la carte) pour retrouver chaque carte.

function refresh_printer_dashboard(){
    var slot = document.querySelector('[data-card-url="/admin/printer/dashboard"]');
    if (slot) { htmx.trigger(slot, 'refresh_printer_dashboard'); }
}

function refresh_counter_dashboard(){
    var slot = document.querySelector('[data-card-url="/admin/counter/dashboard"]');
    if (slot) { htmx.trigger(slot, 'refresh_counter_dashboard'); }
}


// ---------------- BOUTONS ----------------

function refresh_button_order(){
    htmx.trigger('#order_buttons', 'refresh_buttons_order', {target: "#order_buttons"});
}


function sortable(){
    var el = document.getElementById('list_order_buttons');
    if (!el) return;
    Sortable.create(el, {
        animation: 150, // ms, animation speed moving items when sorting, `0` — without animation
        // Les commandes clavier (boutons monter/descendre) ne doivent pas
        // déclencher un glissement ; `preventOnFilter: false` laisse toutefois
        // le clic du bouton se propager normalement.
        filter: '.order-move-controls, .order-move-controls *',
        preventOnFilter: false,
        onEnd: function (/**Event*/evt) {
            var itemEl = evt.item;  // dragged HTMLElement
            // Mettre à jour les positions ARIA après un glisser-déposer.
            updateAriaPositions(el);
        }
    });
    // Alternative clavier au glisser-déposer : chaque élément reçoit des
    // boutons « monter »/« descendre » qui réordonnent le DOM. Le bouton
    // « Sauvegarder l'ordre » lit l'ordre du DOM, donc rien d'autre à changer.
    addKeyboardReorderControls(el);
}

// Les trois fragments de reordonnancement (comptoirs, boutons de la page
// patient, langues) amenent tous la liste #list_order_buttons et appelaient
// `sortable()` via un <script> inline, reexecute a chaque echange HTMX.
// Un seul point d'accroche suffit : `sortable()` est deja idempotent
// (garde `if (!el) return` et controles clavier non dupliques).
document.addEventListener('htmx:afterSettle', function (evt) {
    var cible = evt.detail && evt.detail.target;
    if (!cible) { return; }
    if (cible.id === 'list_order_buttons' || cible.querySelector('#list_order_buttons')) {
        sortable();
    }
});


function addKeyboardReorderControls(listEl){
    var items = listEl.querySelectorAll('.button-order-item');
    var total = items.length;

    items.forEach(function(item, index){
        if (item.querySelector('.order-move-controls')) return; // pas de doublon

        // Point 14 : accessibilité du drag-and-drop.
        // aria-posinset/aria-setsize pour annoncer la position à SR.
        item.setAttribute('aria-setsize', total);
        item.setAttribute('aria-posinset', index + 1);

        var controls = document.createElement('span');
        controls.className = 'order-move-controls ms-2';

        var up = document.createElement('button');
        up.type = 'button';
        up.className = 'btn btn-sm btn-outline-secondary';
        up.title = 'Monter';
        up.setAttribute('aria-label', 'Monter cet élément');
        up.innerHTML = '<i class="bi bi-arrow-up" aria-hidden="true"></i>';
        up.addEventListener('click', function(){
            var prev = item.previousElementSibling;
            if (prev) listEl.insertBefore(item, prev);
            updateAriaPositions(listEl);
            up.focus();
        });

        var down = document.createElement('button');
        down.type = 'button';
        down.className = 'btn btn-sm btn-outline-secondary ms-1';
        down.title = 'Descendre';
        down.setAttribute('aria-label', 'Descendre cet élément');
        down.innerHTML = '<i class="bi bi-arrow-down" aria-hidden="true"></i>';
        down.addEventListener('click', function(){
            var next = item.nextElementSibling;
            if (next) listEl.insertBefore(next, item);
            updateAriaPositions(listEl);
            down.focus();
        });

        controls.appendChild(up);
        controls.appendChild(down);
        item.appendChild(controls);

        // Point 14 : navigation clavier sur l'item lui-même.
        // Flèche haut/bas = déplacer l'item (comme les boutons).
        // Home/End = focus le premier/dernier item.
        item.addEventListener('keydown', function(e){
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                var prev = item.previousElementSibling;
                if (prev) {
                    listEl.insertBefore(item, prev);
                    updateAriaPositions(listEl);
                }
                item.focus();
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                var next = item.nextElementSibling;
                if (next) {
                    listEl.insertBefore(next, item);
                    updateAriaPositions(listEl);
                }
                item.focus();
            } else if (e.key === 'Home') {
                e.preventDefault();
                var first = listEl.querySelector('.button-order-item');
                if (first) first.focus();
            } else if (e.key === 'End') {
                e.preventDefault();
                var all = listEl.querySelectorAll('.button-order-item');
                if (all.length) all[all.length - 1].focus();
            }
        });
    });
}

// Met à jour aria-posinset/aria-setsize après un réordonnancement.
function updateAriaPositions(listEl){
    var items = listEl.querySelectorAll('.button-order-item');
    items.forEach(function(item, i){
        item.setAttribute('aria-setsize', items.length);
        item.setAttribute('aria-posinset', i + 1);
    });
}

// ---------------- COUNTERS ----------------

function refresh_counter_order(){
    htmx.trigger('#order_counters', 'refresh_counter_order', {target: "#order_counters"});
}

// ---------------- TRANSLATIONS ----------------

function refresh_languages_order(){
    htmx.trigger('#order_languages', 'refresh_languages_order', {target: "#order_languages"});
}


// ---------------- ANNOUNCES ----------------

function refresh_page(){
    location.reload();
}

function refresh_sound(){
    htmx.trigger('#announce_current_signal', 'refresh_sound', {target: "#announce_current_signal"});
}


// ---------------- ANNOUNCES ----------------

function insertPlaceholder(textareaId, text) {
    var textarea = document.getElementById(textareaId);
    var cursorPos = textarea.selectionStart;
    var v = textarea.value;
    var textBefore = v.substring(0, cursorPos);
    var textAfter = v.substring(cursorPos, v.length);

    textarea.value = textBefore + text + textAfter;
    textarea.selectionStart = cursorPos + text.length;
    textarea.selectionEnd = cursorPos + text.length;
    textarea.focus();
}

// permet de recharger la partie 'lecteur' si l'on modifie le fichier dans "Librairie sonore"
htmx.on('htmx:afterSwap', function(evt) {
    // Vérifiez que l'échange concerne bien le contenu de la modale
    if (evt.detail.target.id === 'modal_display_gallery') {
        var closeModalButton = document.getElementById('closeModalButton');

        if (closeModalButton) {
            closeModalButton.addEventListener('click', function() {
                // Déclencher l'événement personnalisé pour HTMX
                var event = new Event('closeModalEvent');
                document.getElementById('announce_current_signal').dispatchEvent(event);
            });
        }
    }
    
});


// Créez un élément audio global
let audioPlayer = new Audio();

// Fonction pour jouer l'audio
function playAudio(audioUrl) {
    audioPlayer.src = audioUrl.data;
    audioPlayer.play().catch(error => {
        console.error("Erreur lors de la lecture audio:", error);
    });
}



// ---------------- TASKS ----------------

function refresh_schedule_tasks_list(data) {
    htmx.trigger('#div_schedule_tasks_list', 'refresh_schedule_tasks_list', {target: "#div_schedule_tasks_list"});
}


// ---------------- GENERAL ----------------

// L'ancien EventSource vers /events/update_admin_old (route inexistante côté
// serveur) a été supprimé : il ne se déclenchait jamais (404 relancé en boucle)
// et les traitements qu'il portait sont aujourd'hui assurés autrement — les
// retours d'action (toast, liste des tâches planifiées) par Socket.IO
// (/socket_admin), et le nettoyage des formulaires d'ajout par les réponses
// HTMX qui remplacent leur contenu.


// Selects multiples des tableaux (horaires d'une activite, jours d'une plage).
// Ces <select> etaient initialises par un <script> emis DANS la boucle {% for %}
// du gabarit : une page de 30 lignes renvoyait 30 blocs identiques. Ici, une
// seule passe, rejouee apres chaque echange HTMX (voir htmx:afterSettle plus bas).
// `data-placeholder` est lu nativement par select2.
// --- Page « Connexions » de l'App -----------------------------------------
//
// Ce code vivait dans le fragment app_connexion.html, sous un
// `$(document).ready(...)` rejoue a chaque injection HTMX. Delegue ici : pose
// une seule fois, il vaut pour toute injection ulterieure du fragment.
//
// Delegation jQuery et non addEventListener : select2 emet `change` via le
// systeme d'evenements de jQuery, qu'un ecouteur natif ne capte pas de maniere
// fiable.
function afficherListeConnexions() {
    var namespaces = $('#namespaceSelect').val() || [];
    htmx.ajax('POST', '/admin/app/get_connections', {
        target: '#connectionList',
        swap: 'innerHTML',
        values: { 'namespaces[]': namespaces }
    });
}

$(document).on('change', '#namespaceSelect', afficherListeConnexions);
$(document).on('click', '#refreshButton', afficherListeConnexions);

// A l'arrivee du fragment : initialisation de select2 puis premier chargement.
document.addEventListener('htmx:afterSettle', function (evt) {
    var cible = evt.detail && evt.detail.target;
    if (!cible) { return; }
    var select = cible.id === 'namespaceSelect'
        ? cible
        : cible.querySelector('#namespaceSelect');
    if (!select) { return; }
    if (!$(select).data('select2')) { $(select).select2(); }
    afficherListeConnexions();
});


function initSelect2Multiples() {
    $('select.js-select2-multiple').each(function () {
        var $select = $(this);
        if ($select.data('select2')) { return; }   // deja initialise
        $select.select2({ allowClear: true });
    });
}


// Initialisation
document.addEventListener('htmx:afterSettle', initSelect2Multiples);
document.addEventListener('DOMContentLoaded', initSelect2Multiples);


document.addEventListener('DOMContentLoaded', function() {

    // initialisation du modal
    var modal = new bootstrap.Modal(document.getElementById('modal_delete'), {
        keyboard: false
    });
});


