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
        safeTrigger('#button_section', 'refresh_buttons', {target: "#button_section"});
        safeTrigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
        safeTrigger("#patient_on_queue", 'refresh_queue', {target: "#patient_on_queue"});
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

    // Connexion au namespace écran
    var counterSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_counter');

    counterSocket.on('connect', function() {
        console.log('Counter WebSocket connected');
    });

    counterSocket.on('disconnect', function() {
        console.log('Counter WebSocket disconnected');
    });

    counterSocket.on('connect_error', function(err) {
        console.error('Counter WebSocket connection error:', err);
    });

    counterSocket.on('reconnect', function(attempt) {
        console.log('Counter WebSocket reconnected after', attempt, 'attempts');
    });

    counterSocket.on('reconnect_attempt', function(attempt) {
        console.log('Counter WebSocket reconnect attempt', attempt);
    });

    counterSocket.on('update buttons', function() {
        console.log('update buttons', );
        refresh_buttons();
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
    refresh_buttons();    
    htmx.trigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
};

// Verifier l'existence des elements avant d'utiliser htmx.trigger
function safeTrigger(selector, eventName, details) {
    const element = document.querySelector(selector);
    if (element) {
        htmx.trigger(element, eventName, details);
    } else {
        console.error(`Element with selector "${selector}" not found`);
    }
}

function refresh_buttons(){
    console.log("Refresh buttons");
    safeTrigger('#button_section', 'refresh_buttons', {target: "#button_section"});
    console.log("Refresh buttons DONE");
}

eventSource.onmessage = function(event) {
    //htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});
};
