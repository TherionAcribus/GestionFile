document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    // Socket.IO expects an http(s) URL. Use same-origin host/port for reverse proxies (Coolify).
    var socketProtocol = protocol === 'https:' ? 'https://' : 'http://';
    var domain = window.location.host;
    var baseUrl = socketProtocol + domain;
    
    // Connexion au namespace général
    var generalSocket = io.connect(baseUrl + '/socket_update_patient', 
            { query: "username=announce screen" });

    generalSocket.on('connect', function() {
        console.log('General WebSocket connected');
        console.log(generalSocket.io.uri)
    });

    generalSocket.on('disconnect', function() {
        console.log('General WebSocket disconnected');
    });

    generalSocket.on('update', function(msg) {
        console.log("Received general message:", msg);
        // La révision de file accompagne chaque mutation : un trou (évènement
        // manqué hors coupure franche) déclenche une resynchronisation des
        // bannières, comme sur l'App comptoir.
        noteQueueRevision(msg && msg.revision);
        refresh_calling_list();
    });

    generalSocket.on('connect_error', function(err) {
        console.error('General WebSocket connection error:', err);
    });

    // En Socket.IO client v4, les évènements de reconnexion sont émis par le
    // Manager (generalSocket.io), pas par le socket : sans le .io, ce rattrapage
    // ne s'exécutait jamais.
    generalSocket.io.on('reconnect', function(attempt) {
        console.log('General WebSocket reconnected after', attempt, 'attempts');
        // Rattrape les mises à jour manquées pendant la coupure (cet écran
        // tourne sans surveillance, une coupure passée inaperçue le laisserait
        // figé indéfiniment sinon). syncCallList réconcilie les bannières avec
        // l'état autoritatif ; refresh_calling_list recharge « prochains
        // patients ».
        syncCallList();
        refresh_calling_list();
    });

    generalSocket.io.on('reconnect_attempt', function(attempt) {
        console.log('General WebSocket reconnect attempt', attempt);
    });

    generalSocket.onAny((event, ...args) => {
        console.log(`General WebSocket Event: ${event}`, args);
    });

    // Connexion au namespace écran
    var screenSocket = io.connect(baseUrl + '/socket_update_screen', 
        { query: "username=announce screen" });

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

    // NOTE: l'évènement 'spotify' (qui transportait le token d'accès Spotify
    // jusqu'à la page d'annonce pour y instancier un Web Playback SDK) n'est
    // plus émis : la lecture et le ducking sont gérés côté serveur via
    // spotipy (routes/admin_music.py). Le handler et receive_spotify_playlist_old
    // ont été retirés — plus de token OAuth diffusé sur ce namespace.

    screenSocket.on('refresh', function(msg) {
        console.log("Received screen message:", msg);
        refresh_page();
    });

    screenSocket.on('add_calling', function(msg) {
        console.log("Received screen message:", msg);
        add_calling(msg);
        var nextPatientsDiv = document.getElementById('div_next_patients');
        if (nextPatientsDiv) {
            htmx.trigger(nextPatientsDiv, 'refresh_next_patients');
        }
    });

    screenSocket.on('spotify_status', function(msg) {
        console.log("spotify_status:", msg);
        isSpotifyConnected = msg.data;
        console.log("isSpotifyConnected WS:", isSpotifyConnected);
    });

    screenSocket.on('remove_calling', function(msg) {
        console.log("Received screen message:", msg);
        console.log('REMOVED')
        remove_calling(msg);
    });


    // Pas de handlers 'add_on_counter' / 'remove_on_counter' : aucun
    // émetteur serveur, les anciens ne faisaient que console.log.

    screenSocket.on('connect_error', function(err) {
        console.error('Screen WebSocket connection error:', err);
    });

    // Évènements de reconnexion sur le Manager (screenSocket.io), cf. plus haut.
    screenSocket.io.on('reconnect', function(attempt) {
        console.log('Screen WebSocket reconnected after', attempt, 'attempts');
        // Resynchronisation ciblée des bannières via /announce/state au lieu du
        // rechargement complet d'avant : même convergence, sans perdre la file
        // audio en cours ni l'état de la page (le 'refresh' explicite conserve
        // le rechargement pour les changements de configuration).
        syncCallList();
        refresh_calling_list();
        var ongoingDiv = document.getElementById('div_ongoing');
        if (ongoingDiv) {
            htmx.trigger(ongoingDiv, 'refresh_ongoing');
        }
    });

    screenSocket.io.on('reconnect_attempt', function(attempt) {
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

// Le flux SSE ci-dessus est commenté : pas de EventSource à fermer au
// déchargement. On retire le handler qui référençait une variable inexistante
// (ReferenceError systématique au unload).

// Initialisation audio : déclenchée par un clic sur le titre (anciennement
// onclick inline, incompatible avec la CSP script-src 'self').
document.addEventListener('click', function (evt) {
    if (evt.target && evt.target.dataset && evt.target.dataset.action === 'initialize-audio') {
        if (typeof initializeAudio === 'function') {
            initializeAudio();
        }
    }
});


function refresh_calling_list() {
    //htmx.trigger('#div_calling', 'refresh_calling', {target: '#div_calling'});
    //htmx.trigger('#div_ongoing', 'refresh_ongoing', {target: '#div_ongoing'});
    var nextPatientsDiv = document.getElementById('div_next_patients');
    if (nextPatientsDiv) {
        htmx.trigger(nextPatientsDiv, 'refresh_next_patients');
    }
}


// --- Suivi de révision + resynchronisation des bannières d'appel ------------
//
// Les évènements add_calling/remove_calling sont incrémentaux : Socket.IO ne
// rejoue rien, un message perdu hors coupure franche laissait une bannière
// fantôme ou manquante jusqu'au prochain évènement. Chaque mutation de la file
// porte une révision croissante (enveloppe des évènements 'update') : un trou
// détecté -> on recharge l'état autoritatif via /announce/state et on
// réconcilie le DOM. Une resync périodique couvre le cas « dernier évènement
// perdu » sur cet écran qui tourne sans surveillance.

var lastQueueRevision = -1;

function noteQueueRevision(rev) {
    // rev peut être absent (diffusion sans révision si le magasin de révision
    // est en panne) : on ignore plutôt que de casser le suivi.
    if (typeof rev !== 'number') {
        return;
    }
    if (lastQueueRevision >= 0 && rev > lastQueueRevision + 1) {
        console.warn('Évènement(s) de file manqué(s) (rév. ' + lastQueueRevision +
            ' -> ' + rev + ') : resynchronisation des bannières');
        syncCallList();
    }
    if (rev > lastQueueRevision) {
        lastQueueRevision = rev;
    }
}

function syncCallList() {
    fetch('/announce/state')
        .then(function(response) { return response.json(); })
        .then(function(state) {
            // Une réponse plus vieille que ce qu'on a déjà vu (mutation entre
            // la lecture serveur et l'application) est écartée : l'évènement
            // suivant déclenchera une nouvelle resync.
            if (typeof state.revision === 'number' && lastQueueRevision > state.revision) {
                return;
            }
            var wanted = {};
            (state.calling || []).forEach(function(c) { wanted[c.id] = c; });

            // Retire les bannières absentes de l'état autoritatif.
            Array.from(patientList.children).forEach(function(item) {
                var id = Number(String(item.id).replace('patient-', ''));
                if (!wanted[id]) {
                    item.remove();
                }
            });

            // Ajoute ou met à jour les bannières de l'état (sans animation :
            // c'est un rattrapage, pas un nouvel appel).
            (state.calling || []).forEach(function(c) {
                var item = document.getElementById('patient-' + c.id);
                if (!item) {
                    item = document.createElement('li');
                    item.id = 'patient-' + c.id;
                    item.className = 'text_patient_calling';
                    item.setAttribute('data-counter', c.counter_id);
                    patientList.appendChild(item);
                }
                item.textContent = c.text;
            });

            if (typeof state.revision === 'number' && state.revision > lastQueueRevision) {
                lastQueueRevision = state.revision;
            }
            updateEmptyStateTexts();
        })
        .catch(function(err) {
            console.error('Resynchronisation des bannières impossible :', err);
        });
}

// Rattrapage périodique (60 s) : si l'évènement perdu était le DERNIER (rien
// ne redéclenche une resync), la prochaine passe le corrige quand même.
setInterval(syncCallList, 60000);

// Le ducking Spotify (baisser/couper la musique pendant les annonces) est
// désormais géré côté serveur : l'écran d'annonce public n'appelle plus aucune
// route Spotify (voir routes/admin_music.duck_for_announcement).

const audioQueue = [];
let isPlaying = false;
const AUDIO_LOAD_TIMEOUT_MS = 10000;

function receive_audio(msg) {
    console.log("Received audio data :", msg);
    const audioUrl = msg.data;
    console.log("Queueing audio...", audioUrl);
    queueAudio(audioUrl);
}

function queueAudio(audioUrl) {
    if (typeof audioUrl !== 'string' || !audioUrl) {
        console.error('Annonce audio invalide ignorée :', audioUrl);
        return;
    }
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
    let finished = false;
    let started = false;
    let loadTimeout;

    function finish(reason) {
        if (finished) {
            return;
        }
        finished = true;
        window.clearTimeout(loadTimeout);
        player.onended = null;
        player.onerror = null;
        player.onplaying = null;
        if (reason !== 'ended') {
            console.error('Annonce audio abandonnée :', reason, audioUrl);
        }
        playNextAudio();
    }

    player.onended = function() {
        console.log("Audio ended");
        finish('ended');
    };
    player.onerror = function() {
        finish('media-error');
    };
    player.onplaying = function() {
        started = true;
        window.clearTimeout(loadTimeout);
    };

    player.src = audioUrl;
    player.load();
    loadTimeout = window.setTimeout(function() {
        if (!started) {
            finish('load-timeout');
        }
    }, AUDIO_LOAD_TIMEOUT_MS);

    const playAttempt = player.play();
    // play() est une promesse sur les navigateurs modernes : un refus
    // d'autoplay ne doit jamais bloquer les annonces suivantes.
    if (playAttempt && typeof playAttempt.catch === 'function') {
        playAttempt.catch(function(error) {
            finish(error && error.name ? error.name : 'play-rejected');
        });
    }
}


function initializeAudio() {
    const player = document.getElementById('player');
    // Cette fonction est appelée par un clic utilisateur, ce qui 'déverrouille' la capacité de jouer des sons.
    player.src = '/static/audio/beep.wav';
    const playAttempt = player.play();
    if (playAttempt && typeof playAttempt.catch === 'function') {
        playAttempt.catch(function(error) {
            console.error('Activation audio refusée :', error);
        });
    }
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
    console.log('add_calling', msg); 

    const patient = msg.data;
    const counterId = patient.counter_id;

    // Système de sécurité. On vérifie qu'il n'existe plus d'appel pour ce comptoir avant d'en afficher un nouveau
    // Cela peut arriver si bug au cours de la transmission des infos remove_calling
    // Rechercher tous les appels existants pour ce comptoir
    const existingCalls = Array.from(patientList.children).filter(item => 
        item.getAttribute('data-counter') === counterId.toString()
    );

    // Supprimer les appels existants avec animation
    for (const existingCall of existingCalls) {
        await animateElement(existingCall, {
            opacity: [1, 0],
            translateX: [0, 50],
            duration: 500,
            easing: 'easeInQuad'
        });
        existingCall.remove();
    }
    
    add_text_up();

    console.log('ADDED', patient)
    // Créer un nouvel élément <li>
    const newListItem = document.createElement('li');
    newListItem.id = 'patient-' + patient.id;
    newListItem.className = 'text_patient_calling hidden'; // Caché initialement   
    newListItem.textContent = patient.text;
    newListItem.setAttribute('data-counter', counterId);

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

    updateEmptyStateTexts();
}


// Réévalue les textes "file vide / comptoir libre" après tout changement de
// la liste des appels (utilisé par remove_calling et par la resync /announce/state).
function updateEmptyStateTexts() {
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


// NOTE: un EventSource vers /events/update_announce vivait ici. Cette route SSE
// n'existe pas cote serveur : 404 puis reconnexion en boucle. Le rafraichissement
// arrive de toute facon par Socket.IO -- screenSocket.on('refresh') appelle
// refresh_page() plus haut dans ce fichier.

// refresh page pour appliquer les modifications
function refresh_page() {
    console.log("Refresh page...");
    window.location.reload();
}

remove_text_up();
remove_text_down();


// --- Carrousel d'informations (fragment announce/gallery.html) -------------
//
// Ce code vivait dans le fragment, sous un `htmx:afterSettle` REPOSE a chaque
// injection : les ecouteurs s'empilaient. Il est ici enregistre une seule fois.
// Le delai et l'effet de transition, autrefois interpoles par Jinja DANS le
// script, arrivent par des attributs data-* du conteneur.
document.addEventListener('htmx:afterSettle', function () {
    var conteneur = document.querySelector('.swiper[data-swiper-delay]');
    if (!conteneur) { return; }
    if (typeof Swiper === 'undefined') {
        // Librairie non chargée : la galerie reste affichée statique plutôt que
        // de lever une exception à chaque injection HTMX du fragment.
        console.warn('Swiper indisponible : carrousel d\'images non initialisé.');
        return;
    }

    var delai = Number(conteneur.dataset.swiperDelay || 0) * 1000;
    var effet = conteneur.dataset.swiperEffect || 'slide';

    if (window.swiperInstance) {
        // L'instance existe déjà (le conteneur a été rechargé par HTMX avec
        // de nouvelles slides). update() seul ne recharge pas les slides du
        // DOM — il faut détruire et recréer l'instance pour prendre en
        // compte le nouveau contenu.
        window.swiperInstance.destroy(true, true);
        window.swiperInstance = null;
    }
    window.swiperInstance = new Swiper('.swiper', {
        loop: true,
        effect: effet,
        slidesPerView: 1,
        pagination: { el: '.swiper-pagination', clickable: true },
        autoplay: { delay: delai, disableOnInteraction: false }
    });
});
