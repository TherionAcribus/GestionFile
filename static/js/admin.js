document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    // Connexion au namespace général
    var generalSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_patient', 
                                    { query: "username=admin_interface" });
    console.log("adresse")
    console.log(socketProtocol + domain + ':' + port + '/socket_update_patient')

    generalSocket.on('connect', function() {
        console.log('General WebSocket connected');
    });

    generalSocket.on('disconnect', function() {
        console.log('General WebSocket disconnected');
    });

    generalSocket.on('update', function(msg) {
        console.log("Received general message:", msg.flag);
        refresh_queue();
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
    var adminSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_admin',
                    { query: "username=admin_interface" }
    );

    adminSocket.on('connect', function() {
        console.log('Admin WebSocket connected');
    });

    adminSocket.on('disconnect', function() {
        console.log('Admin WebSocket disconnected');
    });

    adminSocket.on('update', function(msg) {
        console.log("Received Admin message:", msg);
        display_toast(msg);
    });

    adminSocket.on("refresh_activity_table", function(msg) {
        console.log("refresh_activity_table:", msg);
        refresh_activity_table();
        refresh_activity_staff_table();
    })

    adminSocket.on("refresh_button_order", function(msg) {
        console.log("refresh_button_order:", msg);
        refresh_button_order();
    })

    adminSocket.on("refresh_counter_order", function(msg) {
        console.log("refresh_counter_order:", msg);
        refresh_counter_order();
    })

    adminSocket.on("refresh_languages_order", function(msg) {
        console.log("refresh_languages_order:", msg);
        refresh_languages_order();
    })

    adminSocket.on("refresh_sound", function(msg) {
        console.log("refresh_sound:", msg);
        refresh_sound();
    })

    adminSocket.on("refresh_colors", function(msg) {
        console.log("refresh_colors:", msg);
        refresh_page();
    })

    adminSocket.on("refresh_dashboard_select", function(msg) {
        console.log("refresh_dashboard_select:", msg);
        refresh_dashboard_select();
    })

    adminSocket.on("display_new_gallery", function(msg) {
        console.log("display_new_gallery:", msg);
        document.getElementById("name").value = ""
        display_new_gallery(msg);
    })

    adminSocket.on("refresh_gallery_list", function(msg) {
        console.log("refresh_gallery_list:", msg);
        refresh_gallery_list(msg);
    })

    adminSocket.on("refresh_schedule_tasks_list", function(msg) {
        console.log("refresh_schedule_tasks_list:", msg);
        refresh_schedule_tasks_list(msg);
    })

    adminSocket.on("refresh_printer_dashboard", function(msg) {
        console.log("refresh_printer_dashboard:", msg);
        refresh_printer_dashboard(msg);
    })

    adminSocket.on("audio_test", function(msg) {
        console.log("audio_test:", msg);
        playAudio(msg);
    })


    adminSocket.on('connect_error', function(err) {
        console.error('Admin WebSocket connection error:', err);
    });

    adminSocket.on('reconnect', function(attempt) {
        console.log('Admin WebSocket reconnected after', attempt, 'attempts');
    });

    adminSocket.on('reconnect_attempt', function(attempt) {
        console.log('Admin WebSocket reconnect attempt', attempt);
    });

    adminSocket.onAny((event, ...args) => {
        console.log(`Admin WebSocket Event: ${event}`, args);
    });
});



