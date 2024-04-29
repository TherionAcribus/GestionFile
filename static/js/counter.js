var socket = io.connect();

socket.on('trigger_new_patient', function() {
// Utiliser HTMX pour déclencher une mise à jour
console.log("Nouveau patient...");
htmx.trigger('#patient_on_queue', 'refresh_queue', {target: '#patient_on_queue'});
});