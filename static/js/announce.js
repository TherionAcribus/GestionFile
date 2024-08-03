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
    var screenSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_screen');

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

    screenSocket.on('add_calling', function(msg) {
        console.log("Received screen message:", msg);
        add_calling(msg);
    });

    screenSocket.on('remove_calling', function(msg) {
        console.log("Received screen message:", msg);
        console.log('REMOVED')
        remove_calling(msg);
    });


    screenSocket.on('add_on_counter', function(msg) {
        console.log("Received screen message:", msg);
    });

    screenSocket.on('remove_on_counter', function(msg) {
        console.log("Received screen message REMOVED:", msg);
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
    //htmx.trigger('#div_calling', 'refresh_calling', {target: '#div_calling'});
    //htmx.trigger('#div_ongoing', 'refresh_ongoing', {target: '#div_ongoing'});
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

const announce_call_text_size = document.getElementById('announce_call_text_size').textContent;
const announce_text_up_patients = document.getElementById('announce_text_up_patients').textContent;
const announce_text_up_patients_display = document.getElementById('announce_text_up_patients_display').textContent;
const announce_text_down_patients = document.getElementById('announce_text_down_patients').textContent;
const announce_text_down_patients_display = document.getElementById('announce_text_down_patients_display').textContent;
const display_text_up = document.getElementById('div_display_text_up');
const display_text_down = document.getElementById('div_display_text_down');
const patientList = document.getElementById('patient_list');


function animateElement(element, properties, options) {
    return anime({
        targets: element,
        ...properties,
        ...options
    }).finished;
}


async function add_calling(msg) {   
    
    add_text_up();

    const patient = msg.data;
    console.log('ADDED', patient)
    // Créer un nouvel élément <li>
    const newListItem = document.createElement('li');
    newListItem.id = 'patient-' + patient.id;
    newListItem.className = 'text_patient_calling hidden'; // Caché initialement
    if (announce_call_text_size) {
        newListItem.style.fontSize = announce_call_text_size +'px'; // Taille de texte dynamique
    }    
    newListItem.textContent = patient.text;

    // Ajouter le nouvel élément à la liste
    patientList.appendChild(newListItem);

    remove_text_up(); 
    remove_text_down();

    // Animer l'apparition
    await animateElement(newListItem, {
        opacity: [0, 1],
        translateX: [-50, 0],
        duration: 500,
        easing: 'easeOutQuad'
    }, {
        begin: () => {
            newListItem.classList.remove('hidden');
        }
    });


}

async function remove_calling(msg) {
    // Sélectionner l'élément à supprimer
    console.log('REMOVED', msg)
    const id = msg.data.id;
    const listItem = document.getElementById('patient-' + id);
    if (listItem) {
        // Animer la disparition
        await animateElement(listItem, {
            opacity: [1, 0],
            translateX: [0, 50],
            duration: 500,
            easing: 'easeInQuad'
        });
        listItem.remove();
    }

    // reste t'il des enfants ?
    var childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);

    if (announce_text_up_patients_display == "empty"){
        add_text_up();
    } else if (announce_text_up_patients_display == "full"){
        remove_text_up();
    }
    if (announce_text_down_patients_display == "empty"){
        add_text_down();
    }  

}


function add_text_up(){
    if ((announce_text_up_patients != "") 
        || (announce_text_up_patients_display != "never")){
            childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);
            if (childElements == 0){
            display_text_up.style.display = "block";
        }
    }
}



function remove_text_up(){
    console.log("remove_text_up", announce_text_up_patients_display)
    if (announce_text_up_patients_display == "never"){
        display_text_up.style.display = "none";
    } else if (announce_text_up_patients_display == "empty"){
        childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);
        if (childElements.length > 0){
            display_text_up.style.display = "none";
        }
    }  else if(announce_text_up_patients_display == "full"){
        childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);
        if (childElements.length == 0){
            display_text_up.style.display = "none";
        }
    } 
}


function add_text_down(){
    if ((announce_text_down_patients != "") 
        || (announce_text_down_patients_display != "never")){
        childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);
        if (childElements == 0){
            display_text_down.style.display = "block";
        }

    }
}

// on efface si jamais affiché ou si pas d'enfant
function remove_text_down(){
    console.log("remove_text_down", announce_text_down_patients_display)
    if (announce_text_down_patients_display == "never"){
        display_text_down.style.display = "none";
    } else if (announce_text_down_patients_display == "empty"){
        childElements = Array.from(patientList.childNodes).filter(node => node.nodeType === Node.ELEMENT_NODE);
        if (childElements.length > 0){
            display_text_down.style.display = "none";
        }
    } 
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

remove_text_up();
remove_text_down();