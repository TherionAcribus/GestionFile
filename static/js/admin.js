document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    // Connexion au namespace général
    var generalSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_patient');
    console.log("adresse")
    console.log(socketProtocol + domain + ':' + port + '/socket_update_patient')

    generalSocket.on('connect', function() {
        console.log('General WebSocket connected');
    });

    generalSocket.on('disconnect', function() {
        console.log('General WebSocket disconnected');
    });

    generalSocket.on('update', function(msg) {
        console.log("Received general message:", msg);
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
    var adminSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_admin');

    adminSocket.on('connect', function() {
        console.log('Admin WebSocket connected');
    });

    adminSocket.on('disconnect', function() {
        console.log('Admin WebSocket disconnected');
    });

    adminSocket.on('update', function(msg) {
        console.log("Received Admin message:", msg);
        console.log(msg.data.success)
        display_toast(msg);
    });

    adminSocket.on("refresh_activity_table", function(msg) {
        console.log("refresh_activity_table:", msg);
        refresh_activity_table();
        refresh_activity_staff_table();
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
    if (data.data.success === true) {
        console.log('ok');
        M.toast({html: data.data.message, classes: 'green'});
    } else {
        M.toast({html: data.data.message, classes: 'red'});
    }    
}


// -------------- QUEUE  --------------

var eventSource = new EventSource('/events/update_patients');
eventSource.onmessage = function(event) {
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
};


function refresh_queue(){
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
}


// -------------- ACTIVITY --------------

function refresh_activity_table(){
    htmx.trigger('#div_activity_table', 'refresh_activity_table', {target: "#div_activity_table"});
}

function refresh_activity_staff_table(){
    htmx.trigger('#div_activity_staff_table', 'refresh_activity_staff_table', {target: "#div_activity_staff_table"});
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




function initSelects() {
    var elems = document.querySelectorAll('select');
    var instances = M.FormSelect.init(elems);
}

// réinitialisation des selects, seulement une fois l'htmx chargé
document.body.addEventListener('htmx:afterSwap', function(event) {
    initSelects();
});

document.addEventListener('DOMContentLoaded', function() {
    // initialisation de la sidenav
    var elems = document.querySelectorAll('.sidenav');
    var instances = M.Sidenav.init(elems, {draggable: false});

    // initialisation du modal
    var elems = document.querySelectorAll('.modal');
    var instances = M.Modal.init(elems);

    // initialisation des tabs
    var elems = document.querySelectorAll('.tabs');
    var instance = M.Tabs.init(elems);

    // initialisation du select
    initSelects();
   

});

