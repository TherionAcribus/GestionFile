// Parcours QR courant (data-journey-id du fragment affiché, null hors page
// QR). Chaque parcours a une salle Socket.IO dédiée (scan_<uuid>) : la borne
// la rejoint quand le QR apparaît, la quitte quand il disparaît, et
// update_scan_phone n'arrive alors qu'à la borne affichant CE QR — plus de
// confirmation affichée pour le scan d'une autre borne.
var _scanJourney = null;

function syncScanJourney() {
    var el = document.getElementById('div_for_scan');
    var journey = el ? el.getAttribute('data-journey-id') : null;
    if (journey === _scanJourney) { return; }
    var previous = _scanJourney;
    _scanJourney = journey;
    var s = window.__patientSocket;
    if (!s || !s.connected) { return; }
    if (previous) { s.emit('leave_scan_journey', { journey: previous }); }
    if (journey) { s.emit('join_scan_journey', { journey: journey }); }
}

function rejoinScanJourney() {
    var s = window.__patientSocket;
    if (s && s.connected && _scanJourney) {
        s.emit('join_scan_journey', { journey: _scanJourney });
    }
}

document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    // Socket.IO expects an http(s) URL. Use same-origin host/port for reverse proxies (Coolify).
    var socketProtocol = protocol === 'https:' ? 'https://' : 'http://';
    var domain = window.location.host;
    var baseUrl = socketProtocol + domain;

    // Connexion au namespace général
    var patientSocket = io.connect(baseUrl + '/socket_patient');
    window.__patientSocket = patientSocket;

    patientSocket.on('connect', function() {
        console.log('Patient WebSocket connected');
        // Les salles Socket.IO ne survivent pas à une déconnexion : si un QR
        // est affiché à la (re)connexion, on rejoint à nouveau sa salle.
        rejoinScanJourney();
        // Le réseau est de retour : toute la file d'acquittements
        // d'impression en attente peut être vidée.
        drainPrintConfirmations();
        // Accusé pour l'éditeur visuel : la borne déclare la révision qu'elle
        // affiche (méta injectée au rendu) à chaque (re)connexion.
        patientSocket.emit('page_editor_ack', {
            page: 'patient',
            revision: pageEditorRevision(),
        });
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
        var scanDiv = document.getElementById('div_for_scan');
        if (!scanDiv) { return; }
        // Le payload porte l'id du patient inscrit : c'est lui qui identifie
        // la conclusion (le call_number est réutilisé d'un jour à l'autre et
        // peut différer du « futur » affiché). On l'injecte dans hx-vals
        // avant le POST, avec le numéro en repli pour compat.
        var callNumber = msg && msg.data && msg.data.call_number;
        var patientId = msg && msg.data && msg.data.patient_id;
        if (patientId || callNumber) {
            scanDiv.setAttribute('hx-vals', JSON.stringify({
                patient_id: patientId || '',
                patient_call_number: callNumber || ''
            }));
        }
        htmx.trigger('#div_for_scan', 'qrcode_is_scanned');
    });

    // Impression de test déclenchée depuis l'admin (onglet Ticket). Le payload
    // est le même que le flux normal : base64 ESC/POS passé au pont pywebview.
    // Une page ouverte dans un navigateur classique n'imprime rien
    // (sendPrintTicket renvoie 'no_api' sans window.pywebview).
    // msg.flag porte le job_id de corrélation : le résultat est renvoyé au
    // serveur (print_test_result) qui le relaie aux pages admin.
    patientSocket.on('print_ticket', function(msg) {
        console.log("Print ticket demandé par l'admin");
        var jobId = (msg && msg.flag) ? String(msg.flag) : '';
        sendPrintTicket(msg.data).then(function(result) {
            if (!jobId) { return; }
            patientSocket.emit('print_test_result', {
                job_id: jobId,
                success: !!(result && result.success),
                code: (result && result.code) ? String(result.code) : 'unknown',
                message: (result && result.message) ? String(result.message) : '',
                borne_id: (result && result.borne_id) ? String(result.borne_id) : ''
            });
        });
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
function pageEditorRevision() {
    var meta = document.querySelector('meta[name="page-editor-revision"]');
    return meta ? (parseInt(meta.content, 10) || 0) : 0;
}

function refresh_page() {
    if (printFlowActive()) {
        console.log("Refresh différé : flux d'impression en cours.");
        return;
    }
    console.log("Refresh page...");
    window.location.reload();
}

function refresh_buttons(){
    if (printFlowActive()) {
        // Le fragment de conclusion porte un print_job_id en cours : un swap
        // ici détruirait l'écran de décision sans résoudre l'inscription.
        console.log("Refresh des boutons différé : flux d'impression en cours.");
        return;
    }
    htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
}

function refresh_title(){
    htmx.trigger('#div_title_area', 'refresh_title', {target: "#div_title_area"});
}

// Sélecteur de langue : le swap HTMX ne touche pas <html> — on met à jour
// l'attribut lang du document pour que les lecteurs d'écran annoncent la
// bonne langue. Délégation sur document : #main est remplacé à chaque
// changement, un écouteur direct serait perdu.
document.addEventListener('click', function (evt) {
    var button = evt.target && evt.target.closest
        ? evt.target.closest('.flag-button[data-lang-code]') : null;
    if (button) {
        document.documentElement.lang = button.getAttribute('data-lang-code');
    }
});


// Point d'entrée UNIQUE pour l'impression, partagé entre la première
// impression (htmx:afterSwap ci-dessous) et la réimpression
// (conclusion_page.html). La Borne expose l'API sous
// window.pywebview.api.printer.print_ticket — et non window.pywebview.api.print_ticket.
// Contrat de retour : { success, code, message }, plus maybe_printed quand le
// résultat matériel est inconnu et attempted=false quand rien n'a été envoyé.
var _printInProgress = false;
// Un parcours serveur ne doit déclencher qu'UNE impression automatique par
// print_job_id : un re-rendu HTMX du fragment de conclusion n'est pas une
// demande de réimpression. Les réessais volontaires passent options.manual.
var _automaticPrintJobs = {};
var _activePrintJobId = null;
var PRINT_BUSY_RETRY_DELAY_MS = 1000;
var PRINT_BUSY_MAX_ATTEMPTS = 5;
var PRINT_BRIDGE_TIMEOUT_MS = 30000;
var PRINT_REQUEST_TIMEOUT_MS = 15000;

function _printNotAttempted(code, message) {
    return {
        success: false,
        code: code,
        message: message,
        attempted: false,
        maybe_printed: false
    };
}

function sendPrintTicket(printData, printJobId) {
    // Protection contre les clics/déclenchements répétés : tant qu'une
    // impression est en cours, toute nouvelle demande est ignorée pour éviter
    // les doubles tickets. Ce résultat n'est PAS un échec matériel : rien n'a
    // été envoyé à l'imprimante, il ne doit donc jamais être confirmé tel quel.
    if (_printInProgress) {
        console.warn("Impression déjà en cours, demande ignorée.");
        return Promise.resolve(_printNotAttempted('busy', 'Impression déjà en cours'));
    }

    if (!printData || printData === 'False') {
        console.error("Les données d'impression ne sont pas disponibles.");
        return Promise.resolve(_printNotAttempted('no_data', "Données d'impression indisponibles"));
    }

    if (!(window.pywebview && window.pywebview.api && window.pywebview.api.printer &&
          typeof window.pywebview.api.printer.print_ticket === 'function')) {
        console.error("L'API PyWebView (printer) n'est pas disponible.");
        return Promise.resolve(_printNotAttempted('no_api', "API d'impression indisponible"));
    }

    _printInProgress = true;
    var printerApi = window.pywebview.api.printer;
    // La capacité est déclarée par le pont de la borne. Sans elle, on appelle
    // l'ancienne signature à un argument : surtout pas de rejeu après erreur,
    // qui risquerait d'imprimer une seconde fois si l'appel initial était déjà
    // arrivé jusqu'à l'imprimante.
    var supportsPrintJobId = printerApi.print_ticket.supportsPrintJobId === true;
    var printCall = Promise.resolve()
        .then(function() {
            if (supportsPrintJobId) {
                return printerApi.print_ticket(printData, printJobId || null);
            }
            return printerApi.print_ticket(printData);
        });
    // Le pont JS->Python peut laisser une promesse non résolue si la WebView
    // ou le callback natif meurt avant le retour. Sans borne de temps, le
    // verrou JS resterait vrai et bloquerait tout le parcours d'impression.
    // Le résultat reste incertain : le tirage a pu avoir lieu physiquement.
    var bridgeTimer = null;
    var bridgeTimeout = new Promise(function(resolve) {
        bridgeTimer = setTimeout(function() {
            resolve({
                success: false,
                code: 'bridge_timeout',
                message: "Pas de réponse du pont d'impression",
                maybe_printed: true
            });
        }, PRINT_BRIDGE_TIMEOUT_MS);
    });
    return Promise.race([printCall, bridgeTimeout])
        .then(function(result) {
            if (!result || typeof result !== 'object') {
                // La commande a pu atteindre l'imprimante avant de produire un
                // retour invalide : résultat ambigu, pas « échec certain ».
                return {
                    success: false,
                    code: 'invalid_result',
                    message: "Résultat d'impression invalide",
                    maybe_printed: true
                };
            }
            if (result.success) {
                console.log("Impression réussie:", result.message);
            } else {
                console.error("Échec de l'impression:", result.message);
            }
            return result;
        })
        .catch(function(error) {
            console.error("Erreur lors de l'impression:", error);
            return {
                success: false,
                code: 'exception',
                message: String(error),
                // Le rejet peut survenir après l'envoi effectif au périphérique
                // ou pendant le retour du pont : résultat matériel inconnu.
                maybe_printed: true
            };
        })
        .finally(function() {
            if (bridgeTimer) { clearTimeout(bridgeTimer); }
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

function fetchJsonWithTimeout(url, options) {
    var controller = (typeof AbortController === 'function') ? new AbortController() : null;
    options = options || {};
    if (controller) { options.signal = controller.signal; }
    var request = fetch(url, options).then(function(response) {
        // Le délai couvre aussi la lecture du corps : une réponse HTTP qui
        // s'ouvre puis se fige ne doit pas laisser l'écran « en cours ».
        return response.json().then(function(data) {
            return { status: response.status, data: data };
        });
    });
    var timeout = new Promise(function(resolve, reject) {
        var timer = setTimeout(function() {
            if (controller) { controller.abort(); }
            reject(new Error('Délai dépassé pour ' + url));
        }, PRINT_REQUEST_TIMEOUT_MS);
        request.then(
            function() { clearTimeout(timer); },
            function() { clearTimeout(timer); });
    });
    return Promise.race([request, timeout]);
}

function postPrintConfirmation(printJobId, result) {
    return fetchJsonWithTimeout('/patient/confirm_print', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            print_job_id: printJobId,
            success: !!(result && result.success),
            code: result ? result.code : 'unknown',
            message: result ? result.message : '',
            maybe_printed: !!(result && result.maybe_printed)
        })
    }).then(function(r) {
        // 5xx = état serveur inconnu (crash possible avant/après écriture) :
        // l'acquittement doit être retenté. 2xx/4xx = réponse définitive
        // (confirm_print est idempotent), le job peut sortir de la file.
        if (r.status >= 500) { throw new Error('confirm_print HTTP ' + r.status); }
        return r.data;
    });
}

// ---------------------------------------------------------------------------
// File durable des acquittements d'impression.
//
// Après une impression physique réussie, /patient/confirm_print doit
// IMPÉRATIVEMENT atteindre le serveur : sinon le patient repart avec un
// ticket qui n'existe pas dans la file. L'acquittement est donc persisté en
// localStorage AVANT le premier essai, puis retenté jusqu'à réponse
// définitive — coupure réseau, rechargement de page ou redémarrage de la
// borne : la vidange reprend dès que possible (connect Socket.IO, minuterie,
// chargement de page). Repli mémoire si localStorage est indisponible.
// ---------------------------------------------------------------------------

var PRINT_QUEUE_KEY = 'gf_pending_print_confirmations';
var PRINT_ATTEMPT_KEY = 'gf_print_attempts';
var PRINT_ATTEMPT_TTL_MS = 24 * 60 * 60 * 1000;
var PRINT_DRAIN_DELAY_MS = 5000;       // délai de réessai après un échec réseau
var PRINT_DRAIN_PERIOD_MS = 15000;     // vidange périodique de sécurité
var PRINT_CONFIRM_FALLBACK_MS = 45000; // échec affiché si toujours injoignable
var _memPrintQueue = null;             // repli mémoire (localStorage HS)
var _memPrintAttempts = null;          // repli mémoire (localStorage HS)
var _drainingPrintQueue = false;
var _printDrainTimer = null;
var _confirmFallbackTimer = null;
var _printQueueSequence = 0;

function _readPrintQueue() {
    if (_memPrintQueue !== null) { return _memPrintQueue; }
    try {
        var jobs = JSON.parse(localStorage.getItem(PRINT_QUEUE_KEY) || '[]');
        return Array.isArray(jobs) ? jobs : [];
    } catch (e) {
        _memPrintQueue = [];
        return _memPrintQueue;
    }
}

function _writePrintQueue(jobs) {
    if (_memPrintQueue !== null) { _memPrintQueue = jobs; return; }
    try {
        localStorage.setItem(PRINT_QUEUE_KEY, JSON.stringify(jobs));
    } catch (e) {
        _memPrintQueue = jobs;
    }
}

function enqueuePrintConfirmation(printJobId, result, printData) {
    var jobs = _readPrintQueue().filter(function(j) { return j.printJobId !== printJobId; });
    var entry = {
        queueId: printJobId + ':' + Date.now() + ':' + (_printQueueSequence++),
        printJobId: printJobId,
        result: {
            success: !!(result && result.success),
            code: result ? result.code : 'unknown',
            message: result ? result.message : '',
            maybe_printed: !!(result && result.maybe_printed)
        },
        printData: printData || null,
        queuedAt: Date.now()
    };
    jobs.push(entry);
    _writePrintQueue(jobs);
    return entry;
}

function dequeuePrintConfirmation(printJobId, queueId) {
    _writePrintQueue(_readPrintQueue().filter(function(j) {
        // Une vieille requête en vol ne doit pas retirer une décision plus
        // récente réenfilée sous le même print_job_id : on compare la version.
        return j.printJobId !== printJobId ||
               (queueId !== undefined && j.queueId !== queueId);
    }));
}

function hasNewerQueuedConfirmation(printJobId, queueId) {
    return _readPrintQueue().some(function(j) {
        return j.printJobId === printJobId && j.queueId !== queueId;
    });
}

// Journal persistant des TENTATIVES automatiques, distinct de la file des
// acquittements : il est posé AVANT l'appel matériel. Sans lui, un refresh
// ou une réinjection HTMX pendant le pont JS/Python pouvait lancer un second
// ticket physique pour la même inscription.
function _readPrintAttempts() {
    var attempts = null;
    if (_memPrintAttempts !== null) {
        attempts = _memPrintAttempts;
    } else {
        try {
            attempts = JSON.parse(localStorage.getItem(PRINT_ATTEMPT_KEY) || '{}');
        } catch (e) {
            _memPrintAttempts = {};
            attempts = _memPrintAttempts;
        }
        if (!attempts || typeof attempts !== 'object' || Array.isArray(attempts)) {
            attempts = {};
        }
    }
    var now = Date.now();
    Object.keys(attempts).forEach(function(jobId) {
        if (!attempts[jobId] || now - (attempts[jobId].at || 0) > PRINT_ATTEMPT_TTL_MS) {
            delete attempts[jobId];
        }
    });
    return attempts;
}

function _writePrintAttempts(attempts) {
    if (_memPrintAttempts !== null) { _memPrintAttempts = attempts; return; }
    try {
        localStorage.setItem(PRINT_ATTEMPT_KEY, JSON.stringify(attempts));
    } catch (e) {
        _memPrintAttempts = attempts;
    }
}

function getPrintAttempt(printJobId) {
    return printJobId ? _readPrintAttempts()[printJobId] || null : null;
}

function markPrintAttempt(printJobId) {
    if (!printJobId) { return; }
    var attempts = _readPrintAttempts();
    attempts[printJobId] = { state: 'in_progress', at: Date.now() };
    _writePrintAttempts(attempts);
}

function recordPrintAttemptResult(printJobId, result) {
    if (!printJobId || !result) { return; }
    var attempts = _readPrintAttempts();
    attempts[printJobId] = {
        state: 'result',
        at: Date.now(),
        result: {
            success: !!result.success,
            code: result.code || 'unknown',
            message: result.message || '',
            maybe_printed: !!result.maybe_printed
        }
    };
    _writePrintAttempts(attempts);
}

// Job actuellement affiché à l'écran (la conclusion n'en présente qu'un).
// Sert à ne mettre à jour l'UI que pour lui : les jobs plus anciens sont
// acquittés silencieusement — c'est le but de la file.
function displayedPrintJobId() {
    var el = document.getElementById('print_data');
    var id = el ? el.getAttribute('data-print-job-id') : null;
    return id || null;
}

function clearConfirmFallbackTimer() {
    if (_confirmFallbackTimer) {
        clearTimeout(_confirmFallbackTimer);
        _confirmFallbackTimer = null;
    }
}

function schedulePrintDrain(delay) {
    if (_printDrainTimer) { return; }
    _printDrainTimer = setTimeout(function() {
        _printDrainTimer = null;
        drainPrintConfirmations();
    }, delay || PRINT_DRAIN_DELAY_MS);
}

// Vidange séquentielle : un job ne sort de la file qu'après RÉPONSE du
// serveur (succès ou échec applicatif — les deux sont définitifs). Un échec
// réseau arrête la passe et reprogramme un essai : inutile de marteler un
// serveur injoignable.
function drainPrintConfirmations() {
    if (_drainingPrintQueue) { return; }
    var jobs = _readPrintQueue();
    if (!jobs.length) { return; }
    _drainingPrintQueue = true;

    var step = function(index) {
        if (index >= jobs.length) {
            _drainingPrintQueue = false;
            // Un job enfilé pendant la passe n'est pas dans l'instantané :
            // on relance si la file n'est pas vide.
            if (_readPrintQueue().length) { schedulePrintDrain(0); }
            return;
        }
        var job = jobs[index];
        postPrintConfirmation(job.printJobId, job.result)
            .then(function(data) {
                dequeuePrintConfirmation(job.printJobId, job.queueId);
                if (job.printJobId === displayedPrintJobId() &&
                    !hasNewerQueuedConfirmation(job.printJobId, job.queueId)) {
                    clearConfirmFallbackTimer();
                    handlePrintConfirmation(job.printData, job.printJobId, data);
                }
                step(index + 1);
            })
            .catch(function(err) {
                console.warn('confirm_print toujours injoignable, réessai programmé', err);
                _drainingPrintQueue = false;
                schedulePrintDrain();
            });
    };
    step(0);
}

function postPrintCallStaff(printJobId) {
    return fetchJsonWithTimeout('/patient/print_call_staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ print_job_id: printJobId })
    }).then(function(r) { return r.data; });
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
        print_failed: 'Impression impossible. Votre numéro est le {N}.',
        retry: 'Réessayer',
        call_staff: 'Appeler le personnel',
        staff_called: 'Le personnel a été prévenu. Veuillez noter votre numéro :',
        no_ticket: 'Ticket non imprimé. Veuillez noter votre numéro :',
        print_failed_staff: 'Impression impossible. Veuillez vous adresser au personnel.',
        print_uncertain: 'Impossible de vérifier si le ticket est sorti. Votre numéro est le {N}.',
        ticket_received: "J'ai récupéré mon ticket",
        ticket_missing: "Je n'ai pas de ticket",
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
    return fetchJsonWithTimeout('/patient/print_abandon', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ print_job_id: printJobId })
    }).then(function(r) { return r.data; });
}

