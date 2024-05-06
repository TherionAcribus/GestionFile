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

// ouvre le modal de confirmation staff
socket.on('open_modal_confirm_delete', function(data) {
    instance_staff.open();
});

// ouvre le modal de confirmation activité
socket.on('open_modal_confirm_delete_activity', function(data) {
    instance_activity.open();
});

// ouvre le modal de confirmation counter
socket.on('open_modal_confirm_delete_counter', function(data) {
    instance_activity.open();
});

// supprime le formulaire d'ajout d'un membre
socket.on('delete_add_staff_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_staff_form').innerHTML = "";
});

// supprime le formulaire d'ajout d'une activité
socket.on('delete_add_activity_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_activity_form').innerHTML = "";
});

// supprime le formulaire d'ajout d'un counter
socket.on('delete_add_counter_form', function(data) {
    console.log("Delete add staff form...");
    document.getElementById('div_add_counter_form').innerHTML = "";
});

document.addEventListener('DOMContentLoaded', function() {
    // initialisation de la sidenav
    var elems = document.querySelectorAll('.sidenav');
    var instances = M.Sidenav.init(elems, {draggable: false});

    // initialisation du modal
    var elems = document.querySelectorAll('.modal');
    var instances = M.Modal.init(elems);
    //var elem_staff = document.getElementById('modal_delete_staff');
    //var instance_staff = M.Modal.getInstance(elem_staff);
    //var elem_activity = document.getElementById('modal_delete_activity');
    //var instance_activity = M.Modal.getInstance(elem_activity);
});

