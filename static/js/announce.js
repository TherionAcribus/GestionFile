document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    // Connexion au namespace général
    var generalSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_patient');

    generalSocket.on('connect', function() {
        console.log('General WebSocket connected');
        console.log(generalSocket.io.uri)
    });

    generalSocket.on('disconnect', function() {
        console.log('General WebSocket disconnected');
    });

    generalSocket.on('update', function(msg) {
        console.log("Received general message:", msg);
        refresh_calling_list();
    });

    generalSocket.on('connect_error', function(err) {
        console.error('General WebSocket connection error:', err);
    });

    generalSocket.on('reconnect', function(attempt) {
        console.log('General WebSocket reconnected after', attempt, 'attempts');
    });

    generalSocket.on('reconnect_attempt', function(attempt) {
        console.log('General WebSocket reconnect attempt', attempt);
    });

    generalSocket.onAny((event, ...args) => {
        console.log(`General WebSocket Event: ${event}`, args);
    });

    // Connexion au namespace écran
    var screenSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_sound');

    screenSocket.on('connect', function() {
        console.log('Screen WebSocket connected');
    });

    screenSocket.on('disconnect', function() {
        console.log('Screen WebSocket disconnected');
    });

    screenSocket.on('update', function(msg) {
        console.log("Received screen message:", msg);
        receive_audio(msg);
    });

    screenSocket.on('refresh', function(msg) {
        console.log("Received screen message:", msg);
        refresh_page();
    });

    screenSocket.on('connect_error', function(err) {
        console.error('Screen WebSocket connection error:', err);
    });

    screenSocket.on('reconnect', function(attempt) {
        console.log('Screen WebSocket reconnected after', attempt, 'attempts');
    });

    screenSocket.on('reconnect_attempt', function(attempt) {
        console.log('Screen WebSocket reconnect attempt', attempt);
    });

    screenSocket.onAny((event, ...args) => {
        console.log(`Screen WebSocket Event: ${event}`, args);
    });
});



// création du flux SSE
//const eventSourceCalling = new EventSource("/events/update_patients");
//        eventSourceCalling.onmessage = function(event) {
//            console.log("Calling... SSE");
//            refresh_calling_list();            
//        };

// Assurez-vous de fermer le flux SSE lorsque la page est fermée/unloaded
window.onunload = function() {
    eventSource.close();
};



function refresh_calling_list() {
    htmx.trigger('#div_calling', 'refresh_calling', {target: '#div_calling'});
    htmx.trigger('#div_ongoing', 'refresh_ongoing', {target: '#div_ongoing'});
}


const audioQueue = [];
let isPlaying = false;

const eventSourceSoundCalling = new EventSource("/events/sound_calling");
eventSourceSoundCalling.onmessage = function(event) {
    console.log("Calling... Audio");
    console.log("Received audio data 1 :", event.data);
    const data = JSON.parse(event.data);
    console.log("Received audio data: 2", data);

    let audioUrl = data.data.audio_url;
    console.log("Queueing audio...", audioUrl);
    queueAudio(audioUrl);
}

function receive_audio(msg) {
    console.log("Received audio data :", msg);
    const audioUrl = msg.data;
    console.log("Queueing audio...", audioUrl);
    queueAudio(audioUrl);
}

function queueAudio(audioUrl) {
    audioQueue.push(audioUrl);
    if (!isPlaying) {
        playNextAudio();
    }
}

function playNextAudio() {
    if (audioQueue.length === 0) {
        isPlaying = false;
        return;
    }
    isPlaying = true;
    const nextAudioUrl = audioQueue.shift();
    playAudio(nextAudioUrl);
}

function playAudio(audioUrl) {
    const player = document.getElementById('player');
    console.log("Playing audio...", audioUrl);
    player.src = audioUrl;
    player.play();
    console.log("Playing audio... DONE");

    player.onended = function() {
        console.log("Audio ended");
        playNextAudio();
    }
}


function initializeAudio() {
    const player = document.getElementById('player');
    // Cette fonction est appelée par un clic utilisateur, ce qui 'déverrouille' la capacité de jouer des sons.
    player.src = '/static/audio/beep.wav';
    player.play();  // Essayez de jouer quelque chose immédiatement pour confirmer l'activation.
}


if (Notification.permission !== 'granted'){
    requestPermissions();
}

function requestPermissions() {
            Notification.requestPermission().then(permission => {
                if (permission === 'granted') {
                    alert('Notifications activées!');
                } else {
                    alert('Notifications refusées. Le son ne fonctionnera pas sans cette autorisation.');
                }
            });
        }


// stream permettant de rafraichir la page pour appliquer les modifications
const eventSourceAnnounce = new EventSource("/events/update_announce");
eventSourceAnnounce.onmessage = function(event) {
    console.log("Update announce");
    refresh_page();            
};

// refresh page pour appliquer les modifications
function refresh_page() {
    console.log("Refresh page...");
    eventSourceAnnounce.close(); // Ferme la connexion SSE
    window.location.reload();
}


window.addEventListener('beforeunload', function() {
    eventSourceAnnounce.close();
});