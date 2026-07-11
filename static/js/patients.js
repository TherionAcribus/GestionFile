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
                    // Point d'entrée unique, partagé avec la réimpression.
                    sendPrintTicket(printData);
                } else {
                    console.log("Pas d'impression demandée");
                }
            } else {
                console.error("L'élément print_data est introuvable.");
            }
        }
    });
});
