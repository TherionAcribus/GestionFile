document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    // Connexion au namespace général
    var patientSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_patient');

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

    patientSocket.on('reconnect', function(attempt) {
        console.log('Patient WebSocket reconnected after', attempt, 'attempts');
    });

    patientSocket.on('reconnect_attempt', function(attempt) {
        console.log('Patient WebSocket reconnect attempt', attempt);
    });

    patientSocket.onAny((event, ...args) => {
        console.log(`Patient WebSocket Event: ${event}`, args);
    });
});


// refresh page pour appliquer les modifications
function refresh_page() {
    console.log("Refresh page...");
    eventSource.close(); // Ferme la connexion SSE
    window.location.reload();
}

function refresh_buttons(){
    htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
}

function refresh_title(){
    htmx.trigger('#div_title_area', 'refresh_title', {target: "#div_title_area"});
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
                    if (printData) {
                        console.log("Données d'impression récupérées :", printData);

                        // Vérifier que l'API PyWebView est disponible
                        if (window.pywebview && window.pywebview.api) {
                            window.pywebview.api.printer.print_ticket(printData)
                                .then(function(result) {
                                    if (result.success) {
                                        console.log("Impression réussie:", result.message);
                                    } else {
                                        console.error("Échec de l'impression:", result.message);
                                    }
                                })
                                .catch(function(error) {
                                    console.error("Erreur lors de l'impression:", error);
                                });
                        } else {
                            console.error("L'API PyWebView n'est pas disponible.");
                        }
                    } else {
                        console.error("Les données d'impression ne sont pas disponibles.");
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