function display_toast(data) {
    console.log('toast', data);

    // Déterminez la classe à utiliser pour le toast (success ou error)
    let toastClass = data.data.success === true ? 'bg-success text-white' : 'bg-danger text-white';

    // Créez le contenu HTML pour le toast
    let toastHTML = `
        <div class="toast align-items-center ${toastClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${data.data.message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    // Ajoutez le toast au conteneur des toasts
    let toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.classList.add('toast-container', 'position-fixed', 'top-0', 'end-0', 'p-3');
        document.body.appendChild(toastContainer);
    }
    toastContainer.innerHTML = toastHTML;

    // Initialisez le toast
    let toastElement = toastContainer.querySelector('.toast');
    let toast = new bootstrap.Toast(toastElement);
    toast.show();
}

// -------------- QUEUE  --------------

var eventSource = new EventSource('/events/update_patients');
eventSource.onmessage = function(event) {
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
};


function refresh_queue(){
    var queueTable = document.querySelector('#div_queue_table');
    var card_queue = document.querySelector('#card-queue');
    console.log("card_queue", card_queue)

    // Vérifie si div_queue_table existe
    if (queueTable) {
        htmx.trigger(queueTable, 'refresh_queue_patient', {target: "#div_queue_table"});
    }

    // Vérifie si queue_dashboard existe
    if (card_queue) {
        htmx.trigger(card_queue, 'refresh_queue_patient', {target: "#card_queue"});
    }
}

$(document).ready(function() {
    $('#select_patient_filter').select2({
    placeholder: "Patients à afficher",
    allowClear: true
        });
    });
    window.addEventListener("DOMContentLoaded", (e) => {
        $('select').on('select2:select select2:unselect', function (e) {
            // Déclencher manuellement l'événement change pour HTMX
            $(this).closest('select').get(0).dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

// -------------- GALERIES --------------

function display_new_gallery(data) {
    console.log(data);
    
    // Construire l'URL de la galerie
    let url = "/admin/gallery/__NAME__".replace('__NAME__', data.data);
    console.log("URL", url);

    // Utiliser HTMX pour envoyer une requête GET
    htmx.ajax('GET', url, { target: '#content' });
}


function refresh_gallery_list(data) {
    console.log(data);
    htmx.trigger('#galleries_list', 'refresh_gallery_list', {target: "#galleries_list"});
}

// -------------- DASHBOARD --------------

function refresh_dashboard_select(){
    htmx.trigger('#div_select_dashboard', 'refresh_dashboard_select', {target: "#div_select_dashboard"});
}

// -------------- ACTIVITY --------------

function refresh_activity_table(){
    htmx.trigger('#div_activity_table', 'refresh_activity_table', {target: "#div_activity_table"});
}

function refresh_activity_staff_table(){
    htmx.trigger('#div_activity_staff_table', 'refresh_activity_staff_table', {target: "#div_activity_staff_table"});
}


// ---------------- DASHBOARD ----------------

function refresh_printer_dashboard(){
    htmx.trigger('#card-printer', 'refresh_printer_dashboard', {target: "#card-printer"});
}


// ---------------- BOUTONS ----------------

function submitFile(buttonId) {
    var input = document.getElementById('file-input-' + buttonId);
    var file = input.files[0];
    if (file) {
        var formData = new FormData();
        formData.append('file', file);

        // Pour le débogage, loggez le contenu de FormData
        for (var pair of formData.entries()) {
            console.log(pair[0]+ ', ' + pair[1]); 
        }

        htmx.ajax('POST', '/upload_image/' + buttonId, {
            body: formData,
            headers: {
                'HX-Request': 'true',
                'Content-Type': 'multipart/form-data' // Assurez-vous de ne pas définir explicitement Content-Type
            },
            target: '#button-image-' + buttonId
        });
    } else {
        console.log('No file selected.');
    }
}

function refresh_button_order(){
    htmx.trigger('#order_buttons', 'refresh_buttons_order', {target: "#order_buttons"});
}


function sortable(){
    var el = document.getElementById('list_order_buttons');
    var sortable = Sortable.create(el, {
        animation: 150, // ms, animation speed moving items when sorting, `0` — without animation
        onEnd: function (/**Event*/evt) {
            var itemEl = evt.item;  // dragged HTMLElement
            console.log('New index: ' + evt.newIndex); // index of the new position
            // Vous pouvez ici ajouter une requête pour sauvegarder l'ordre
        }
    });
}

// ---------------- COUNTERS ----------------

function refresh_counter_order(){
    htmx.trigger('#order_counters', 'refresh_counter_order', {target: "#order_counters"});
}

// ---------------- TRANSLATIONS ----------------

function refresh_languages_order(){
    htmx.trigger('#order_languages', 'refresh_languages_order', {target: "#order_languages"});
}


// ---------------- ANNOUNCES ----------------

function refresh_page(){
    location.reload();
}

function refresh_sound(){
    htmx.trigger('#announce_current_signal', 'refresh_sound', {target: "#announce_current_signal"});
}


// ---------------- ANNOUNCES ----------------

function insertPlaceholder(textareaId, text) {
    var textarea = document.getElementById(textareaId);
    var cursorPos = textarea.selectionStart;
    var v = textarea.value;
    var textBefore = v.substring(0, cursorPos);
    var textAfter = v.substring(cursorPos, v.length);

    textarea.value = textBefore + text + textAfter;
    textarea.selectionStart = cursorPos + text.length;
    textarea.selectionEnd = cursorPos + text.length;
    textarea.focus();
}

// permet de recharger la partie 'lecteur' si l'on modifie le fichier dans "Librairie sonore"
htmx.on('htmx:afterSwap', function(evt) {
    // Vérifiez que l'échange concerne bien le contenu de la modale
    if (evt.detail.target.id === 'modal_display_gallery') {
        var closeModalButton = document.getElementById('closeModalButton');
        console.log("Close modal button", closeModalButton);

        if (closeModalButton) {
            closeModalButton.addEventListener('click', function() {
                // Déclencher l'événement personnalisé pour HTMX
                var event = new Event('closeModalEvent');
                console.log("Close modal event dispatched");
                document.getElementById('announce_current_signal').dispatchEvent(event);
            });
        }
    }
    
});


// Créez un élément audio global
let audioPlayer = new Audio();

// Fonction pour jouer l'audio
function playAudio(audioUrl) {
    console.log("Playing audio:", audioUrl);
    audioPlayer.src = audioUrl.data;
    audioPlayer.play().catch(error => {
        console.error("Erreur lors de la lecture audio:", error);
    });
}



// ---------------- TASKS ----------------

function refresh_schedule_tasks_list(data) {
    console.log(data);
    htmx.trigger('#div_schedule_tasks_list', 'refresh_schedule_tasks_list', {target: "#div_schedule_tasks_list"});
}


// ---------------- GENERAL ----------------

// utiliser pour les communications spécifiques du serveur vers l'admin
var eventSource = new EventSource('/events/update_admin_old');
eventSource.onmessage = function(event) {
    console.log("toqt ?", event.data);
    console.log(typeof(event.data));
    data = JSON.parse(event.data);
    console.log("toqt ?", data);
    console.log("ACTION", data.action);
    if (data.toast){
        //display_toast(data);
    }
    else if (event.data === "schedule_tasks_list"){
        htmx.trigger('#div_schedule_tasks_list', 'refresh_schedule_tasks_list', {target: "#div_schedule_tasks_list"});
    }
    else if (data.action === "delete_add_activity_form"){
        document.getElementById('div_add_activity_form').innerHTML = "";
    }
    else if (data.action === "delete_add_schedule_form"){
        document.getElementById('div_add_schedule_form').innerHTML = "";
    }
    else if (data.action=== "delete_add_staff_form"){
        document.getElementById('div_add_staff_form').innerHTML = "";
    }
    else if (data.action == "delete_add_rule_form"){
        document.getElementById('div_add_rule_form').innerHTML = "";
    }
    else if (data.action == "delete_add_counter_form"){
        document.getElementById('div_add_counter_form').innerHTML = "";
    }
    else if (data.action == "delete_add_button_form"){
        console.log("delete_add_button_form");
        document.getElementById('div_add_button_form').innerHTML = "";
    }
    else if (data.action === "delete_add_activity_form_staff"){
        console.log("delete_add_activity_form_staff");
        document.getElementById('div_add_activity_form_staff').innerHTML = "";
    }
};


document.addEventListener('DOMContentLoaded', function() {

    // initialisation du modal
    var modal = new bootstrap.Modal(document.getElementById('modal_delete'), {
        keyboard: false
    });
});

