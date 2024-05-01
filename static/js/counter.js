var socket = io.connect();

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