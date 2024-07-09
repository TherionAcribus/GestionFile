var eventSource = new EventSource('/events/update_page_patient');

eventSource.onmessage = function(event) {
    console.log(event.data);
    var data = JSON.parse(event.data);

    if (event.data == "refresh buttons") {
        htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
    } else if (data.action == "refresh page") {
        console.log("Refresh activities...");
        refresh_page();        
    }
};


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
