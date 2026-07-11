document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    // Socket.IO expects an http(s) URL. Use same-origin host/port for reverse proxies (Coolify).
    var socketProtocol = protocol === 'https:' ? 'https://' : 'http://';
    var domain = window.location.host;
    var baseUrl = socketProtocol + domain;
    
    // Connexion au namespace général
    var patientSocket = io.connect(baseUrl + '/socket_patient');

    patientSocket.on('connect', function() {
        console.log('Patient WebSocket connected');
    });

    patientSocket.on('disconnect', function() {
        console.log('Patient WebSocket disconnected');
    });

    patientSocket.on('refresh', function(msg) {
        console.log("Received Patient message:", msg);
        refresh_page();
    });

    patientSocket.on('refresh_buttons', function(msg) {
        console.log("REFRESH BUTTONS:", msg);
        refresh_buttons();
    });

    patientSocket.on('refresh_title', function(msg) {
        console.log("Received Patient message:", msg);
        refresh_title();
    });    

    patientSocket.on('update_scan_phone', function(msg) {
        console.log("Update Patient:", msg);
        htmx.trigger('#div_for_scan', 'qrcode_is_scanned');
    });

    patientSocket.on('connect_error', function(err) {
        console.error('Patient WebSocket connection error:', err);
    });

    // En Socket.IO client v4, les évènements de reconnexion sont émis par le
    // Manager (patientSocket.io), PAS par le socket lui-même. Écouter
    // 'reconnect' sur patientSocket ne se déclenchait donc jamais : le
    // rattrapage d'état ci-dessous ne s'exécutait pas.
    patientSocket.io.on('reconnect', function(attempt) {
        console.log('Patient WebSocket reconnected after', attempt, 'attempts');
        // SocketIO ne rejoue pas les évènements manqués pendant la coupure :
        // on rattrape l'état (boutons/titre) au lieu de compter sur le
        // prochain évènement poussé, qui peut ne jamais arriver si rien ne
        // change côté serveur entretemps.
        refresh_buttons();
        refresh_title();
    });

    patientSocket.io.on('reconnect_attempt', function(attempt) {
        console.log('Patient WebSocket reconnect attempt', attempt);
    });

    patientSocket.onAny((event, ...args) => {
        console.log(`Patient WebSocket Event: ${event}`, args);
    });
});


// refresh page pour appliquer les modifications
function refresh_page() {
    console.log("Refresh page...");
    window.location.reload();
}

function refresh_buttons(){
    htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
}

function refresh_title(){
    htmx.trigger('#div_title_area', 'refresh_title', {target: "#div_title_area"});
}


// Point d'entrée UNIQUE pour l'impression, partagé entre la première
// impression (htmx:afterSwap ci-dessous) et la réimpression
// (conclusion_page.html). La Borne expose l'API sous
// window.pywebview.api.printer.print_ticket — et non window.pywebview.api.print_ticket.
// Le contrat de retour est { success, code, message } (voir printer.py).
var _printInProgress = false;

function sendPrintTicket(printData) {
    // Protection contre les clics/déclenchements répétés : tant qu'une
    // impression est en cours, toute nouvelle demande est ignorée pour éviter
    // les doubles tickets.
    if (_printInProgress) {
        console.warn("Impression déjà en cours, demande ignorée.");
        return Promise.resolve({ success: false, code: 'busy', message: 'Impression déjà en cours' });
    }

    if (!printData) {
        console.error("Les données d'impression ne sont pas disponibles.");
        return Promise.resolve({ success: false, code: 'no_data', message: "Données d'impression indisponibles" });
    }

    if (!(window.pywebview && window.pywebview.api && window.pywebview.api.printer)) {
        console.error("L'API PyWebView (printer) n'est pas disponible.");
        return Promise.resolve({ success: false, code: 'no_api', message: "API d'impression indisponible" });
    }

    _printInProgress = true;
    return window.pywebview.api.printer.print_ticket(printData)
        .then(function(result) {
            if (result && result.success) {
                console.log("Impression réussie:", result.message);
            } else {
                console.error("Échec de l'impression:", result ? result.message : result);
            }
            return result;
        })
        .catch(function(error) {
            console.error("Erreur lors de l'impression:", error);
            return { success: false, code: 'exception', message: String(error) };
        })
        .finally(function() {
            _printInProgress = false;
        });
}


