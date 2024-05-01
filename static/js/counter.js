var socket = io.connect();

socket.on('trigger_new_patient', function() {
// Utiliser HTMX pour déclencher une mise à jour
console.log("Nouveau patient...");
htmx.trigger('#patient_on_queue', 'refresh_queue', {target: "#patient_on_queue"});

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