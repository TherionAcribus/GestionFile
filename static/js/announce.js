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

    screenSocket.on('audio', function(msg) {
        console.log("Received sound message:", msg);
        receive_audio(msg);
    });

    screenSocket.on('spotify', function(msg) {
        console.log("Received spotify message:", msg);
        receive_spotify_playlist_old(msg);
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
        console.log(`Screen WebSocket Event Any: ${event}`, args);
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
    console.log("Playing next audio...", audioQueue);
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


        function receive_spotify_playlist(msg){
            console.log("Received spotify data:", msg);
            const playlistUri = msg.data.playlist_uri;
            const accessToken = msg.data.access_token;
        
            console.log("checkTokenValidity(accessToken)");
            checkTokenValidity(accessToken);
        
            getSpotifyDevices(accessToken);
        
            if (msg.data.shuffle) {
            // Activer le mode shuffle avant de lire la playlist
            setShuffleMode(accessToken, "654b698ab877883de7666432f88bbb9e9de05725", true)
                .then(() => {
                    playSpotifyPlaylistOnDevice(accessToken, "654b698ab877883de7666432f88bbb9e9de05725", playlistUri);
                })
                .catch(error => {
                    console.error('Error setting shuffle mode:', error);
                });
        }

    }


// Fonction pour activer ou désactiver le mode shuffle
function setShuffleMode(accessToken, deviceId, shuffle) {
    return fetch(`https://api.spotify.com/v1/me/player/shuffle?state=${shuffle}&device_id=${deviceId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
        },
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(errorData => {
                console.error('Error setting shuffle mode:', errorData);
                throw new Error(errorData.error.message);
            });
        } else {
            console.log(`Shuffle mode set to ${shuffle ? 'on' : 'off'}`);
        }
    });
}


function getSpotifyDevices(accessToken) {
    fetch('https://api.spotify.com/v1/me/player/devices', {
        method: 'GET',
        headers: {
            'Authorization': `Bearer ${accessToken}`
        }
    })
    .then(response => response.json())
    .then(data => {
        console.log('Available devices:', data.devices);
        // Affiche les appareils disponibles avec leurs IDs
        data.devices.forEach(device => {
            console.log(`Device Name: ${device.name}, Device ID: ${device.id}`);
        });
    })
    .catch(error => {
        console.error('Error fetching devices:', error);
    });
}


function playSpotifyPlaylistOnDevice(accessToken, deviceId, playlistUri) {
    fetch(`https://api.spotify.com/v1/me/player/play?device_id=${deviceId}`, {
        method: 'PUT',
        body: JSON.stringify({ context_uri: playlistUri }),
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
        },
    })
    .then(response => {
        if (response.ok) {
            console.log('Playback started successfully');
        } else {
            return response.json().then(errorData => {
                console.error('Error starting playback:', errorData);
            });
        }
    })
    .catch(error => {
        console.error('Fetch error:', error);
    });
}


        function receive_spotify_playlist_old(msg) {



            console.log("Received spotify data:", msg);
            const playlistUri = msg.data.playlist_uri;
            const accessToken = msg.data.access_token;

            console.log("checkTokenValidity(accessToken)");
            checkTokenValidity(accessToken);


            fetch('https://api.spotify.com/v1/me', {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${accessToken}`
                }
            })
            .then(response => {
                if (response.ok) {
                    console.log('Token is working with the general API');
                } else {
                    console.error('Error with the token on general API:', response.status, response.statusText);
                }
            })
            .catch(error => {
                console.error('Fetch error:', error);
            });

        
            // Log du token et de l'URI de la playlist
            console.log("Playlist URI:", playlistUri);
            console.log("Access Token:", accessToken);
        
            
            // Créer un lecteur Spotify Web API
            var player = new Spotify.Player({
                name: 'PharmaFile Player',
                getOAuthToken: cb => { 
                    console.log("Providing access token to Spotify Player");
                    cb(accessToken);  // Utilisation du token ici
                }
            });
        
            // Connexion au lecteur
            player.connect().then(success => {
                if (success) {
                    console.log('The Web Playback SDK successfully connected to Spotify!');
                } else {
                    console.error('Failed to connect the Web Playback SDK');
                }
            }).catch(error => {
                console.error('Error during player connection:', error);
            });
        
            // Écouter l'événement "ready"
            player.addListener('ready', ({ device_id }) => {
                console.log('Player is ready with Device ID:', device_id);

                checkTokenValidity(accessToken);
                console.log('Authorization Header:', `Bearer ${accessToken}`);
        
                // Démarre la lecture de la playlist sur le device connecté
                console.log('Starting playback for playlist:', playlistUri);
                fetch(`https://api.spotify.com/v1/me/player/play?device_id=${device_id}`, {
                    method: 'PUT',
                    body: JSON.stringify({ context_uri: playlistUri }),
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${accessToken}`
                    },
                })
                .then(response => {
                    if (response.ok) {
                        console.log('Playback started successfully');
                    } else {
                        return response.json().then(errorData => {
                            console.error('Error starting playback:', errorData);
                        });
                    }
                })
                .catch(error => {
                    console.error('Fetch error:', error);
                });
            });
        
            // Écouter l'événement "not_ready"
            player.addListener('not_ready', ({ device_id }) => {
                console.warn('Device ID has gone offline:', device_id);
            });
        
            // Gérer les erreurs du lecteur
            player.addListener('initialization_error', ({ message }) => {
                console.error('Initialization Error:', message);
            });
        
            player.addListener('authentication_error', ({ message }) => {
                console.error('Authentication Error:', message);
            });
        
            player.addListener('account_error', ({ message }) => {
                console.error('Account Error:', message);
            });
        
            player.addListener('playback_error', ({ message }) => {
                console.error('Playback Error:', message);
            });
        }

        function checkTokenValidity(accessToken) {
            return fetch('https://api.spotify.com/v1/me', {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${accessToken}`
                }
            })
            .then(response => {
                if (response.ok) {
                    console.log('Token is valid');
                    return true;
                } else if (response.status === 401) {
                    console.error('Token is invalid or expired');
                    return false;
                } else {
                    console.error('Unexpected error:', response.status);
                    return false;
                }
            })
            .catch(error => {
                console.error('Fetch error:', error);
                return false;
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