function abandonFlow(printJobId) {
    // Neutralise immédiatement les choix « Réessayer »/« Personnel » : sinon un
    // clic pendant le POST d'abandon pouvait imprimer un ticket pour une
    // inscription déjà annulée.
    renderPrintOverlay('', []);
    postPrintAbandon(printJobId)
        .then(function(data) {
            if (data && (data.status === 'activated' || data.status === 'standing')) {
                // La confirmation a gagné la course : l'inscription est en file.
                handlePrintConfirmation(null, printJobId, data);
                return;
            }
            finishPrintFlow(printJobId);
            conclusionTimer().goHome();
        })
        .catch(function(e) {
            console.error('Abandon: erreur', e);
            finishPrintFlow(printJobId);
            conclusionTimer().goHome();
        });
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
    renderPrintOverlay(messageHtml(printLabels().printing), []);
}

// Numéro d'appel du patient : passé dans la réponse confirm_print, sinon lu
// dans #print_data (attribut posé au rendu de conclusion_page.html). Sert à
// résoudre la balise {N} des messages d'impression.
function printCallNumber(callNumber) {
    if (callNumber) return callNumber;
    var el = document.getElementById('print_data');
    return el ? (el.getAttribute('data-call-number') || '') : '';
}

// {N} est remplacé par le numéro dans un élément dédié (.print_status_number,
// stylé depuis l'éditeur de page — composant « Erreur d'impression »).
function fillCallNumber(text, callNumber) {
    return String(text).replace(/\{n\}/gi,
        '<span class="print_status_number">' + printCallNumber(callNumber) + '</span>');
}

