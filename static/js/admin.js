

function display_toast(data) {
    console.log('toast', data);
    if (data.success === true) {
        M.toast({html: data.message, classes: 'green'});
    } else {
        M.toast({html: data.message, classes: 'red'});
    }    
}


// -------------- QUEUE  --------------

var eventSource = new EventSource('/events/update_patients');
eventSource.onmessage = function(event) {
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
};





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


// ---------------- GENERAL ----------------

// utiliser pour les communications spécifiques du serveur vers l'admin
var eventSource = new EventSource('/events/update_admin');
eventSource.onmessage = function(event) {
    console.log("toqt ?", event.data);
    console.log(typeof(event.data));
    data = JSON.parse(event.data);
    console.log("toqt ?", data);
    console.log("toqt ?", data.toast);
    if (data.toast){
        display_toast(data);
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
    else if (data.action = "delete_add_rule_form"){
        document.getElementById('div_add_rule_form').innerHTML = "";
    }
    else if (data.action = "delete_add_counter_form"){
        document.getElementById('div_add_counter_form').innerHTML = "";
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

