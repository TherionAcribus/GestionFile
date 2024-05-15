var socket = io.connect('http://gestionfile.onrender.com:5000');

var eventSource = new EventSource('/stream');
var lastEventTime = 0;

function handleNewPatient() {
    var now = Date.now();
    // Empêcher la double exécution en vérifiant l'heure du dernier événement
    if (now - lastEventTime > 1000) { // Par exemple, ignorer les événements dans un intervalle de 1 seconde
        lastEventTime = now;
        console.log("Nouveau patient...");
        htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});
    }
}

socket.on('trigger_update_patient', function() {
    handleNewPatient();
});

eventSource.onmessage = function(event) {
    console.log("SSE received:", event.data); // Ajout pour voir les données reçues
    var data = JSON.parse(event.data);
    handleNewPatient();
};


socket.on('trigger_new_patient', function() {
// Utiliser HTMX pour déclencher une mise à jour
console.log("Nouveau patient...");
htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});
});

socket.on('trigger_patient_ongoing', function() {
    //htmx.trigger('#change_buttons', 'refresh_buttons', {target: "#validation_buttons"});
    htmx.trigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
    htmx.trigger('#button_section', 'refresh_current_buttons', {target: "#button_section"});
});


// si user se déconnecte de tous les postes on raffraichit tous les postes pour effacer le nom de l'utilisateur
socket.on('trigger_disconnect_staff', function() {
    htmx.trigger('#staff_on_counter', 'refresh_staff_on_counter', {target: "#staff_on_counter"});
});


// affiche la liste des activites par défaut d'un membre de l'équipe dès que l'on raffraichi l'utilisateur au comptoir
socket.on("trigger_connect_staff", function() {
    htmx.trigger('#list_of_activities', 'on_update_staff_on_counter', {target: "#list_of_activities"});
})

document.body.addEventListener('htmx:afterSwap', function(event) {
    if (event.detail.target.id === "patient_on_queue") {
        attachButtonListeners();
    }
});


function attachButtonListeners() {
    var buttons = document.querySelectorAll('.btn_select_patient');
    buttons.forEach(function(button) {
        button.addEventListener('click', function() {
            var patientId = this.id.split('_').pop(); // Récupérer l'ID du patient depuis l'ID du bouton
            var counterNumber = document.getElementById('counter_number').textContent; // Supposons que vous avez un moyen de connaître le numéro du comptoir
            // Appel AJAX pour appeler le patient spécifique
            console.log(counterNumber, patientId);
            fetch(`/call_specific_patient/${counterNumber}/${patientId}`)
                .then(response => response.text())
                .then(html => {
                    console.log("Patient appelé :", patientId);
                }).catch(error => {
                    console.error("Erreur lors de l'appel du patient :", error);
                });
        });
    });
}


function selectPatient(patientId) {
    console.log("Patient sélectionné :", patientId);
    // Ajoutez ici plus de logique pour gérer le patient sélectionné
}

// ces deux fonctions sont appelées via un script dans l'htmx qui affiche le 
// nom de l'utilisateur lorsqu'il est connecté  ou le nom de personne si 
// personne n'est connecté (ou erreur)
function displayButtonLeaveCounter() {
    document.getElementById('button_leave_counter').style.display = 'block';
}

function hideButtonLeaveCounter() {
    document.getElementById('button_leave_counter').style.display = 'none';
}