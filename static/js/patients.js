var eventSource = new EventSource('/events/update_page_patient');

eventSource.onmessage = function(event) {
    console.log(event.data);
    htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
};


var socket = io.connect();


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


