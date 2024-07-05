const counter_id = document.getElementById('counter_id').textContent;

var eventSource = new EventSource('/events/update_patients');
var eventSourceforCounter = new EventSource(`/events/update_counter/${counter_id}`);


document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    var socket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_patient');

    socket.on('connect', function() {
        console.log('WebSocket connected');
    });

    socket.on('disconnect', function() {
        console.log('WebSocket disconnected');
    });

    socket.on('update', function(msg) {
        console.log("Received message:", msg.data);
        htmx.trigger('#button_section', 'refresh_buttons', {target: "#button_section"});
        htmx.trigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
        htmx.trigger("#patient_on_queue", 'refresh_queue', {target: "#patient_on_queue"});
        var messages = document.getElementById('messages');
        if (messages) {
            var message = document.createElement('div');
            message.textContent = msg.data;
            messages.appendChild(message);
        } else {
            console.error('Element with ID "messages" not found');
        }
    });

    socket.on('connect_error', function(err) {
        console.error('WebSocket connection error:', err);
    });

    socket.on('reconnect', function(attempt) {
        console.log('WebSocket reconnected after', attempt, 'attempts');
    });

    socket.on('reconnect_attempt', function(attempt) {
        console.log('WebSocket reconnect attempt', attempt);
    });
});

function sendMessage() {
    const url = window.location.protocol + '//' + window.location.host + '/send_message';
    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({message: 'Hello from client'}),
    }).then(response => response.text())
      .then(data => console.log("Message sent:", data))
      .catch(error => console.error('Error:', error));
}

eventSourceforCounter.onmessage = function(event) {
    console.log("Refresh :", event.data);
    htmx.trigger('#button_section', 'refresh_buttons', {target: "#button_section"});
    htmx.trigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
};



eventSource.onmessage = function(event) {
    //htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});
};



document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target.id === "patient_on_queue") {
        attachButtonListeners();
    }
});


function attachButtonListeners() {
    var buttons = document.querySelectorAll('.btn_select_patient');
    buttons.forEach(function(button) {
        button.addEventListener('click', function() {
            var patientId = this.id.split('_').pop(); // Récupérer l'ID du patient depuis l'ID du bouton
            var counterNumber = document.getElementById('counter_id').textContent; // Supposons que vous avez un moyen de connaître le numéro du comptoir
            // Appel AJAX pour appeler le patient spécifique
            console.log(counterNumber, patientId);
            fetch(`/call_specific_patient/${counterNumber}/${patientId}`)
                .then(response => response.text())
                .then(html => {
                    console.log("Patient appelé :", patientId);
                }).catch(error => {
                    console.error("Erreur lors de l'appel du patient :", error);
                });
        });
    });
}


function selectPatient(patientId) {
    console.log("Patient sélectionné :", patientId);
}


