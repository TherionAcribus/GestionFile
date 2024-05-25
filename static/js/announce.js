// création du flux SSE
const eventSourceCalling = new EventSource("/events/update_patients");
        eventSourceCalling.onmessage = function(event) {
            console.log("Calling... SSE");
            refresh_calling_list();            
        };

// Assurez-vous de fermer le flux SSE lorsque la page est fermée/unloaded
window.onunload = function() {
    eventSource.close();
};


function refresh_calling_list() {
    htmx.trigger('#div_calling', 'refresh_calling', {target: '#div_calling'});
    htmx.trigger('#div_ongoing', 'refresh_ongoing', {target: '#div_ongoing'});
}



const eventSourceSoundCalling = new EventSource("/events/sound_calling");
eventSourceSoundCalling.onmessage = function(event) {
    // Utiliser HTMX pour déclencher AUDIO
    console.log("Calling... Audio");
    console.log("Received audio data 1 :", event.data);
    const data = JSON.parse(event.data);
    console.log("Received audio data: 2", data);

    let audioUrl = data.data.audio_url;
    console.log("Playing audio...", audioUrl);
    playAudio(audioUrl);
}

function playAudio(audioUrl) {
    const player = document.getElementById('player');
    console.log("Playing audio...", audioUrl);
    player.src = audioUrl;
    player.play();
    console.log("Playing audio... DONE");
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


