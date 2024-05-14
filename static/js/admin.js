var socket = io.connect();

// affiche le toast de succes ou d'erreur lors d'un changement d'informations pour un membre
socket.on('display_toast', function(data) {
    // Utilisez HTMX ou une requête AJAX pour rafraîchir le contenu du tableau
    if (data.success === true) {
        M.toast({html: data.message});
    } else {
        M.toast({html: data.message, classes: 'red'});
    }
});


// ------ STAFF ------
// supprime le formulaire d'ajout d'un membre
socket.on('delete_add_rule_form', function(data) {
    document.getElementById('div_add_rule_form').innerHTML = "";
});

// ------ STAFF ------

// ouvre le modal de confirmation staff
socket.on('open_modal_confirm_delete', function(data) {
    instance_staff.open();
});

// supprime le formulaire d'ajout d'un membre
socket.on('delete_add_staff_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_staff_form').innerHTML = "";
});

// ouvre le modal de confirmation bouton supprimer staff
socket.on('open_modal_confirm_delete_button', function(data) {
    instance_activity.open();
});


// ------ ACTIVITE ------

// ouvre le modal de confirmation activité
socket.on('open_modal_confirm_delete_activity', function(data) {
    instance_activity.open();
});

// supprime le formulaire d'ajout d'une activité
socket.on('delete_add_activity_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_activity_form').innerHTML = "";
});


// --- COMPTOIR ---


// ouvre le modal de confirmation counter
socket.on('open_modal_confirm_delete_counter', function(data) {
    instance_activity.open();
});


// supprime le formulaire d'ajout d'un counter
socket.on('delete_add_counter_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_counter_form').innerHTML = "";
});


// -------------- QUEUE  --------------

function refreshQueueTable() {
    console.log("Refresh queue table...");
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
}

// quand un nouveau patient est ajouté, au comptoir, etc... on rafraichit le tableau
socket.on('trigger_new_patient', refreshQueueTable);
socket.on('trigger_patient_ongoing', refreshQueueTable);
socket.on('trigger_patient_calling', refreshQueueTable);


// ouvre le modal de confirmation counter
socket.on('open_modal_confirm_delete_patient_table', function(data) {
    instance_activity.open();
});


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


// ---------------- GENERAL ----------------

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

    // initialisation du select
    initSelects();
    // 
    

});

