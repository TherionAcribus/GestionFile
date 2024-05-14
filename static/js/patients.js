const baseUrl = window.location.origin;

const socket = io(baseUrl, {
    reconnection: true,        // Activer la reconnexion automatique
    reconnectionAttempts: Infinity, // Nombre maximal de tentatives de reconnexion
    reconnectionDelay: 1000,   // Délai initial de reconnexion en millisecondes
    reconnectionDelayMax: 5000, // Délai maximal de reconnexion
    randomizationFactor: 0.5   // Facteur de randomisation du délai de reconnexion
});


socket.on('trigger_valide_activity', function(data) {
    console.log(data.activity);
});


function printDiv(divId) {
    var content = document.getElementById(divId).innerHTML;
    var originalContent = document.body.innerHTML;
    document.body.innerHTML = content;
    window.print();
    document.body.innerHTML = originalContent;
}