function messageHtml(text, callNumber) {
    return '<p class="text_summary">' + fillCallNumber(text, callNumber) + '</p>';
}

function bigNumberHtml(prefixText, callNumber) {
    var html = messageHtml(prefixText, callNumber);
    // Sans balise {N} dans le texte, le numéro reste affiché en grand en
    // dessous du message (comportement d'origine).
    if (printCallNumber(callNumber) && !/\{n\}/i.test(prefixText)) {
        html += '<p class="print_status_number">' + printCallNumber(callNumber) + '</p>';
    }
    return html;
}

function finishPrintFlow(printJobId) {
    if (!printJobId || _activePrintJobId === printJobId) {
        _activePrintJobId = null;
    }
}

function printFlowActive() {
    return _printInProgress || _activePrintJobId !== null;
}

// Enfile puis confirme un résultat déjà connu — utilisé après l'appel
// matériel, mais aussi quand le patient tranche un résultat « incertain ».
function confirmPrintResult(printJobId, result, printData) {
    showPrintBusy();
    clearConfirmFallbackTimer();
    // Enfilé AVANT le premier POST : une coupure (réseau, rechargement, crash)
    // entre l'impression physique et l'acquittement ne perd plus l'inscription.
    recordPrintAttemptResult(printJobId, result);
    var queued = enqueuePrintConfirmation(printJobId, result, printData);
    return postPrintConfirmation(printJobId, result)
        .then(function(data) {
            dequeuePrintConfirmation(printJobId, queued && queued.queueId);
            clearConfirmFallbackTimer();
            if (!hasNewerQueuedConfirmation(printJobId, queued && queued.queueId)) {
                handlePrintConfirmation(printData, printJobId, data);
            }
        })
        .catch(function(err) {
            // Réseau/serveur injoignable : l'acquittement reste en file et
            // sera retenté jusqu'à réponse. L'écran « impression en cours »
            // reste affiché puis, passé un délai, bascule sur l'échec — la
            // file continue de vider en arrière-plan dans les deux cas, et
            // une confirmation tardive remettra l'écran d'aplomb si le job
            // est encore affiché.
            console.error('confirm_print injoignable — acquittement conservé en file locale', err);
            schedulePrintDrain();
            _confirmFallbackTimer = setTimeout(function() {
                _confirmFallbackTimer = null;
                var L = printLabels();
                renderPrintOverlay(messageHtml(L.print_failed_staff), [
                    { label: L.back, onClick: function() {
                        finishPrintFlow(printJobId);
                        conclusionTimer().goHome();
                    } }
                ]);
                conclusionTimer().start();
            }, PRINT_CONFIRM_FALLBACK_MS);
        });
}