// ---------------------------------------------------------------------------
// Flux d'impression de la PREMIÈRE impression (page de conclusion).
//
// Orchestration côté patient (point 5 du diagnostic) :
//   1. écran "Impression en cours" + boutons neutralisés + minuteur stoppé ;
//   2. selon le résultat confirmé par le serveur (/confirm_print) :
//      - succès          -> confirmation normale + (re)démarrage du minuteur ;
//      - échec "ask"      -> écran d'échec avec Réessayer / Appeler le personnel,
//                            AUCUN retour auto tant qu'aucun choix n'est fait ;
//      - échec "keep"     -> numéro affiché en grand + retour auto ;
//      - échec "cancel"   -> message d'erreur (pas de confirmation) + retour auto.
// Le minuteur de retour à l'accueil est exposé par conclusion_page.html
// (window.__conclusionTimer) et n'est (re)lancé qu'après succès ou décision.
// ---------------------------------------------------------------------------

function postPrintConfirmation(printJobId, result) {
    return fetch('/patient/confirm_print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            print_job_id: printJobId,
            success: !!(result && result.success),
            code: result ? result.code : 'unknown',
            message: result ? result.message : ''
        })
    }).then(function(r) { return r.json(); });
}

function postPrintCallStaff(printJobId) {
    return fetch('/patient/print_call_staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ print_job_id: printJobId })
    }).then(function(r) { return r.json(); });
}

// Contrôles du minuteur exposés par conclusion_page.html. Fallback no-op si
// absent (mode scan, ou si le template a changé).
function conclusionTimer() {
    return window.__conclusionTimer || { start: function(){}, stop: function(){}, goHome: function(){} };
}

// Libellés patient du flux d'impression, résolus dans la langue courante et
// injectés par conclusion_page.html (#print_ui_labels). Repli FR si absent.
function printLabels() {
    var defaults = {
        printing: 'Impression en cours…',
        print_failed: 'Impression impossible.',
        retry: 'Réessayer',
        call_staff: 'Appeler le personnel',
        staff_called: 'Le personnel a été prévenu. Veuillez noter votre numéro :',
        no_ticket: 'Ticket non imprimé. Veuillez noter votre numéro :',
        print_failed_staff: 'Impression impossible. Veuillez vous adresser au personnel.',
        back: 'Retour'
    };
    try {
        var el = document.getElementById('print_ui_labels');
        if (el && el.textContent.trim()) {
            var parsed = JSON.parse(el.textContent);
            return Object.assign({}, defaults, parsed);
        }
    } catch (e) {
        console.error('Libellés impression illisibles, repli FR', e);
    }
    return defaults;
}

// Garde-fou anti-blocage de l'écran d'échec « ask » : si le patient ne choisit
// rien au bout du délai configuré, on annule le pending et on retourne à
// l'accueil. Le minuteur est annulé dès qu'un choix est fait (Réessayer / Staff).
var _askAbandonTimeout = null;

function clearAbandonTimer() {
    if (_askAbandonTimeout) {
        clearTimeout(_askAbandonTimeout);
        _askAbandonTimeout = null;
    }
}

function postPrintAbandon(printJobId) {
    return fetch('/patient/print_abandon', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ print_job_id: printJobId })
    }).then(function(r) { return r.json(); });
}

function abandonFlow(printJobId) {
    postPrintAbandon(printJobId)
        .catch(function(e) { console.error('Abandon: erreur', e); })
        .then(function() { conclusionTimer().goHome(); });
}

// Affiche/masque l'overlay d'état d'impression et, en miroir, la confirmation
// normale (on ne veut PAS montrer la confirmation normale pendant l'impression
// ni en cas d'échec).
function setPrintOverlay(visible) {
    var overlay = document.getElementById('print_status_overlay');
    var normal = document.getElementById('conclusion_normal');
    if (overlay) overlay.style.display = visible ? 'flex' : 'none';
    if (normal) normal.style.display = visible ? 'none' : '';
}

// Rend le contenu de l'overlay : un message HTML + une liste de boutons
// [{label, onClick}]. Chaque bouton reprend le style des boutons de validation.
function renderPrintOverlay(messageHtml, buttons) {
    var msg = document.getElementById('print_status_message');
    var btns = document.getElementById('print_status_buttons');
    if (msg) msg.innerHTML = messageHtml;
    if (btns) {
        btns.innerHTML = '';
        (buttons || []).forEach(function(b) {
            var el = document.createElement('div');
            el.className = 'div_validation_one_button';
            el.innerHTML = '<p class="validation_button">' + b.label + '</p>';
            el.addEventListener('click', b.onClick);
            btns.appendChild(el);
        });
    }
    setPrintOverlay(true);
}

function showPrintBusy() {
    conclusionTimer().stop();
    clearAbandonTimer();
    renderPrintOverlay('<p class="text_summary">' + printLabels().printing + '</p>', []);
}

function bigNumberHtml(prefixText, callNumber) {
    var html = '<p class="text_summary">' + prefixText + '</p>';
    if (callNumber) {
        html += '<p class="text_summary" style="font-size:3em;font-weight:bold;">' + callNumber + '</p>';
    }
    return html;
}

