var eventSource = new EventSource('/events/update_page_patient');

eventSource.onmessage = function(event) {
    console.log(event.data);
    var data = JSON.parse(event.data);

    if (event.data == "refresh buttons") {
        htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
    } else if (data.action == "refresh page") {
        console.log("Refresh activities...");
        refresh_page();        
    }
};


// refresh page pour appliquer les modifications
function refresh_page() {
    console.log("Refresh page...");
    eventSource.close(); // Ferme la connexion SSE
    window.location.reload();
}