function _runPrintAttempt(printData, printJobId, busyAttempts) {
    return sendPrintTicket(printData, printJobId)
        .then(function(result) {
            if (result && result.code === 'busy' && result.attempted === false) {
                // Occupation JS transitoire (ex. tirage de test admin) : rien
                // n'a été envoyé pour CE job. On attend puis on retente ; on ne
                // POSTe surtout pas ce « busy » comme un échec d'impression.
                if (busyAttempts >= PRINT_BUSY_MAX_ATTEMPTS) {
                    return confirmPrintResult(printJobId, {
                        success: false,
                        code: 'busy_timeout',
                        message: "Imprimante occupée trop longtemps",
                        attempted: false,
                        // Après les retenues, l'autre tirage en cours peut
                        // avoir été celui de ce job : ne jamais annuler
                        // automatiquement — laisser le patient vérifier.
                        maybe_printed: true
                    }, printData);
                }
                return new Promise(function(resolve) {
                    setTimeout(resolve, PRINT_BUSY_RETRY_DELAY_MS);
                }).then(function() {
                    return _runPrintAttempt(printData, printJobId, busyAttempts + 1);
                });
            }
            return confirmPrintResult(printJobId, result, printData);
        });
}

// Lance le flux impression -> confirmation. options.manual distingue le
// « Réessayer » choisi par le patient des re-rendus automatiques HTMX.
function runPrintFlow(printData, printJobId, options) {
    var manual = !!(options && options.manual);
    if (!manual && printJobId && _automaticPrintJobs[printJobId]) {
        // Re-rendu HTMX : l'écran courant (impression, choix, succès) doit
        // rester tel quel — ne pas l'écraser par « Impression en cours ».
        console.warn('Impression automatique déjà déclenchée pour ce job — rejeu ignoré.');
        return Promise.resolve();
    }
    _activePrintJobId = printJobId || null;
    showPrintBusy();
    if (!manual && printJobId) {
        var attempt = getPrintAttempt(printJobId);
        if (attempt) {
            // Le rejeu automatique ne doit JAMAIS rappeler l'imprimante : il
            // rejoue seulement l'acquittement du dernier résultat connu. Si la
            // page a été rechargée avant la réponse du pont, le résultat est
            // incertain et le patient vérifiera physiquement le ticket.
            _automaticPrintJobs[printJobId] = true;
            return confirmPrintResult(printJobId, attempt.result || {
                success: false,
                code: 'print_interrupted',
                message: 'Impression interrompue avant réponse du pont',
                maybe_printed: true
            }, printData);
        }
        _automaticPrintJobs[printJobId] = true;
    }
    if (printJobId) {
        // Posée avant l'appel matériel : un rechargement pendant l'appel est
        // alors vu comme une tentative déjà lancée, pas comme une nouvelle
        // demande d'impression.
        markPrintAttempt(printJobId);
    }
    return _runPrintAttempt(printData, printJobId, 0);
}

