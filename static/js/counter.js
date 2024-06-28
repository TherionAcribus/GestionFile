const counter_id = document.getElementById('counter_id').textContent;

var eventSource = new EventSource('/events/update_patients');
var eventSourceforCounter = new EventSource(`/events/update_counter/${counter_id}`);


eventSourceforCounter.onmessage = function(event) {
    console.log("Refresh :", event.data);
    htmx.trigger('#button_section', 'refresh_buttons', {target: "#button_section"});
    htmx.trigger('#div_current_patient', 'refresh_current_patient', {target: "#div_current_patient"});
};


eventSource.onmessage = function(event) {
    htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});
};



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
            var counterNumber = document.getElementById('counter_id').textContent; // Supposons que vous avez un moyen de connaître le numéro du comptoir
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
}