// Lance (ou relance) tout le flux impression -> confirmation.
function runPrintFlow(printData, printJobId) {
    showPrintBusy();
    sendPrintTicket(printData)
        .then(function(result) { return postPrintConfirmation(printJobId, result); })
        .then(function(data) { handlePrintConfirmation(printData, printJobId, data); })
        .catch(function(err) {
            console.error('Flux impression: erreur', err);
            var L = printLabels();
            renderPrintOverlay('<p class="text_summary">' + L.print_failed_staff + '</p>', [
                { label: L.back, onClick: function() { conclusionTimer().goHome(); } }
            ]);
        });
}

function handlePrintConfirmation(printData, printJobId, data) {
    console.log('confirm_print:', data);
    var L = printLabels();
    switch (data && data.status) {
        case 'activated':
            // Succès : confirmation normale + (re)démarrage du minuteur.
            setPrintOverlay(false);
            conclusionTimer().start();
            break;
        case 'activated_no_ticket':
            // Conservé (mode keep) : numéro en grand + retour auto.
            renderPrintOverlay(bigNumberHtml(L.no_ticket, data.call_number), []);
            conclusionTimer().start();
            break;
        case 'cancelled':
            // Annulé (mode cancel) : pas de confirmation normale + retour auto.
            renderPrintOverlay('<p class="text_summary">' + L.print_failed_staff + '</p>', []);
            conclusionTimer().start();
            break;
        case 'ask':
            // Décision au patient : Réessayer / Appeler le personnel. AUCUN
            // retour automatique tant qu'aucun choix n'est fait — hormis le
            // garde-fou d'abandon (délai configurable) qui annule et rentre.
            var buttons = [];
            if (data.show_retry) {
                buttons.push({ label: L.retry, onClick: function() { clearAbandonTimer(); runPrintFlow(printData, printJobId); } });
            }
            if (data.show_staff) {
                buttons.push({ label: L.call_staff, onClick: function() { clearAbandonTimer(); callStaffFlow(printJobId); } });
            }
            if (buttons.length === 0) {
                buttons.push({ label: L.back, onClick: function() { clearAbandonTimer(); conclusionTimer().goHome(); } });
            }
            renderPrintOverlay('<p class="text_summary">' + L.print_failed + '</p>', buttons);

            clearAbandonTimer();
            var abandon = parseInt(data.abandon_timer, 10);
            if (abandon > 0) {
                _askAbandonTimeout = setTimeout(function() { abandonFlow(printJobId); }, abandon * 1000);
            }
            break;
        default:
            // 'expired' / inattendu : proposer le retour.
            renderPrintOverlay('<p class="text_summary">' + L.print_failed_staff + '</p>', [
                { label: L.back, onClick: function() { conclusionTimer().goHome(); } }
            ]);
            conclusionTimer().start();
    }
}

function callStaffFlow(printJobId) {
    var L = printLabels();
    renderPrintOverlay('<p class="text_summary">' + L.call_staff + '…</p>', []);
    postPrintCallStaff(printJobId)
        .then(function(data) {
            renderPrintOverlay(bigNumberHtml(L.staff_called, data.call_number), []);
            conclusionTimer().start();
        })
        .catch(function(err) {
            console.error('Appel personnel: erreur', err);
            renderPrintOverlay('<p class="text_summary">' + L.staff_called + '</p>', [
                { label: L.back, onClick: function() { conclusionTimer().goHome(); } }
            ]);
        });
}


document.addEventListener('DOMContentLoaded', function() {
    // Écoute de l'événement htmx:afterSwap sur le document
    document.body.addEventListener('htmx:afterSwap', function(event) {
        console.log("htmx:afterSwap déclenché", event);

        // Vérifier que la cible mise à jour est celle que nous attendons
        if (event.detail.target.id === 'div_buttons_parents') {
            console.log("div_buttons_parents a été mise à jour");

            // Récupérer les données d'impression
            var printDataElement = document.getElementById('print_data');
            if (printDataElement) {
                var printData = printDataElement.getAttribute('data-print');
                var printTicket = printDataElement.getAttribute('print-ticket');
                console.log('printTicket', printTicket);

                // Vérifier si l'impression est demandée
                if (printTicket !== "False") {
                    console.log("Données d'impression récupérées :", printData);
                    var printJobId = printDataElement.getAttribute('data-print-job-id');
                    if (printJobId) {
                        // Flux complet : impression -> confirmation -> activation
                        // (avec écran "impression en cours" et gestion d'échec).
                        runPrintFlow(printData, printJobId);
                    } else {
                        // Pas de job id (cas inattendu) : impression simple.
                        sendPrintTicket(printData);
                    }
                } else {
                    console.log("Pas d'impression demandée");
                }
            } else {
                console.error("L'élément print_data est introuvable.");
            }
        }
    });
});