function handlePrintConfirmation(printData, printJobId, data) {
    console.log('confirm_print:', data);
    var L = printLabels();
    switch (data && data.status) {
        case 'activated':
        case 'standing': // réponse perdue puis retentée : déjà en file côté serveur
            // Succès : confirmation normale + (re)démarrage du minuteur.
            finishPrintFlow(printJobId);
            setPrintOverlay(false);
            conclusionTimer().start();
            break;
        case 'activated_no_ticket':
            // Conservé (mode keep) : numéro en grand + retour auto.
            finishPrintFlow(printJobId);
            renderPrintOverlay(bigNumberHtml(L.no_ticket, data.call_number), []);
            conclusionTimer().start();
            break;
        case 'cancelled':
            // Annulé (mode cancel) : pas de confirmation normale + retour auto.
            finishPrintFlow(printJobId);
            renderPrintOverlay(messageHtml(L.print_failed_staff, data.call_number), []);
            conclusionTimer().start();
            break;
        case 'uncertain':
            // La borne ne sait pas si le ticket est sorti : jamais de succès
            // affiché, jamais d'annulation automatique. Le patient tranche ce
            // qu'il voit physiquement, ou demande le personnel.
            var uncertainButtons = [
                { label: L.ticket_received, onClick: function() {
                    clearAbandonTimer();
                    confirmPrintResult(printJobId, {
                        success: true,
                        code: 'ticket_received',
                        message: 'Ticket confirmé par le patient'
                    }, printData);
                } },
                { label: L.ticket_missing, onClick: function() {
                    clearAbandonTimer();
                    confirmPrintResult(printJobId, {
                        success: false,
                        code: 'not_printed',
                        message: 'Ticket absent selon le patient',
                        maybe_printed: false
                    }, printData);
                } }
            ];
            if (data.show_staff) {
                uncertainButtons.push({ label: L.call_staff, onClick: function() { clearAbandonTimer(); callStaffFlow(printJobId); } });
            }
            renderPrintOverlay(bigNumberHtml(L.print_uncertain, data.call_number), uncertainButtons);

            // Garde-fou d'affichage : sans choix, on revient à l'accueil
            // SANS appeler print_abandon. Un résultat incertain ne doit jamais
            // annuler automatiquement l'inscription : le ticket est peut-être
            // sorti. Le pending sera expiré par son TTL et pourra toujours
            // être réconcilié si l'acquittement devient explicite.
            clearAbandonTimer();
            var uncertainAbandon = parseInt(data.abandon_timer, 10);
            if (uncertainAbandon > 0) {
                _askAbandonTimeout = setTimeout(function() {
                    finishPrintFlow(printJobId);
                    conclusionTimer().goHome();
                }, uncertainAbandon * 1000);
            }
            break;
        case 'ask':
            // Décision au patient : Réessayer / Appeler le personnel. AUCUN
            // retour automatique tant qu'aucun choix n'est fait — hormis le
            // garde-fou d'abandon (délai configurable) qui annule et rentre.
            var buttons = [];
            if (data.show_retry) {
                buttons.push({ label: L.retry, onClick: function() {
                    clearAbandonTimer();
                    runPrintFlow(printData, printJobId, { manual: true });
                } });
            }
            if (data.show_staff) {
                buttons.push({ label: L.call_staff, onClick: function() { clearAbandonTimer(); callStaffFlow(printJobId); } });
            }
            if (buttons.length === 0) {
                buttons.push({ label: L.back, onClick: function() {
                    clearAbandonTimer();
                    finishPrintFlow(printJobId);
                    conclusionTimer().goHome();
                } });
            }
            renderPrintOverlay(bigNumberHtml(L.print_failed, data.call_number), buttons);

            clearAbandonTimer();
            var abandon = parseInt(data.abandon_timer, 10);
            if (abandon > 0) {
                _askAbandonTimeout = setTimeout(function() { abandonFlow(printJobId); }, abandon * 1000);
            }
            break;
        default:
            // 'expired' / inattendu : proposer le retour.
            finishPrintFlow(printJobId);
            renderPrintOverlay(messageHtml(L.print_failed_staff), [
                { label: L.back, onClick: function() { conclusionTimer().goHome(); } }
            ]);
            conclusionTimer().start();
    }
}

function callStaffFlow(printJobId) {
    var L = printLabels();
    renderPrintOverlay(messageHtml(L.call_staff + '…'), []);
    postPrintCallStaff(printJobId)
        .then(function(data) {
            if (data && data.staff_called) {
                finishPrintFlow(printJobId);
                renderPrintOverlay(bigNumberHtml(L.staff_called, data.call_number), []);
                conclusionTimer().start();
            } else {
                // Réponse sans confirmation (expired, état déjà tranché…) :
                // on n'annonce PAS un appel qui n'a pas eu lieu.
                callStaffFailed(printJobId);
            }
        })
        .catch(function(err) {
            // Réseau/serveur injoignable : pareil — jamais « personnel
            // prévenu » sans réponse du serveur.
            console.error('Appel personnel: erreur', err);
            callStaffFailed(printJobId);
        });
}

function callStaffFailed(printJobId) {
    var L = printLabels();
    renderPrintOverlay(messageHtml(L.print_failed_staff), [
        { label: L.retry, onClick: function() { callStaffFlow(printJobId); } },
        { label: L.back, onClick: function() {
            finishPrintFlow(printJobId);
            conclusionTimer().goHome();
        } }
    ]);
}


document.addEventListener('DOMContentLoaded', function() {
    // Reprise après rechargement/redémarrage : des acquittements
    // d'impression peuvent être restés en file (le ticket est imprimé, le
    // patient doit rejoindre la file même si personne n'est plus devant).
    drainPrintConfirmations();
    setInterval(drainPrintConfirmations, PRINT_DRAIN_PERIOD_MS);

    // Écoute de l'événement htmx:afterSwap sur le document
    document.body.addEventListener('htmx:afterSwap', function(event) {
        console.log("htmx:afterSwap déclenché", event);

        // Vérifier que la cible mise à jour est celle que nous attendons
        if (event.detail.target.id === 'div_buttons_parents') {
            console.log("div_buttons_parents a été mise à jour");

            // Le fragment QR vient peut-être d'apparaître (ou de disparaître) :
            // rejoint/quitte la salle Socket.IO du parcours affiché.
            syncScanJourney();

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
                        // Le marquage automatique empêche un re-rendu HTMX du
                        // même fragment de relancer un second ticket.
                        runPrintFlow(printData, printJobId, { automatic: true });
                    } else {
                        // Sans print_job_id, l'inscription ne peut pas être
                        // acquittée de façon idempotente. Ne JAMAIS imprimer
                        // « quand même » : un re-rendu relancerait des tickets
                        // impossibles à rattacher au patient.
                        console.error("Impression automatique refusée : print_job_id absent.");
                        var L = printLabels();
                        _activePrintJobId = 'missing-print-job';
                        renderPrintOverlay(messageHtml(L.print_failed_staff), [{
                            label: L.back,
                            onClick: function() {
                                finishPrintFlow(null);
                                conclusionTimer().goHome();
                            }
                        }]);
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


// --- Extraits des fragments de la page patient (Phase 8, point 2) ----------

// Fonction qui permet de désactiver les boutons de validation pour éviter double validation
function disableButtons(button) {
    // Désactive le bouton cliqué
    button.style.pointerEvents = 'none';
    button.style.opacity = '0.6';
    
    // Désactive tous les boutons de validation
    const buttons = document.querySelectorAll('.div_validation_one_button');
    buttons.forEach(function(btn) {
        btn.style.pointerEvents = 'none';
        btn.style.opacity = '0.6';
    });
}


// Etat « activite indisponible » : la bande de boutons est grisee, puis
// retablie apres un delai, et le sous-titre revient a sa valeur par defaut.
//
// Les trois valeurs (delai, sous-titre) venaient de Jinja interpole DANS le
// script du fragment ; elles arrivent desormais par des attributs data-* du
// bloc, ce qui rend ce code generique. Un `console.log({{...}})` de mise au
// point a ete supprime : si l'option de configuration etait vide, il produisait
// un `console.log()` vide, voire une erreur de syntaxe.
function initActiviteIndisponible() {
    var bloc = document.querySelector('[data-activity-inactive]');
    if (!bloc || bloc.dataset.activityInactiveInit === '1') { return; }
    bloc.dataset.activityInactiveInit = '1';

    var boutons = document.getElementById('div_buttons_children');
    if (!boutons) { return; }
    boutons.classList.add('buttons_children_inactive');
    boutons.classList.remove('buttons_children_active');

    var delai = Number(bloc.dataset.activityInactiveDelay || 0) * 1000;
    var sousTitre = bloc.dataset.activityInactiveSubtitle || '';

    setTimeout(function () {
        boutons.classList.remove('buttons_children_inactive');
        boutons.classList.add('buttons_children_active');
        var texte = document.querySelector('.div_text_explanation_inactive');
        if (texte) {
            texte.classList.remove('div_text_explanation_inactive');
            texte.textContent = sousTitre;
        }
    }, delai);
}

document.addEventListener('DOMContentLoaded', initActiviteIndisponible);
document.addEventListener('htmx:afterSettle', initActiviteIndisponible);

// Bouton « Imprimer » générique des fragments (htmx/patient_qr_right_page.html) :
// l'ancien onclick="printDiv(...)" était bloqué par la CSP — et printDiv
// n'existait d'ailleurs plus. Délégation sur data-print-target="<id>".
document.addEventListener('click', function (e) {
    var btn = (e.target && e.target.closest)
        ? e.target.closest('[data-print-target]') : null;
    if (!btn) return;
    var target = document.getElementById(btn.getAttribute('data-print-target'));
    if (!target) return;
    var win = window.open('', '_blank');
    if (!win) return;
    win.document.write('<!doctype html><html><body>' + target.innerHTML + '</body></html>');
    win.document.close();
    win.focus();
    win.print();
    win.close();
});
